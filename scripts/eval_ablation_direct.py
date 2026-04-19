"""
Q3 Ablation: Direct prompting baseline.

For each (rule, post) pair in ModBench, asks the LLM directly:
"Does this post violate this rule?" — without any compiled checklist.

Compare results to eval_functional.py (the checklist pipeline) to answer:
"Does compiling into logic improve performance compared to simple prompting?"

Usage:
    python scripts/eval_ablation_direct.py
    python scripts/eval_ablation_direct.py --modbench scripts/modbench.json --limit 20
"""

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent
DEFAULT_MODBENCH = SCRIPTS_DIR / "modbench.json"
DEFAULT_OUTPUT = SCRIPTS_DIR / "eval_ablation_direct_results.json"
MAX_CONCURRENT = 20

_DIRECT_SYSTEM = """\
You are an experienced content moderator. You will be given a community rule \
and a post. Determine whether the post violates the rule.

Use the submit_verdict tool to report your decision."""

_DIRECT_TOOL = {
    "name": "submit_verdict",
    "description": "Submit your moderation verdict for the post",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["approve", "remove", "review"],
                "description": "approve = post is fine; remove = clear violation; review = borderline, needs human review",
            },
            "confidence": {
                "type": "number",
                "description": "Your confidence in this verdict, 0.0-1.0",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of your verdict",
            },
        },
        "required": ["verdict", "confidence", "reasoning"],
    },
}


def _format_post(post: dict) -> str:
    content = post.get("content", {})
    title = content.get("title", "") if isinstance(content, dict) else ""
    body = content.get("body", "") if isinstance(content, dict) else ""
    author = post.get("author", {})
    username = author.get("username", "unknown") if isinstance(author, dict) else "unknown"
    account_age = author.get("account_age_days", "?") if isinstance(author, dict) else "?"

    parts = [f"Author: u/{username} (account age: {account_age} days)"]
    if title:
        parts.append(f"Title: {title}")
    if body:
        parts.append(f"Body: {body}")
    return "\n".join(parts)


def _format_rules(mb_entry: dict) -> str:
    """Format rule(s) from a modbench entry. Handles both formats."""
    # Real format: rules is a list of {title, description}
    if "rules" in mb_entry and isinstance(mb_entry["rules"], list):
        parts = []
        for i, rule in enumerate(mb_entry["rules"], 1):
            title = rule.get("title", f"Rule {i}")
            desc = rule.get("description", "")
            if desc:
                parts.append(f"{i}. {title}\n   {desc}")
            else:
                parts.append(f"{i}. {title}")
        return "\n".join(parts)
    # Compiler format: single rule_text string
    return mb_entry.get("rule_text", "")


