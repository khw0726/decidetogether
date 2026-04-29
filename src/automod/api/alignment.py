"""Alignment endpoints: suggestions CRUD, preview-recompile, accept/dismiss."""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..config import get_anthropic_client, settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, Community, Decision, Example, ExampleChecklistItemLink, ExampleRuleLink, Rule, Suggestion
from ..models.schemas import CommunityContextNote, RuleContextTag, SuggestionRead
from ..core.subjective import SubjectiveEvaluator
from ..core.tree_evaluator import TreeEvaluator
from .rules import _compile_rule_background
logger = logging.getLogger(__name__)
router = APIRouter(tags=["alignment"])


class AcceptSuggestionBody(BaseModel):
    label_override: str | None = None
    # For context suggestions: which rules the moderator opted into (in addition
    # to the source rule). If None, only the source rule is updated.
    affected_rule_ids: list[str] | None = None


def get_compiler() -> RuleCompiler:
    client = get_anthropic_client()
    return RuleCompiler(client, settings)


async def _recompile_after_text_accept(rule_id: str) -> None:
    """Background: silently recompile the checklist after rule.text changes."""
    from ..db.database import AsyncSessionLocal
    from .rules import _apply_diff_operations, _re_resolve_checklist_links

    async with AsyncSessionLocal() as db:
        try:
            rule_res = await db.execute(select(Rule).where(Rule.id == rule_id))
            rule = rule_res.scalar_one_or_none()
            if not rule:
                return
            community_res = await db.execute(
                select(Community).where(Community.id == rule.community_id)
            )
            community = community_res.scalar_one_or_none()
            if not community:
                return
            other_rules_res = await db.execute(
                select(Rule).where(Rule.community_id == rule.community_id, Rule.id != rule_id)
            )
            other_rules = list(other_rules_res.scalars().all())

            existing_res = await db.execute(
                select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
            )
            existing = list(existing_res.scalars().all())
            if not existing:
                logger.info(f"No existing checklist for rule {rule_id}; skipping silent recompile")
                return

            compiler = get_compiler()
            ops = await compiler.recompile_with_diff(
                rule=rule, community=community, other_rules=other_rules, existing_items=existing,
            )
            material_ops = [o for o in ops if o.get("op") != "keep"]
            existing_by_id = {it.id: it for it in existing}
            await _apply_diff_operations(db, ops, existing_by_id, rule_id)
            await db.flush()
            await _re_resolve_checklist_links(db, rule_id)
            await db.commit()
            logger.info(f"Silent recompile applied for rule {rule_id} ({len(ops)} ops)")

            if material_ops:
                _spawn_post_recompile_reevals(rule_id)
        except Exception:
            logger.exception(f"Silent recompile failed for rule {rule_id}")
            await db.rollback()


def _spawn_post_recompile_reevals(rule_id: str) -> None:
    """Mirror the fan-out from accept_recompile: re-link orphans, re-eval errors,
    re-eval the moderation queue. Each is debounced via its own generation counter."""
    from .checklist import (
        _link_uncovered_violations,
        _link_generation,
        _reevaluate_error_cases,
        _reeval_generation,
        _reevaluate_pending_queue,
        schedule_pending_queue_reeval,
        _detached_reeval_tasks,
    )

    link_gen = _link_generation.get(rule_id, 0) + 1
    _link_generation[rule_id] = link_gen
    t1 = asyncio.create_task(_link_uncovered_violations(rule_id, link_gen))
    _detached_reeval_tasks.add(t1)
    t1.add_done_callback(_detached_reeval_tasks.discard)

    err_gen = _reeval_generation.get(rule_id, 0) + 1
    _reeval_generation[rule_id] = err_gen
    t2 = asyncio.create_task(_reevaluate_error_cases(rule_id, err_gen))
    _detached_reeval_tasks.add(t2)
    t2.add_done_callback(_detached_reeval_tasks.discard)

    queue_gen = schedule_pending_queue_reeval(rule_id)
    t3 = asyncio.create_task(_reevaluate_pending_queue(rule_id, queue_gen))
    _detached_reeval_tasks.add(t3)
    t3.add_done_callback(_detached_reeval_tasks.discard)


