"""
Pairwise LLM-as-judge evaluation: compare two compiled rule sets head-to-head.

For each shared rule, the judge sees both trees and picks a winner per dimension.
To mitigate position bias, every pair is evaluated in BOTH orders (A-first and
B-first). If the winner flips between orders, the result is recorded as a
"position-biased tie" and counted separately from genuine ties.

Supports two modes:
  cross-llm  — Compare compiled rules from different compilers (Q5)
  ablation   — Compare with/without atmosphere compilations (Q6)

Usage:
    # Q5: pairwise comparison across compilers (all 3 pairs)
    python scripts/eval_pairwise.py cross-llm

    # Q6: pairwise comparison with/without atmosphere
    python scripts/eval_pairwise.py ablation

    # Use a specific judge model (default: claude-sonnet)
    python scripts/eval_pairwise.py cross-llm --judge claude-sonnet

    # Use all three judges and average (cross-judge)
    python scripts/eval_pairwise.py cross-llm --cross-judge
"""

import argparse
import asyncio
import json
import logging
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.eval_cross_llm import UnifiedLLMClient, _get_api_key, MODEL_CONFIGS
from scripts.evaluate_output import (
    _PAIRWISE_JUDGE_SYSTEM,
    _PAIRWISE_JUDGE_TOOL,
    JUDGE_DIMS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent
MAX_CONCURRENT = 5

COMPILER_MODELS = ["claude-sonnet", "claude-sonnet-bedrock", "gemini-pro", "gpt-5.4"]
JUDGE_MODELS = ["claude-sonnet", "gemini-pro", "gpt-5.4"]


def _build_pairwise_prompt(
    rule_text: str,
    tree_1: dict,
    tree_2: dict,
) -> str:
    """Build the user prompt for a pairwise comparison."""
    parts = [
        f"## Original rule text\n{rule_text}",
        f"## Tree 1\n### Checklist\n```json\n{json.dumps(tree_1['checklist'], indent=2)}\n```"
        f"\n### Examples\n```json\n{json.dumps(tree_1['examples'], indent=2)}\n```",
        f"## Tree 2\n### Checklist\n```json\n{json.dumps(tree_2['checklist'], indent=2)}\n```"
        f"\n### Examples\n```json\n{json.dumps(tree_2['examples'], indent=2)}\n```",
    ]
    return "\n\n".join(parts)


async def pairwise_judge_call(
    llm_client: UnifiedLLMClient,
    rule_text: str,
    tree_1: dict,
    tree_2: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Run a single pairwise judge call."""
    user_prompt = _build_pairwise_prompt(rule_text, tree_1, tree_2)
    async with semaphore:
        try:
            result = await llm_client.call_with_tool(
                system=_PAIRWISE_JUDGE_SYSTEM,
                user=user_prompt,
                tool=_PAIRWISE_JUDGE_TOOL,
                max_tokens=4096,
                temperature=0,
            )
            return result
        except Exception as e:
            logger.error(f"Pairwise judge failed: {e}")
            return {f"{d}_winner": "tie" for d in JUDGE_DIMS} | {
                "overall_winner": "tie",
                "overall_reasoning": f"ERROR: {e}",
            }


def _flip_winner(winner: str) -> str:
    """Swap tree_1 ↔ tree_2, keep tie unchanged."""
    if winner == "tree_1":
        return "tree_2"
    elif winner == "tree_2":
        return "tree_1"
    return winner


async def pairwise_compare_rule(
    llm_client: UnifiedLLMClient,
    rule_text: str,
    tree_a: dict,
    tree_b: dict,
    semaphore: asyncio.Semaphore,
    n_runs: int = 3,
) -> dict:
    """Compare two trees with multiple independent judge calls.

    Runs n_runs judge calls with alternating orders (AB, BA, AB, ...),
    normalizes all results to the A/B reference frame, then resolves
    each dimension by majority vote.

    If no majority exists (e.g., all 3 disagree) → "position_bias_tie".
    """
    # Build alternating-order tasks
    tasks = []
    for i in range(n_runs):
        if i % 2 == 0:
            tasks.append(("ab", pairwise_judge_call(llm_client, rule_text, tree_a, tree_b, semaphore)))
        else:
            tasks.append(("ba", pairwise_judge_call(llm_client, rule_text, tree_b, tree_a, semaphore)))

    raw_results = await asyncio.gather(*[t[1] for t in tasks])
    orders = [t[0] for t in tasks]

    # Normalize all results to A/B reference frame
    normalized = []
    for order, result in zip(orders, raw_results):
        normed = {}
        for dim in JUDGE_DIMS + ["overall"]:
            key = f"{dim}_winner"
            winner = result.get(key, "tie")
            if order == "ba":
                winner = _flip_winner(winner)
            normed[key] = winner
            normed[f"{dim}_reasoning"] = result.get(f"{dim}_reasoning", "")
        normalized.append(normed)

    # Majority vote per dimension
    resolved = {}
    for dim in JUDGE_DIMS + ["overall"]:
        key = f"{dim}_winner"
        votes = [n[key] for n in normalized]
        vote_counts: dict[str, int] = {}
        for v in votes:
            vote_counts[v] = vote_counts.get(v, 0) + 1

        # Find majority (> n_runs / 2)
        majority_winner = None
        for candidate, count in vote_counts.items():
            if count > n_runs / 2:
                majority_winner = candidate
                break

        if majority_winner:
            resolved[key] = majority_winner
            resolved[f"{dim}_position_bias"] = False
        else:
            resolved[key] = "position_bias_tie"
            resolved[f"{dim}_position_bias"] = True

        # Keep reasoning from the first AB-order run
        resolved[f"{dim}_reasoning"] = normalized[0].get(f"{dim}_reasoning", "")

    resolved["rule_text_short"] = rule_text[:80].replace("\n", " ")
    resolved["n_runs"] = n_runs
    resolved["vote_details"] = {
        dim: [n[f"{dim}_winner"] for n in normalized]
        for dim in JUDGE_DIMS + ["overall"]
    }
    return resolved


def _load_compiled(path: Path) -> list[dict]:
    """Load compiled rules from a JSON file, flattening subreddit grouping."""
    with open(path) as f:
        data = json.load(f)
    rules = []
    if isinstance(data, dict):
        for sub, sub_rules in data.items():
            for r in sub_rules:
                if r.get("checklist"):
                    rules.append(r)
    elif isinstance(data, list):
        for r in data:
            if r.get("checklist"):
                rules.append(r)
    return rules


def _match_rules(rules_a: list[dict], rules_b: list[dict]) -> list[tuple[dict, dict]]:
    """Match rules across two sets by (subreddit, rule_text).

    Returns list of (rule_a, rule_b) pairs where rule_text matches.
    """
    index_b = {}
    for r in rules_b:
        key = (r.get("subreddit", ""), r.get("rule_text", ""))
        index_b[key] = r

    pairs = []
    for r in rules_a:
        key = (r.get("subreddit", ""), r.get("rule_text", ""))
        if key in index_b:
            pairs.append((r, index_b[key]))
    return pairs


def _aggregate_pairwise(results: list[dict], label_a: str, label_b: str) -> dict:
    """Aggregate pairwise results into win/tie/loss counts per dimension."""
    agg = {}
    for dim in JUDGE_DIMS + ["overall"]:
        key = f"{dim}_winner"
        bias_key = f"{dim}_position_bias"
        counts = {label_a: 0, label_b: 0, "tie": 0, "position_bias_tie": 0}
        for r in results:
            winner = r.get(key, "tie")
            if winner == "tree_1":
                counts[label_a] += 1
            elif winner == "tree_2":
                counts[label_b] += 1
            elif winner == "position_bias_tie":
                counts["position_bias_tie"] += 1
            else:
                counts["tie"] += 1
        n = len(results)
        bias_count = sum(1 for r in results if r.get(bias_key, False))
        agg[dim] = {
            **counts,
            "total": n,
            "position_bias_rate": round(bias_count / n, 3) if n else 0,
        }
    return agg


def _print_pairwise_table(agg: dict, label_a: str, label_b: str):
    """Print a formatted pairwise comparison table."""
    header = f"  {'Dimension':<22} {label_a:>10} {label_b:>10} {'Tie':>6} {'Pos.Bias':>9} {'Bias%':>7}"
    print(header)
    print(f"  {'-' * (22 + 10 + 10 + 6 + 9 + 7 + 5)}")
    for dim in JUDGE_DIMS + ["overall"]:
        d = agg[dim]
        print(
            f"  {dim:<22} {d[label_a]:>10} {d[label_b]:>10} "
            f"{d['tie']:>6} {d['position_bias_tie']:>9} "
            f"{d['position_bias_rate']:>6.1%}"
        )


async def run_cross_llm(judge_names: list[str], compiler_models: list[str] | None = None, n_runs: int = 3):
    """Q5: Pairwise comparison across compiler models."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    if compiler_models is None:
        compiler_models = COMPILER_MODELS

    # Load all compiled rules
    compiled = {}
    for model_name in compiler_models:
        path = SCRIPTS_DIR / f"compiled_{model_name}.json"
        if not path.exists():
            logger.error(f"Missing {path} — run eval_cross_llm.py compile first")
            sys.exit(1)
        compiled[model_name] = _load_compiled(path)
        logger.info(f"Loaded {len(compiled[model_name])} rules from {model_name}")

    # Generate all pairs
    pairs = list(combinations(compiler_models, 2))
    logger.info(f"Comparing {len(pairs)} compiler pairs: {pairs}")

    all_results = {}

    for judge_name in judge_names:
        cfg = MODEL_CONFIGS[judge_name]
        key = _get_api_key(cfg["provider"])
        if not key:
            logger.warning(f"No API key for {cfg['provider']}, skipping judge {judge_name}")
            continue

        llm_client = UnifiedLLMClient(cfg["provider"], cfg["model"], key)
        judge_results = {}

        for model_a, model_b in pairs:
            pair_key = f"{model_a}_vs_{model_b}"
            matched = _match_rules(compiled[model_a], compiled[model_b])
            logger.info(f"  Judge {judge_name}: {pair_key} — {len(matched)} matched rules")

            if not matched:
                logger.warning(f"  No matched rules for {pair_key}")
                judge_results[pair_key] = {"per_rule": [], "aggregate": {}}
                continue

            tasks = [
                pairwise_compare_rule(llm_client, ra["rule_text"], ra, rb, semaphore, n_runs=n_runs)
                for ra, rb in matched
            ]
            results = await asyncio.gather(*tasks)

            agg = _aggregate_pairwise(results, model_a, model_b)
            judge_results[pair_key] = {
                "per_rule": results,
                "aggregate": agg,
                "label_a": model_a,
                "label_b": model_b,
                "n_runs": n_runs,
            }

            print(f"\n  [{judge_name}] {model_a} vs {model_b} ({len(matched)} rules, {n_runs} runs/pair):")
            _print_pairwise_table(agg, model_a, model_b)

        all_results[judge_name] = judge_results

    # Cross-judge aggregation (if multiple judges)
    if len(all_results) > 1:
        print(f"\n{'='*80}")
        print("Cross-Judge Pairwise Summary (averaged across judges)")
        print(f"{'='*80}")

        for pair_key in [f"{a}_vs_{b}" for a, b in pairs]:
            pair_data = [
                jr[pair_key] for jr in all_results.values()
                if pair_key in jr and jr[pair_key].get("aggregate")
            ]
            if not pair_data:
                continue

            label_a = pair_data[0]["label_a"]
            label_b = pair_data[0]["label_b"]

            print(f"\n  {label_a} vs {label_b}:")
            # Merge per_rule results across judges
            merged_agg = {}
            for dim in JUDGE_DIMS + ["overall"]:
                totals = {label_a: 0, label_b: 0, "tie": 0, "position_bias_tie": 0, "total": 0}
                bias_total = 0
                for pd in pair_data:
                    d = pd["aggregate"].get(dim, {})
                    totals[label_a] += d.get(label_a, 0)
                    totals[label_b] += d.get(label_b, 0)
                    totals["tie"] += d.get("tie", 0)
                    totals["position_bias_tie"] += d.get("position_bias_tie", 0)
                    totals["total"] += d.get("total", 0)
                n = totals["total"]
                totals["position_bias_rate"] = round(
                    (totals["position_bias_tie"]) / n, 3
                ) if n else 0
                merged_agg[dim] = totals
            _print_pairwise_table(merged_agg, label_a, label_b)

    return all_results


async def run_ablation(judge_names: list[str], n_runs: int = 3):
    """Q6: Pairwise comparison with/without atmosphere."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    path_with = SCRIPTS_DIR / "compiled_with_atmosphere.json"
    path_without = SCRIPTS_DIR / "compiled_without_atmosphere.json"

    for p in [path_with, path_without]:
        if not p.exists():
            logger.error(f"Missing {p} — run eval_ablation_atmosphere.py first")
            sys.exit(1)

    rules_with = _load_compiled(path_with)
    rules_without = _load_compiled(path_without)
    matched = _match_rules(rules_with, rules_without)
    logger.info(f"Matched {len(matched)} rules for atmosphere ablation")

    if not matched:
        logger.error("No matched rules between conditions")
        sys.exit(1)

    label_a = "with_atm"
    label_b = "without_atm"
    all_results = {}

    for judge_name in judge_names:
        cfg = MODEL_CONFIGS[judge_name]
        key = _get_api_key(cfg["provider"])
        if not key:
            logger.warning(f"No API key for {cfg['provider']}, skipping judge {judge_name}")
            continue

        llm_client = UnifiedLLMClient(cfg["provider"], cfg["model"], key)
        logger.info(f"Judge {judge_name}: with vs without atmosphere ({len(matched)} rules)")

        tasks = [
            pairwise_compare_rule(llm_client, ra["rule_text"], ra, rb, semaphore, n_runs=n_runs)
            for ra, rb in matched
        ]
        results = await asyncio.gather(*tasks)

        agg = _aggregate_pairwise(results, label_a, label_b)
        all_results[judge_name] = {
            "per_rule": results,
            "aggregate": agg,
            "label_a": label_a,
            "label_b": label_b,
            "n_runs": n_runs,
        }

        print(f"\n  [{judge_name}] With atmosphere vs Without atmosphere ({len(matched)} rules, {n_runs} runs/pair):")
        _print_pairwise_table(agg, label_a, label_b)

    # Cross-judge aggregation
    if len(all_results) > 1:
        print(f"\n{'='*80}")
        print("Cross-Judge Pairwise Summary: Atmosphere Ablation")
        print(f"{'='*80}")

        merged_agg = {}
        for dim in JUDGE_DIMS + ["overall"]:
            totals = {label_a: 0, label_b: 0, "tie": 0, "position_bias_tie": 0, "total": 0}
            for jr in all_results.values():
                d = jr["aggregate"].get(dim, {})
                totals[label_a] += d.get(label_a, 0)
                totals[label_b] += d.get(label_b, 0)
                totals["tie"] += d.get("tie", 0)
                totals["position_bias_tie"] += d.get("position_bias_tie", 0)
                totals["total"] += d.get("total", 0)
            n = totals["total"]
            totals["position_bias_rate"] = round(
                totals["position_bias_tie"] / n, 3
            ) if n else 0
            merged_agg[dim] = totals
        _print_pairwise_table(merged_agg, label_a, label_b)

    return all_results


async def main():
    parser = argparse.ArgumentParser(description="Pairwise LLM-as-judge evaluation")
    sub = parser.add_subparsers(dest="mode", required=True)

    cross = sub.add_parser("cross-llm", help="Q5: compare compiler models pairwise")
    cross.add_argument("--judge", default="claude-sonnet-bedrock", help="Judge model (default: claude-sonnet-bedrock)")
    cross.add_argument("--cross-judge", action="store_true", help="Use all 3 judge models")
    cross.add_argument("--models", nargs="+", default=None,
                       help="Compiler models to compare (default: all with compiled_*.json files)")
    cross.add_argument("--n-runs", type=int, default=3,
                       help="Independent judge calls per pair (default: 3)")

    abl = sub.add_parser("ablation", help="Q6: compare with/without atmosphere pairwise")
    abl.add_argument("--judge", default="claude-sonnet", help="Judge model (default: claude-sonnet)")
    abl.add_argument("--cross-judge", action="store_true", help="Use all 3 judge models")
    abl.add_argument("--n-runs", type=int, default=3,
                       help="Independent judge calls per pair (default: 3)")

    args = parser.parse_args()

    if args.cross_judge:
        judge_names = JUDGE_MODELS
    else:
        judge_names = [args.judge]

    n_runs = getattr(args, "n_runs", 3)

    if args.mode == "cross-llm":
        all_results = await run_cross_llm(judge_names, getattr(args, "models", None), n_runs=n_runs)
        output_path = SCRIPTS_DIR / "eval_pairwise_cross_llm.json"
    else:
        all_results = await run_ablation(judge_names, n_runs=n_runs)
        output_path = SCRIPTS_DIR / "eval_pairwise_ablation.json"

    # Save results (strip reasoning to keep file manageable)
    save_data = {}
    for judge_name, jr in all_results.items():
        if isinstance(jr, dict) and "aggregate" in jr:
            # Ablation mode: single pair
            save_data[judge_name] = {
                "aggregate": jr["aggregate"],
                "label_a": jr["label_a"],
                "label_b": jr["label_b"],
                "n_rules": len(jr["per_rule"]),
            }
        else:
            # Cross-LLM mode: multiple pairs
            save_data[judge_name] = {}
            for pair_key, pair_data in jr.items():
                save_data[judge_name][pair_key] = {
                    "aggregate": pair_data.get("aggregate", {}),
                    "label_a": pair_data.get("label_a", ""),
                    "label_b": pair_data.get("label_b", ""),
                    "n_rules": len(pair_data.get("per_rule", [])),
                }

    with open(output_path, "w") as f:
        json.dump(save_data, f, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