async def evaluate_direct(
    client: anthropic.AsyncAnthropic,
    mb_entry: dict,
    model: str,
    escalation_model: str,
    escalation_threshold: float,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Evaluate a single post against rule(s) using direct prompting."""
    post_text = _format_post(mb_entry["post"])
    subreddit = mb_entry["subreddit"]
    rules_text = _format_rules(mb_entry)

    user_prompt = (
        f"## Community: r/{subreddit}\n\n"
        f"## Rules\n{rules_text}\n\n"
        f"## Post\n{post_text}\n\n"
        "Does this post violate any of the rules above? Use the submit_verdict tool."
    )

    async with semaphore:
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=1024,
                system=_DIRECT_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[_DIRECT_TOOL],
                tool_choice={"type": "tool", "name": "submit_verdict"},
            )
            result = response.content[0].input
        except Exception as e:
            logger.error(f"Direct eval failed for {mb_entry['id']}: {e}")
            return {
                "id": mb_entry["id"],
                "subreddit": subreddit,
                "ground_truth": mb_entry["ground_truth_verdict"],
                "predicted": "error",
                "correct": False,
                "confidence": 0.0,
                "difficulty": mb_entry.get("difficulty", ""),
                "source": mb_entry.get("source", ""),
                "reasoning": str(e),
                "escalated": False,
            }

        verdict = result.get("verdict", "review")
        confidence = result.get("confidence", 0.5)
        reasoning = result.get("reasoning", "")
        escalated = False

        # Escalate low-confidence to Sonnet (same logic as checklist pipeline)
        if confidence < escalation_threshold and model != escalation_model:
            try:
                response2 = await client.messages.create(
                    model=escalation_model,
                    max_tokens=1024,
                    system=_DIRECT_SYSTEM,
                    messages=[{"role": "user", "content": user_prompt}],
                    tools=[_DIRECT_TOOL],
                    tool_choice={"type": "tool", "name": "submit_verdict"},
                )
                result2 = response2.content[0].input
                verdict = result2.get("verdict", verdict)
                confidence = result2.get("confidence", confidence)
                reasoning = result2.get("reasoning", reasoning)
                escalated = True
            except Exception as e:
                logger.warning(f"Escalation failed for {mb_entry['id']}: {e}")

    ground_truth = mb_entry["ground_truth_verdict"]

    return {
        "id": mb_entry["id"],
        "subreddit": subreddit,
        "ground_truth": ground_truth,
        "predicted": verdict,
        "correct": verdict == ground_truth,
        "confidence": confidence,
        "difficulty": mb_entry.get("difficulty", ""),
        "source": mb_entry.get("source", ""),
        "reasoning": reasoning,
        "escalated": escalated,
    }


def compute_metrics(results: list[dict]) -> dict:
    """Same metric computation as eval_functional.py for comparability."""
    valid = [r for r in results if r["predicted"] != "error"]
    n = len(valid)
    if not n:
        return {}

    correct = sum(1 for r in valid if r["correct"])
    metrics = {
        "n_total": n,
        "n_errors": len(results) - n,
        "accuracy": round(correct / n, 4),
    }

    for v in ["approve", "remove", "review"]:
        tp = sum(1 for r in valid if r["ground_truth"] == v and r["predicted"] == v)
        fp = sum(1 for r in valid if r["ground_truth"] != v and r["predicted"] == v)
        fn = sum(1 for r in valid if r["ground_truth"] == v and r["predicted"] != v)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        metrics[f"{v}_precision"] = round(precision, 4)
        metrics[f"{v}_recall"] = round(recall, 4)
        metrics[f"{v}_f1"] = round(f1, 4)

    for diff in ["easy", "medium", "hard"]:
        subset = [r for r in valid if r["difficulty"] == diff]
        if subset:
            acc = sum(1 for r in subset if r["correct"]) / len(subset)
            metrics[f"accuracy_{diff}"] = round(acc, 4)
            metrics[f"n_{diff}"] = len(subset)

    # Per-rule accuracy
    by_rule: dict[str, list[bool]] = defaultdict(list)
    for r in valid:
        by_rule[r["subreddit"]].append(r["correct"])
    rule_accs = [sum(v) / len(v) for v in by_rule.values()]
    metrics["mean_per_subreddit_accuracy"] = round(sum(rule_accs) / len(rule_accs), 4) if rule_accs else 0

    return metrics


def compare_results(direct_path: Path, functional_path: Path):
    """Print side-by-side comparison of direct vs. checklist pipeline."""
    with open(direct_path) as f:
        direct = json.load(f)
    with open(functional_path) as f:
        functional = json.load(f)

    dm = direct.get("metrics", {})
    fm = functional.get("metrics", {})

    print(f"\n{'='*70}")
    print(f"Q3 Comparison: Checklist Pipeline vs. Direct Prompting")
    print(f"{'='*70}")
    print(f"{'Metric':<30} {'Checklist':>12} {'Direct':>12} {'Delta':>10}")
    print(f"{'-'*70}")

    compare_keys = [
        ("accuracy", "Accuracy"),
        ("mean_per_subreddit_accuracy", "Per-subreddit Accuracy"),
        ("remove_f1", "Remove F1"),
        ("remove_precision", "Remove Precision"),
        ("remove_recall", "Remove Recall"),
        ("approve_f1", "Approve F1"),
    ]

    for key, label in compare_keys:
        fv = fm.get(key, 0)
        dv = dm.get(key, 0)
        delta = fv - dv
        sign = "+" if delta >= 0 else ""
        print(f"  {label:<28} {fv:>11.1%} {dv:>11.1%} {sign}{delta:>9.1%}")

    # Per-difficulty comparison
    print(f"\n{'By difficulty:'}")
    for diff in ["easy", "medium", "hard"]:
        fv = fm.get(f"accuracy_{diff}", 0)
        dv = dm.get(f"accuracy_{diff}", 0)
        fn = fm.get(f"n_{diff}", 0)
        dn = dm.get(f"n_{diff}", 0)
        if fn or dn:
            delta = fv - dv
            sign = "+" if delta >= 0 else ""
            print(f"  {diff:<28} {fv:>11.1%} {dv:>11.1%} {sign}{delta:>9.1%}")

    # McNemar's test
    dr = {r["id"]: r["correct"] for r in direct.get("results", []) if r["predicted"] != "error"}
    fr = {r["id"]: r["correct"] for r in functional.get("results", []) if r["predicted"] != "error"}
    common_ids = set(dr.keys()) & set(fr.keys())

    if common_ids:
        # b = checklist correct, direct wrong; c = checklist wrong, direct correct
        b = sum(1 for i in common_ids if fr[i] and not dr[i])
        c = sum(1 for i in common_ids if not fr[i] and dr[i])

        if (b + c) > 0:
            chi2 = (abs(b - c) - 1) ** 2 / (b + c)
            # chi2 with df=1: p < 0.05 if chi2 > 3.84
            significant = chi2 > 3.84
            print(f"\nMcNemar's test: b={b} (checklist+/direct-), c={c} (checklist-/direct+)")
            print(f"  chi2={chi2:.2f}, {'significant' if significant else 'not significant'} at p<0.05")


async def main():
    parser = argparse.ArgumentParser(description="Q3: Direct prompting ablation")
    parser.add_argument("--modbench", type=Path, default=DEFAULT_MODBENCH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--compare", type=Path, default=None,
                        help="Path to eval_functional_results.json for comparison")
    args = parser.parse_args()

    settings = Settings()

    from scripts.evaluate_output import _make_anthropic_client
    client, model = _make_anthropic_client()
    if "bedrock" in type(client).__name__.lower():
        settings.haiku_model = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        settings.sonnet_model = "global.anthropic.claude-sonnet-4-6"

    # Load modbench
    with open(args.modbench) as f:
        modbench = json.load(f)
    if args.limit:
        modbench = modbench[:args.limit]
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    logger.info(f"Evaluating {len(modbench)} pairs with direct prompting")
    tasks = [
        evaluate_direct(
            client, mb,
            model=settings.haiku_model,
            escalation_model=settings.sonnet_model,
            escalation_threshold=settings.escalation_confidence_threshold,
            semaphore=semaphore,
        )
        for mb in modbench
    ]
    results = await asyncio.gather(*tasks)
    results = list(results)

    metrics = compute_metrics(results)

    output = {
        "config": {
            "modbench": str(args.modbench),
            "model": settings.haiku_model,
            "escalation_model": settings.sonnet_model,
            "n_pairs": len(modbench),
        },
        "metrics": metrics,
        "results": results,
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)

    # Print summary
    print(f"\n{'='*60}")
    print(f"Direct Prompting Results")
    print(f"{'='*60}")
    print(f"Pairs evaluated: {metrics.get('n_total', 0)}")
    print(f"Overall accuracy: {metrics.get('accuracy', 0):.1%}")

    for v in ["approve", "remove", "review"]:
        p = metrics.get(f"{v}_precision", 0)
        r = metrics.get(f"{v}_recall", 0)
        f1 = metrics.get(f"{v}_f1", 0)
        print(f"  {v:>8}: P={p:.3f}  R={r:.3f}  F1={f1:.3f}")

    print(f"\nResults written to {args.output}")

    # Compare if requested
    if args.compare and args.compare.exists():
        compare_results(args.output, args.compare)


if __name__ == "__main__":
    asyncio.run(main())
