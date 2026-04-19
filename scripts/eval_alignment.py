"""
Q2: Alignment/Suggestion pipeline evaluation.

Evaluates two alignment functions via functional accuracy (not LLM-as-judge):

  RQ2A — suggest_from_examples:
    1. Evaluate compiled rules on ModBench set 1 to find FN/FP
    2. Feed FN/FP as labeled examples to suggest_from_examples()
    3. Auto-apply returned suggestions to the checklist
    4. Re-evaluate on ModBench set 2 with updated checklists
    5. Compare accuracy before vs. after suggestions

  RQ2C — recompile_with_diff:
    1. Compile "old" rule text → checklist_old
    2. Edit rule text → apply recompile_with_diff() → checklist_diff
    3. Compile "new" rule text from scratch → checklist_fresh
    4. Evaluate both on ModBench
    5. Compare: does diff-based recompilation match fresh compilation accuracy?

Usage:
    python scripts/eval_alignment.py --mode suggest-from-examples \\
        --modbench-set1 scripts/modbench_set1.json \\
        --modbench-set2 scripts/modbench_set2.json \\
        --compiled scripts/compiler_test_output.json

    python scripts/eval_alignment.py --mode recompile-diff \\
        --modbench scripts/modbench.json \\
        --compiled scripts/compiler_test_output.json

    python scripts/eval_alignment.py --mode all
"""

import argparse
import asyncio
import copy
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
from src.automod.compiler.compiler import RuleCompiler
from src.automod.core.tree_evaluator import TreeEvaluator
from src.automod.core.subjective import SubjectiveEvaluator
from src.automod.core.actions import VERDICT_PRECEDENCE