async def _recompile_after_context_accept(rule_id: str) -> None:
    """Background: re-run adjust_for_context after a context note changes."""
    from ..db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            rule_res = await db.execute(select(Rule).where(Rule.id == rule_id))
            rule = rule_res.scalar_one_or_none()
            if not rule:
                return
            community_res = await db.execute(
                select(Community).where(Community.id == rule.community_id)
            )
            community = community_res.scalar_one_or_none()
            if not community or not community.community_context:
                return

            existing_res = await db.execute(
                select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
            )
            existing = list(existing_res.scalars().all())
            if not existing:
                return

            compiler = get_compiler()
            pinned_ids = [it.id for it in existing if getattr(it, "context_pinned", False)] or None
            adjusted_items, summary, ops = await compiler.adjust_for_context(
                rule=rule,
                community=community,
                current_items=existing,
                community_context=community.community_context,
                pinned_item_ids=pinned_ids,
                relevant_context=rule.relevant_context,
                custom_context_notes=rule.custom_context_notes,
            )
            if not ops:
                logger.info(f"No context-driven ops for rule {rule_id}")
                return
            existing_by_id = {it.id: it for it in existing}
            from .rules import _apply_diff_operations, _re_resolve_checklist_links
            await _apply_diff_operations(db, ops, existing_by_id, rule_id)
            if summary:
                rule.context_adjustment_summary = summary
            await db.flush()
            await _re_resolve_checklist_links(db, rule_id)
            await db.commit()
            logger.info(f"Silent context-recompile applied for rule {rule_id} ({len(ops)} ops)")
            _spawn_post_recompile_reevals(rule_id)
        except Exception:
            logger.exception(f"Silent context-recompile failed for rule {rule_id}")
            await db.rollback()


