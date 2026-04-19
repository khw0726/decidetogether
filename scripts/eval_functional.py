"""
Q1 Layer C: Functional accuracy evaluation.

Runs compiled checklists against ModBench posts using the TreeEvaluator
directly (bypassing DB/HTTP). Measures whether the compiled logic produces
correct moderation verdicts.

Also used as the "treatment" condition for Q3 and Q4 ablations.

Supports two ModBench formats:
  - "compiler" format: each entry has a single rule_text, matched to compiled outputs
  - "real" format: each entry has a rules list, requires a compiled-rules index

Usage:
    python scripts/eval_functional.py
    python scripts/eval_functional.py --modbench scripts/modbench.json --compiled scripts/compiler_test_output.json
    python scripts/eval_functional.py --no-llm          # skip subjective items (structural/det only)
    python scripts/eval_functional.py --no-examples      # Q4 ablation: strip examples from subjective eval
    python scripts/eval_functional.py --limit 20         # evaluate first N pairs only
"""

import argparse
import asyncio
import csv
import json
import logging
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.config import Settings
from src.automod.core.tree_evaluator import TreeEvaluator
from src.automod.core.subjective import SubjectiveEvaluator
from src.automod.core.actions import VERDICT_PRECEDENCE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent
DEFAULT_MODBENCH = SCRIPTS_DIR / "modbench.json"
DEFAULT_COMPILED_SOURCES = [
    SCRIPTS_DIR / "compiler_test_output.json",
    SCRIPTS_DIR / "compiler_test_sampled.json",
]
DEFAULT_OUTPUT = SCRIPTS_DIR / "eval_functional_results.json"

MAX_CONCURRENT = 20


# ---------------------------------------------------------------------------
# Convert compiler JSON to in-memory objects the evaluator expects
# ---------------------------------------------------------------------------

def _flatten_checklist(items: list[dict], rule_id: str, parent_id: str | None = None) -> list[SimpleNamespace]:
    """Flatten a nested checklist tree into a flat list of SimpleNamespace objects
    with parent_id links, as the TreeEvaluator expects."""
    flat = []
    for i, item in enumerate(items):
        item_id = item.get("id") or str(uuid.uuid4())
        obj = SimpleNamespace(
            id=item_id,
            rule_id=rule_id,
            order=i,
            parent_id=parent_id,
            description=item.get("description", ""),
            rule_text_anchor=item.get("rule_text_anchor"),
            item_type=item.get("item_type", "subjective"),
            logic=item.get("logic", {}),
            action=item.get("action", "flag"),
        )
        flat.append(obj)
        for child in item.get("children", []):
            flat.extend(_flatten_checklist([child], rule_id, parent_id=item_id))
    return flat


def _make_example_objects(examples: list[dict]) -> list[SimpleNamespace]:
    """Convert example dicts to SimpleNamespace objects matching the Example ORM interface."""
    objs = []
    for ex in examples:
        label = ex.get("label", "")
        label_map = {"positive": "compliant", "negative": "violating"}
        objs.append(SimpleNamespace(
            id=str(uuid.uuid4()),
            label=label_map.get(label, label),
            content=ex.get("content", {}),
            source="generated",
        ))
    return objs


def _make_rule_object(rule_text: str, rule_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=rule_id,
        title=rule_text[:80],
        text=rule_text,
        rule_type="actionable",
    )


# ---------------------------------------------------------------------------
# Build a rule index from compiled outputs
# ---------------------------------------------------------------------------

