"""
Q4 Ablation: Effect of examples on moderation accuracy.

Runs the checklist pipeline twice — with and without examples — and compares.
Wraps eval_functional.py with --no-examples for the ablation condition.

Usage:
    python scripts/eval_ablation_examples.py
    python scripts/eval_ablation_examples.py --with-examples scripts/eval_functional_results.json
    python scripts/eval_ablation_examples.py --limit 20
"""

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent
DEFAULT_OUTPUT = SCRIPTS_DIR / "eval_ablation_examples_results.json"
DEFAULT_WITH = SCRIPTS_DIR / "eval_functional_results.json"
DEFAULT_WITHOUT = SCRIPTS_DIR / "eval_functional_no_examples_results.json"


def compare_conditions(with_path: Path, without_path: Path, output_path: Path):
    """Compare with-examples vs. without-examples results."""
    with open(with_path) as f:
        with_data = json.load(f)
    with open(without_path) as f:
        without_data = json.load(f)

    wm = with_data.get("metrics", {})
    wom = without_data.get("metrics", {})

    # Build per-entry lookup
    wr = {r["id"]: r for r in with_data.get("results", []) if r["predicted"] != "error"}
    wor = {r["id"]: r for r in without_data.get("results", []) if r["predicted"] != "error"}
    common_ids = sorted(set(wr.keys()) & set(wor.keys()))

    # Per-entry comparison
    entry_comparisons = []
    for eid in common_ids:
        w = wr[eid]
        wo = wor[eid]
        entry_comparisons.append({
            "id": eid,
            "subreddit": w["subreddit"],
            "rule_text_short": w.get("rule_text_short", ""),
            "ground_truth": w["ground_truth"],
            "with_examples_verdict": w["predicted"],
            "without_examples_verdict": wo["predicted"],
            "with_examples_correct": w["correct"],
            "without_examples_correct": wo["correct"],
            "with_examples_confidence": w["confidence"],
            "without_examples_confidence": wo["confidence"],
            "difficulty": w.get("difficulty", ""),
            "n_subjective": w.get("n_subjective", 0),
            "changed": w["predicted"] != wo["predicted"],
        })

    # Count changes
    n_changed = sum(1 for e in entry_comparisons if e["changed"])
    n_improved = sum(1 for e in entry_comparisons if e["with_examples_correct"] and not e["without_examples_correct"])
    n_degraded = sum(1 for e in entry_comparisons if not e["with_examples_correct"] and e["without_examples_correct"])

    # McNemar's test
    b = n_improved  # with correct, without wrong
    c = n_degraded  # with wrong, without correct
    mcnemar_chi2 = (abs(b - c) - 1) ** 2 / (b + c) if (b + c) > 0 else 0
    mcnemar_significant = mcnemar_chi2 > 3.84

    # Confidence calibration
    calibration = {"with_examples": {}, "without_examples": {}}
    for condition, results in [("with_examples", wr), ("without_examples", wor)]:
        bins = defaultdict(lambda: {"correct": 0, "total": 0})
        for r in results.values():
            conf = r["confidence"]
            # Only count entries with subjective items
            if r.get("n_subjective", 0) == 0:
                continue
            bin_key = f"{int(conf * 10) / 10:.1f}"
            bins[bin_key]["total"] += 1
            if r["correct"]:
                bins[bin_key]["correct"] += 1
        calibration[condition] = {
            k: round(v["correct"] / v["total"], 4) if v["total"] > 0 else None
            for k, v in sorted(bins.items())
        }

    # Stratify by subjective item count
    strat_by_subjective = {}
    for bucket_name, filter_fn in [
        ("no_subjective", lambda e: e["n_subjective"] == 0),
        ("1-2_subjective", lambda e: 1 <= e["n_subjective"] <= 2),
        ("3+_subjective", lambda e: e["n_subjective"] >= 3),
    ]:
        subset = [e for e in entry_comparisons if filter_fn(e)]
        if subset:
            w_acc = sum(1 for e in subset if e["with_examples_correct"]) / len(subset)
            wo_acc = sum(1 for e in subset if e["without_examples_correct"]) / len(subset)
            strat_by_subjective[bucket_name] = {
                "n": len(subset),
                "with_examples_accuracy": round(w_acc, 4),
                "without_examples_accuracy": round(wo_acc, 4),
                "delta": round(w_acc - wo_acc, 4),
            }

    # Stratify by difficulty
    strat_by_difficulty = {}
    for diff in ["easy", "medium", "hard"]:
        subset = [e for e in entry_comparisons if e["difficulty"] == diff]
        if subset:
            w_acc = sum(1 for e in subset if e["with_examples_correct"]) / len(subset)
            wo_acc = sum(1 for e in subset if e["without_examples_correct"]) / len(subset)
            strat_by_difficulty[diff] = {
                "n": len(subset),
                "with_examples_accuracy": round(w_acc, 4),
                "without_examples_accuracy": round(wo_acc, 4),
                "delta": round(w_acc - wo_acc, 4),
            }

    comparison = {
        "summary": {
            "n_common_pairs": len(common_ids),
            "n_changed_verdicts": n_changed,
            "n_improved_by_examples": n_improved,
            "n_degraded_by_examples": n_degraded,
            "with_examples_accuracy": wm.get("accuracy", 0),
            "without_examples_accuracy": wom.get("accuracy", 0),
            "accuracy_delta": round(wm.get("accuracy", 0) - wom.get("accuracy", 0), 4),
        },
        "mcnemar": {
            "b_improved": b,
            "c_degraded": c,
            "chi2": round(mcnemar_chi2, 4),
            "significant_p05": mcnemar_significant,
        },
        "by_subjective_count": strat_by_subjective,
        "by_difficulty": strat_by_difficulty,
        "calibration": calibration,
        "entry_comparisons": entry_comparisons,
    }

    with open(output_path, "w") as f:
        json.dump(comparison, f, indent=2)

    # Print summary
    print(f"\n{'='*70}")
    print(f"Q4: Effect of Examples on Moderation")
    print(f"{'='*70}")
    s = comparison["summary"]
    print(f"Common pairs: {s['n_common_pairs']}")
    print(f"Changed verdicts: {s['n_changed_verdicts']}")
    print(f"  Improved by examples: {s['n_improved_by_examples']}")
    print(f"  Degraded by examples: {s['n_degraded_by_examples']}")
    print(f"\nAccuracy:")
    print(f"  With examples:    {s['with_examples_accuracy']:.1%}")
    print(f"  Without examples: {s['without_examples_accuracy']:.1%}")
    print(f"  Delta:            {'+' if s['accuracy_delta'] >= 0 else ''}{s['accuracy_delta']:.1%}")

    mc = comparison["mcnemar"]
    print(f"\nMcNemar's test: chi2={mc['chi2']:.2f}, {'significant' if mc['significant_p05'] else 'not significant'} at p<0.05")

    print(f"\nBy subjective item count:")
    for k, v in strat_by_subjective.items():
        print(f"  {k:<18} n={v['n']:>4}  with={v['with_examples_accuracy']:.1%}  without={v['without_examples_accuracy']:.1%}  delta={'+' if v['delta'] >= 0 else ''}{v['delta']:.1%}")

    print(f"\nBy difficulty:")
    for k, v in strat_by_difficulty.items():
        print(f"  {k:<18} n={v['n']:>4}  with={v['with_examples_accuracy']:.1%}  without={v['without_examples_accuracy']:.1%}  delta={'+' if v['delta'] >= 0 else ''}{v['delta']:.1%}")

    print(f"\nResults written to {output_path}")