@router.get("/rules/{rule_id}/suggestions", response_model=list[SuggestionRead])
async def list_suggestions(
    rule_id: str,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[SuggestionRead]:
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    if not rule_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Rule not found")

    query = (
        select(Suggestion)
        .where(Suggestion.rule_id == rule_id)
        .order_by(Suggestion.created_at.desc())
    )
    if status:
        query = query.where(Suggestion.status == status)

    result = await db.execute(query)
    suggestions = result.scalars().all()
    return [SuggestionRead.model_validate(s) for s in suggestions]


@router.post("/suggestions/{suggestion_id}/accept", response_model=SuggestionRead)
async def accept_suggestion(
    suggestion_id: str,
    background_tasks: BackgroundTasks,
    body: AcceptSuggestionBody = Body(default=AcceptSuggestionBody()),
    db: AsyncSession = Depends(get_db),
) -> SuggestionRead:
    result = await db.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail=f"Suggestion is already {suggestion.status}")

    suggestion.status = "accepted"

    # Apply the suggestion if it's a rule_text update
    if suggestion.suggestion_type == "rule_text" and suggestion.rule_id:
        rule_result = await db.execute(select(Rule).where(Rule.id == suggestion.rule_id))
        rule = rule_result.scalar_one_or_none()
        if rule:
            c = suggestion.content
            proposed = (
                c.get("proposed_text")
                or c.get("proposed_change", {}).get("text")
            )
            if proposed:
                rule.text = proposed
                # Mark linked L1 superseded — silent recompile re-derives the logic fix.
                superseded_id = c.get("supersedes_logic_suggestion_id")
                if superseded_id:
                    sup_res = await db.execute(
                        select(Suggestion).where(
                            Suggestion.id == superseded_id,
                            Suggestion.status == "pending",
                        )
                    )
                    sup = sup_res.scalar_one_or_none()
                    if sup:
                        sup.status = "superseded"
                # Silent recompile: re-derive checklist from the new text.
                background_tasks.add_task(
                    _recompile_after_text_accept, str(suggestion.rule_id)
                )

    # Apply the suggestion if it's a context update
    if suggestion.suggestion_type == "context" and suggestion.rule_id:
        c = suggestion.content
        proposed_note = c.get("proposed_note") or {}
        if proposed_note.get("text"):
            target_rule_ids = [suggestion.rule_id]
            if body.affected_rule_ids:
                # Validate that the requested rule_ids are in affects_rules
                allowed = {r.get("rule_id") for r in (c.get("affects_rules") or [])}
                target_rule_ids.extend([
                    rid for rid in body.affected_rule_ids if rid in allowed
                ])

            for tgt_id in target_rule_ids:
                tgt_res = await db.execute(select(Rule).where(Rule.id == tgt_id))
                tgt_rule = tgt_res.scalar_one_or_none()
                if not tgt_rule:
                    continue
                notes = list(tgt_rule.custom_context_notes or [])
                notes.append({
                    "text": proposed_note.get("text", ""),
                    "tag": proposed_note.get("tag", ""),
                })
                tgt_rule.custom_context_notes = notes
                flag_modified(tgt_rule, "custom_context_notes")
                background_tasks.add_task(
                    _recompile_after_context_accept, tgt_id
                )

    # Apply if it's an example suggestion
    if suggestion.suggestion_type == "example" and suggestion.rule_id:
        rule_result = await db.execute(select(Rule).where(Rule.id == suggestion.rule_id))
        rule = rule_result.scalar_one_or_none()
        ex_content = suggestion.content.get("content", {})
        # Use label_override if provided (moderator decision on borderline examples)
        ex_label = body.label_override or suggestion.content.get("label", "compliant")
        relevance = suggestion.content.get("relevance_note", "")
        if ex_content:
            example = Example(
                community_id=rule.community_id if rule else None,
                content=ex_content,
                label=ex_label,
                source="generated",
            )
            db.add(example)
            await db.flush()
            link = ExampleRuleLink(
                example_id=example.id,
                rule_id=suggestion.rule_id,
                relevance_note=relevance,
            )
            db.add(link)
            related_desc = suggestion.content.get("related_checklist_item_description")
            if related_desc and suggestion.rule_id:
                item_result = await db.execute(
                    select(ChecklistItem)
                    .where(ChecklistItem.rule_id == suggestion.rule_id)
                    .where(ChecklistItem.description == related_desc)
                    .limit(1)
                )
                item = item_result.scalar_one_or_none()
                db.add(ExampleChecklistItemLink(
                    example_id=example.id,
                    checklist_item_id=item.id if item else None,
                    checklist_item_description=related_desc,
                ))
    # Create a new rule from synthesized suggestion
    if suggestion.suggestion_type == "new_rule":
        content = suggestion.content
        community_id = content.get("community_id")
        if not community_id:
            raise HTTPException(status_code=400, detail="Suggestion missing community_id")

        comm_result = await db.execute(select(Community).where(Community.id == community_id))
        community = comm_result.scalar_one_or_none()
        if not community:
            raise HTTPException(status_code=404, detail="Community not found")

        # Assign priority after existing rules
        last_result = await db.execute(
            select(Rule)
            .where(Rule.community_id == community_id)
            .order_by(Rule.priority.desc())
            .limit(1)
        )
        last_rule = last_result.scalar_one_or_none()
        next_priority = (last_rule.priority + 1) if last_rule else 0

        new_rule = Rule(
            community_id=community_id,
            title=content["title"],
            text=content["text"],
            priority=next_priority,
        )
        db.add(new_rule)
        await db.flush()

        # Triage the new rule
        compiler = get_compiler()
        triage = await compiler.triage_rule(new_rule.text, community.name, community.platform)
        new_rule.rule_type = triage["rule_type"]
        new_rule.rule_type_reasoning = triage.get("reasoning")

        # Link the orphaned examples to the new rule
        for example_id in content.get("example_ids", []):
            db.add(ExampleRuleLink(
                example_id=example_id,
                rule_id=new_rule.id,
                relevance_note="Auto-linked from rule synthesis",
            ))

        # Enqueue background compilation if actionable
        if new_rule.rule_type == "actionable":
            background_tasks.add_task(_compile_rule_background, str(new_rule.id), community_id)

    await db.commit()
    await db.refresh(suggestion)
    return SuggestionRead.model_validate(suggestion)


class ContextDraft(BaseModel):
    """Draft per-rule context state — sent by the client when previewing/committing.

    Setting a `ContextDraft` on a request means the client is supplying the *intended*
    relevant_context + custom_context_notes for this preview/commit. Absent → no context
    change is intended.
    """
    relevant_context: Optional[list[RuleContextTag]] = None
    custom_context_notes: list[CommunityContextNote] = []


class PreviewRecompileRequest(BaseModel):
    rule_text: str
    context: Optional[ContextDraft] = None