# Reuse helpers from eval_functional
from scripts.eval_functional import (
    _flatten_checklist,
    _make_example_objects,
    _make_rule_object,
    build_subreddit_rule_index,
    evaluate_post_all_rules,
    evaluate_single_rule,
    compute_metrics,
    bootstrap_ci,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent
DEFAULT_COMPILED_SOURCES = [
    SCRIPTS_DIR / "compiler_test_output.json",
    SCRIPTS_DIR / "compiler_test_sampled.json",
]
DEFAULT_OUTPUT = SCRIPTS_DIR / "eval_alignment_results.json"
MAX_CONCURRENT = 20


# ---------------------------------------------------------------------------
# Helpers: create in-memory objects for compiler calls
# ---------------------------------------------------------------------------

def _make_objects(rule_text: str, subreddit: str, checklist: list[dict], examples: list[dict]):
    """Create SimpleNamespace objects for rule, community, checklist, examples."""
    rule_id = str(uuid.uuid4())
    community_id = str(uuid.uuid4())

    rule = SimpleNamespace(
        id=rule_id,
        community_id=community_id,
        title=rule_text[:80],
        text=rule_text,
        rule_type="actionable",
    )
    community = SimpleNamespace(
        id=community_id,
        name=subreddit,
        platform="reddit",
    )

    items = _flatten_checklist(checklist, rule_id)

    label_map = {"positive": "compliant", "negative": "violating"}
    ex_objs = [
        SimpleNamespace(
            id=str(uuid.uuid4()),
            community_id=community_id,
            label=label_map.get(ex.get("label", ""), ex.get("label", "")),
            content=ex.get("content", {}),
            source="generated",
            moderator_reasoning=None,
        )
        for ex in examples
    ]

    return rule, community, items, ex_objs


# ---------------------------------------------------------------------------
# Auto-apply suggestions to a checklist
# ---------------------------------------------------------------------------

def apply_suggestions(checklist: list[dict], suggestions: list[dict]) -> list[dict]:
    """Apply checklist suggestions to produce an updated checklist.

    Handles three cases:
      - Update existing item: suggestion has target=<item_id> + proposed_change
      - Add new item: suggestion has target=null + proposed_change
      - Rule text suggestions: skipped (we only modify checklists here)

    Returns a deep copy of the checklist with modifications applied.
    """
    updated = copy.deepcopy(checklist)

    # Build a flat index of all items (including nested children)
    def _index_items(items, parent_path=None):
        """Returns {item_id: (items_list, index_in_list)} for mutation."""
        idx = {}
        for i, item in enumerate(items):
            item_id = item.get("id")
            if item_id:
                idx[item_id] = (items, i)
            children = item.get("children", [])
            if children:
                idx.update(_index_items(children))
        return idx

    item_index = _index_items(updated)

    for suggestion in suggestions:
        if suggestion.get("suggestion_type") != "checklist":
            continue

        proposed = suggestion.get("proposed_change")
        if not proposed:
            continue

        target_id = suggestion.get("target")
        parent_id = suggestion.get("parent_id")

        if target_id and target_id in item_index:
            # Update existing item
            items_list, idx = item_index[target_id]
            existing = items_list[idx]
            # Merge proposed_change into existing item, preserving id and children
            for key, val in proposed.items():
                if key not in ("id", "children"):
                    existing[key] = val
        else:
            # Add new item
            if not proposed.get("id"):
                proposed["id"] = str(uuid.uuid4())

            if parent_id and parent_id in item_index:
                # Add as child of parent
                parent_list, parent_idx = item_index[parent_id]
                parent_item = parent_list[parent_idx]
                if "children" not in parent_item:
                    parent_item["children"] = []
                parent_item["children"].append(proposed)
            else:
                # Add as root-level item
                updated.append(proposed)

            # Refresh index
            item_index = _index_items(updated)

    return updated


# ---------------------------------------------------------------------------
# RQ2A: suggest_from_examples — accuracy before/after applying suggestions
# ---------------------------------------------------------------------------

async def _find_fn_fp(
    tree_eval: TreeEvaluator,
    modbench_entries: list[dict],
    compiled_rules_by_sub: dict[str, list[dict]],
    use_examples: bool,
    semaphore: asyncio.Semaphore,
) -> dict[str, dict[str, list[dict]]]:
    """Evaluate modbench and return FN/FP per (subreddit, rule_text_prefix).

    Returns: {subreddit: {"false_negatives": [...], "false_positives": [...]}}
    where each entry is a modbench post with its evaluation result.
    """
    errors_by_sub: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: {"false_negatives": [], "false_positives": []}
    )

    # Build tasks for all posts that have compiled rules, run concurrently
    pairs = []
    for mb in modbench_entries:
        sub = mb["subreddit"]
        compiled_rules = compiled_rules_by_sub.get(sub)
        if compiled_rules:
            pairs.append((mb, compiled_rules))

    tasks = [
        evaluate_post_all_rules(tree_eval, mb, compiled_rules, use_examples, semaphore)
        for mb, compiled_rules in pairs
    ]
    results = await asyncio.gather(*tasks)

    for (mb, _), result in zip(pairs, results):
        sub = mb["subreddit"]
        gt = result["ground_truth"]
        pred = result["predicted"]

        if gt == "remove" and pred == "approve":
            errors_by_sub[sub]["false_negatives"].append({
                "post": mb["post"],
                "ground_truth": gt,
                "predicted": pred,
                # Option B: rule attribution (populated if modbench has "violated_rules" field)
                "attributed_rule_keys": mb.get("violated_rules", []),
            })
        elif gt == "approve" and pred in ("remove", "review"):
            errors_by_sub[sub]["false_positives"].append({
                "post": mb["post"],
                "ground_truth": gt,
                "predicted": pred,
                # Option A: which specific rules incorrectly triggered
                "triggered_rule_keys": [
                    tr["rule_text_key"] for tr in result.get("triggered_rules", [])
                ],
            })

    return dict(errors_by_sub)


def _errors_to_examples_for_rule(errors: dict[str, list[dict]], rule_key: str) -> list[dict]:
    """Build error examples relevant to a specific rule.

    Routing:
      - FP (ground truth: approve, predicted: remove): include only if this rule was
        among the ones that triggered (Option A).
      - FN (ground truth: remove, predicted: approve): include only if this rule was
        attributed as the one violated (Option B, via modbench's violated_rules field).
        If no attribution exists, include the FN for all rules as a fallback (legacy).
    """
    examples = []
    for post_info in errors.get("false_negatives", []):
        attrib = post_info.get("attributed_rule_keys", [])
        # If no attribution info, fall back to broadcast (include for all rules)
        if not attrib or rule_key in attrib:
            examples.append({
                "label": "negative",
                "content": post_info["post"],
            })
    for post_info in errors.get("false_positives", []):
        triggered = post_info.get("triggered_rule_keys", [])
        # Only include FP if this specific rule was among those that fired
        if rule_key in triggered:
            examples.append({
                "label": "positive",
                "content": post_info["post"],
            })
    return examples