def build_rule_index(compiled_sources: list[Path]) -> dict[str, dict]:
    """Build a lookup from (subreddit, rule_text_prefix) → compiled data.

    Returns {key: {"rule_text", "checklist", "examples", "subreddit"}}.
    """
    index = {}

    for source_path in compiled_sources:
        if not source_path.exists():
            continue
        with open(source_path) as f:
            data = json.load(f)

        for entry in data:
            if "rules" in entry and isinstance(entry["rules"], list) and entry["rules"] and isinstance(entry["rules"][0], dict) and "rule_text" in entry["rules"][0]:
                # Nested format (compiler_test_output.json)
                for rule in entry.get("rules", []):
                    triage = rule.get("triage") or {}
                    if triage.get("rule_type") != "actionable":
                        continue
                    key = (entry["subreddit"], rule["rule_text"][:60])
                    index[key] = {
                        "subreddit": entry["subreddit"],
                        "rule_text": rule["rule_text"],
                        "checklist": rule.get("checklist", []),
                        "examples": rule.get("examples", []),
                    }
            elif "title" in entry:
                # Flat format (compiler_test_sampled.json)
                triage = entry.get("triage") or {}
                if triage.get("rule_type") != "actionable":
                    continue
                rule_text = entry.get("title", "")
                desc = entry.get("description")
                if desc:
                    rule_text = f"{rule_text}\n\n{desc}"
                key = (entry["subreddit"], rule_text[:60])
                index[key] = {
                    "subreddit": entry["subreddit"],
                    "rule_text": rule_text,
                    "checklist": entry.get("checklist", []),
                    "examples": entry.get("examples", []),
                }

    return index


def build_subreddit_rule_index(compiled_sources: list[Path]) -> dict[str, list[dict]]:
    """Build a lookup from subreddit → list of compiled rules.

    Used for 'real' format modbench entries where we evaluate against all rules.
    """
    by_sub: dict[str, list[dict]] = defaultdict(list)

    for source_path in compiled_sources:
        if not source_path.exists():
            continue
        with open(source_path) as f:
            data = json.load(f)

        for entry in data:
            if "rules" in entry and isinstance(entry["rules"], list) and entry["rules"] and isinstance(entry["rules"][0], dict) and "rule_text" in entry["rules"][0]:
                for rule in entry.get("rules", []):
                    triage = rule.get("triage") or {}
                    if triage.get("rule_type") != "actionable":
                        continue
                    if not rule.get("checklist"):
                        continue
                    by_sub[entry["subreddit"]].append({
                        "rule_text": rule["rule_text"],
                        "checklist": rule["checklist"],
                        "examples": rule.get("examples", []),
                        "applies_to": rule.get("applies_to", "both"),
                    })
            elif "title" in entry:
                triage = entry.get("triage") or {}
                if triage.get("rule_type") != "actionable":
                    continue
                if not entry.get("checklist"):
                    continue
                rule_text = entry.get("title", "")
                desc = entry.get("description")
                if desc:
                    rule_text = f"{rule_text}\n\n{desc}"
                by_sub[entry["subreddit"]].append({
                    "rule_text": rule_text,
                    "checklist": entry["checklist"],
                    "examples": entry.get("examples", []),
                    "applies_to": entry.get("applies_to", "both"),
                })

    return dict(by_sub)


def _detect_modbench_format(modbench: list[dict]) -> str:
    """Detect whether modbench entries use 'compiler' or 'real' format."""
    if not modbench:
        return "compiler"
    first = modbench[0]
    if "rules" in first and isinstance(first["rules"], list):
        return "real"
    return "compiler"


# ---------------------------------------------------------------------------
# Stub subjective evaluator (for --no-llm mode)
# ---------------------------------------------------------------------------

class StubSubjectiveEvaluator:
    """Returns neutral results for all subjective items (no LLM calls)."""

    async def evaluate_batch(self, items, post, community_name, examples):
        return [
            {
                "item_id": item.id,
                "triggered": False,
                "confidence": 0.5,
                "reasoning": "Skipped (--no-llm mode)",
            }
            for item in items
        ]


# ---------------------------------------------------------------------------
# Evaluation: single rule against a post
# ---------------------------------------------------------------------------

async def evaluate_single_rule(
    tree_eval: TreeEvaluator,
    compiled_rule: dict,
    post: dict,
    subreddit: str,
    use_examples: bool,
) -> dict:
    """Evaluate one compiled rule against one post. Returns tree evaluator result."""
    rule_id = str(uuid.uuid4())
    rule = _make_rule_object(compiled_rule["rule_text"], rule_id)
    checklist = _flatten_checklist(compiled_rule["checklist"], rule_id)
    examples = _make_example_objects(compiled_rule["examples"]) if use_examples else []

    return await tree_eval.evaluate_rule(
        rule=rule,
        checklist=checklist,
        post=post,
        community_name=subreddit,
        examples=examples,
    )


