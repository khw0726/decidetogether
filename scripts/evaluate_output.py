"""
Evaluate a compiler_test_output.json file on two layers:
  1. Automated structural checks (pass/fail)
  2. LLM-as-judge on 5 semantic dimensions (1-5 scores)

When rule_descriptions.json is available, the LLM judge is grounded in the
moderator's actual description rather than doing open-ended quality scoring.

Usage (from repo root):
    python scripts/evaluate_output.py
    python scripts/evaluate_output.py --input path/to/output.json --output path/to/results.csv
    python scripts/evaluate_output.py --no-llm
    python scripts/evaluate_output.py --compare scripts/compiler_test_output.json scripts/compiler_test_output_augmented.json

Output: scripts/eval_results.csv
"""

import argparse
import asyncio
import csv
import json
import logging
import re
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_INPUT = Path(__file__).parent / "compiler_test_output.json"
DEFAULT_OUTPUT = Path(__file__).parent / "eval_results.csv"
DESCRIPTIONS_PATH = Path(__file__).parent / "rule_descriptions.json"

MAX_CONCURRENT = 10

_JUDGE_TOOL = {
    "name": "submit_rule_scores",
    "description": "Submit evaluation scores for a compiled rule",
    "input_schema": {
        "type": "object",
        "properties": {
            "coverage": {
                "type": "integer",
                "description": "1-5: Do checklist items cover all distinct requirements in the rule?",
            },
            "logic_specificity": {
                "type": "integer",
                "description": "1-5: Are patterns/rubrics specific enough to avoid false positives?",
            },
            "item_type_fit": {
                "type": "integer",
                "description": "1-5: Is det/str/sub classification appropriate for each item?",
            },
            "example_quality": {
                "type": "integer",
                "description": "1-5: Are examples realistic, diverse, and near the decision boundary?",
            },
            "anchor_accuracy": {
                "type": "integer",
                "description": "1-5: Do rule_text_anchor values quote the rule precisely?",
            },
            "notes": {
                "type": "string",
                "description": "Required if any score <= 3: explain what's missing or weak.",
            },
        },
        "required": [
            "coverage", "logic_specificity", "item_type_fit",
            "example_quality", "anchor_accuracy", "notes",
        ],
    },
}

_JUDGE_SYSTEM = """\
You are an expert evaluator of content moderation rule compilation quality.
You will be given a rule text (as seen by the compiler), its compiled checklist tree,
and generated examples. Optionally you will also see the moderator's full description
of the rule — when present, use it as the authoritative ground truth for coverage.

Score each dimension 1–5:
  5 = excellent, no issues
  4 = good, minor issues
  3 = adequate but notable gaps
  2 = significant problems
  1 = fails the dimension entirely

Dimensions:
- coverage: Do checklist items collectively address all distinct criteria in the rule?
  (If a moderator description is provided, every criterion mentioned there should be covered.)
- logic_specificity: Are regex patterns, structural checks, and LLM rubrics specific enough
  to distinguish true violations from edge cases without over-triggering?
- item_type_fit: Is the det/str/sub classification appropriate? (deterministic = pure pattern
  matching; structural = metadata fields; subjective = requires judgment)
- example_quality: Are examples realistic, diverse, and near the decision boundary — not all
  obvious clear-cut cases?
- anchor_accuracy: Do rule_text_anchor values quote exact phrases from the rule text (not
  paraphrases or invented phrases)?

If any score <= 3, explain what specifically is missing or wrong in the notes field.
"""


# ---------------------------------------------------------------------------
# Layer 1: Automated structural checks
# ---------------------------------------------------------------------------

def _walk_items(items: list[dict], depth: int = 0):
    """Yield (item, depth) for all nodes in the tree."""
    for item in items:
        yield item, depth
        for child in item.get("children", []):
            yield from _walk_items([child], depth + 1)