def _errors_to_examples(errors: dict[str, list[dict]]) -> list[dict]:
    """Legacy: all errors for all rules. Kept for backward compat but not used in per-rule routing."""
    examples = []
    for post_info in errors.get("false_negatives", []):
        examples.append({"label": "negative", "content": post_info["post"]})
    for post_info in errors.get("false_positives", []):
        examples.append({"label": "positive", "content": post_info["post"]})
    return examples


async def eval_suggest_from_examples(
    modbench_set1_path: Path,
    modbench_set2_path: Path,
    compiled_sources: list[Path],
    settings: Settings,
    output_path: Path,
    limit: int | None = None,
):
    """RQ2A: Measure accuracy improvement from suggest_from_examples.

    Flow:
      1. Run set 1 → find FN/FP per subreddit
      2. For each subreddit's rules: call suggest_from_examples with error examples
      3. Auto-apply suggestions to checklist
      4. Run set 2 with original checklists → baseline accuracy
      5. Run set 2 with updated checklists → post-suggestion accuracy
      6. Compare
    """
    from scripts.evaluate_output import _make_anthropic_client
    client, model = _make_anthropic_client()
    if "bedrock" in type(client).__name__.lower():
        settings.haiku_model = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        settings.sonnet_model = "global.anthropic.claude-sonnet-4-6"
        settings.compiler_model = "global.anthropic.claude-sonnet-4-6"
    sub_eval = SubjectiveEvaluator(client, settings)
    tree_eval = TreeEvaluator(sub_eval)
    compiler = RuleCompiler(client, settings)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    # Load data
    logger.info(f"Loading ModBench set 1: {modbench_set1_path}")
    with open(modbench_set1_path) as f:
        set1 = json.load(f)
    logger.info(f"Loading ModBench set 2: {modbench_set2_path}")
    with open(modbench_set2_path) as f:
        set2 = json.load(f)

    if limit:
        set1 = set1[:limit]
        set2 = set2[:limit]

    compiled_by_sub = build_subreddit_rule_index(compiled_sources)
    logger.info(f"Compiled rules for {len(compiled_by_sub)} subreddits")

    # Step 1: Find FN/FP on set 1
    logger.info("Step 1: Evaluating set 1 to find FN/FP...")
    errors_by_sub = await _find_fn_fp(tree_eval, set1, compiled_by_sub, True, semaphore)

    total_fn = sum(len(e["false_negatives"]) for e in errors_by_sub.values())
    total_fp = sum(len(e["false_positives"]) for e in errors_by_sub.values())
    logger.info(f"Found {total_fn} FN + {total_fp} FP across {len(errors_by_sub)} subreddits")

    # Step 2-3: Generate and apply suggestions per subreddit per rule
    updated_rules_by_sub: dict[str, list[dict]] = {}
    suggestion_log = []

    for sub, errors in errors_by_sub.items():
        has_any_errors = errors.get("false_negatives") or errors.get("false_positives")
        if not has_any_errors:
            updated_rules_by_sub[sub] = compiled_by_sub[sub]
            continue

        original_rules = compiled_by_sub.get(sub, [])
        updated_rules = []

        for rule_data in original_rules:
            # Route errors per-rule (Option A for FP, Option B for FN)
            rule_key = rule_data["rule_text"][:60]
            error_examples = _errors_to_examples_for_rule(errors, rule_key)

            if not error_examples:
                # No errors relevant to this rule — skip suggestion generation, keep original
                updated_rules.append(rule_data)
                suggestion_log.append({
                    "subreddit": sub,
                    "rule_text_short": rule_data["rule_text"][:80],
                    "n_fn": 0, "n_fp": 0,
                    "n_suggestions": 0,
                    "n_checklist_suggestions": 0,
                    "n_rule_text_suggestions": 0,
                    "suggestions": [],
                    "skipped_reason": "no relevant errors",
                })
                continue

            rule, community, items, _ = _make_objects(
                rule_data["rule_text"], sub,
                rule_data["checklist"], rule_data.get("examples", [])
            )

            # Build example objects from the error posts (per-rule routed)
            label_map = {"positive": "compliant", "negative": "violating"}
            error_ex_objs = [
                SimpleNamespace(
                    id=str(uuid.uuid4()),
                    community_id=community.id,
                    label=label_map.get(ex["label"], ex["label"]),
                    content=ex["content"],
                    source="modbench_error",
                    moderator_reasoning=None,
                )
                for ex in error_examples
            ]

            n_fn_here = sum(1 for e in error_examples if e["label"] == "negative")
            n_fp_here = sum(1 for e in error_examples if e["label"] == "positive")
            logger.info(f"  Suggesting for {sub} / {rule_data['rule_text'][:50]}... ({n_fn_here} FN + {n_fp_here} FP routed to this rule)")
            try:
                suggestions = await compiler.suggest_from_examples(
                    rule=rule, checklist=items, examples=error_ex_objs
                )
            except Exception as e:
                logger.error(f"  suggest_from_examples failed: {e}")
                suggestions = []

            # Auto-apply suggestions
            updated_checklist = apply_suggestions(rule_data["checklist"], suggestions)

            updated_rule = {
                **rule_data,
                "checklist": updated_checklist,
            }
            updated_rules.append(updated_rule)

            suggestion_log.append({
                "subreddit": sub,
                "rule_text_short": rule_data["rule_text"][:80],
                "n_fn_routed": n_fn_here,
                "n_fp_routed": n_fp_here,
                "n_fn_total_sub": len(errors["false_negatives"]),
                "n_fp_total_sub": len(errors["false_positives"]),
                "n_suggestions": len(suggestions),
                "n_checklist_suggestions": sum(1 for s in suggestions if s.get("suggestion_type") == "checklist"),
                "n_rule_text_suggestions": sum(1 for s in suggestions if s.get("suggestion_type") == "rule_text"),
                "suggestions": suggestions,
            })

        updated_rules_by_sub[sub] = updated_rules

    # For subreddits with no errors, keep original rules
    for sub in compiled_by_sub:
        if sub not in updated_rules_by_sub:
            updated_rules_by_sub[sub] = compiled_by_sub[sub]

    # Step 4: Evaluate set 2 with ORIGINAL checklists (baseline)
    logger.info("Step 4: Evaluating set 2 with original checklists (baseline)...")
    baseline_tasks = []
    for mb in set2:
        compiled_rules = compiled_by_sub.get(mb["subreddit"])
        if compiled_rules:
            baseline_tasks.append(
                evaluate_post_all_rules(tree_eval, mb, compiled_rules, True, semaphore)
            )
    baseline_results = list(await asyncio.gather(*baseline_tasks))

    # Step 5: Evaluate set 2 with UPDATED checklists
    logger.info("Step 5: Evaluating set 2 with updated checklists...")
    updated_tasks = []
    for mb in set2:
        compiled_rules = updated_rules_by_sub.get(mb["subreddit"])
        if compiled_rules:
            updated_tasks.append(
                evaluate_post_all_rules(tree_eval, mb, compiled_rules, True, semaphore)
            )
    updated_results = list(await asyncio.gather(*updated_tasks))

    # Step 6: Compare
    baseline_metrics = compute_metrics(baseline_results)
    updated_metrics = compute_metrics(updated_results)
    baseline_ci = bootstrap_ci(baseline_results)
    updated_ci = bootstrap_ci(updated_results)

    # Paired comparison
    baseline_by_id = {r["id"]: r for r in baseline_results}
    updated_by_id = {r["id"]: r for r in updated_results}
    common_ids = set(baseline_by_id.keys()) & set(updated_by_id.keys())

    n_improved = 0
    n_degraded = 0
    n_unchanged = 0
    for pid in common_ids:
        b_correct = baseline_by_id[pid]["correct"]
        u_correct = updated_by_id[pid]["correct"]
        if not b_correct and u_correct:
            n_improved += 1
        elif b_correct and not u_correct:
            n_degraded += 1
        else:
            n_unchanged += 1

    # McNemar's test
    b = n_improved  # updated correct, baseline wrong
    c = n_degraded  # baseline correct, updated wrong
    chi2 = ((abs(b - c) - 1) ** 2 / (b + c)) if (b + c) > 0 else 0

    output = {
        "mode": "suggest-from-examples",
        "config": {
            "modbench_set1": str(modbench_set1_path),
            "modbench_set2": str(modbench_set2_path),
            "n_set1": len(set1),
            "n_set2": len(set2),
            "n_subreddits_with_errors": len(errors_by_sub),
            "total_fn": total_fn,
            "total_fp": total_fp,
        },
        "baseline_metrics": baseline_metrics,
        "updated_metrics": updated_metrics,
        "baseline_ci": baseline_ci,
        "updated_ci": updated_ci,
        "comparison": {
            "n_common_pairs": len(common_ids),
            "n_improved": n_improved,
            "n_degraded": n_degraded,
            "n_unchanged": n_unchanged,
            "baseline_accuracy": baseline_metrics.get("accuracy", 0),
            "updated_accuracy": updated_metrics.get("accuracy", 0),
            "accuracy_delta": round(
                updated_metrics.get("accuracy", 0) - baseline_metrics.get("accuracy", 0), 4
            ),
        },
        "mcnemar": {
            "b_improved": b,
            "c_degraded": c,
            "chi2": round(chi2, 4),
            "significant_p05": chi2 > 3.84,
        },
        "suggestion_log": suggestion_log,
    }

    # Print summary
    print(f"\n{'='*60}")
    print(f"RQ2A: suggest_from_examples — Accuracy Before/After")
    print(f"{'='*60}")
    print(f"Set 1 errors: {total_fn} FN + {total_fp} FP")
    print(f"Suggestions generated: {sum(s['n_suggestions'] for s in suggestion_log)}")
    print(f"  Checklist suggestions: {sum(s['n_checklist_suggestions'] for s in suggestion_log)}")
    print(f"  Rule text suggestions: {sum(s['n_rule_text_suggestions'] for s in suggestion_log)}")
    print(f"\nBaseline accuracy (set 2): {baseline_metrics.get('accuracy', 0):.1%}")
    print(f"Updated accuracy (set 2):  {updated_metrics.get('accuracy', 0):.1%}")
    print(f"Delta:                     {output['comparison']['accuracy_delta']:+.1%}")
    print(f"Improved: {n_improved}, Degraded: {n_degraded}, Unchanged: {n_unchanged}")
    print(f"McNemar chi2={chi2:.2f} ({'sig' if chi2 > 3.84 else 'n.s.'} at p<.05)")

    return output


