"""Rule health metrics computation + LLM-based diagnosis."""

import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..config import get_anthropic_client, settings
from ..compiler.compiler import RuleCompiler
from ..core.subjective import SubjectiveEvaluator
from ..core.tree_evaluator import TreeEvaluator
from ..db.database import get_db
from ..db.models import (
    ChecklistItem,
    Community,
    Decision,
    Example,
    ExampleChecklistItemLink,
    ExampleRuleLink,
    Rule,
    Suggestion,
)
from .alignment import _apply_diff_to_checklist

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def get_compiler() -> RuleCompiler:
    client = get_anthropic_client()
    return RuleCompiler(client, settings)


@router.get("/communities/{community_id}/rules-health-summary")
async def get_rules_health_summary(
    community_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """Lightweight per-rule error summary for sidebar display."""
    # Fetch all active rules for this community
    rules_result = await db.execute(
        select(Rule).where(Rule.community_id == community_id, Rule.is_active == True)
    )
    rules = list(rules_result.scalars().all())
    if not rules:
        return []

    rule_ids = {r.id for r in rules}

    # Fetch all checklist items for these rules
    items_result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.rule_id.in_(rule_ids))
    )
    all_items = list(items_result.scalars().all())
    items_by_rule: dict[str, set[str]] = defaultdict(set)
    for item in all_items:
        items_by_rule[item.rule_id].add(item.id)

    # Fetch all resolved decisions for this community
    decisions_result = await db.execute(
        select(Decision).where(
            Decision.community_id == community_id,
            Decision.moderator_verdict != "pending",
        )
    )
    all_resolved = list(decisions_result.scalars().all())

    # Rule-level FN: examples linked to a rule by the moderator where the agent
    # did not trigger that rule. Mirrors get_rule_health's rule_fn_count.
    decisions_by_post_id = {d.post_platform_id: d for d in all_resolved if d.post_platform_id}
    fn_rows_result = await db.execute(
        select(ExampleRuleLink.rule_id, Example.content)
        .select_from(ExampleRuleLink)
        .join(Example, Example.id == ExampleRuleLink.example_id)
        .where(ExampleRuleLink.rule_id.in_(rule_ids))
        .where(Example.source == "moderator_decision")
        .where(Example.label.in_(["violating", "borderline"]))
    )
    rule_fn_by_rule: dict[str, int] = defaultdict(int)
    for rid_fn, content in fn_rows_result.all():
        post_id = (content or {}).get("id", "")
        if not post_id:
            continue
        decision = decisions_by_post_id.get(post_id)
        if decision and rid_fn not in (decision.triggered_rules or []):
            rule_fn_by_rule[rid_fn] += 1

    summaries = []
    for rule in rules:
        rid = rule.id
        rule_item_ids = items_by_rule.get(rid, set())

        # Filter to decisions where this rule was evaluated
        rule_decisions = [
            d for d in all_resolved if rid in (d.agent_reasoning or {})
        ]
        decision_count = len(rule_decisions)

        error_count = 0
        for decision in rule_decisions:
            reasoning = decision.agent_reasoning.get(rid, {})
            item_reasoning = reasoning.get("item_reasoning", {})
            mod_verdict = decision.moderator_verdict

            any_item_triggered = any(
                ir.get("triggered", False)
                for iid, ir in item_reasoning.items()
                if iid in rule_item_ids
            )

            for item_id, item_data in item_reasoning.items():
                if item_id not in rule_item_ids:
                    continue
                triggered = item_data.get("triggered", False)
                # FP: item triggered but mod approved
                if triggered and mod_verdict == "approve":
                    error_count += 1
                # FN: item missed but mod acted (removed/warned), and rule was relevant
                elif not triggered and mod_verdict in ("remove", "warn") and any_item_triggered:
                    error_count += 1

        # Rule-level FN: mod explicitly linked this rule to a violation the agent missed.
        # Match get_rule_health: denominator grows with these cases too.
        rule_fn = rule_fn_by_rule.get(rid, 0)
        error_count += rule_fn
        denom = decision_count + rule_fn

        summaries.append({
            "rule_id": rid,
            "decision_count": decision_count,
            "error_count": error_count,
            "error_rate": error_count / denom if denom > 0 else 0.0,
        })

    return summaries