def structural_checks(rule_text: str, checklist: list[dict], examples: list[dict]) -> dict:
    checks = {
        "anchor_in_rule": True,
        "non_leaf_action": True,
        "regex_compiles": True,
        "tree_depth_ok": True,
        "example_count_ok": True,
        "rubric_nonempty": True,
    }

    label_counts = {"positive": 0, "negative": 0, "borderline": 0}
    for ex in examples:
        label = ex.get("label", "")
        if label in label_counts:
            label_counts[label] += 1
    total_examples = sum(label_counts.values())
    if total_examples < 3 or label_counts["positive"] < 1 or label_counts["negative"] < 1:
        checks["example_count_ok"] = False

    for item, depth in _walk_items(checklist):
        # Anchor check
        anchor = item.get("rule_text_anchor")
        if anchor and anchor not in rule_text:
            checks["anchor_in_rule"] = False

        # Non-leaf action check
        children = item.get("children", [])
        if children and item.get("action") != "continue":
            checks["non_leaf_action"] = False

        # Tree depth
        if depth >= 2:
            checks["tree_depth_ok"] = False

        # Logic checks
        logic = item.get("logic", {})
        item_type = item.get("item_type", "")

        if item_type == "deterministic":
            patterns = logic.get("patterns", [])
            for pat in patterns:
                regex = pat.get("regex", "") if isinstance(pat, dict) else str(pat)
                try:
                    re.compile(regex)
                except re.error:
                    checks["regex_compiles"] = False

        elif item_type == "subjective":
            rubric = logic.get("rubric", "")
            prompt_template = logic.get("prompt_template", "")
            threshold = logic.get("threshold", 0)
            if not rubric or not prompt_template or len(prompt_template) <= 50:
                checks["rubric_nonempty"] = False
            if not (0.4 <= threshold <= 0.95):
                checks["rubric_nonempty"] = False

    return checks


# ---------------------------------------------------------------------------
# Layer 2: LLM-as-judge
# ---------------------------------------------------------------------------

async def llm_judge(
    client: anthropic.AsyncAnthropic,
    model: str,
    rule_text: str,
    checklist: list[dict],
    examples: list[dict],
    description: str | None,
    semaphore: asyncio.Semaphore,
) -> dict:
    user_parts = []

    if description:
        user_parts.append(f"## Moderator's full rule description (ground truth)\n{description}")

    user_parts.append(f"## Rule text (as seen by compiler)\n{rule_text}")
    user_parts.append(f"## Compiled checklist tree\n```json\n{json.dumps(checklist, indent=2)}\n```")
    user_parts.append(f"## Generated examples\n```json\n{json.dumps(examples, indent=2)}\n```")

    user_prompt = "\n\n".join(user_parts)

    async with semaphore:
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=1024,
                system=_JUDGE_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[_JUDGE_TOOL],
                tool_choice={"type": "tool", "name": "submit_rule_scores"},
            )
            return response.content[0].input
        except Exception as e:
            logger.error(f"LLM judge failed: {e}")
            return {
                "coverage": 0, "logic_specificity": 0, "item_type_fit": 0,
                "example_quality": 0, "anchor_accuracy": 0, "notes": f"ERROR: {e}",
            }


# ---------------------------------------------------------------------------
# Evaluation orchestration
# ---------------------------------------------------------------------------

def iter_rules(compiler_output: list[dict]):
    """Yield (subreddit, rule_text, description, triage, checklist, examples) for each rule.

    Handles two formats:
    - Flat (test_compiler_sampled.py): [{subreddit, title, description, triage, checklist, examples}]
    - Nested (test_compiler.py):       [{subreddit, rules: [{rule_text, triage, checklist, examples}]}]
    """
    for entry in compiler_output:
        if "title" in entry:
            # Flat format
            yield (
                entry["subreddit"],
                entry["title"],
                entry.get("description"),
                entry.get("triage") or {},
                entry.get("checklist") or [],
                entry.get("examples") or [],
            )
        else:
            # Nested format
            for rule in entry.get("rules", []):
                yield (
                    entry["subreddit"],
                    rule["rule_text"],
                    None,
                    rule.get("triage") or {},
                    rule.get("checklist") or [],
                    rule.get("examples") or [],
                )