# ---------------------------------------------------------------------------
# RQ2C: recompile_with_diff — diff vs. fresh compilation equivalence
# ---------------------------------------------------------------------------

def _generate_rule_edits(compiled_rules: list[dict], n: int = 10) -> list[dict]:
    """Generate edit scenarios from compiled rules by simulating rule text changes."""
    import random
    random.seed(42)

    sample = random.sample(compiled_rules, min(n, len(compiled_rules)))
    scenarios = []

    for rule in sample:
        original = rule["rule_text"]

        # Edit 1: Minor rewording (append clarification)
        scenarios.append({
            "subreddit": rule["subreddit"],
            "original_rule_text": original,
            "edited_rule_text": original.rstrip() + " Please follow this rule.",
            "checklist": rule["checklist"],
            "examples": rule.get("examples", []),
            "edit_type": "minor_rewording",
        })

        # Edit 2: Add clause (append new constraint)
        scenarios.append({
            "subreddit": rule["subreddit"],
            "original_rule_text": original,
            "edited_rule_text": original + "\n\nRepeat offenders will face a permanent ban.",
            "checklist": rule["checklist"],
            "examples": rule.get("examples", []),
            "edit_type": "add_clause",
        })

    return scenarios


async def eval_recompile_diff(
    modbench_path: Path,
    compiled_sources: list[Path],
    settings: Settings,
    output_path: Path,
    n_scenarios: int = 10,
    limit: int | None = None,
):
    """RQ2C: Test whether old_checklist + diff ≈ fresh compilation of new rule.

    For each edit scenario:
      1. Take existing checklist (compiled from original rule) → checklist_old
      2. Apply recompile_with_diff(edited_rule, checklist_old) → checklist_diff
      3. Compile edited rule from scratch → checklist_fresh
      4. Evaluate both on ModBench
      5. Compare accuracy: diff-based vs. fresh
    """
    from scripts.evaluate_output import _make_anthropic_client
    client, model = _make_anthropic_client()
    if "bedrock" in type(client).__name__.lower():
        settings.haiku_model = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        settings.sonnet_model = "global.anthropic.claude-sonnet-4-6"
        settings.compiler_model = "global.anthropic.claude-sonnet-4-6"
    sub_eval = SubjectiveEvaluator(client, settings)
    tree_eval = TreeEvaluator(sub_eval)
    compiler = RuleCompiler(client, settings)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    # Load ModBench
    logger.info(f"Loading ModBench: {modbench_path}")
    with open(modbench_path) as f:
        modbench = json.load(f)
    if limit:
        modbench = modbench[:limit]

    # Load compiled rules and generate edit scenarios
    compiled_by_sub = build_subreddit_rule_index(compiled_sources)

    # Flatten all rules for sampling
    all_rules = []
    for sub, rules in compiled_by_sub.items():
        for r in rules:
            all_rules.append({**r, "subreddit": sub})

    scenarios = _generate_rule_edits(all_rules, n_scenarios)
    logger.info(f"Generated {len(scenarios)} edit scenarios")

    results = []

    for scenario in scenarios:
        sub = scenario["subreddit"]
        original_text = scenario["original_rule_text"]
        edited_text = scenario["edited_rule_text"]
        edit_type = scenario["edit_type"]
        old_checklist = scenario["checklist"]

        logger.info(f"  {edit_type}: {sub} / {original_text[:50]}...")

        # Step 2: recompile_with_diff
        rule_edited, community, old_items, _ = _make_objects(
            edited_text, sub, old_checklist, []
        )
        try:
            operations = await compiler.recompile_with_diff(
                rule=rule_edited, community=community,
                other_rules=[], existing_items=old_items,
            )
            # Apply operations to build checklist_diff
            checklist_diff = _apply_diff_operations(old_checklist, operations)
        except Exception as e:
            logger.error(f"  recompile_with_diff failed: {e}")
            results.append({
                "subreddit": sub,
                "edit_type": edit_type,
                "error": f"recompile_with_diff: {e}",
            })
            continue

        # Step 3: Compile from scratch
        try:
            fresh_items, fresh_examples = await compiler.compile_rule(
                rule=SimpleNamespace(
                    id=str(uuid.uuid4()),
                    community_id=community.id,
                    title=edited_text[:80],
                    text=edited_text,
                    rule_type="actionable",
                ),
                community=community,
                other_rules=[],
            )
            checklist_fresh = _items_to_nested(fresh_items)
        except Exception as e:
            logger.error(f"  Fresh compilation failed: {e}")
            results.append({
                "subreddit": sub,
                "edit_type": edit_type,
                "error": f"fresh_compile: {e}",
            })
            continue

        # Step 4: Evaluate both on relevant ModBench entries
        sub_entries = [mb for mb in modbench if mb["subreddit"] == sub]
        if not sub_entries:
            logger.warning(f"  No ModBench entries for {sub}, skipping")
            results.append({
                "subreddit": sub,
                "edit_type": edit_type,
                "error": "no_modbench_entries",
            })
            continue

        diff_rule_data = {
            "rule_text": edited_text,
            "checklist": checklist_diff,
            "examples": scenario.get("examples", []),
        }
        fresh_rule_data = {
            "rule_text": edited_text,
            "checklist": checklist_fresh,
            "examples": fresh_examples,
        }

        # Evaluate both
        diff_tasks = [
            evaluate_post_all_rules(tree_eval, mb, [diff_rule_data], True, semaphore)
            for mb in sub_entries
        ]
        fresh_tasks = [
            evaluate_post_all_rules(tree_eval, mb, [fresh_rule_data], True, semaphore)
            for mb in sub_entries
        ]

        diff_results, fresh_results = await asyncio.gather(
            asyncio.gather(*diff_tasks),
            asyncio.gather(*fresh_tasks),
        )
        diff_results = list(diff_results)
        fresh_results = list(fresh_results)

        diff_metrics = compute_metrics(diff_results)
        fresh_metrics = compute_metrics(fresh_results)

        # Count operation types
        op_counts = {}
        for op in operations:
            op_type = op.get("op") or op.get("operation") or "unknown"
            op_counts[op_type] = op_counts.get(op_type, 0) + 1

        # Agreement rate: how often diff and fresh give same verdict
        n_agree = 0
        for dr, fr in zip(diff_results, fresh_results):
            if dr["predicted"] == fr["predicted"]:
                n_agree += 1
        agreement_rate = n_agree / len(diff_results) if diff_results else 0

        results.append({
            "subreddit": sub,
            "edit_type": edit_type,
            "rule_text_short": original_text[:80],
            "n_entries": len(sub_entries),
            "op_counts": op_counts,
            "diff_accuracy": diff_metrics.get("accuracy", 0),
            "fresh_accuracy": fresh_metrics.get("accuracy", 0),
            "accuracy_delta": round(
                diff_metrics.get("accuracy", 0) - fresh_metrics.get("accuracy", 0), 4
            ),
            "agreement_rate": round(agreement_rate, 4),
            "diff_metrics": diff_metrics,
            "fresh_metrics": fresh_metrics,
        })

    # Aggregate
    valid = [r for r in results if "error" not in r]
    avg_diff_acc = sum(r["diff_accuracy"] for r in valid) / len(valid) if valid else 0
    avg_fresh_acc = sum(r["fresh_accuracy"] for r in valid) / len(valid) if valid else 0
    avg_agreement = sum(r["agreement_rate"] for r in valid) / len(valid) if valid else 0

    output = {
        "mode": "recompile-diff",
        "config": {
            "modbench": str(modbench_path),
            "n_scenarios": len(scenarios),
            "n_errors": len(results) - len(valid),
        },
        "summary": {
            "mean_diff_accuracy": round(avg_diff_acc, 4),
            "mean_fresh_accuracy": round(avg_fresh_acc, 4),
            "mean_accuracy_delta": round(avg_diff_acc - avg_fresh_acc, 4),
            "mean_agreement_rate": round(avg_agreement, 4),
        },
        "by_edit_type": _aggregate_by_edit_type(valid),
        "results": results,
    }

    # Print summary
    print(f"\n{'='*60}")
    print(f"RQ2C: recompile_with_diff — Diff vs. Fresh Compilation")
    print(f"{'='*60}")
    print(f"Scenarios: {len(scenarios)} ({len(valid)} valid, {len(results) - len(valid)} errors)")
    print(f"Mean diff accuracy:  {avg_diff_acc:.1%}")
    print(f"Mean fresh accuracy: {avg_fresh_acc:.1%}")
    print(f"Mean delta:          {avg_diff_acc - avg_fresh_acc:+.1%}")
    print(f"Mean agreement rate: {avg_agreement:.1%}")

    by_type = output["by_edit_type"]
    if by_type:
        print(f"\nBy edit type:")
        for et, info in by_type.items():
            print(f"  {et}: diff={info['diff_accuracy']:.1%} fresh={info['fresh_accuracy']:.1%} "
                  f"agree={info['agreement_rate']:.1%} (n={info['n']})")

    return output


