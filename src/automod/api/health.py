"""Rule health metrics computation + LLM-based diagnosis."""

import logging
import re
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


def _dedup_by_post(decisions: list) -> list:
    """Collapse decisions sharing a post_platform_id, keeping the most recently
    resolved one. Health metrics treat a post as one moderation event regardless
    of how many times it was re-ingested or re-evaluated; without this dedup,
    crawler overlap or manual re-runs inflate FP/FN rates.
    """
    by_post: dict[str, Any] = {}
    extras: list = []
    for d in decisions:
        pid = d.post_platform_id
        if not pid:
            extras.append(d)
            continue
        existing = by_post.get(pid)
        if existing is None:
            by_post[pid] = d
            continue
        # Prefer the more-recently resolved decision
        if d.resolved_at and (not existing.resolved_at or d.resolved_at > existing.resolved_at):
            by_post[pid] = d
    return list(by_post.values()) + extras


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
    all_resolved = _dedup_by_post(list(decisions_result.scalars().all()))

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

        # Rule-level FP: any item triggered (rule wanted to act) but mod approved.
        # Mirror get_rule_health exactly so panel and sidebar share a numerator.
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
            if any_item_triggered and mod_verdict == "approve":
                error_count += 1

        # Rule-level FN: mod explicitly linked this rule to a violation the agent missed.
        # The rule_fn posts are already in the decision pool (see rule_fn_by_rule
        # construction at line 99-114, which requires post_id ∈ decisions_by_post_id),
        # so the denominator stays at decision_count — no inflation.
        rule_fn = rule_fn_by_rule.get(rid, 0)
        error_count += rule_fn
        denom = decision_count

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
    all_resolved = _dedup_by_post(list(decisions_result.scalars().all()))

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
    # Decision IDs powering the click-through filter on the health panel boxes.
    wrongly_flagged_decision_ids: list[str] = []
    missed_decision_ids: list[str] = []

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
        # Treat the rule as "wanting to act" iff any item flagged. This is the same
        # predicate the sidebar summary uses, so rule-wide FP and sidebar error_count
        # stay aligned. (Older versions also accepted rule_verdict == "review", but
        # that's only ever set by the community-norms path, not by an actual rule.)
        rule_would_act = any_item_triggered
        mod_would_act = mod_verdict in ("remove", "warn")
        if rule_would_act != mod_would_act:
            override_count += 1
        if rule_would_act and mod_verdict == "approve":
            rule_fp_count += 1
            wrongly_flagged_decision_ids.append(decision.id)

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
            missed_decision_ids.append(decision.id)

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

    # Denominator is the decision pool. rule_fn_count is a subset of those decisions
    # (the FN candidate query at line 297-311 filters to posts already in the pool),
    # so adding it would double-count.
    rule_denominator = total_decisions

    return {
        "rule_id": rule_id,
        "overall": {
            "total_decisions": total_decisions,
            "rule_denominator": rule_denominator,
            "override_rate": override_count / total_decisions if total_decisions > 0 else 0.0,
            "covered_by_examples": covered_pct,
            "wrongly_flagged_count": rule_fp_count,
            "wrongly_flagged_rate": rule_fp_count / rule_denominator if rule_denominator > 0 else 0.0,
            "wrongly_flagged_decision_ids": wrongly_flagged_decision_ids,
            "missed_count": rule_fn_count,
            "missed_rate": rule_fn_count / rule_denominator if rule_denominator > 0 else 0.0,
            "missed_decision_ids": missed_decision_ids,
        },
        "items": item_metrics,
        "uncovered_violations": uncovered_violations,
    }


_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "be", "by", "with", "this", "that", "these", "those", "it", "its", "as",
    "at", "from", "but", "not", "no", "any", "all", "can", "may", "must",
    "should", "would", "will", "do", "does", "did", "have", "has", "had",
    "you", "your", "we", "our", "they", "them", "their", "if", "when", "than",
}


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    tokens = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return {t for t in tokens if t not in _STOPWORDS}