@router.get("/rules/{rule_id}/health")
async def get_rule_health(rule_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Pure computation — no LLM. Returns per-item FP/FN rates from resolved decisions."""
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    # Fetch all checklist items for this rule
    items_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    all_items = list(items_result.scalars().all())
    items_by_id = {item.id: item for item in all_items}

    # Fetch resolved decisions where this rule is in agent_reasoning
    decisions_result = await db.execute(
        select(Decision)
        .where(
            Decision.community_id == rule.community_id,
            Decision.moderator_verdict != "pending",
        )
    )
    all_resolved = list(decisions_result.scalars().all())

    # Filter to decisions where this rule was evaluated
    rule_decisions = [
        d for d in all_resolved
        if rule_id in (d.agent_reasoning or {})
    ]

    # Per-item accumulators
    item_fp_count: dict[str, int] = defaultdict(int)
    item_fn_count: dict[str, int] = defaultdict(int)
    item_total: dict[str, int] = defaultdict(int)
    item_conf_correct: dict[str, list[float]] = defaultdict(list)
    item_conf_errors: dict[str, list[float]] = defaultdict(list)
    # Collect actual FP/FN cases (up to 5 per item) so moderators can inspect them
    item_fp_cases: dict[str, list[dict]] = defaultdict(list)
    item_fn_cases: dict[str, list[dict]] = defaultdict(list)
    _MAX_ERROR_CASES = 5

    total_decisions = len(rule_decisions)
    override_count = 0
    rule_fp_count = 0  # rule triggered but mod approved
    rule_fn_count = 0  # rule didn't trigger but mod removed (and rule looks relevant)

    for decision in rule_decisions:
        reasoning = decision.agent_reasoning.get(rule_id, {})
        item_reasoning = reasoning.get("item_reasoning", {})
        mod_verdict = decision.moderator_verdict  # approve | remove | review
        rule_verdict = reasoning.get("verdict", "approve")

        # Determine if at least one item on this rule triggered for this decision.
        # A "missed" (FN) only makes sense for an item when the rule was relevant
        # to the post — i.e. some sibling item triggered, proving the rule applied.
        # Without this, unlinked removals (mod removed with no rule association)
        # get counted as FN for every item on every rule.
        any_item_triggered = any(
            ir.get("triggered", False)
            for iid, ir in item_reasoning.items()
            if iid in items_by_id
        )

        # Per-rule override: this rule's verdict disagrees with the moderator.
        # Treat the rule as "wanting to act" if either the tree resolved to an action
        # OR any item flagged — the latter aligns the rule-level FP count with the
        # per-item FP counts shown in the health panel (otherwise 2 items can be
        # marked unhealthy while the rule-level box reads 0).
        rule_would_act = rule_verdict in ("remove", "warn", "review") or any_item_triggered
        mod_would_act = mod_verdict in ("remove", "warn")
        if rule_would_act != mod_would_act:
            override_count += 1
        if rule_would_act and mod_verdict == "approve":
            rule_fp_count += 1

        post = decision.post_content or {}
        inner = post.get("content", {})
        case_title = (inner.get("title", "") if isinstance(inner, dict) else "") or "(no title)"

        for item_id, item_data in item_reasoning.items():
            if item_id not in items_by_id:
                continue  # item was recompiled away

            triggered = item_data.get("triggered", False)
            confidence = item_data.get("confidence", 0.5)
            item_total[item_id] += 1

            # FP: item triggered but mod approved (wrongly flagged)
            if triggered and mod_verdict == "approve":
                item_fp_count[item_id] += 1
                item_conf_errors[item_id].append(confidence)
                if len(item_fp_cases[item_id]) < _MAX_ERROR_CASES:
                    item_fp_cases[item_id].append({
                        "decision_id": decision.id,
                        "title": case_title,
                        "confidence": round(confidence, 2),
                        "moderator_notes": decision.moderator_notes,
                        "moderator_reasoning_category": decision.moderator_reasoning_category,
                    })
            # FN: item didn't trigger but mod removed, AND this rule was relevant
            # (at least one sibling item triggered — proving the rule applied to this post).
            # If no items triggered, the removal is unlinked and belongs in
            # uncovered_violations, not as a per-item miss.
            elif not triggered and mod_verdict in ("remove", "warn") and any_item_triggered:
                item_fn_count[item_id] += 1
                item_conf_errors[item_id].append(confidence)
                if len(item_fn_cases[item_id]) < _MAX_ERROR_CASES:
                    item_fn_cases[item_id].append({
                        "decision_id": decision.id,
                        "title": case_title,
                        "confidence": round(confidence, 2),
                        "moderator_notes": decision.moderator_notes,
                        "moderator_reasoning_category": decision.moderator_reasoning_category,
                    })
            else:
                item_conf_correct[item_id].append(confidence)

    # Rule-level FN: examples auto-linked to this rule from a moderator decision where
    # the agent did NOT trigger this rule (moderator flagged it as a miss).
    decisions_by_post_id = {d.post_platform_id: d for d in all_resolved if d.post_platform_id}
    fn_candidates_result = await db.execute(
        select(Example)
        .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
        .where(ExampleRuleLink.rule_id == rule_id)
        .where(Example.source == "moderator_decision")
        .where(Example.label.in_(["violating", "borderline"]))
    )
    for ex in fn_candidates_result.scalars().all():
        post_id = (ex.content or {}).get("id", "")
        if not post_id:
            continue
        decision = decisions_by_post_id.get(post_id)
        if decision and rule_id not in (decision.triggered_rules or []):
            rule_fn_count += 1

    # Fetch examples linked to this rule
    example_ids_result = await db.execute(
        select(ExampleRuleLink.example_id).where(ExampleRuleLink.rule_id == rule_id)
    )
    example_ids = [r[0] for r in example_ids_result]

    # Group examples by checklist item
    item_example_groups: dict[str, dict[str, list]] = defaultdict(
        lambda: {"compliant": [], "violating": [], "borderline": []}
    )
    uncovered_violations: list[dict] = []

    if example_ids:
        examples_result = await db.execute(
            select(Example).where(Example.id.in_(example_ids))
        )
        examples_by_id = {e.id: e for e in examples_result.scalars().all()}

        links_result = await db.execute(
            select(ExampleChecklistItemLink)
            .where(ExampleChecklistItemLink.example_id.in_(example_ids))
        )
        all_links = list(links_result.scalars().all())

        linked_example_ids: set[str] = set()
        for link in all_links:
            if link.checklist_item_id and link.checklist_item_id in items_by_id:
                linked_example_ids.add(link.example_id)
                example = examples_by_id.get(link.example_id)
                if example:
                    content = example.content or {}
                    inner = content.get("content", {})
                    title = inner.get("title", "") if isinstance(inner, dict) else ""
                    summary = {
                        "example_id": example.id,
                        "label": example.label,
                        "title": title or "(no title)",
                    }
                    item_example_groups[link.checklist_item_id][example.label].append(summary)

        # Uncovered violations: violating examples with no valid item link
        for eid, example in examples_by_id.items():
            if example.label == "violating" and eid not in linked_example_ids:
                content = example.content or {}
                inner = content.get("content", {})
                title = inner.get("title", "") if isinstance(inner, dict) else ""
                uncovered_violations.append({
                    "example_id": example.id,
                    "label": "violating",
                    "title": title or "(no title)",
                })

    # Compute per-item metrics and attach examples
    item_metrics = []
    for item in all_items:
        total = item_total[item.id]
        fp = item_fp_count[item.id]
        fn = item_fn_count[item.id]

        fp_rate = fp / total if total > 0 else 0.0
        fn_rate = fn / total if total > 0 else 0.0
        sort_score = max(fp_rate, fn_rate)

        conf_correct_vals = item_conf_correct[item.id]
        conf_error_vals = item_conf_errors[item.id]

        item_metrics.append({
            "item_id": item.id,
            "parent_id": item.parent_id,
            "description": item.description,
            "item_type": item.item_type,
            "action": item.action,
            "sort_score": sort_score,
            "false_positive_rate": fp_rate,
            "false_positive_count": fp,
            "false_negative_rate": fn_rate,
            "false_negative_count": fn,
            "avg_confidence_correct": (
                sum(conf_correct_vals) / len(conf_correct_vals) if conf_correct_vals else None
            ),
            "avg_confidence_errors": (
                sum(conf_error_vals) / len(conf_error_vals) if conf_error_vals else None
            ),
            "decision_count": total,
            "examples": item_example_groups[item.id],
            "wrongly_flagged": item_fp_cases[item.id],
            "missed_violations": item_fn_cases[item.id],
        })

    # Sort worst first
    item_metrics.sort(key=lambda x: x["sort_score"], reverse=True)

    # % items with at least one linked example
    items_with_examples = sum(
        1 for iid in items_by_id
        if any(item_example_groups[iid][label] for label in ("compliant", "violating", "borderline"))
    )
    covered_pct = items_with_examples / len(all_items) if all_items else 0.0

    fn_denominator = total_decisions + rule_fn_count

    return {
        "rule_id": rule_id,
        "overall": {
            "total_decisions": total_decisions,
            "override_rate": override_count / total_decisions if total_decisions > 0 else 0.0,
            "covered_by_examples": covered_pct,
            "wrongly_flagged_count": rule_fp_count,
            "wrongly_flagged_rate": rule_fp_count / total_decisions if total_decisions > 0 else 0.0,
            "missed_count": rule_fn_count,
            "missed_rate": rule_fn_count / fn_denominator if fn_denominator > 0 else 0.0,
        },
        "items": item_metrics,
        "uncovered_violations": uncovered_violations,
    }


@router.post("/rules/{rule_id}/analyze-health")
async def analyze_rule_health(rule_id: str, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """LLM call: diagnose per-item issues and create Suggestion records."""
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    items_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    checklist = list(items_result.scalars().all())
    items_by_id = {item.id: item for item in checklist}

    health_data = await get_rule_health(rule_id, db)

    compiler = get_compiler()
    diagnoses = await compiler.diagnose_rule_health(rule, checklist, health_data)

    created: list[Suggestion] = []

    # Index new_items by split_from so split halves can be merged into one suggestion
    new_items_by_split: dict[str, dict] = {}
    standalone_new_items: list[dict] = []
    for new_item_diag in diagnoses.get("new_items", []):
        split_from = new_item_diag.get("split_from")
        if split_from and split_from in items_by_id:
            new_items_by_split[split_from] = new_item_diag
        else:
            standalone_new_items.append(new_item_diag)

    for diag in diagnoses.get("diagnoses", []):
        item_id = diag.get("item_id")
        if not item_id or item_id not in items_by_id:
            continue

        action = diag.get("action", "tighten_rubric")
        reasoning = diag.get("reasoning", "")
        proposed_change = diag.get("proposed_change") or {}
        confidence = diag.get("confidence", "medium")

        # Build as a recompile diff operation so accept_recompile can apply it
        update_op: dict = {"op": "update", "existing_id": item_id}
        update_op.update({k: v for k, v in proposed_change.items() if k != "id"})

        # Only include "children" for actions that restructure child items (split_item).
        # For other actions (promote, tighten, threshold), preserve existing children.
        if action != "split_item":
            update_op.pop("children", None)

        operations = [update_op]

        # For split_item, merge the second half (from new_items) into the same suggestion
        if action == "split_item" and item_id in new_items_by_split:
            split_new = new_items_by_split.pop(item_id)
            add_op = {"op": "add", **(split_new.get("proposed_item") or {})}
            if "children" not in add_op:
                add_op["children"] = []
            operations.append(add_op)
            reasoning = f"{reasoning} — also adds: {split_new.get('reasoning', '')}"

        suggestion = Suggestion(
            rule_id=rule_id,
            checklist_item_id=item_id,
            suggestion_type="checklist",
            content={
                "operations": operations,
                "action": action,
                "reasoning": reasoning,
                "confidence": confidence,
                "description": f"[{action}] {reasoning[:100]}",
            },
        )
        db.add(suggestion)
        created.append(suggestion)

    # Any new_items not consumed by a split_item diagnosis (+ unlinked ones)
    for new_item_diag in standalone_new_items + list(new_items_by_split.values()):
        action = new_item_diag.get("action", "add_item")
        reasoning = new_item_diag.get("reasoning", "")
        proposed_item = new_item_diag.get("proposed_item") or {}
        motivated_by = new_item_diag.get("motivated_by", [])

        op = {"op": "add", **proposed_item}
        if "children" not in op:
            op["children"] = []

        suggestion = Suggestion(
            rule_id=rule_id,
            checklist_item_id=None,
            suggestion_type="checklist",
            content={
                "operations": [op],
                "action": action,
                "reasoning": reasoning,
                "description": f"[add_item] {reasoning[:100]}",
                "motivated_by": motivated_by,
            },
        )
        db.add(suggestion)
        created.append(suggestion)

    await db.commit()
    for s in created:
        await db.refresh(s)

    return [
        {
            "id": s.id,
            "rule_id": s.rule_id,
            "checklist_item_id": s.checklist_item_id,
            "suggestion_type": s.suggestion_type,
            "content": s.content,
            "status": s.status,
            "created_at": s.created_at.isoformat(),
        }
        for s in created
    ]


_HEALTH_ACTIONS = {"tighten_rubric", "adjust_threshold", "promote_to_deterministic", "split_item", "add_item"}


@router.post("/rules/{rule_id}/preview-fixes")
async def preview_fixes(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Evaluate error cases against a hypothetical checklist with ALL pending fixes applied.

    Merges operations from every pending health suggestion into one combined
    hypothetical checklist, then evaluates each error case against it.
    Returns per-decision old vs new verdicts.
    """
    empty = {"evaluations": [], "summary": {"total_error_cases": 0, "would_fix": 0, "would_remain": 0, "would_regress": 0}}

    # Load rule and community
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    comm_result = await db.execute(select(Community).where(Community.id == rule.community_id))
    community = comm_result.scalar_one_or_none()
    community_name = community.name if community else ""

    # Collect all pending health suggestions and merge their operations
    sug_result = await db.execute(
        select(Suggestion).where(
            Suggestion.rule_id == rule_id,
            Suggestion.suggestion_type == "checklist",
            Suggestion.status == "pending",
        )
    )
    all_suggestions = list(sug_result.scalars().all())
    merged_ops: list[dict] = []
    for sug in all_suggestions:
        action = (sug.content or {}).get("action", "")
        if action not in _HEALTH_ACTIONS:
            continue
        merged_ops.extend((sug.content or {}).get("operations", []))

    if not merged_ops:
        return empty

    # Load all checklist items for the rule
    items_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    all_existing = list(items_result.scalars().all())

    # Build combined hypothetical checklist
    hypothetical = _apply_diff_to_checklist(all_existing, merged_ops, rule_id)
    if not hypothetical:
        return empty

    # Collect error case decision IDs from health data
    health_data = await get_rule_health(rule_id, db)
    error_cases: list[dict] = []
    seen_decision_ids: set[str] = set()

    for item_metrics in health_data.get("items", []):
        item_id = item_metrics["item_id"]
        for case in item_metrics.get("wrongly_flagged", []):
            did = case["decision_id"]
            if did not in seen_decision_ids:
                seen_decision_ids.add(did)
                error_cases.append({**case, "error_type": "wrongly_flagged", "source_item_id": item_id})
        for case in item_metrics.get("missed_violations", []):
            did = case["decision_id"]
            if did not in seen_decision_ids:
                seen_decision_ids.add(did)
                error_cases.append({**case, "error_type": "missed_violation", "source_item_id": item_id})

    if not error_cases:
        return empty

    # Load decision rows to get post_content
    decisions_result = await db.execute(
        select(Decision).where(Decision.id.in_(list(seen_decision_ids)))
    )
    decisions_by_id = {d.id: d for d in decisions_result.scalars().all()}

    # Set up evaluator
    client = get_anthropic_client()
    subjective_evaluator = SubjectiveEvaluator(client, settings)
    tree_evaluator = TreeEvaluator(subjective_evaluator)

    # Evaluate each error case against the hypothetical checklist
    evaluations: list[dict] = []
    would_fix = 0
    would_remain = 0
    would_regress = 0

    for case in error_cases:
        decision = decisions_by_id.get(case["decision_id"])
        if not decision:
            continue

        old_rule_reasoning = (decision.agent_reasoning or {}).get(rule_id, {})
        old_verdict = old_rule_reasoning.get("verdict", "approve")

        try:
            new_result = await tree_evaluator.evaluate_rule(
                rule=rule,
                checklist=hypothetical,
                post=decision.post_content or {},
                community_name=community_name,
                examples=[],
            )
            new_verdict = new_result["verdict"]
            new_confidence = new_result["confidence"]
        except Exception as e:
            logger.warning(f"Preview evaluation failed for decision {decision.id}: {e}")
            new_verdict = "error"
            new_confidence = 0.0

        mod_verdict = decision.moderator_verdict
        error_type = case["error_type"]

        old_aligned = (old_verdict == "approve" and mod_verdict == "approve") or \
                      (old_verdict in ("remove", "warn", "review") and mod_verdict in ("remove", "warn"))
        new_aligned = (new_verdict == "approve" and mod_verdict == "approve") or \
                      (new_verdict in ("remove", "warn", "review") and mod_verdict in ("remove", "warn"))

        # A case counts as "fixed" only if the rule-level verdict actually moved
        # from misaligned to aligned with the moderator. If old_verdict was already
        # aligned (e.g. an item-level FP that the tree absorbed into "approve"),
        # treat the case as unchanged — not "fixed" — to avoid inflating the count.
        fixed = (not old_aligned) and new_aligned
        regressed = old_aligned and not new_aligned

        if fixed:
            would_fix += 1
        elif regressed:
            would_regress += 1
        else:
            would_remain += 1

        evaluations.append({
            "decision_id": decision.id,
            "title": case["title"],
            "error_type": error_type,
            "source_item_id": case["source_item_id"],
            "moderator_verdict": mod_verdict,
            "old_verdict": old_verdict,
            "new_verdict": new_verdict,
            "new_confidence": round(new_confidence, 3),
            "fixed": fixed,
            "regressed": regressed,
        })

    return {
        "evaluations": evaluations,
        "summary": {
            "total_error_cases": len(evaluations),
            "would_fix": would_fix,
            "would_remain": would_remain,
            "would_regress": would_regress,
        },
    }


@router.post("/rules/{rule_id}/reevaluate")
async def reevaluate_decisions(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Re-evaluate override decisions against the current checklist.

    Updates Decision.agent_reasoning[rule_id] in-place so that
    subsequent health metric reads reflect the updated checklist.
    Call this after applying fixes to get accurate health numbers immediately.
    """
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    comm_result = await db.execute(select(Community).where(Community.id == rule.community_id))
    community = comm_result.scalar_one_or_none()
    community_name = community.name if community else ""

    items_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    checklist = list(items_result.scalars().all())
    if not checklist:
        return {"reevaluated": 0}

    decisions_result = await db.execute(
        select(Decision).where(
            Decision.community_id == rule.community_id,
            Decision.moderator_verdict != "pending",
            Decision.was_override == True,  # noqa: E712
        )
    )
    decisions = [
        d for d in decisions_result.scalars().all()
        if rule_id in (d.agent_reasoning or {})
    ]

    if not decisions:
        return {"reevaluated": 0}

    client = get_anthropic_client()
    subjective_evaluator = SubjectiveEvaluator(client, settings)
    tree_evaluator = TreeEvaluator(subjective_evaluator)

    updated = 0
    for decision in decisions:
        try:
            new_result = await tree_evaluator.evaluate_rule(
                rule=rule,
                checklist=checklist,
                post=decision.post_content or {},
                community_name=community_name,
                examples=[],
            )

            reasoning = dict(decision.agent_reasoning or {})
            old_rule = reasoning.get(rule_id, {})
            reasoning[rule_id] = {
                "rule_title": old_rule.get("rule_title", rule.title),
                "verdict": new_result["verdict"],
                "confidence": new_result["confidence"],
                "item_reasoning": new_result["reasoning"],
                "triggered_items": new_result["triggered_items"],
            }
            decision.agent_reasoning = reasoning
            flag_modified(decision, "agent_reasoning")
            updated += 1
        except Exception as e:
            logger.warning(f"Re-evaluation failed for decision {decision.id}: {e}")

    await db.commit()
    return {"reevaluated": updated}