def _apply_diff_operations(old_checklist: list[dict], operations: list[dict]) -> list[dict]:
    """Apply recompile_with_diff operations to produce an updated checklist.

    Operation schema (from _RECOMPILE_TOOL):
      op: keep|update|delete|add
      existing_id: id of item to keep/update/delete
      description, rule_text_anchor, item_type, logic, action, children: fields for add/update
    """
    result = []

    # Build index of old items by id
    old_by_id = {}
    def _index(items):
        for item in items:
            if "id" in item:
                old_by_id[item["id"]] = item
            for child in item.get("children", []):
                _index([child])
    _index(old_checklist)

    def _extract_item_fields(op_dict: dict) -> dict:
        """Extract checklist-item fields from an operation dict."""
        fields = {}
        for k in ("description", "rule_text_anchor", "item_type", "logic", "action", "children"):
            if k in op_dict and op_dict[k] is not None:
                fields[k] = op_dict[k]
        return fields

    for op in operations:
        operation = op.get("op") or op.get("operation") or "keep"
        item_id = op.get("existing_id") or op.get("item_id")

        if operation == "keep":
            if item_id and item_id in old_by_id:
                result.append(copy.deepcopy(old_by_id[item_id]))
        elif operation == "update":
            updated_fields = _extract_item_fields(op)
            if item_id and item_id in old_by_id:
                merged = copy.deepcopy(old_by_id[item_id])
                for k, v in updated_fields.items():
                    merged[k] = v
                result.append(merged)
            elif updated_fields:
                updated_fields.setdefault("id", str(uuid.uuid4()))
                result.append(updated_fields)
        elif operation == "add":
            new_fields = _extract_item_fields(op)
            if new_fields:
                new_fields.setdefault("id", str(uuid.uuid4()))
                result.append(new_fields)
        elif operation == "delete":
            pass  # skip deleted items

    # If no operations matched anything, fall back to old checklist
    if not result and old_checklist:
        logger.warning("No operations produced results, falling back to old checklist")
        return copy.deepcopy(old_checklist)

    return result


