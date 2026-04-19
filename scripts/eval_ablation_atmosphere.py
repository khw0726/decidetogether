"""
Q6 Ablation: Effect of community atmosphere on compilation accuracy.

Compiles the same rules under two conditions:
  A) With atmosphere: run generate_community_atmosphere() first, then compile with
     the atmosphere context attached
  B) Without atmosphere: compile rules with no atmosphere context (current default
     in eval scripts)

Then evaluates both sets of compiled checklists against ModBench posts.

The atmosphere is generated from the rules themselves (no sample posts needed)
using the existing compiler.generate_community_atmosphere() function with the
rules text as the sole signal. For subreddits where pushshift data is available,
sample posts are also used.

Usage:
    # Full pipeline: generate atmosphere, compile both conditions, evaluate
    python scripts/eval_ablation_atmosphere.py \\
        --rules scripts/modbench_rules.json \\
        --subreddits AskReddit science \\
        --modbench scripts/modbench.json

    # Compare pre-existing compiled outputs
    python scripts/eval_ablation_atmosphere.py compare \\
        --with-atmosphere scripts/compiled_with_atmosphere.json \\
        --without-atmosphere scripts/compiled_without_atmosphere.json \\
        --modbench scripts/modbench.json
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.config import Settings
from src.automod.compiler.compiler import RuleCompiler
from src.automod.compiler import prompts
from src.automod.core.tree_evaluator import TreeEvaluator
from src.automod.core.subjective import SubjectiveEvaluator
from src.automod.core.actions import VERDICT_PRECEDENCE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent
DEFAULT_MODBENCH = SCRIPTS_DIR / "modbench.json"
DEFAULT_OUTPUT = SCRIPTS_DIR / "eval_ablation_atmosphere_results.json"
MAX_CONCURRENT = 20


# ---------------------------------------------------------------------------
# Helpers: stub objects for the compiler (no DB)
# ---------------------------------------------------------------------------

def _make_community(name: str, platform: str = "reddit") -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        name=name,
        platform=platform,
        atmosphere=None,
    )


def _make_rule(title: str, description: str, rule_id: str | None = None) -> SimpleNamespace:
    text = f"{title}\n\n{description}" if description else title
    return SimpleNamespace(
        id=rule_id or str(uuid.uuid4()),
        title=title,
        text=text,
        rule_type="actionable",
    )


# ---------------------------------------------------------------------------
# Step 1: Generate atmosphere for each subreddit
# ---------------------------------------------------------------------------

async def generate_atmospheres(
    compiler: RuleCompiler,
    rules_by_sub: dict[str, list[dict]],
    subreddits: list[str],
    pushshift_dir: Path | None = None,
) -> dict[str, dict]:
    """Generate community atmosphere for each subreddit.

    Uses the rules themselves as the primary signal. If pushshift archives are
    available, also samples posts for richer atmosphere inference.
    """
    atmospheres = {}

    for sub in subreddits:
        sub_rules = rules_by_sub.get(sub, [])
        if not sub_rules:
            logger.warning(f"No rules for {sub}, skipping atmosphere generation")
            continue

        community = _make_community(sub)

        # Build rule objects for the rules summary
        rule_objects = [_make_rule(r["title"], r.get("description", "")) for r in sub_rules]

        # Use rules as "acceptable" signal — the rules describe what's expected
        # We create synthetic descriptions as proxy for actual posts
        acceptable_posts = [
            {
                "content": f"[Example of content that follows: {r['title']}]",
                "label": "acceptable",
            }
            for r in sub_rules[:5]
        ]
        unacceptable_posts = [
            {
                "content": f"[Example of content that violates: {r['title']}]",
                "label": "unacceptable",
            }
            for r in sub_rules[:5]
        ]

        # If pushshift data is available, use real posts instead
        if pushshift_dir:
            real_posts = _load_sample_posts(pushshift_dir, sub)
            if real_posts:
                acceptable_posts = [
                    {"content": p.get("body", ""), "label": "acceptable"}
                    for p in real_posts[:10]
                ]

        try:
            atmosphere = await compiler.generate_community_atmosphere(
                community=community,
                acceptable_posts=acceptable_posts,
                unacceptable_posts=unacceptable_posts,
                other_rules=rule_objects,
            )
            atmospheres[sub] = atmosphere
            logger.info(f"Generated atmosphere for {sub}: tone='{atmosphere.get('tone', '')[:50]}'")
        except Exception as e:
            logger.error(f"Atmosphere generation failed for {sub}: {e}")
            atmospheres[sub] = None

    return atmospheres


def _load_sample_posts(pushshift_dir: Path, subreddit: str, max_posts: int = 20) -> list[dict]:
    """Load a small sample of posts from pushshift archive for atmosphere generation."""
    import subprocess
    import random

    zst_path = pushshift_dir / f"{subreddit}_comments.zst"
    if not zst_path.exists():
        for p in pushshift_dir.glob("*_comments.zst"):
            if p.stem.rsplit("_comments", 1)[0].lower() == subreddit.lower():
                zst_path = p
                break
        if not zst_path.exists():
            return []

    candidates = []
    try:
        proc = subprocess.Popen(
            ["zstd", "-d", "-c", str(zst_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        for i, line_bytes in enumerate(proc.stdout):
            if i > 50000:
                break
            try:
                comment = json.loads(line_bytes)
            except json.JSONDecodeError:
                continue
            body = comment.get("body", "")
            if len(body) < 30 or body in ("[deleted]", "[removed]"):
                continue
            candidates.append(comment)
        proc.kill()
        proc.wait()
    except FileNotFoundError:
        return []

    if len(candidates) > max_posts:
        candidates = random.sample(candidates, max_posts)
    return candidates


# ---------------------------------------------------------------------------
# Step 2: Compile rules with and without atmosphere
# ---------------------------------------------------------------------------

async def compile_rules_for_subreddit(
    compiler: RuleCompiler,
    sub: str,
    rules: list[dict],
    atmosphere: dict | None,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """Compile all rules for a subreddit, optionally with atmosphere."""
    community = _make_community(sub)
    rule_objects = [_make_rule(r["title"], r.get("description", "")) for r in rules]

    compiled_rules = []
    for rule_dict, rule_obj in zip(rules, rule_objects):
        async with semaphore:
            try:
                items, examples = await compiler.compile_rule(
                    rule=rule_obj,
                    community=community,
                    other_rules=[r for r in rule_objects if r.id != rule_obj.id],
                    community_atmosphere=atmosphere,
                )

                # Convert ChecklistItem objects to dicts for serialization
                checklist_tree = _items_to_nested_dicts(items)

                compiled_rules.append({
                    "rule_text": rule_obj.text,
                    "checklist": checklist_tree,
                    "examples": examples,
                    "subreddit": sub,
                    "atmosphere_used": atmosphere is not None,
                    "n_atmosphere_influenced": sum(
                        1 for item in items
                        if getattr(item, "atmosphere_influenced", False)
                    ),
                })
            except Exception as e:
                logger.error(f"Compilation failed for {sub}/{rule_dict['title']}: {e}")
                compiled_rules.append({
                    "rule_text": rule_obj.text,
                    "checklist": [],
                    "examples": [],
                    "subreddit": sub,
                    "error": str(e),
                })

    return compiled_rules


def _items_to_nested_dicts(items: list) -> list[dict]:
    """Convert flat ChecklistItem list (with parent_id) back to nested tree dicts."""
    items_by_id = {}
    for item in items:
        d = {
            "id": item.id,
            "description": item.description,
            "rule_text_anchor": getattr(item, "rule_text_anchor", None),
            "item_type": item.item_type,
            "logic": item.logic if isinstance(item.logic, dict) else {},
            "action": item.action,
            "atmosphere_influenced": getattr(item, "atmosphere_influenced", False),
            "atmosphere_note": getattr(item, "atmosphere_note", None),
            "children": [],
        }
        items_by_id[item.id] = d

    roots = []
    for item in items:
        d = items_by_id[item.id]
        parent_id = getattr(item, "parent_id", None)
        if parent_id and parent_id in items_by_id:
            items_by_id[parent_id]["children"].append(d)
        else:
            roots.append(d)

    return roots


async def compile_both_conditions(
    compiler: RuleCompiler,
    rules_by_sub: dict[str, list[dict]],
    subreddits: list[str],
    atmospheres: dict[str, dict],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Compile rules with and without atmosphere for all subreddits."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    with_atm: dict[str, list[dict]] = {}
    without_atm: dict[str, list[dict]] = {}

    for sub in subreddits:
        rules = rules_by_sub.get(sub, [])
        if not rules:
            continue

        logger.info(f"Compiling {sub} ({len(rules)} rules) — without atmosphere...")
        without_atm[sub] = await compile_rules_for_subreddit(
            compiler, sub, rules, atmosphere=None, semaphore=semaphore,
        )

        atm = atmospheres.get(sub)
        if atm:
            logger.info(f"Compiling {sub} ({len(rules)} rules) — with atmosphere...")
            with_atm[sub] = await compile_rules_for_subreddit(
                compiler, sub, rules, atmosphere=atm, semaphore=semaphore,
            )
        else:
            logger.warning(f"No atmosphere for {sub}, using without-atmosphere for both")
            with_atm[sub] = without_atm[sub]

    return with_atm, without_atm


# ---------------------------------------------------------------------------
# Step 3: Evaluate both conditions against ModBench
# ---------------------------------------------------------------------------

async def evaluate_condition(
    compiled_by_sub: dict[str, list[dict]],
    modbench: list[dict],
    settings: Settings,
    condition_name: str,
) -> list[dict]:
    """Evaluate a set of compiled rules against ModBench."""
    from scripts.eval_functional import (
        _flatten_checklist, _make_example_objects, _make_rule_object,
        evaluate_single_rule, compute_metrics,
    )

    from scripts.evaluate_output import _make_anthropic_client
    client, _ = _make_anthropic_client()
    if "bedrock" in type(client).__name__.lower():
        settings.haiku_model = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        settings.sonnet_model = "global.anthropic.claude-sonnet-4-6"
    sub_eval = SubjectiveEvaluator(client, settings)
    tree_eval = TreeEvaluator(sub_eval)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    results = []
    for mb in modbench:
        subreddit = mb["subreddit"]
        compiled_rules = compiled_by_sub.get(subreddit, [])
        compiled_rules = [r for r in compiled_rules if r.get("checklist")]

        if not compiled_rules:
            results.append({
                "id": mb["id"],
                "subreddit": subreddit,
                "ground_truth": mb["ground_truth_verdict"],
                "predicted": "error",
                "correct": False,
                "confidence": 0.0,
                "condition": condition_name,
            })
            continue

        async def _eval_rule(cr):
            async with semaphore:
                try:
                    return await evaluate_single_rule(
                        tree_eval, cr, mb["post"], subreddit, use_examples=True,
                    )
                except Exception as e:
                    logger.error(f"Eval failed {mb['id']}: {e}")
                    return None

        raw = await asyncio.gather(*[_eval_rule(cr) for cr in compiled_rules])
        rule_results = [r for r in raw if r is not None]

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

        ground_truth = mb["ground_truth_verdict"]
        results.append({
            "id": mb["id"],
            "subreddit": subreddit,
            "ground_truth": ground_truth,
            "predicted": final_verdict,
            "correct": final_verdict == ground_truth,
            "confidence": final_confidence,
            "difficulty": mb.get("difficulty", ""),
            "source": mb.get("source", ""),
            "condition": condition_name,
        })

    return results


# ---------------------------------------------------------------------------
# Step 4: Compare conditions
# ---------------------------------------------------------------------------

def compute_metrics(results: list[dict]) -> dict:
    """Compute metrics (same as eval_functional)."""
    valid = [r for r in results if r["predicted"] != "error"]
    n = len(valid)
    if not n:
        return {"n_total": 0, "n_errors": len(results)}

    correct = sum(1 for r in valid if r["correct"])
    metrics: dict[str, Any] = {
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

    by_sub: dict[str, list[bool]] = defaultdict(list)
    for r in valid:
        by_sub[r["subreddit"]].append(r["correct"])
    sub_accs = [sum(v) / len(v) for v in by_sub.values()]
    metrics["mean_per_subreddit_accuracy"] = round(sum(sub_accs) / len(sub_accs), 4) if sub_accs else 0

    return metrics


async def run_quality_judge(
    compiled_with: dict[str, list[dict]],
    compiled_without: dict[str, list[dict]],
    settings: Settings,
) -> tuple[dict, dict]:
    """Run LLM-as-a-judge on both compiled conditions."""
    from scripts.evaluate_output import judge_compiled_rules

    logger.info("Running LLM-judge on with-atmosphere compiled rules...")
    with_quality = await judge_compiled_rules(compiled_with, settings)

    logger.info("Running LLM-judge on without-atmosphere compiled rules...")
    without_quality = await judge_compiled_rules(compiled_without, settings)

    return with_quality, without_quality


def compare_conditions(
    with_results: list[dict],
    without_results: list[dict],
    compiled_with: dict[str, list[dict]] | None,
    compiled_without: dict[str, list[dict]] | None,
    atmospheres: dict[str, dict] | None,
    output_path: Path,
    quality_scores: tuple[dict, dict] | None = None,
):
    """Compare with-atmosphere vs without-atmosphere and write results."""
    wm = compute_metrics(with_results)
    wom = compute_metrics(without_results)

    # Per-entry comparison
    wr = {r["id"]: r for r in with_results if r["predicted"] != "error"}
    wor = {r["id"]: r for r in without_results if r["predicted"] != "error"}
    common_ids = sorted(set(wr.keys()) & set(wor.keys()))

    n_changed = sum(1 for eid in common_ids if wr[eid]["predicted"] != wor[eid]["predicted"])
    n_improved = sum(1 for eid in common_ids if wr[eid]["correct"] and not wor[eid]["correct"])
    n_degraded = sum(1 for eid in common_ids if not wr[eid]["correct"] and wor[eid]["correct"])

    # McNemar
    b, c = n_improved, n_degraded
    mcnemar_chi2 = (abs(b - c) - 1) ** 2 / (b + c) if (b + c) > 0 else 0

    # Stratify by subreddit
    by_sub = defaultdict(lambda: {"with": [], "without": []})
    for eid in common_ids:
        sub = wr[eid]["subreddit"]
        by_sub[sub]["with"].append(wr[eid]["correct"])
        by_sub[sub]["without"].append(wor[eid]["correct"])

    per_sub_delta = {}
    for sub, data in by_sub.items():
        w_acc = sum(data["with"]) / len(data["with"]) if data["with"] else 0
        wo_acc = sum(data["without"]) / len(data["without"]) if data["without"] else 0
        per_sub_delta[sub] = {
            "n": len(data["with"]),
            "with_atmosphere": round(w_acc, 4),
            "without_atmosphere": round(wo_acc, 4),
            "delta": round(w_acc - wo_acc, 4),
        }

    # Count atmosphere-influenced items
    atm_influence_stats = {}
    if compiled_with:
        for sub, rules in compiled_with.items():
            total_items = 0
            atm_items = 0
            for r in rules:
                n_atm = r.get("n_atmosphere_influenced", 0)
                n_total = len(r.get("checklist", []))
                atm_items += n_atm
                total_items += n_total
            atm_influence_stats[sub] = {
                "total_items": total_items,
                "atmosphere_influenced": atm_items,
                "pct_influenced": round(atm_items / total_items, 4) if total_items > 0 else 0,
            }

    # Stratify by rule subjectivity (% subjective items)
    strat_by_subjectivity = {}
    if compiled_with and compiled_without:
        from scripts.eval_functional import _flatten_checklist

        # Compute subjective % per subreddit from without-atmosphere (baseline)
        sub_subjectivity = {}
        for sub, rules in compiled_without.items():
            total = 0
            subjective = 0
            for r in rules:
                items = _flatten_checklist(r.get("checklist", []), "x")
                total += len(items)
                subjective += sum(1 for i in items if i.item_type == "subjective")
            sub_subjectivity[sub] = subjective / total if total > 0 else 0

        for bucket_name, filter_fn in [
            ("mostly_deterministic", lambda pct: pct < 0.3),
            ("mixed", lambda pct: 0.3 <= pct <= 0.7),
            ("mostly_subjective", lambda pct: pct > 0.7),
        ]:
            matching_subs = [s for s, pct in sub_subjectivity.items() if filter_fn(pct)]
            w_entries = [wr[eid] for eid in common_ids if wr[eid]["subreddit"] in matching_subs]
            wo_entries = [wor[eid] for eid in common_ids if wor[eid]["subreddit"] in matching_subs]
            if w_entries:
                w_acc = sum(1 for e in w_entries if e["correct"]) / len(w_entries)
                wo_acc = sum(1 for e in wo_entries if e["correct"]) / len(wo_entries)
                strat_by_subjectivity[bucket_name] = {
                    "n": len(w_entries),
                    "subreddits": matching_subs,
                    "with_atmosphere": round(w_acc, 4),
                    "without_atmosphere": round(wo_acc, 4),
                    "delta": round(w_acc - wo_acc, 4),
                }

    # Quality scores from LLM-judge
    quality_data = {}
    if quality_scores:
        wq, woq = quality_scores
        quality_data = {
            "with_atmosphere_quality": wq.get("aggregate", {}),
            "without_atmosphere_quality": woq.get("aggregate", {}),
            "with_atmosphere_per_rule": wq.get("per_rule", []),
            "without_atmosphere_per_rule": woq.get("per_rule", []),
        }

    output = {
        "summary": {
            "n_common_pairs": len(common_ids),
            "n_changed_verdicts": n_changed,
            "n_improved_by_atmosphere": n_improved,
            "n_degraded_by_atmosphere": n_degraded,
            "with_atmosphere_accuracy": wm.get("accuracy", 0),
            "without_atmosphere_accuracy": wom.get("accuracy", 0),
            "accuracy_delta": round(wm.get("accuracy", 0) - wom.get("accuracy", 0), 4),
        },
        "mcnemar": {
            "b_improved": b,
            "c_degraded": c,
            "chi2": round(mcnemar_chi2, 4),
            "significant_p05": mcnemar_chi2 > 3.84,
        },
        "with_atmosphere_metrics": wm,
        "without_atmosphere_metrics": wom,
        "per_subreddit": per_sub_delta,
        "atmosphere_influence": atm_influence_stats,
        "by_subjectivity": strat_by_subjectivity,
        "atmospheres_used": atmospheres or {},
        "with_atmosphere_results": with_results,
        "without_atmosphere_results": without_results,
        **quality_data,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    # Print summary
    print(f"\n{'='*70}")
    print(f"Q6: Effect of Community Atmosphere on Compilation")
    print(f"{'='*70}")
    s = output["summary"]
    print(f"Common pairs: {s['n_common_pairs']}")
    print(f"Changed verdicts: {s['n_changed_verdicts']}")
    print(f"  Improved by atmosphere: {s['n_improved_by_atmosphere']}")
    print(f"  Degraded by atmosphere: {s['n_degraded_by_atmosphere']}")
    print(f"\nAccuracy:")
    print(f"  With atmosphere:    {s['with_atmosphere_accuracy']:.1%}")
    print(f"  Without atmosphere: {s['without_atmosphere_accuracy']:.1%}")
    sign = "+" if s["accuracy_delta"] >= 0 else ""
    print(f"  Delta:              {sign}{s['accuracy_delta']:.1%}")

    mc = output["mcnemar"]
    sig = "significant" if mc["significant_p05"] else "not significant"
    print(f"\nMcNemar's test: chi2={mc['chi2']:.2f}, {sig} at p<0.05")

    if per_sub_delta:
        print(f"\nPer subreddit:")
        for sub, info in sorted(per_sub_delta.items(), key=lambda x: -abs(x[1]["delta"])):
            sign = "+" if info["delta"] >= 0 else ""
            print(f"  {sub:<25} with={info['with_atmosphere']:.1%}  without={info['without_atmosphere']:.1%}  delta={sign}{info['delta']:.1%}  (n={info['n']})")

    if atm_influence_stats:
        print(f"\nAtmosphere influence on checklist items:")
        for sub, info in atm_influence_stats.items():
            print(f"  {sub:<25} {info['atmosphere_influenced']}/{info['total_items']} items ({info['pct_influenced']:.0%})")

    if strat_by_subjectivity:
        print(f"\nBy rule subjectivity:")
        for bucket, info in strat_by_subjectivity.items():
            sign = "+" if info["delta"] >= 0 else ""
            print(f"  {bucket:<25} n={info['n']:>4}  delta={sign}{info['delta']:.1%}")

    if quality_scores:
        wq, woq = quality_scores
        wq_llm = wq.get("aggregate", {}).get("llm_scores", {})
        woq_llm = woq.get("aggregate", {}).get("llm_scores", {})
        print(f"\nCompilation Quality (LLM-judge, 1-5 scale):")
        print(f"  {'Dimension':<22} {'With':>8} {'Without':>8} {'Delta':>8}")
        print(f"  {'-'*46}")
        for dim in ["coverage", "logical_correctness", "minimality", "clarity", "anchor_accuracy", "example_quality", "mean"]:
            wv = wq_llm.get(dim, 0)
            wov = woq_llm.get(dim, 0)
            delta = wv - wov
            sign = "+" if delta >= 0 else ""
            print(f"  {dim:<22} {wv:>8.2f} {wov:>8.2f} {sign}{delta:>7.2f}")

    print(f"\nResults written to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(
        description="Q6: Community atmosphere ablation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode", help="Mode")

    # Full pipeline mode (default)
    full = sub.add_parser("run", help="Generate atmosphere, compile both conditions, evaluate")
    full.add_argument("--rules", type=Path, required=True,
                      help="modbench_rules.json (subreddit → rules list)")
    full.add_argument("--subreddits", nargs="+", required=True)
    full.add_argument("--modbench", type=Path, default=DEFAULT_MODBENCH)
    full.add_argument("--pushshift-dir", type=Path, default=None,
                      help="Directory with *_comments.zst archives for richer atmosphere")
    full.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    full.add_argument("--limit", type=int, default=None)
    full.add_argument("--save-compiled", action="store_true",
                      help="Save compiled rules to separate JSON files")
    full.add_argument("--judge", action="store_true",
                      help="Run LLM-as-a-judge on compiled rules for quality scores")

    # Compare pre-existing compiled outputs
    comp = sub.add_parser("compare", help="Compare pre-compiled with/without atmosphere results")
    comp.add_argument("--with-atmosphere", type=Path, required=True)
    comp.add_argument("--without-atmosphere", type=Path, required=True)
    comp.add_argument("--modbench", type=Path, default=DEFAULT_MODBENCH)
    comp.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    comp.add_argument("--limit", type=int, default=None)
    comp.add_argument("--judge", action="store_true",
                      help="Run LLM-as-a-judge on compiled rules for quality scores")

    args = parser.parse_args()

    if not args.mode:
        parser.print_help()
        return

    settings = Settings()

    from scripts.evaluate_output import _make_anthropic_client
    client, model = _make_anthropic_client()
    if "bedrock" in type(client).__name__.lower():
        settings.haiku_model = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        settings.sonnet_model = "global.anthropic.claude-sonnet-4-6"
        settings.compiler_model = "global.anthropic.claude-sonnet-4-6"
    compiler = RuleCompiler(client, settings)

    if args.mode == "run":
        with open(args.rules) as f:
            rules_by_sub = json.load(f)

        with open(args.modbench) as f:
            modbench = json.load(f)
        if args.limit:
            modbench = modbench[:args.limit]

        # Step 1: Generate atmospheres
        logger.info("Step 1: Generating community atmospheres...")
        atmospheres = await generate_atmospheres(
            compiler, rules_by_sub, args.subreddits, args.pushshift_dir,
        )

        # Step 2: Compile both conditions
        logger.info("Step 2: Compiling rules (with and without atmosphere)...")
        with_compiled, without_compiled = await compile_both_conditions(
            compiler, rules_by_sub, args.subreddits, atmospheres,
        )

        if args.save_compiled:
            with open(SCRIPTS_DIR / "compiled_with_atmosphere.json", "w") as f:
                json.dump(with_compiled, f, indent=2)
            with open(SCRIPTS_DIR / "compiled_without_atmosphere.json", "w") as f:
                json.dump(without_compiled, f, indent=2)
            logger.info("Saved compiled rules to scripts/compiled_with_atmosphere.json and compiled_without_atmosphere.json")

        # Step 3: Evaluate both conditions
        logger.info("Step 3: Evaluating with-atmosphere condition...")
        with_results = await evaluate_condition(
            with_compiled, modbench, settings, "with_atmosphere",
        )

        logger.info("Step 3: Evaluating without-atmosphere condition...")
        without_results = await evaluate_condition(
            without_compiled, modbench, settings, "without_atmosphere",
        )

        # Step 4: Quality judge (optional)
        quality = None
        if args.judge:
            logger.info("Step 4: Running LLM-judge on compiled rules...")
            quality = await run_quality_judge(with_compiled, without_compiled, settings)

        # Step 5: Compare
        compare_conditions(
            with_results, without_results,
            with_compiled, without_compiled,
            atmospheres, args.output,
            quality_scores=quality,
        )

    elif args.mode == "compare":
        with open(args.with_atmosphere) as f:
            with_compiled = json.load(f)
        with open(args.without_atmosphere) as f:
            without_compiled = json.load(f)

        with open(args.modbench) as f:
            modbench = json.load(f)
        if args.limit:
            modbench = modbench[:args.limit]

        logger.info("Evaluating with-atmosphere condition...")
        with_results = await evaluate_condition(
            with_compiled, modbench, settings, "with_atmosphere",
        )

        logger.info("Evaluating without-atmosphere condition...")
        without_results = await evaluate_condition(
            without_compiled, modbench, settings, "without_atmosphere",
        )

        quality = None
        if args.judge:
            logger.info("Running LLM-judge on compiled rules...")
            quality = await run_quality_judge(with_compiled, without_compiled, settings)

        compare_conditions(
            with_results, without_results,
            with_compiled, without_compiled,
            None, args.output,
            quality_scores=quality,
        )


if __name__ == "__main__":
    asyncio.run(main())