async def run_without_examples(modbench_path: Path, compiled_sources: list[Path], output_path: Path, limit: int | None, settings: Settings):
    """Run eval_functional with --no-examples."""
    from scripts.eval_functional import run_eval
    await run_eval(
        modbench_path=modbench_path,
        compiled_sources=compiled_sources,
        output_path=output_path,
        use_llm=True,
        use_examples=False,
        limit=limit,
        settings=settings,
    )


async def main():
    parser = argparse.ArgumentParser(description="Q4: Examples ablation")
    parser.add_argument("--with-examples", type=Path, default=DEFAULT_WITH,
                        help="Path to eval_functional_results.json (with examples)")
    parser.add_argument("--without-examples", type=Path, default=DEFAULT_WITHOUT,
                        help="Path to without-examples results (will be generated if missing)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--modbench", type=Path, default=SCRIPTS_DIR / "modbench.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--compare-only", action="store_true",
                        help="Skip generation, just compare existing results")
    args = parser.parse_args()

    settings = Settings()

    # Generate without-examples results if needed
    if not args.compare_only and not args.without_examples.exists():
        logger.info("Generating without-examples results...")
        from scripts.eval_functional import DEFAULT_COMPILED_SOURCES
        await run_without_examples(
            args.modbench, DEFAULT_COMPILED_SOURCES,
            args.without_examples, args.limit, settings,
        )

    # Generate with-examples results if needed
    if not args.compare_only and not args.with_examples.exists():
        logger.info("Generating with-examples results...")
        from scripts.eval_functional import run_eval, DEFAULT_COMPILED_SOURCES
        await run_eval(
            modbench_path=args.modbench,
            compiled_sources=DEFAULT_COMPILED_SOURCES,
            output_path=args.with_examples,
            use_llm=True,
            use_examples=True,
            limit=args.limit,
            settings=settings,
        )

    if not args.with_examples.exists() or not args.without_examples.exists():
        logger.error("Both with-examples and without-examples results are needed.")
        logger.error("Run eval_functional.py first, then eval_functional.py --no-examples")
        sys.exit(1)

    compare_conditions(args.with_examples, args.without_examples, args.output)


if __name__ == "__main__":
    asyncio.run(main())