def _items_to_nested(items: list) -> list[dict]:
    """Convert flat ChecklistItem objects back to nested dicts with children."""
    items_by_id = {}
    for item in items:
        d = {
            "id": item.id,
            "description": getattr(item, "description", ""),
            "rule_text_anchor": getattr(item, "rule_text_anchor", None),
            "item_type": getattr(item, "item_type", "subjective"),
            "logic": item.logic if isinstance(getattr(item, "logic", None), dict) else {},
            "action": getattr(item, "action", "flag"),
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


def _aggregate_by_edit_type(results: list[dict]) -> dict:
    """Aggregate results by edit_type."""
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_type[r["edit_type"]].append(r)

    agg = {}
    for et, items in by_type.items():
        agg[et] = {
            "n": len(items),
            "diff_accuracy": round(sum(r["diff_accuracy"] for r in items) / len(items), 4),
            "fresh_accuracy": round(sum(r["fresh_accuracy"] for r in items) / len(items), 4),
            "agreement_rate": round(sum(r["agreement_rate"] for r in items) / len(items), 4),
        }
    return agg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Q2: Alignment evaluation")
    parser.add_argument("--mode", choices=["suggest-from-examples", "recompile-diff", "all"],
                        required=True)
    parser.add_argument("--modbench-set1", type=Path, default=SCRIPTS_DIR / "modbench_set1.json",
                        help="ModBench set 1 for RQ2A (suggestion discovery)")
    parser.add_argument("--modbench-set2", type=Path, default=SCRIPTS_DIR / "modbench_set2.json",
                        help="ModBench set 2 for RQ2A (re-evaluation)")
    parser.add_argument("--modbench", type=Path, default=SCRIPTS_DIR / "modbench.json",
                        help="ModBench for RQ2C")
    parser.add_argument("--compiled", nargs="*", type=Path, default=None,
                        help="Compiled rule sources")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--n-scenarios", type=int, default=10,
                        help="Number of edit scenarios for recompile-diff")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit ModBench entries per set")
    args = parser.parse_args()

    settings = Settings()

    compiled_sources = args.compiled or DEFAULT_COMPILED_SOURCES

    all_results = {}
    modes = ["suggest-from-examples", "recompile-diff"] if args.mode == "all" else [args.mode]

    for mode in modes:
        logger.info(f"\n--- Running mode: {mode} ---")

        if mode == "suggest-from-examples":
            result = await eval_suggest_from_examples(
                modbench_set1_path=args.modbench_set1,
                modbench_set2_path=args.modbench_set2,
                compiled_sources=compiled_sources,
                settings=settings,
                output_path=args.output,
                limit=args.limit,
            )
            all_results[mode] = result

        elif mode == "recompile-diff":
            result = await eval_recompile_diff(
                modbench_path=args.modbench,
                compiled_sources=compiled_sources,
                settings=settings,
                output_path=args.output,
                n_scenarios=args.n_scenarios,
                limit=args.limit,
            )
            all_results[mode] = result

    with open(args.output, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