def _same_relevant_context(
    a: Optional[list], b: Optional[list],
) -> bool:
    """Compare relevant_context lists; treat None==None as equal, but None!=[]."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if len(a) != len(b):
        return False
    def key(t):
        if isinstance(t, dict):
            return (t.get("dimension"), t.get("tag"))
        return (getattr(t, "dimension", None), getattr(t, "tag", None))
    return set(map(key, a)) == set(map(key, b))


def _same_custom_notes(a: Optional[list], b: Optional[list]) -> bool:
    a = a or []
    b = b or []
    if len(a) != len(b):
        return False
    def to_dict(n):
        if isinstance(n, dict):
            return {"text": n.get("text", ""), "tag": n.get("tag", "")}
        return {"text": getattr(n, "text", ""), "tag": getattr(n, "tag", "")}
    return [to_dict(n) for n in a] == [to_dict(n) for n in b]


def _items_equal(a: ChecklistItem, b: ChecklistItem) -> bool:
    return (
        a.description == b.description
        and a.rule_text_anchor == b.rule_text_anchor
        and a.item_type == b.item_type
        and (a.logic or {}) == (b.logic or {})
        and a.action == b.action
        and a.parent_id == b.parent_id
        and a.context_influenced == b.context_influenced
        and a.context_note == b.context_note
        and (a.context_change_types or []) == (b.context_change_types or [])
    )


def _compute_ops_diff(
    current: list[ChecklistItem], final: list[ChecklistItem],
) -> list[dict]:
    """Compute keep/update/add/delete ops describing how to transform current → final.

    Items are matched by id. New items get "add" ops; missing items get "delete";
    matched items get "keep" if identical, "update" otherwise.
    """
    current_by_id = {it.id: it for it in current}
    final_by_id = {it.id: it for it in final}
    ops: list[dict] = []
    for fid, fitem in final_by_id.items():
        if fid in current_by_id:
            citem = current_by_id[fid]
            if _items_equal(citem, fitem):
                ops.append({"op": "keep", "existing_id": fid})
            else:
                ops.append({
                    "op": "update",
                    "existing_id": fid,
                    "description": fitem.description,
                    "rule_text_anchor": fitem.rule_text_anchor,
                    "item_type": fitem.item_type,
                    "logic": fitem.logic,
                    "action": fitem.action,
                    "context_influenced": fitem.context_influenced,
                    "context_note": fitem.context_note,
                    "context_change_types": fitem.context_change_types,
                })
        else:
            ops.append({
                "op": "add",
                "description": fitem.description,
                "rule_text_anchor": fitem.rule_text_anchor,
                "item_type": fitem.item_type,
                "logic": fitem.logic,
                "action": fitem.action,
                "context_influenced": fitem.context_influenced,
                "context_note": fitem.context_note,
                "context_change_types": fitem.context_change_types,
            })
    for cid in current_by_id:
        if cid not in final_by_id:
            ops.append({"op": "delete", "existing_id": cid})
    return ops


# In-process LRU cache for preview_recompile responses. Keyed on (rule_id, rule_text,
# context payload, rule.updated_at). Repeat visits to the same carousel slide hit
# instantly; the TTL ensures we don't serve stale results across longer sessions.
_PREVIEW_CACHE_TTL_SECONDS = 600  # 10 minutes
_PREVIEW_CACHE_MAX_ENTRIES = 64
_preview_cache: dict[str, tuple[float, dict]] = {}


def _make_preview_cache_key(
    rule_id: str,
    rule_version_token: str,
    rule_text: str,
    context_payload: Any,
) -> str:
    payload = json.dumps(
        {
            "rule_id": rule_id,
            "ver": rule_version_token,
            "text": rule_text,
            "ctx": context_payload,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _preview_cache_get(key: str) -> dict | None:
    entry = _preview_cache.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.monotonic() - ts > _PREVIEW_CACHE_TTL_SECONDS:
        _preview_cache.pop(key, None)
        return None
    return value


def _preview_cache_put(key: str, value: dict) -> None:
    if len(_preview_cache) >= _PREVIEW_CACHE_MAX_ENTRIES:
        # Evict the oldest entry (linear scan; cache is small).
        oldest = min(_preview_cache.items(), key=lambda kv: kv[1][0])[0]
        _preview_cache.pop(oldest, None)
    _preview_cache[key] = (time.monotonic(), value)


@router.post("/rules/{rule_id}/preview-recompile")
async def preview_recompile(
    rule_id: str,
    body: PreviewRecompileRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Preview how draft rule-text and/or context changes would affect the checklist.

    Body fields:
    - rule_text: required (send the rule's current text if no text edit is intended)
    - context: optional ContextDraft with relevant_context + custom_context_notes
      to apply. Absent → no context change is intended.

    Does NOT save anything. Returns:
    - operations: a single diff list (keep/update/add/delete) describing the full
      transformation from the current checklist to the proposed one.
    - adjustment_summary: short purpose sentence when context calibration ran.
    - example_verdicts: per-example, whether the new checklist would change the verdict.
    """
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    # Cache lookup: same rule version + same draft inputs → return cached response.
    # `updated_at` is the version token; if the rule changes underneath, the key shifts.
    rule_version = str(getattr(rule, "updated_at", "") or rule.id)
    cache_key = _make_preview_cache_key(
        rule_id=rule_id,
        rule_version_token=rule_version,
        rule_text=body.rule_text,
        context_payload=(body.context.model_dump() if body.context is not None else None),
    )
    cached = _preview_cache_get(cache_key)
    if cached is not None:
        return cached

    comm_result = await db.execute(select(Community).where(Community.id == rule.community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    other_rules_result = await db.execute(
        select(Rule).where(
            Rule.community_id == rule.community_id,
            Rule.is_active == True,
            Rule.id != rule_id,
        )
    )
    other_rules = list(other_rules_result.scalars().all())

    # Fetch ALL existing checklist items so we can apply text ops, then context ops.
    all_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    all_existing = list(all_result.scalars().all())
    root_items = [it for it in all_existing if it.parent_id is None]

    # Build a draft Rule object with the preview text (not persisted)
    draft_rule = Rule(
        id=rule.id,
        community_id=rule.community_id,
        title=rule.title,
        text=body.rule_text,
        priority=rule.priority,
        rule_type=rule.rule_type,
    )

    text_changed = body.rule_text != rule.text
    context_changed = False
    draft_relevant: Optional[list[dict]] = None
    draft_notes: list[dict] = []
    if body.context is not None:
        draft_relevant = (
            [t.model_dump() for t in body.context.relevant_context]
            if body.context.relevant_context is not None else None
        )
        draft_notes = [n.model_dump() for n in body.context.custom_context_notes]
        context_changed = (
            not _same_relevant_context(draft_relevant, rule.relevant_context)
            or not _same_custom_notes(draft_notes, rule.custom_context_notes)
        )

    compiler = get_compiler()
    final_items: list[ChecklistItem] = list(all_existing)
    adjustment_summary: str = ""

    if text_changed:
        text_ops = await compiler.recompile_with_diff(
            rule=draft_rule,
            community=community,
            other_rules=other_rules,
            existing_items=root_items,
        )
        final_items = _apply_diff_to_checklist(all_existing, text_ops, rule_id)

    if context_changed and community.community_context:
        pinned_ids = [it.id for it in final_items if it.context_pinned] or None
        adjusted_items, summary, _ctx_ops = await compiler.adjust_for_context(
            rule=draft_rule,
            community=community,
            current_items=final_items,
            community_context=community.community_context,
            pinned_item_ids=pinned_ids,
            relevant_context=draft_relevant,
            custom_context_notes=draft_notes,
        )
        final_items = adjusted_items
        adjustment_summary = summary

    operations = _compute_ops_diff(all_existing, final_items)

    # Fetch up to 20 labeled examples for re-evaluation preview
    examples_result = await db.execute(
        select(Example)
        .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
        .where(ExampleRuleLink.rule_id == rule_id)
        .where(Example.label.in_(["compliant", "violating", "borderline"]))
        .order_by(Example.created_at.desc())
        .limit(20)
    )
    examples = list(examples_result.scalars().all())

    existing_by_id = {item.id: item for item in all_existing}
    item_changes: dict[str, str] = {}
    added_descriptions: list[str] = []
    for op in operations:
        kind = op.get("op")
        if kind == "update":
            item_changes[op["existing_id"]] = "update"
        elif kind == "delete":
            item_changes[op["existing_id"]] = "delete"
        elif kind == "add":
            added_descriptions.append(op.get("description", ""))

    # For each example, determine if any of its linked checklist items would change
    example_verdicts = []
    for ex in examples:
        links_result = await db.execute(
            select(ExampleChecklistItemLink)
            .where(ExampleChecklistItemLink.example_id == ex.id)
        )
        linked_item_ids = {
            link.checklist_item_id
            for link in links_result.scalars()
            if link.checklist_item_id
        }
        affected_items = [
            existing_by_id[iid].description
            for iid in linked_item_ids
            if iid in item_changes
        ]
        may_change = bool(affected_items) or bool(added_descriptions)
        example_verdicts.append({
            "example_id": ex.id,
            "label": ex.label,
            "content_title": (ex.content or {}).get("content", {}).get("title", ""),
            "may_change": may_change,
            "affected_checklist_items": affected_items,
        })

    response = {
        "operations": operations,
        "adjustment_summary": adjustment_summary or None,
        "example_verdicts": example_verdicts,
        "summary": {
            "keep": sum(1 for op in operations if op.get("op") == "keep"),
            "update": sum(1 for op in operations if op.get("op") == "update"),
            "delete": sum(1 for op in operations if op.get("op") == "delete"),
            "add": sum(1 for op in operations if op.get("op") == "add"),
            "examples_may_change": sum(1 for ev in example_verdicts if ev["may_change"]),
        },
    }
    _preview_cache_put(cache_key, response)
    return response


def _apply_diff_to_checklist(
    all_existing: list[ChecklistItem],
    operations: list[dict],
    rule_id: str,
) -> list[ChecklistItem]:
    """Apply diff operations to produce a hypothetical in-memory checklist."""
    op_by_existing_id: dict[str, dict] = {
        op["existing_id"]: op
        for op in operations
        if op.get("existing_id")
    }
    deleted_root_ids: set[str] = {
        op["existing_id"]
        for op in operations
        if op.get("op") == "delete" and op.get("existing_id")
    }

    # Find all descendants of deleted root items
    def get_descendants(item_id: str) -> set[str]:
        result: set[str] = set()
        for item in all_existing:
            if item.parent_id == item_id:
                result.add(item.id)
                result |= get_descendants(item.id)
        return result

    excluded_ids: set[str] = set(deleted_root_ids)
    for did in deleted_root_ids:
        excluded_ids |= get_descendants(did)

    hypothetical: list[ChecklistItem] = []
    for item in all_existing:
        if item.id in excluded_ids:
            continue
        op = op_by_existing_id.get(item.id)
        if op and op.get("op") == "update":
            hypothetical.append(ChecklistItem(
                id=item.id,
                rule_id=item.rule_id,
                parent_id=item.parent_id,
                order=item.order,
                description=op.get("description") or item.description,
                rule_text_anchor=op.get("rule_text_anchor", item.rule_text_anchor),
                item_type=op.get("item_type") or item.item_type,
                logic=op.get("logic") or item.logic,
                action=op.get("action") or item.action,
                context_influenced=op.get("context_influenced", item.context_influenced),
                context_note=op.get("context_note", item.context_note),
                context_change_types=op.get("context_change_types", item.context_change_types),
            ))
        else:
            hypothetical.append(item)

    # Append new items from "add" ops
    for i, op in enumerate(operations):
        if op.get("op") == "add":
            hypothetical.append(ChecklistItem(
                id=str(uuid.uuid4()),
                rule_id=rule_id,
                parent_id=None,
                order=1000 + i,
                description=op.get("description", ""),
                rule_text_anchor=op.get("rule_text_anchor"),
                item_type=op.get("item_type", "subjective"),
                logic=op.get("logic") or {},
                action=op.get("action", "warn"),
                context_influenced=op.get("context_influenced", False),
                context_note=op.get("context_note"),
                context_change_types=op.get("context_change_types"),
            ))

    return hypothetical


async def _build_draft_checklist(
    rule_id: str,
    rule_text: str,
    db: AsyncSession,
) -> tuple[Rule, Community, list[ChecklistItem]]:
    """Compile a hypothetical checklist from a draft rule text without persisting anything.

    Shared by `evaluate-examples-with-draft` and `preview-decisions`.
    Returns (draft_rule, community, hypothetical_checklist_items).
    """
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    comm_result = await db.execute(select(Community).where(Community.id == rule.community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    other_rules_result = await db.execute(
        select(Rule).where(
            Rule.community_id == rule.community_id,
            Rule.is_active == True,  # noqa: E712
            Rule.id != rule_id,
        )
    )
    other_rules = list(other_rules_result.scalars().all())

    root_result = await db.execute(
        select(ChecklistItem).where(
            ChecklistItem.rule_id == rule_id,
            ChecklistItem.parent_id == None,  # noqa: E711
        )
    )
    root_items = list(root_result.scalars().all())

    all_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    all_existing = list(all_result.scalars().all())

    draft_rule = Rule(
        id=rule.id,
        community_id=rule.community_id,
        title=rule.title,
        text=rule_text,
        priority=rule.priority,
        rule_type=rule.rule_type,
    )

    compiler = get_compiler()
    operations = await compiler.recompile_with_diff(
        rule=draft_rule,
        community=community,
        other_rules=other_rules,
        existing_items=root_items,
    )

    hypothetical = _apply_diff_to_checklist(all_existing, operations, rule_id)
    return draft_rule, community, hypothetical


@router.post("/rules/{rule_id}/evaluate-examples-with-draft")
async def evaluate_examples_with_draft(
    rule_id: str,
    body: PreviewRecompileRequest,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Evaluate linked examples against a hypothetical checklist built from the draft rule text.

    Returns per-example: old label and new verdict, so the UI can highlight verdict flips.
    Does NOT save anything to the database.
    """
    draft_rule, community, hypothetical = await _build_draft_checklist(rule_id, body.rule_text, db)
    if not hypothetical:
        return []

    # Fetch up to 20 labeled examples linked to this rule
    examples_result = await db.execute(
        select(Example)
        .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
        .where(ExampleRuleLink.rule_id == rule_id)
        .where(Example.label.in_(["compliant", "violating", "borderline"]))
        .order_by(Example.created_at.desc())
        .limit(20)
    )
    examples = list(examples_result.scalars().all())
    if not examples:
        return []

    client = get_anthropic_client()
    subjective_evaluator = SubjectiveEvaluator(client, settings)
    tree_evaluator = TreeEvaluator(subjective_evaluator)

    results: list[dict[str, Any]] = []
    for ex in examples:
        try:
            rule_result_data = await tree_evaluator.evaluate_rule(
                rule=draft_rule,
                checklist=hypothetical,
                post=ex.content,
                community_name=community.name,
                examples=[],
            )
            new_verdict = rule_result_data["verdict"]
            new_confidence = rule_result_data["confidence"]
        except Exception as e:
            logger.warning(f"Draft evaluation failed for example {ex.id}: {e}")
            new_verdict = "error"
            new_confidence = 0.0

        results.append({
            "example_id": ex.id,
            "old_label": ex.label,
            "new_verdict": new_verdict,
            "new_confidence": new_confidence,
        })

    return results


class PreviewDecisionsRequest(BaseModel):
    rule_text: str | None = None
    # If provided, skip compiling from rule_text and evaluate against these ops directly.
    checklist_override_operations: list[dict[str, Any]] | None = None
    limit: int = 50
    # If provided, evaluate against exactly this curated set instead of recent-N.
    decision_ids: list[str] | None = None


@router.post("/rules/{rule_id}/preview-decisions")
async def preview_decisions(
    rule_id: str,
    body: PreviewDecisionsRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Re-evaluate recent decisions against a hypothetical checklist.

    Source of the draft checklist (exactly one):
    - `rule_text`: compile from draft rule text (same path as evaluate-examples-with-draft).
    - `checklist_override_operations`: apply these diff ops to the current checklist
      (for Analyze-style previews where fix operations are already shaped).

    Returns per-decision old vs new verdict/confidence so the UI can preview how
    the change would affect past decisions.
    """
    empty = {"results": []}

    if bool(body.rule_text) == bool(body.checklist_override_operations):
        raise HTTPException(
            status_code=400,
            detail="Exactly one of rule_text or checklist_override_operations is required.",
        )

    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if body.rule_text is not None:
        draft_rule, community, hypothetical = await _build_draft_checklist(
            rule_id, body.rule_text, db
        )
    else:
        comm_result = await db.execute(select(Community).where(Community.id == rule.community_id))
        community = comm_result.scalar_one_or_none()
        if not community:
            raise HTTPException(status_code=404, detail="Community not found")
        all_result = await db.execute(
            select(ChecklistItem)
            .where(ChecklistItem.rule_id == rule_id)
            .order_by(ChecklistItem.order.asc())
        )
        all_existing = list(all_result.scalars().all())
        hypothetical = _apply_diff_to_checklist(
            all_existing, body.checklist_override_operations or [], rule_id
        )
        draft_rule = rule

    if not hypothetical:
        return empty

    # Fetch decisions: either a curated subset by id, or recent resolved decisions
    if body.decision_ids:
        decisions_result = await db.execute(
            select(Decision)
            .where(Decision.id.in_(body.decision_ids))
            .where(Decision.community_id == rule.community_id)
        )
        decisions = list(decisions_result.scalars().all())
    else:
        decisions_result = await db.execute(
            select(Decision)
            .where(
                Decision.community_id == rule.community_id,
                Decision.moderator_verdict != "pending",
            )
            .order_by(Decision.resolved_at.desc())
            .limit(body.limit * 4)
        )
        candidates = list(decisions_result.scalars().all())
        decisions = [d for d in candidates if rule_id in (d.agent_reasoning or {})][: body.limit]
    if not decisions:
        return empty

    client = get_anthropic_client()
    subjective_evaluator = SubjectiveEvaluator(client, settings)
    tree_evaluator = TreeEvaluator(subjective_evaluator)

    results: list[dict[str, Any]] = []
    for decision in decisions:
        old_rule_reasoning = (decision.agent_reasoning or {}).get(rule_id, {})
        old_verdict = old_rule_reasoning.get("verdict", "approve")
        old_confidence = old_rule_reasoning.get("confidence", 0.0)
        old_triggered_items = list(old_rule_reasoning.get("triggered_items") or [])

        try:
            new_result = await tree_evaluator.evaluate_rule(
                rule=draft_rule,
                checklist=hypothetical,
                post=decision.post_content or {},
                community_name=community.name,
                examples=[],
            )
            new_verdict = new_result["verdict"]
            new_confidence = new_result["confidence"]
            new_triggered_items = list(new_result.get("triggered_items") or [])
        except Exception as e:
            logger.warning(f"Preview-decisions evaluation failed for {decision.id}: {e}")
            new_verdict = "error"
            new_confidence = 0.0
            new_triggered_items = []

        post = decision.post_content or {}
        inner = post.get("content", {}) if isinstance(post, dict) else {}
        post_title = (inner.get("title") if isinstance(inner, dict) else "") or "(no title)"

        results.append({
            "decision_id": decision.id,
            "post_title": post_title,
            "moderator_verdict": decision.moderator_verdict,
            "old_verdict": old_verdict,
            "old_confidence": round(float(old_confidence), 3),
            "new_verdict": new_verdict,
            "new_confidence": round(float(new_confidence), 3),
            "old_triggered_items": old_triggered_items,
            "new_triggered_items": new_triggered_items,
        })

    return {"results": results}


@router.post("/suggestions/{suggestion_id}/revert", response_model=SuggestionRead)
async def revert_suggestion(
    suggestion_id: str, db: AsyncSession = Depends(get_db)
) -> SuggestionRead:
    """Reset a suggestion to pending and clean up any examples created from acceptance.

    Used when a moderator wants to undo a calibration choice.
    """
    result = await db.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if suggestion.status == "pending":
        return SuggestionRead.model_validate(suggestion)

    # If this was an accepted "example" suggestion, remove the example we created.
    if suggestion.status == "accepted" and suggestion.suggestion_type == "example" and suggestion.rule_id:
        ex_content = (suggestion.content or {}).get("content", {})
        if ex_content:
            link_result = await db.execute(
                select(ExampleRuleLink).where(ExampleRuleLink.rule_id == suggestion.rule_id)
            )
            for link in link_result.scalars().all():
                ex_result = await db.execute(select(Example).where(Example.id == link.example_id))
                example = ex_result.scalar_one_or_none()
                if example and example.source == "generated" and example.content == ex_content:
                    await db.execute(
                        ExampleChecklistItemLink.__table__.delete().where(
                            ExampleChecklistItemLink.example_id == example.id
                        )
                    )
                    await db.execute(
                        ExampleRuleLink.__table__.delete().where(
                            ExampleRuleLink.example_id == example.id
                        )
                    )
                    await db.delete(example)
                    break

    suggestion.status = "pending"
    await db.commit()
    await db.refresh(suggestion)
    return SuggestionRead.model_validate(suggestion)


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuggestionRead)
async def dismiss_suggestion(
    suggestion_id: str, db: AsyncSession = Depends(get_db)
) -> SuggestionRead:
    result = await db.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail=f"Suggestion is already {suggestion.status}")

    suggestion.status = "dismissed"
    await db.commit()
    await db.refresh(suggestion)
    return SuggestionRead.model_validate(suggestion)
