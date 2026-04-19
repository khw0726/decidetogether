"""
Cross-judge evaluation: run LLM-as-a-judge with multiple evaluator models
to eliminate single-judge bias. All evaluators use temperature=0.

Loads pre-compiled rules from disk (compiled_*.json), then scores each
with all three judge models. Produces a 3x3 matrix of quality scores.

Usage:
    python scripts/eval_cross_judge.py
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.eval_cross_llm import UnifiedLLMClient, _get_api_key, MODEL_CONFIGS
from scripts.evaluate_output import _JUDGE_SYSTEM, _JUDGE_TOOL

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent
MAX_CONCURRENT = 5

COMPILER_MODELS = ["claude-sonnet", "claude-sonnet-bedrock", "gemini-pro", "gpt-5.4"]
JUDGE_MODELS = ["claude-sonnet", "gemini-pro", "gpt-5.4"]
DIMS = ["coverage", "logical_correctness", "minimality", "clarity", "anchor_accuracy", "example_quality"]


async def judge_with_llm(
    llm_client: UnifiedLLMClient,
    rule_text: str,
    checklist: list[dict],
    examples: list[dict],
    semaphore: asyncio.Semaphore,
) -> dict:
    """Run the LLM-judge via UnifiedLLMClient with temperature=0."""
    user_parts = [
        f"## Rule text (as seen by compiler)\n{rule_text}",
        f"## Compiled checklist tree\n```json\n{json.dumps(checklist, indent=2)}\n```",
        f"## Generated examples\n```json\n{json.dumps(examples, indent=2)}\n```",
    ]
    user_prompt = "\n\n".join(user_parts)

    async with semaphore:
        try:
            result = await llm_client.call_with_tool(
                system=_JUDGE_SYSTEM,
                user=user_prompt,
                tool=_JUDGE_TOOL,
                max_tokens=4096,
                temperature=0,
            )
            return result
        except Exception as e:
            logger.error(f"Judge failed: {e}")
            return {d: 0 for d in DIMS} | {"notes": f"ERROR: {e}"}


async def main():
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    # Load compiled rules from disk
    compiled_by_compiler: dict[str, list[dict]] = {}
    for model_name in COMPILER_MODELS:
        path = SCRIPTS_DIR / f"compiled_{model_name}.json"
        if not path.exists():
            logger.error(f"Missing {path} — run eval_cross_llm.py compile first")
            sys.exit(1)
        with open(path) as f:
            compiled_by_sub = json.load(f)
        # Flatten to list of rules with checklists
        rules = []
        for sub, sub_rules in compiled_by_sub.items():
            for r in sub_rules:
                if r.get("checklist"):
                    rules.append(r)
        compiled_by_compiler[model_name] = rules
        logger.info(f"Loaded {len(rules)} compiled rules from {model_name}")

    # Run each judge on each compiler's output
    scores: dict[str, dict[str, dict[str, float]]] = {}

    for compiler_name, rules in compiled_by_compiler.items():
        scores[compiler_name] = {}

        if not rules:
            for judge_name in JUDGE_MODELS:
                scores[compiler_name][judge_name] = {d: 0 for d in DIMS} | {"mean": 0}
            continue

        for judge_name in JUDGE_MODELS:
            cfg = MODEL_CONFIGS[judge_name]
            key = _get_api_key(cfg["provider"])
            if not key:
                logger.warning(f"No API key for {cfg['provider']}, skipping judge {judge_name}")
                scores[compiler_name][judge_name] = {d: 0 for d in DIMS} | {"mean": 0}
                continue

            llm_client = UnifiedLLMClient(cfg["provider"], cfg["model"], key)
            logger.info(f"Judging {compiler_name} with {judge_name} (temp=0)...")

            tasks = [
                judge_with_llm(
                    llm_client,
                    r.get("rule_text", ""),
                    r.get("checklist", []),
                    r.get("examples", []),
                    semaphore,
                )
                for r in rules
            ]
            results = await asyncio.gather(*tasks)

            # Average scores
            dim_scores = {d: [] for d in DIMS}
            for result in results:
                for d in DIMS:
                    val = result.get(d, 0)
                    if isinstance(val, (int, float)) and val > 0:
                        dim_scores[d].append(val)

            avg = {}
            for d in DIMS:
                avg[d] = round(sum(dim_scores[d]) / len(dim_scores[d]), 2) if dim_scores[d] else 0
            avg["mean"] = round(sum(avg[d] for d in DIMS) / len(DIMS), 2)
            scores[compiler_name][judge_name] = avg
            logger.info(f"  → mean={avg['mean']}")

    # Print summary table: averaged across judges
    print(f"\n{'='*80}")
    print("Cross-Judge Compilation Quality (temperature=0, averaged across 3 judges)")
    print(f"{'='*80}")
    print(f"\n  {'Dimension':<22}" + "".join(f"{c:>16}" for c in COMPILER_MODELS))
    print(f"  {'-'*(22 + 16 * len(COMPILER_MODELS))}")
    for dim in DIMS + ["mean"]:
        row = f"  {dim:<22}"
        for compiler_name in COMPILER_MODELS:
            vals = [scores[compiler_name].get(j, {}).get(dim, 0) for j in JUDGE_MODELS]
            avg = sum(vals) / len(vals) if vals else 0
            row += f"{avg:>16.2f}"
        print(row)

    # Per-judge breakdown
    for dim in DIMS + ["mean"]:
        print(f"\n  {dim}:")
        header = f"    {'Compiler':<18}" + "".join(f"  judge:{j:<14}" for j in JUDGE_MODELS) + f"  {'Avg':>6}"
        print(header)
        print(f"    {'-'*(18 + 18 * len(JUDGE_MODELS) + 8)}")
        for compiler_name in COMPILER_MODELS:
            row = f"    {compiler_name:<18}"
            vals = []
            for judge_name in JUDGE_MODELS:
                val = scores[compiler_name].get(judge_name, {}).get(dim, 0)
                row += f"{val:>18.2f}"
                vals.append(val)
            avg = sum(vals) / len(vals) if vals else 0
            row += f"{avg:>8.2f}"
            print(row)

    # Save
    output = {
        "scores": scores,
        "compilers": COMPILER_MODELS,
        "judges": JUDGE_MODELS,
        "temperature": 0,
    }
    output_path = SCRIPTS_DIR / "eval_cross_judge_results.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults written to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