async def evaluate_file(
    input_path: Path,
    output_path: Path,
    use_llm: bool,
    settings: Settings,
) -> list[dict]:
    logger.info(f"Loading {input_path}")
    with open(input_path) as f:
        compiler_output = json.load(f)

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) if use_llm else None
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    rows = []
    judge_tasks = []
    rule_meta = []

    # Layer 1: structural checks (synchronous)
    for subreddit, rule_text, description, triage, checklist, examples in iter_rules(compiler_output):
        rule_type = triage.get("rule_type", "unknown")

        if rule_type != "actionable" or not checklist or "error" in (checklist[0] if checklist else {}):
            continue

        checks = structural_checks(rule_text, checklist, examples)

        rule_meta.append({
            "subreddit": subreddit,
            "rule_text_short": rule_text[:80].replace("\n", " "),
            "rule_type": rule_type,
            "has_description": description is not None,
            **checks,
        })

        if use_llm:
            task = asyncio.create_task(
                llm_judge(client, settings.compiler_model, rule_text, checklist, examples, description, semaphore)
            )
            judge_tasks.append(task)
        else:
            judge_tasks.append(None)

    # Layer 2: LLM judge (parallel)
    if use_llm:
        logger.info(f"Running LLM judge on {len(judge_tasks)} rules (concurrent={MAX_CONCURRENT})")
        judge_results = await asyncio.gather(*judge_tasks)
    else:
        judge_results = [None] * len(judge_tasks)

    llm_cols = ["coverage", "logic_specificity", "item_type_fit", "example_quality", "anchor_accuracy", "notes"]

    for meta, judge in zip(rule_meta, judge_results):
        row = dict(meta)
        if judge:
            for col in llm_cols:
                row[col] = judge.get(col, "")
        else:
            for col in llm_cols:
                row[col] = ""
        rows.append(row)
        logger.info(
            f"  {meta['subreddit']} | {meta['rule_text_short'][:40]!r} | "
            + " ".join(f"{k}={'OK' if v else 'FAIL'}" for k, v in meta.items() if k in (
                "anchor_in_rule", "non_leaf_action", "regex_compiles"
            ))
            + (f" | cov={judge.get('coverage', '?')}" if judge else "")
        )

    # Write CSV
    if not rows:
        logger.warning("No actionable compiled rules found in input.")
        return rows

    fieldnames = list(rows[0].keys())
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

        # Summary rows
        f.write("\n")
        writer.writerow({k: "" for k in fieldnames} | {"subreddit": "--- SUMMARY ---"})

        struct_cols = ["anchor_in_rule", "non_leaf_action", "regex_compiles",
                       "tree_depth_ok", "example_count_ok", "rubric_nonempty"]

        for group_name, group_rows in [
            ("all", rows),
            ("with_description", [r for r in rows if r.get("has_description")]),
            ("without_description", [r for r in rows if not r.get("has_description")]),
        ]:
            if not group_rows:
                continue
            n = len(group_rows)
            summary = {"subreddit": f"MEAN ({group_name}, n={n})"}
            for col in struct_cols:
                rate = sum(1 for r in group_rows if r.get(col)) / n
                summary[col] = f"{rate:.2f}"
            if use_llm:
                for col in ["coverage", "logic_specificity", "item_type_fit",
                             "example_quality", "anchor_accuracy"]:
                    vals = [r[col] for r in group_rows if isinstance(r.get(col), int)]
                    summary[col] = f"{sum(vals)/len(vals):.2f}" if vals else ""
            writer.writerow(summary)

    logger.info(f"Wrote {len(rows)} rows to {output_path}")
    return rows


async def compare_files(
    path_a: Path,
    path_b: Path,
    settings: Settings,
):
    """Evaluate both files and write side-by-side comparison."""
    output_a = path_a.parent / (path_a.stem + "_eval.csv")
    output_b = path_b.parent / (path_b.stem + "_eval.csv")

    logger.info("Evaluating file A...")
    rows_a = await evaluate_file(path_a, output_a, True, settings)
    logger.info("Evaluating file B...")
    rows_b = await evaluate_file(path_b, output_b, True, settings)

    # Print summary diff
    llm_dims = ["coverage", "logic_specificity", "item_type_fit", "example_quality", "anchor_accuracy"]
    print(f"\n{'Dimension':<22} {'File A':>8} {'File B':>8} {'Delta':>8}")
    print("-" * 50)
    for dim in llm_dims:
        vals_a = [r[dim] for r in rows_a if isinstance(r.get(dim), int)]
        vals_b = [r[dim] for r in rows_b if isinstance(r.get(dim), int)]
        mean_a = sum(vals_a) / len(vals_a) if vals_a else 0
        mean_b = sum(vals_b) / len(vals_b) if vals_b else 0
        delta = mean_b - mean_a
        sign = "+" if delta >= 0 else ""
        print(f"{dim:<22} {mean_a:>8.2f} {mean_b:>8.2f} {sign}{delta:>7.2f}")

    print(f"\nDetailed results: {output_a}, {output_b}")


async def main():
    parser = argparse.ArgumentParser(description="Evaluate compiler output quality")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM judge, structural checks only")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("FILE_A", "FILE_B"),
                        help="Compare two output files side-by-side")
    args = parser.parse_args()

    settings = Settings()
    if not args.no_llm and not settings.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY not set. Use --no-llm for structural checks only.")
        sys.exit(1)

    if args.compare:
        await compare_files(args.compare[0], args.compare[1], settings)
    else:
        await evaluate_file(args.input, args.output, not args.no_llm, settings)


if __name__ == "__main__":
    asyncio.run(main())