# ---------------------------------------------------------------------------
# Evaluation: all rules for a post (real format)
# ---------------------------------------------------------------------------

async def _eval_one_rule(
    tree_eval: TreeEvaluator,
    compiled_rule: dict,
    post: dict,
    subreddit: str,
    use_examples: bool,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """Evaluate a single rule with semaphore-guarded concurrency."""
    async with semaphore:
        try:
            return await evaluate_single_rule(
                tree_eval, compiled_rule, post, subreddit, use_examples
            )
        except Exception as e:
            logger.error(f"Rule eval failed: {e}")
            return None


async def evaluate_post_all_rules(
    tree_eval: TreeEvaluator,
    mb_entry: dict,
    compiled_rules: list[dict],
    use_examples: bool,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Evaluate a post against all compiled rules for its subreddit.

    Aggregates verdicts across rules: worst verdict wins (same as engine.py).
    Rules are evaluated concurrently, with the semaphore capping total API calls.
    """
    subreddit = mb_entry["subreddit"]
    post = mb_entry["post"]

    # Filter rules by content type (applies_to)
    post_type = post.get("context", {}).get("post_type", "")
    applicable_rules = []
    for cr in compiled_rules:
        applies_to = cr.get("applies_to", "both")
        if applies_to == "both":
            applicable_rules.append(cr)
        elif applies_to == "posts" and post_type in ("self", "link", ""):
            applicable_rules.append(cr)
        elif applies_to == "comments" and post_type == "comment":
            applicable_rules.append(cr)

    # Evaluate applicable rules concurrently
    tasks = [
        _eval_one_rule(tree_eval, cr, post, subreddit, use_examples, semaphore)
        for cr in applicable_rules
    ]
    results_raw = await asyncio.gather(*tasks)
    rule_results = [r for r in results_raw if r is not None]

    # Track which rules triggered (verdict in remove/review) — needed by RQ2A Option A
    triggered_rules = []  # list of {rule_text_short, verdict, confidence}
    for cr, rr in zip(applicable_rules, results_raw):
        if rr is None:
            continue
        v = rr.get("verdict", "approve")
        if v in ("remove", "review"):
            triggered_rules.append({
                "rule_text_short": cr["rule_text"][:80].replace("\n", " "),
                "rule_text_key": cr["rule_text"][:60],  # matches index key format
                "verdict": v,
                "confidence": rr.get("confidence", 0.5),
            })

    total_checklist = 0
    total_subjective = 0
    total_deterministic = 0
    total_structural = 0
    for compiled_rule in applicable_rules:
        checklist = _flatten_checklist(compiled_rule["checklist"], "count")
        total_checklist += len(checklist)
        total_subjective += sum(1 for c in checklist if c.item_type == "subjective")
        total_deterministic += sum(1 for c in checklist if c.item_type == "deterministic")
        total_structural += sum(1 for c in checklist if c.item_type == "structural")

    # Aggregate: worst verdict wins
    if not rule_results:
        final_verdict = "error"
        final_confidence = 0.0
    else:
        final_verdict = "approve"
        final_confidence = 1.0
        for rr in rule_results:
            v = rr.get("verdict", "approve")
            c = rr.get("confidence", 0.5)
            if VERDICT_PRECEDENCE.get(v, 0) > VERDICT_PRECEDENCE.get(final_verdict, 0):
                final_verdict = v
                final_confidence = c

    ground_truth = mb_entry["ground_truth_verdict"]

    return {
        "id": mb_entry["id"],
        "subreddit": subreddit,
        "ground_truth": ground_truth,
        "predicted": final_verdict,
        "correct": final_verdict == ground_truth,
        "confidence": final_confidence,
        "difficulty": mb_entry.get("difficulty", ""),
        "source": mb_entry.get("source", ""),
        "n_rules_evaluated": len(compiled_rules),
        "n_rules_triggered": len(triggered_rules),
        "triggered_rules": triggered_rules,
        "n_checklist_items": total_checklist,
        "n_subjective": total_subjective,
        "n_deterministic": total_deterministic,
        "n_structural": total_structural,
    }


# ---------------------------------------------------------------------------
# Evaluation: single rule (compiler format)
# ---------------------------------------------------------------------------

async def evaluate_pair(
    tree_eval: TreeEvaluator,
    mb_entry: dict,
    compiled: dict,
    use_examples: bool,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Evaluate a single (rule, post) pair. Returns result dict."""
    rule_id = str(uuid.uuid4())
    rule = _make_rule_object(compiled["rule_text"], rule_id)
    checklist = _flatten_checklist(compiled["checklist"], rule_id)
    examples = _make_example_objects(compiled["examples"]) if use_examples else []
    post = mb_entry["post"]

    async with semaphore:
        try:
            result = await tree_eval.evaluate_rule(
                rule=rule,
                checklist=checklist,
                post=post,
                community_name=compiled["subreddit"],
                examples=examples,
            )
        except Exception as e:
            logger.error(f"Evaluation failed for {mb_entry['id']}: {e}")
            result = {
                "verdict": "error",
                "confidence": 0.0,
                "reasoning": {"error": str(e)},
                "triggered_items": [],
            }

    ground_truth = mb_entry["ground_truth_verdict"]
    predicted = result["verdict"]

    return {
        "id": mb_entry["id"],
        "subreddit": mb_entry["subreddit"],
        "rule_text_short": mb_entry.get("rule_text", "")[:80].replace("\n", " "),
        "ground_truth": ground_truth,
        "predicted": predicted,
        "correct": predicted == ground_truth,
        "confidence": result.get("confidence", 0.0),
        "difficulty": mb_entry.get("difficulty", ""),
        "source": mb_entry.get("source", ""),
        "n_checklist_items": len(checklist),
        "n_subjective": sum(1 for c in checklist if c.item_type == "subjective"),
        "n_deterministic": sum(1 for c in checklist if c.item_type == "deterministic"),
        "n_structural": sum(1 for c in checklist if c.item_type == "structural"),
        "triggered_items": result.get("triggered_items", []),
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(results: list[dict]) -> dict:
    """Compute aggregate metrics from evaluation results."""
    if not results:
        return {}

    valid = [r for r in results if r["predicted"] != "error"]
    n = len(valid)

    correct = sum(1 for r in valid if r["correct"])
    accuracy = correct / n if n > 0 else 0

    verdicts = ["approve", "remove", "review"]
    metrics = {
        "n_total": n,
        "n_errors": len(results) - n,
        "accuracy": round(accuracy, 4),
    }

    for v in verdicts:
        tp = sum(1 for r in valid if r["ground_truth"] == v and r["predicted"] == v)
        fp = sum(1 for r in valid if r["ground_truth"] != v and r["predicted"] == v)
        fn = sum(1 for r in valid if r["ground_truth"] == v and r["predicted"] != v)
        tn = sum(1 for r in valid if r["ground_truth"] != v and r["predicted"] != v)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0

        metrics[f"{v}_precision"] = round(precision, 4)
        metrics[f"{v}_recall"] = round(recall, 4)
        metrics[f"{v}_f1"] = round(f1, 4)
        metrics[f"{v}_fpr"] = round(fpr, 4)

    for diff in ["easy", "medium", "hard", "unknown"]:
        subset = [r for r in valid if r["difficulty"] == diff]
        if subset:
            acc = sum(1 for r in subset if r["correct"]) / len(subset)
            metrics[f"accuracy_{diff}"] = round(acc, 4)
            metrics[f"n_{diff}"] = len(subset)

    # By subreddit
    by_sub: dict[str, list[bool]] = defaultdict(list)
    for r in valid:
        by_sub[r["subreddit"]].append(r["correct"])
    metrics["per_subreddit"] = {
        sub: {"n": len(v), "accuracy": round(sum(v) / len(v), 4)}
        for sub, v in sorted(by_sub.items())
    }
    sub_accs = [sum(v) / len(v) for v in by_sub.values()]
    metrics["mean_per_subreddit_accuracy"] = round(sum(sub_accs) / len(sub_accs), 4) if sub_accs else 0

    # By source
    by_source: dict[str, list[bool]] = defaultdict(list)
    for r in valid:
        by_source[r.get("source", "unknown")].append(r["correct"])
    metrics["per_source"] = {
        src: {"n": len(v), "accuracy": round(sum(v) / len(v), 4)}
        for src, v in by_source.items()
    }

    # Confusion matrix
    matrix = {gt: {pred: 0 for pred in verdicts} for gt in verdicts}
    for r in valid:
        gt = r["ground_truth"]
        pred = r["predicted"]
        if gt in matrix and pred in matrix[gt]:
            matrix[gt][pred] += 1
    metrics["confusion_matrix"] = matrix

    return metrics


def bootstrap_ci(results: list[dict], n_resamples: int = 1000, alpha: float = 0.05) -> dict:
    """Bootstrap confidence intervals on aggregate accuracy."""
    import random

    valid = [r for r in results if r["predicted"] != "error"]
    if not valid:
        return {}

    accuracies = []
    for _ in range(n_resamples):
        sample = random.choices(valid, k=len(valid))
        acc = sum(1 for r in sample if r["correct"]) / len(sample)
        accuracies.append(acc)

    accuracies.sort()
    lo_idx = int(n_resamples * alpha / 2)
    hi_idx = int(n_resamples * (1 - alpha / 2))

    return {
        "accuracy_mean": round(sum(accuracies) / len(accuracies), 4),
        "accuracy_ci_lower": round(accuracies[lo_idx], 4),
        "accuracy_ci_upper": round(accuracies[hi_idx], 4),
        "ci_alpha": alpha,
        "n_resamples": n_resamples,
    }


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

async def run_eval(
    modbench_path: Path,
    compiled_sources: list[Path],
    output_path: Path,
    use_llm: bool,
    use_examples: bool,
    limit: int | None,
    settings: Settings,
):
    logger.info(f"Loading modbench from {modbench_path}")
    with open(modbench_path) as f:
        modbench = json.load(f)

    if limit:
        modbench = modbench[:limit]

    fmt = _detect_modbench_format(modbench)
    logger.info(f"Detected modbench format: {fmt}")

    # Create evaluator
    if use_llm:
        from scripts.evaluate_output import _make_anthropic_client
        client, model = _make_anthropic_client()
        if "bedrock" in type(client).__name__.lower():
            settings.haiku_model = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
            settings.sonnet_model = "global.anthropic.claude-sonnet-4-6"
        sub_eval = SubjectiveEvaluator(client, settings)
    else:
        sub_eval = StubSubjectiveEvaluator()

    tree_eval = TreeEvaluator(sub_eval)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    if fmt == "real":
        # Real format: each entry has rules list, need compiled checklists per subreddit
        logger.info(f"Building subreddit rule index from {len(compiled_sources)} source files")
        sub_index = build_subreddit_rule_index(compiled_sources)
        logger.info(f"Subreddit index: {len(sub_index)} subreddits")

        pairs = []
        unmatched = 0
        for mb in modbench:
            compiled_rules = sub_index.get(mb["subreddit"])
            if compiled_rules:
                pairs.append((mb, compiled_rules))
            else:
                unmatched += 1

        logger.info(f"Matched {len(pairs)} entries to compiled subreddits, {unmatched} unmatched")
        logger.info(f"Evaluating {len(pairs)} posts (concurrent={MAX_CONCURRENT}, examples={'on' if use_examples else 'off'})")

        tasks = [
            evaluate_post_all_rules(tree_eval, mb, compiled_rules, use_examples, semaphore)
            for mb, compiled_rules in pairs
        ]
    else:
        # Compiler format: each entry has single rule_text
        logger.info(f"Building rule index from {len(compiled_sources)} source files")
        rule_index = build_rule_index(compiled_sources)
        logger.info(f"Rule index: {len(rule_index)} rules")

        pairs = []
        unmatched = 0
        for mb in modbench:
            sub = mb["subreddit"]
            rule_text = mb.get("rule_text", "")
            key = (sub, rule_text[:60])
            compiled = rule_index.get(key)
            if compiled:
                pairs.append((mb, compiled))
            else:
                unmatched += 1

        logger.info(f"Matched {len(pairs)} pairs, {unmatched} unmatched")
        logger.info(f"Evaluating {len(pairs)} pairs (concurrent={MAX_CONCURRENT}, examples={'on' if use_examples else 'off'})")

        tasks = [
            evaluate_pair(tree_eval, mb, compiled, use_examples, semaphore)
            for mb, compiled in pairs
        ]

    results = list(await asyncio.gather(*tasks))

    # Compute metrics
    metrics = compute_metrics(results)
    ci = bootstrap_ci(results)

    output = {
        "config": {
            "modbench": str(modbench_path),
            "modbench_format": fmt,
            "use_llm": use_llm,
            "use_examples": use_examples,
            "n_evaluated": len(pairs),
            "n_unmatched": unmatched,
        },
        "metrics": metrics,
        "bootstrap_ci": ci,
        "results": results,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    # Print summary
    print(f"\n{'='*60}")
    print(f"Functional Evaluation Results")
    print(f"{'='*60}")
    print(f"Format: {fmt}")
    print(f"Pairs evaluated: {metrics.get('n_total', 0)} (errors: {metrics.get('n_errors', 0)})")
    print(f"Overall accuracy: {metrics.get('accuracy', 0):.1%}")
    if ci:
        print(f"  95% CI: [{ci.get('accuracy_ci_lower', 0):.1%}, {ci.get('accuracy_ci_upper', 0):.1%}]")
    print(f"Mean per-subreddit accuracy: {metrics.get('mean_per_subreddit_accuracy', 0):.1%}")
    print()

    for v in ["approve", "remove", "review"]:
        p = metrics.get(f"{v}_precision", 0)
        r = metrics.get(f"{v}_recall", 0)
        f1 = metrics.get(f"{v}_f1", 0)
        print(f"  {v:>8}: P={p:.3f}  R={r:.3f}  F1={f1:.3f}")

    print()
    for diff in ["easy", "medium", "hard", "unknown"]:
        n = metrics.get(f"n_{diff}", 0)
        acc = metrics.get(f"accuracy_{diff}", 0)
        if n:
            print(f"  {diff:>8}: {acc:.1%} (n={n})")

    # Per-subreddit
    per_sub = metrics.get("per_subreddit", {})
    if per_sub:
        print(f"\nPer subreddit:")
        for sub, info in per_sub.items():
            print(f"  {sub:<25} {info['accuracy']:.1%} (n={info['n']})")

    # Per-source
    per_src = metrics.get("per_source", {})
    if per_src:
        print(f"\nPer source:")
        for src, info in per_src.items():
            print(f"  {src:<25} {info['accuracy']:.1%} (n={info['n']})")

    # Confusion matrix
    cm = metrics.get("confusion_matrix", {})
    if cm:
        print(f"\nConfusion Matrix (rows=ground_truth, cols=predicted):")
        verdicts = ["approve", "remove", "review"]
        print(f"  {'':>8}  {'approve':>8} {'remove':>8} {'review':>8}")
        for gt in verdicts:
            row = cm.get(gt, {})
            print(f"  {gt:>8}  {row.get('approve', 0):>8} {row.get('remove', 0):>8} {row.get('review', 0):>8}")

    print(f"\nResults written to {output_path}")


async def main():
    parser = argparse.ArgumentParser(description="Functional accuracy evaluation (Q1/Q3/Q4)")
    parser.add_argument("--modbench", type=Path, default=DEFAULT_MODBENCH)
    parser.add_argument("--compiled", nargs="*", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM calls for subjective items")
    parser.add_argument("--no-examples", action="store_true", help="Q4 ablation: strip examples")
    parser.add_argument("--limit", type=int, default=None, help="Evaluate first N pairs only")
    args = parser.parse_args()

    if not args.no_llm:
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).parent.parent / ".env")
        except ImportError:
            pass
        import os
        if not (os.environ.get("AWS_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("ANTHROPIC_API_KEY")):
            logger.error("No credentials found. Set AWS_ACCESS_KEY or ANTHROPIC_API_KEY, or use --no-llm.")
            sys.exit(1)
    settings = Settings()

    compiled_sources = args.compiled or DEFAULT_COMPILED_SOURCES

    await run_eval(
        modbench_path=args.modbench,
        compiled_sources=compiled_sources,
        output_path=args.output,
        use_llm=not args.no_llm,
        use_examples=not args.no_examples,
        limit=args.limit,
        settings=settings,
    )


if __name__ == "__main__":
    asyncio.run(main())
