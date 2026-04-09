"""Rule health metrics computation + LLM-based diagnosis."""

from collections import defaultdict
from typing import Any

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import (
    ChecklistItem,
    Decision,
    Example,
    ExampleChecklistItemLink,
    ExampleRuleLink,
    Rule,
    Suggestion,
)

router = APIRouter(tags=["health"])


def get_compiler() -> RuleCompiler:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return RuleCompiler(client, settings)


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
    override_count = sum(1 for d in rule_decisions if d.was_override)

    for decision in rule_decisions:
        reasoning = decision.agent_reasoning.get(rule_id, {})
        item_reasoning = reasoning.get("item_reasoning", {})
        mod_verdict = decision.moderator_verdict  # approve | remove | review

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
                    })
            # FN: item didn't trigger but mod removed, AND this rule was relevant
            # (at least one sibling item triggered — proving the rule applied to this post).
            # If no items triggered, the removal is unlinked and belongs in
            # uncovered_violations, not as a per-item miss.
            elif not triggered and mod_verdict == "remove" and any_item_triggered:
                item_fn_count[item_id] += 1
                item_conf_errors[item_id].append(confidence)
                if len(item_fn_cases[item_id]) < _MAX_ERROR_CASES:
                    item_fn_cases[item_id].append({
                        "decision_id": decision.id,
                        "title": case_title,
                        "confidence": round(confidence, 2),
                    })
            else:
                item_conf_correct[item_id].append(confidence)

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

    return {
        "rule_id": rule_id,
        "overall": {
            "total_decisions": total_decisions,
            "override_rate": override_count / total_decisions if total_decisions > 0 else 0.0,
            "covered_by_examples": covered_pct,
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