def find_related_rules_for_context_note(
    proposed_note_text: str,
    proposed_note_tag: str | None,
    source_rule_id: str,
    sibling_rules: list[Rule],
) -> list[dict]:
    """Score sibling rules for likely co-applicability of a proposed context note.

    Phase 1: lexical overlap on Rule.text + custom_context_notes, plus a tag-overlap
    bonus when the sibling already opted into the same context tag.
    """
    note_tokens = _tokenize(proposed_note_text)
    if not note_tokens:
        return []

    out: list[dict] = []
    for rule in sibling_rules:
        if rule.id == source_rule_id:
            continue
        rule_tokens = _tokenize(rule.text or "")
        custom_text = " ".join(
            (n.get("text", "") if isinstance(n, dict) else "")
            for n in (rule.custom_context_notes or [])
        )
        rule_tokens |= _tokenize(custom_text)

        overlap = note_tokens & rule_tokens
        if not overlap:
            continue
        score = len(overlap) / len(note_tokens)
        signals = [f"text-overlap: {sorted(overlap)[:5]}"]

        if proposed_note_tag and rule.relevant_context:
            for tag_obj in rule.relevant_context:
                tag_str = (
                    f"{tag_obj.get('dimension')}:{tag_obj.get('tag')}"
                    if isinstance(tag_obj, dict)
                    else None
                )
                if tag_str and tag_str == proposed_note_tag:
                    score += 0.3
                    signals.append(f"shared-tag: {tag_str}")
                    break

        out.append({"rule_id": rule.id, "score": round(score, 3), "signals": signals})

    out.sort(key=lambda r: r["score"], reverse=True)
    return out


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

    # Discard prior pending suggestions for this rule before generating new ones —
    # users find stale fixes confusing when they overlap with a fresh batch. Accepted
    # / dismissed records are kept for audit.
    from sqlalchemy import delete as sa_delete
    await db.execute(
        sa_delete(Suggestion)
        .where(Suggestion.rule_id == rule_id)
        .where(Suggestion.status == "pending")
    )
    await db.flush()

    # Load community context + sibling rules so the diagnoser can fire L2 triggers.
    community_result = await db.execute(select(Community).where(Community.id == rule.community_id))
    community = community_result.scalar_one_or_none()
    siblings_result = await db.execute(
        select(Rule).where(Rule.community_id == rule.community_id, Rule.id != rule.id)
    )
    sibling_rules = list(siblings_result.scalars().all())
    sibling_rule_dicts = [
        {"id": r.id, "title": r.title, "text": (r.text or "")[:300]}
        for r in sibling_rules
    ]

    compiler = get_compiler()
    diagnoses = await compiler.diagnose_rule_health(
        rule,
        checklist,
        health_data,
        community_context=(community.community_context if community else None),
        sibling_rules=sibling_rule_dicts,
    )

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

    async def _precompute_recompile_ops(proposed_text: str) -> list[dict] | None:
        """Run recompile_with_diff against a draft rule with the proposed text so the
        carousel can show the resulting checklist diff without a per-click LLM call.
        Returns None on failure — the frontend will fall back to the live preview path."""
        if not community or not checklist:
            return None
        try:
            # Build a draft rule with the proposed text. SQLAlchemy ORM objects are
            # mutable; using a detached duplicate avoids touching the persisted rule.
            from copy import copy as _copy
            draft_rule = _copy(rule)
            draft_rule.text = proposed_text
            other_rules_for_recompile = [
                r for r in sibling_rules if r.id != rule_id
            ]
            ops = await compiler.recompile_with_diff(
                rule=draft_rule,
                community=community,
                other_rules=other_rules_for_recompile,
                existing_items=checklist,
            )
            return ops or None
        except Exception:
            logger.exception(f"Failed to precompute recompile for rule {rule_id}")
            return None

    async def _emit_paired(
        l1: Suggestion | None,
        diag: dict,
        rule_id_local: str,
        motivating_clusters: list[str] | None = None,
    ) -> None:
        """If diag asks for rule_text and/or context, emit those as linked suggestions."""
        levels = diag.get("proposed_levels") or []
        level_reasoning = diag.get("level_reasoning", "")

        # Need l1.id for linking; flush so it's assigned.
        if l1 is not None:
            await db.flush()

        if "rule_text" in levels:
            text_change = diag.get("text_change") or {}
            proposed_text = text_change.get("proposed_text")
            if proposed_text:
                # Pre-compute the resulting checklist diff so the carousel preview is
                # instant when the moderator navigates to this slide.
                precomputed_ops = await _precompute_recompile_ops(proposed_text)

                content: dict = {
                    "proposed_text": proposed_text,
                    "reasoning": text_change.get("rationale") or diag.get("reasoning", ""),
                    "description": f"[paired] {level_reasoning or 'rule text clarification'}",
                    "level_reasoning": level_reasoning,
                    "source": "health_analysis",
                    "action": diag.get("action", ""),
                }
                if precomputed_ops is not None:
                    content["precomputed_recompile_ops"] = precomputed_ops
                if l1 is not None:
                    content["linked_suggestion_id"] = l1.id
                    content["supersedes_logic_suggestion_id"] = l1.id
                if motivating_clusters:
                    content["motivating_clusters"] = motivating_clusters
                rt = Suggestion(
                    rule_id=rule_id_local,
                    suggestion_type="rule_text",
                    content=content,
                )
                db.add(rt)
                created.append(rt)
                if l1 is not None:
                    await db.flush()
                    l1_content = dict(l1.content or {})
                    l1_content["linked_suggestion_id"] = rt.id
                    l1.content = l1_content
                    flag_modified(l1, "content")

        if "context" in levels:
            ctx_change = diag.get("context_change") or {}
            proposed_note = ctx_change.get("proposed_note") or {}
            if proposed_note.get("text"):
                affects = find_related_rules_for_context_note(
                    proposed_note_text=proposed_note.get("text", ""),
                    proposed_note_tag=proposed_note.get("tag"),
                    source_rule_id=rule_id_local,
                    sibling_rules=sibling_rules,
                )
                ctx_content: dict = {
                    "proposed_note": proposed_note,
                    "l2_trigger": ctx_change.get("l2_trigger"),
                    "reasoning": ctx_change.get("rationale") or diag.get("reasoning", ""),
                    "description": f"[context] {level_reasoning or 'community calibration'}",
                    "affects_rules": affects,
                    "level_reasoning": level_reasoning,
                    "source": "health_analysis",
                    "action": diag.get("action", ""),
                }
                if l1 is not None:
                    ctx_content["linked_suggestion_id"] = l1.id
                ctx = Suggestion(
                    rule_id=rule_id_local,
                    suggestion_type="context",
                    content=ctx_content,
                )
                db.add(ctx)
                created.append(ctx)

    for diag in diagnoses.get("diagnoses", []):
        item_id = diag.get("item_id")
        if not item_id or item_id not in items_by_id:
            continue

        action = diag.get("action", "tighten_rubric")
        reasoning = diag.get("reasoning", "")
        proposed_change = diag.get("proposed_change") or {}
        confidence = diag.get("confidence", "medium")
        levels = diag.get("proposed_levels") or ["logic"]  # backward-compat default

        # Build the L1 logic suggestion when "logic" is in proposed_levels.
        l1_suggestion: Suggestion | None = None
        if "logic" in levels:
            update_op: dict = {"op": "update", "existing_id": item_id}
            update_op.update({k: v for k, v in proposed_change.items() if k != "id"})

            # Only include "children" for actions that restructure child items (split_item).
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

            l1_suggestion = Suggestion(
                rule_id=rule_id,
                checklist_item_id=item_id,
                suggestion_type="checklist",
                content={
                    "operations": operations,
                    "action": action,
                    "reasoning": reasoning,
                    "confidence": confidence,
                    "description": f"[{action}] {reasoning[:100]}",
                    "level_reasoning": diag.get("level_reasoning", ""),
                    "source": "health_analysis",
                },
            )
            db.add(l1_suggestion)
            created.append(l1_suggestion)

        await _emit_paired(l1_suggestion, diag, rule_id)

    # Any new_items not consumed by a split_item diagnosis (+ unlinked ones)
    for new_item_diag in standalone_new_items + list(new_items_by_split.values()):
        action = new_item_diag.get("action", "add_item")
        reasoning = new_item_diag.get("reasoning", "")
        proposed_item = new_item_diag.get("proposed_item") or {}
        motivated_by = new_item_diag.get("motivated_by", [])
        # Default: rule_text-only for add_item (text gap). Backward-compat: ["logic"] if absent.
        levels = new_item_diag.get("proposed_levels") or ["rule_text"]

        l1_suggestion: Suggestion | None = None
        if "logic" in levels:
            op = {"op": "add", **proposed_item}
            if "children" not in op:
                op["children"] = []

            l1_suggestion = Suggestion(
                rule_id=rule_id,
                checklist_item_id=None,
                suggestion_type="checklist",
                content={
                    "operations": [op],
                    "action": action,
                    "reasoning": reasoning,
                    "description": f"[add_item] {reasoning[:100]}",
                    "motivated_by": motivated_by,
                    "level_reasoning": new_item_diag.get("level_reasoning", ""),
                    "source": "health_analysis",
                },
            )
            db.add(l1_suggestion)
            created.append(l1_suggestion)

        await _emit_paired(l1_suggestion, new_item_diag, rule_id, motivating_clusters=motivated_by)

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
                community_name=community_name
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

    # Re-evaluate ALL resolved decisions where this rule was evaluated, not just
    # was_override=True. After a logic change, a previously-agreeing decision can
    # become wrong (or vice versa) — skipping non-overrides leaves stale
    # item_reasoning that the FP/FN predicates then misread.
    decisions_result = await db.execute(
        select(Decision).where(
            Decision.community_id == rule.community_id,
            Decision.moderator_verdict != "pending",
        )
    )
    decisions = [
        d for d in decisions_result.scalars().all()
        if rule_id in (d.agent_reasoning or {})
    ]

    if not decisions:
        return {"reevaluated": 0}

    from ..core.actions import resolve_verdict

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
                community_name=community_name
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

            # Recompute top-level Decision fields so they don't drift from the
            # per-rule reasoning (mirrors _reevaluate_pending_queue).
            rule_results = [
                {"verdict": v.get("verdict", "approve"), "confidence": v.get("confidence", 0.5)}
                for k, v in reasoning.items()
                if k != "__community_norms__"
            ]
            if rule_results:
                agg_verdict, agg_confidence = resolve_verdict(rule_results)
            else:
                agg_verdict, agg_confidence = "approve", 1.0
            norms = reasoning.get("__community_norms__")
            if norms and agg_verdict == "approve":
                agg_verdict = "review"
                agg_confidence = norms.get("confidence", agg_confidence)
            triggered_rules = [
                rid for rid, r in reasoning.items()
                if rid != "__community_norms__" and r.get("verdict") in ("remove", "warn")
            ]

            decision.agent_reasoning = reasoning
            flag_modified(decision, "agent_reasoning")
            decision.agent_verdict = agg_verdict
            decision.agent_confidence = agg_confidence
            decision.triggered_rules = triggered_rules
            flag_modified(decision, "triggered_rules")
            updated += 1
        except Exception as e:
            logger.warning(f"Re-evaluation failed for decision {decision.id}: {e}")

    await db.commit()
    return {"reevaluated": updated}
