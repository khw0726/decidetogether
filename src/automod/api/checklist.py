"""Checklist item endpoints."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..config import get_anthropic_client, settings
from ..compiler.compiler import RuleCompiler
from ..core.subjective import SubjectiveEvaluator
from ..core.tree_evaluator import TreeEvaluator
from ..db.database import get_db
from ..db.models import ChecklistItem, Community, Decision, Example, ExampleChecklistItemLink, ExampleRuleLink, Rule, Suggestion
from ..models.schemas import ChecklistItemCreate, ChecklistItemRead, ChecklistItemUpdate, SuggestionRead
from .rules import _apply_diff_operations, _persist_new_items, _re_resolve_checklist_links

logger = logging.getLogger(__name__)
router = APIRouter(tags=["checklist"])

# Debounce state: tracks pending link-violation tasks per rule_id.
# Each accept_recompile bumps the generation counter; the background task
# waits a short period then only proceeds if no newer request arrived.
_link_generation: dict[str, int] = {}
_reeval_generation: dict[str, int] = {}
_pending_reeval_generation: dict[str, int] = {}
_LINK_DEBOUNCE_SECONDS = 5
_REEVAL_DEBOUNCE_SECONDS = 5
_PENDING_REEVAL_DEBOUNCE_SECONDS = 5

# Keep references to detached re-eval tasks so the asyncio loop doesn't GC them
# mid-flight when they're spawned from a fire-and-forget context (e.g. inside
# another background task).
_detached_reeval_tasks: set[asyncio.Task] = set()


async def _link_uncovered_violations(rule_id: str, generation: int) -> None:
    """Background task: find uncovered violations for a rule and link them to checklist items via LLM.

    Debounced — if another accept_recompile fires for the same rule before the
    delay elapses, this invocation exits early and the newer one takes over.
    """
    await asyncio.sleep(_LINK_DEBOUNCE_SECONDS)

    # Another accept came in while we were waiting — let that one handle it
    if _link_generation.get(rule_id, 0) != generation:
        logger.debug(f"Skipping debounced violation linking for rule {rule_id} (superseded)")
        return

    from ..db.database import AsyncSessionLocal, write_session

    # ── Read phase ─────────────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
        rule = rule_result.scalar_one_or_none()
        if not rule:
            return

        items_result = await db.execute(
            select(ChecklistItem)
            .where(ChecklistItem.rule_id == rule_id)
            .order_by(ChecklistItem.order.asc())
        )
        all_items = list(items_result.scalars().all())
        if not all_items:
            return
        items_by_id = {i.id: i for i in all_items}

        example_ids_result = await db.execute(
            select(ExampleRuleLink.example_id).where(ExampleRuleLink.rule_id == rule_id)
        )
        example_ids = [r[0] for r in example_ids_result]
        if not example_ids:
            return

        examples_result = await db.execute(
            select(Example).where(Example.id.in_(example_ids))
        )
        examples_by_id = {e.id: e for e in examples_result.scalars().all()}

        links_result = await db.execute(
            select(ExampleChecklistItemLink)
            .where(ExampleChecklistItemLink.example_id.in_(example_ids))
        )
        linked_example_ids: set[str] = set()
        for link in links_result.scalars():
            if link.checklist_item_id and link.checklist_item_id in items_by_id:
                linked_example_ids.add(link.example_id)

        violations = []
        for eid, example in examples_by_id.items():
            if example.label == "violating" and eid not in linked_example_ids:
                content = example.content or {}
                inner = content.get("content", {})
                violations.append({
                    "example_id": example.id,
                    "label": "violating",
                    "title": (inner.get("title", "") if isinstance(inner, dict) else "") or "(no title)",
                    "content": content,
                })

    if not violations:
        logger.info(f"No uncovered violations to link for rule {rule_id}")
        return

    # ── LLM phase (no session, no lock) ────────────────────────────────
    try:
        compiler = get_compiler()
        proposed_links = await compiler.link_violations_to_items(rule, all_items, violations)
    except Exception as e:
        logger.error(f"link_violations_to_items LLM call failed for rule {rule_id}: {e}")
        return

    valid_proposals = [
        p for p in proposed_links
        if p.get("example_id") in examples_by_id and p.get("checklist_item_id") in items_by_id
    ]
    if not valid_proposals:
        return

    # ── Write phase ────────────────────────────────────────────────────
    async with write_session() as db:
        try:
            created = 0
            for proposed in valid_proposals:
                ex_id = proposed["example_id"]
                item_id = proposed["checklist_item_id"]
                item_desc = proposed.get("checklist_item_description", "") or items_by_id[item_id].description

                existing_result = await db.execute(
                    select(ExampleChecklistItemLink).where(
                        ExampleChecklistItemLink.example_id == ex_id,
                    )
                )
                existing = existing_result.scalar_one_or_none()
                if existing:
                    existing.checklist_item_id = item_id
                    existing.checklist_item_description = item_desc
                else:
                    db.add(ExampleChecklistItemLink(
                        example_id=ex_id,
                        checklist_item_id=item_id,
                        checklist_item_description=item_desc,
                    ))
                created += 1

            await db.commit()
            logger.info(f"Linked {created} uncovered violation(s) to checklist items for rule {rule_id}")
        except Exception as e:
            logger.error(f"Failed to persist violation links for rule {rule_id}: {e}")
            await db.rollback()


def get_compiler() -> RuleCompiler:
    client = get_anthropic_client()
    return RuleCompiler(client, settings)


async def _reevaluate_error_cases(rule_id: str, generation: int) -> None:
    """Background task: re-evaluate every resolved decision where this rule was
    evaluated against the updated checklist.

    Updates Decision.agent_reasoning[rule_id] so health metrics self-correct on
    the next read. Includes agreement decisions (not just overrides) so the
    health panel doesn't compute FP/FN against stale per-item verdicts after a
    rule edit. Debounced like _link_uncovered_violations.
    """
    await asyncio.sleep(_REEVAL_DEBOUNCE_SECONDS)

    if _reeval_generation.get(rule_id, 0) != generation:
        logger.debug(f"Skipping debounced re-evaluation for rule {rule_id} (superseded)")
        return

    from ..db.database import AsyncSessionLocal, write_session

    # ── Read phase ─────────────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
        rule = rule_result.scalar_one_or_none()
        if not rule:
            return

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
            return

        decisions_result = await db.execute(
            select(Decision).where(
                Decision.community_id == rule.community_id,
                Decision.moderator_verdict != "pending",
            )
        )
        # Snapshot what we need: id + post_content + existing reasoning.
        candidate_decisions = [
            {
                "id": d.id,
                "post_content": d.post_content or {},
                "agent_reasoning": dict(d.agent_reasoning or {}),
            }
            for d in decisions_result.scalars().all()
            if rule_id in (d.agent_reasoning or {})
        ]

    if not candidate_decisions:
        logger.info(f"No resolved decisions to re-evaluate for rule {rule_id}")
        return

    # ── LLM phase (no session, no lock) ────────────────────────────────
    client = get_anthropic_client()
    subjective_evaluator = SubjectiveEvaluator(client, settings)
    tree_evaluator = TreeEvaluator(subjective_evaluator)

    updates: list[dict] = []
    for snap in candidate_decisions:
        try:
            new_result = await tree_evaluator.evaluate_rule(
                rule=rule,
                checklist=checklist,
                post=snap["post_content"],
                community_name=community_name
            )
            old_rule = snap["agent_reasoning"].get(rule_id, {})
            updates.append({
                "decision_id": snap["id"],
                "rule_entry": {
                    "rule_title": old_rule.get("rule_title", rule.title),
                    "verdict": new_result["verdict"],
                    "confidence": new_result["confidence"],
                    "item_reasoning": new_result["reasoning"],
                    "triggered_items": new_result["triggered_items"],
                },
            })
        except Exception as e:
            logger.warning(f"Re-evaluation failed for decision {snap['id']}: {e}")

    if not updates:
        return

    # ── Write phase ────────────────────────────────────────────────────
    async with write_session() as db:
        try:
            decision_rows = await db.execute(
                select(Decision).where(Decision.id.in_([u["decision_id"] for u in updates]))
            )
            by_id = {d.id: d for d in decision_rows.scalars().all()}
            updated = 0
            for upd in updates:
                d = by_id.get(upd["decision_id"])
                if not d:
                    continue
                reasoning = dict(d.agent_reasoning or {})
                reasoning[rule_id] = upd["rule_entry"]
                d.agent_reasoning = reasoning
                flag_modified(d, "agent_reasoning")
                updated += 1

            await db.commit()
            logger.info(f"Re-evaluated {updated} resolved decision(s) for rule {rule_id}")
        except Exception as e:
            logger.error(f"Failed to persist re-evaluations for rule {rule_id}: {e}")
            await db.rollback()


async def _reevaluate_pending_queue(rule_id: str, generation: int) -> None:
    """Background task: re-evaluate PENDING queue decisions against the rule's
    updated checklist so the moderation queue reflects the new logic.

    Updates Decision.agent_reasoning[rule_id] and recomputes the top-level
    Decision.agent_verdict / agent_confidence / triggered_rules from the merged
    per-rule reasoning. Debounced — rapid edits collapse into a single pass.
    """
    await asyncio.sleep(_PENDING_REEVAL_DEBOUNCE_SECONDS)

    if _pending_reeval_generation.get(rule_id, 0) != generation:
        logger.debug(f"Skipping debounced pending-queue re-eval for rule {rule_id} (superseded)")
        return

    from ..core.actions import resolve_verdict
    from ..db.database import AsyncSessionLocal, write_session

    # ── Read phase ─────────────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
        rule = rule_result.scalar_one_or_none()
        if not rule:
            return

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
            return

        decisions_result = await db.execute(
            select(Decision).where(
                Decision.community_id == rule.community_id,
                Decision.moderator_verdict == "pending",
            )
        )
        # Evaluate every pending decision against this rule — including ones that
        # don't yet have an entry for it. This is what lets a freshly-added rule
        # populate agent_reasoning across the existing pending queue.
        candidate_decisions = [
            {
                "id": d.id,
                "post_content": d.post_content or {},
                "agent_reasoning": dict(d.agent_reasoning or {}),
            }
            for d in decisions_result.scalars().all()
        ]

    if not candidate_decisions:
        logger.info(f"No pending decisions to re-evaluate for rule {rule_id}")
        return

    # ── LLM phase (no session, no lock) ────────────────────────────────
    client = get_anthropic_client()
    subjective_evaluator = SubjectiveEvaluator(client, settings)
    tree_evaluator = TreeEvaluator(subjective_evaluator)

    updates: list[dict] = []
    for snap in candidate_decisions:
        try:
            new_result = await tree_evaluator.evaluate_rule(
                rule=rule,
                checklist=checklist,
                post=snap["post_content"],
                community_name=community_name
            )

            reasoning = dict(snap["agent_reasoning"])
            old_rule = reasoning.get(rule_id, {})
            reasoning[rule_id] = {
                "rule_title": old_rule.get("rule_title", rule.title),
                "verdict": new_result["verdict"],
                "confidence": new_result["confidence"],
                "item_reasoning": new_result["reasoning"],
                "triggered_items": new_result["triggered_items"],
            }

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

            updates.append({
                "decision_id": snap["id"],
                "agent_reasoning": reasoning,
                "agent_verdict": agg_verdict,
                "agent_confidence": agg_confidence,
                "triggered_rules": triggered_rules,
            })
        except Exception as e:
            logger.warning(f"Pending-queue re-eval failed for decision {snap['id']}: {e}")

    if not updates:
        return

    # ── Write phase ────────────────────────────────────────────────────
    async with write_session() as db:
        try:
            decision_rows = await db.execute(
                select(Decision).where(Decision.id.in_([u["decision_id"] for u in updates]))
            )
            by_id = {d.id: d for d in decision_rows.scalars().all()}
            updated = 0
            for upd in updates:
                d = by_id.get(upd["decision_id"])
                if not d:
                    continue
                d.agent_reasoning = upd["agent_reasoning"]
                flag_modified(d, "agent_reasoning")
                d.agent_verdict = upd["agent_verdict"]
                d.agent_confidence = upd["agent_confidence"]
                d.triggered_rules = upd["triggered_rules"]
                flag_modified(d, "triggered_rules")
                updated += 1

            await db.commit()
            logger.info(f"Re-evaluated {updated} pending decision(s) for rule {rule_id}")
        except Exception as e:
            logger.error(f"Failed to persist pending-queue re-evals for rule {rule_id}: {e}")
            await db.rollback()


def schedule_pending_queue_reeval(rule_id: str) -> int:
    """Bump the per-rule generation counter and return the new value.

    Callers schedule `_reevaluate_pending_queue(rule_id, gen)` with this value;
    the debounce inside the task means only the latest generation runs.
    """
    gen = _pending_reeval_generation.get(rule_id, 0) + 1
    _pending_reeval_generation[rule_id] = gen
    return gen


def spawn_pending_queue_reeval(rule_id: str) -> None:
    """Fire-and-forget variant for callers without a FastAPI BackgroundTasks
    handle (e.g. nested background work). Holds a strong reference to the
    spawned task so the asyncio loop doesn't garbage-collect it mid-flight.
    """
    gen = schedule_pending_queue_reeval(rule_id)
    task = asyncio.create_task(_reevaluate_pending_queue(rule_id, gen))
    _detached_reeval_tasks.add(task)
    task.add_done_callback(_detached_reeval_tasks.discard)


async def _drop_rule_from_pending_queue(community_id: str, rule_id: str) -> None:
    """Strip a deactivated rule's contribution from every pending decision in the
    community and recompute the aggregate verdict / triggered_rules. No LLM calls.
    """
    from ..core.actions import resolve_verdict
    from ..db.database import AsyncSessionLocal, write_session

    async with AsyncSessionLocal() as db:
        decisions_result = await db.execute(
            select(Decision).where(
                Decision.community_id == community_id,
                Decision.moderator_verdict == "pending",
            )
        )
        affected_ids = [
            d.id for d in decisions_result.scalars().all()
            if rule_id in (d.agent_reasoning or {})
        ]

    if not affected_ids:
        return

    async with write_session() as db:
        try:
            rows = await db.execute(
                select(Decision).where(Decision.id.in_(affected_ids))
            )
            updated = 0
            for d in rows.scalars().all():
                reasoning = dict(d.agent_reasoning or {})
                reasoning.pop(rule_id, None)
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
                triggered = [
                    rid for rid, r in reasoning.items()
                    if rid != "__community_norms__" and r.get("verdict") in ("remove", "warn")
                ]
                d.agent_reasoning = reasoning
                flag_modified(d, "agent_reasoning")
                d.agent_verdict = agg_verdict
                d.agent_confidence = agg_confidence
                d.triggered_rules = triggered
                flag_modified(d, "triggered_rules")
                updated += 1
            await db.commit()
            logger.info(f"Dropped rule {rule_id} from {updated} pending decision(s)")
        except Exception as e:
            logger.error(f"Failed to drop rule {rule_id} from pending queue: {e}")
            await db.rollback()


def spawn_drop_rule_from_pending_queue(community_id: str, rule_id: str) -> None:
    task = asyncio.create_task(_drop_rule_from_pending_queue(community_id, rule_id))
    _detached_reeval_tasks.add(task)
    task.add_done_callback(_detached_reeval_tasks.discard)


def _item_to_read(item: ChecklistItem) -> ChecklistItemRead:
    """Convert ORM item to schema using only scalar columns (no relationship access)."""
    return ChecklistItemRead(
        id=item.id,
        rule_id=item.rule_id,
        order=item.order,
        parent_id=item.parent_id,
        description=item.description,
        rule_text_anchor=item.rule_text_anchor,
        item_type=item.item_type,
        logic=item.logic,
        action=item.action,
        context_influenced=item.context_influenced,
        context_note=item.context_note,
        context_change_types=item.context_change_types,
        base_description=item.base_description,
        context_pinned=item.context_pinned,
        context_override_note=item.context_override_note,
        pinned_tags=item.pinned_tags,
        user_edited_logic=item.user_edited_logic,
        updated_at=item.updated_at,
        children=[],
    )


def _build_tree(items: list[ChecklistItem]) -> list[ChecklistItemRead]:
    """Build hierarchical tree from flat list of checklist items."""
    id_map: dict[str, ChecklistItemRead] = {item.id: _item_to_read(item) for item in items}

    roots = []
    for item in sorted(items, key=lambda x: x.order):
        node = id_map[item.id]
        if item.parent_id is None:
            roots.append(node)
        else:
            parent = id_map.get(item.parent_id)
            if parent:
                parent.children.append(node)

    return roots


@router.get("/checklist/structural-fields")
async def list_structural_fields() -> list[dict[str, str]]:
    """Return the fixed schema of fields that structural items can check.

    The UI uses this to populate the structural-editor field picker so the
    moderator can only choose fields the evaluator actually understands. The
    list is hardcoded in core/structural.py — adding a new field requires
    extending the field_map there.
    """
    from ..core.structural import STRUCTURAL_FIELDS
    return list(STRUCTURAL_FIELDS)


@router.get("/rules/{rule_id}/checklist", response_model=list[ChecklistItemRead])
async def get_checklist(
    rule_id: str, db: AsyncSession = Depends(get_db)
) -> list[ChecklistItemRead]:
    # Verify rule exists
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    items = list(result.scalars().all())
    return _build_tree(items)


@router.post("/rules/{rule_id}/checklist-items", response_model=ChecklistItemRead, status_code=201)
async def create_checklist_item(
    rule_id: str, body: ChecklistItemCreate, db: AsyncSession = Depends(get_db)
) -> ChecklistItemRead:
    rule_result = await db.execute(
        select(Rule).where(Rule.id == rule_id)
    )
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    community_result = await db.execute(
        select(Community).where(Community.id == rule.community_id)
    )
    community = community_result.scalar_one_or_none()

    # Validate parent belongs to same rule; enforce parent action = continue
    if body.parent_id:
        parent_result = await db.execute(
            select(ChecklistItem).where(
                ChecklistItem.id == body.parent_id,
                ChecklistItem.rule_id == rule_id,
            )
        )
        parent_item = parent_result.scalar_one_or_none()
        if not parent_item:
            raise HTTPException(status_code=400, detail="Parent item not found in this rule")
        if parent_item.action != "continue":
            parent_item.action = "continue"

    # Fetch all existing items for this rule (context for inference)
    existing_result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
    )
    existing_items = list(existing_result.scalars().all())

    # Place at end of siblings
    siblings = [i for i in existing_items if i.parent_id == body.parent_id]
    next_order = max((s.order for s in siblings), default=-1) + 1

    # If the moderator explicitly chose a type in the add form, honor it
    # and only ask the LLM to fill in the logic for that type. Otherwise
    # let it classify too. ChecklistItemCreate's default is "subjective"
    # for back-compat — clients that want full inference should send
    # item_type=None or omit it. Here we treat any explicit value as
    # user-chosen.
    forced_type = body.item_type if body.item_type in ("deterministic", "structural", "subjective") else None
    compiler = get_compiler()
    inferred = await compiler.compile_single_item(
        description=body.description,
        rule=rule,
        community=community,
        existing_items=existing_items,
        force_item_type=forced_type,
    )

    item = ChecklistItem(
        rule_id=rule_id,
        parent_id=body.parent_id,
        order=next_order,
        description=body.description,
        rule_text_anchor=body.rule_text_anchor,
        item_type=inferred["item_type"],
        logic=inferred["logic"],
        action=body.action,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _item_to_read(item)


@router.put("/checklist-items/{item_id}", response_model=ChecklistItemRead)
async def update_checklist_item(
    item_id: str,
    body: ChecklistItemUpdate,
    db: AsyncSession = Depends(get_db),
) -> ChecklistItemRead:
    result = await db.execute(select(ChecklistItem).where(ChecklistItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")

    # Detect the user's intent for the user_edited_logic flag:
    #   - explicit False  → "Regenerate" — clear the pin and re-infer fresh.
    #   - explicit True   → caller wants to pin without sending logic (rare).
    #   - implicit pin    → any item_type/logic in the body flips the flag to True.
    explicit_unpin = body.user_edited_logic is False
    explicit_pin = body.user_edited_logic is True
    implicit_pin = body.item_type is not None or body.logic is not None

    description_changed = body.description is not None and body.description != item.description

    # Re-infer when: regenerate request, OR description changed on a non-pinned
    # item with no explicit logic in the body. Pinned items skip re-inference
    # so a pure description tweak doesn't blow away the moderator's calibration.
    needs_reinfer = explicit_unpin or (
        description_changed
        and body.logic is None
        and not item.user_edited_logic
        and not implicit_pin
    )

    if needs_reinfer:
        rule_result = await db.execute(select(Rule).where(Rule.id == item.rule_id))
        rule = rule_result.scalar_one_or_none()
        community = None
        if rule:
            comm_result = await db.execute(
                select(Community).where(Community.id == rule.community_id)
            )
            community = comm_result.scalar_one_or_none()
        existing_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.rule_id == item.rule_id)
        )
        existing_items = list(existing_result.scalars().all())

        compiler = get_compiler()
        # Re-inference uses the new description if provided, else the current one.
        inferred = await compiler.compile_single_item(
            description=body.description if body.description is not None else item.description,
            rule=rule,
            community=community,
            existing_items=existing_items,
        )
        if body.description is not None:
            item.description = body.description
        if body.item_type is None:
            item.item_type = inferred["item_type"]
        item.logic = inferred["logic"]
        item.user_edited_logic = False  # regeneration always unpins
    else:
        if body.description is not None:
            item.description = body.description
        if body.logic is not None:
            item.logic = body.logic
        if implicit_pin or explicit_pin:
            item.user_edited_logic = True

    if body.rule_text_anchor is not None:
        item.rule_text_anchor = body.rule_text_anchor
    if body.item_type is not None:
        item.item_type = body.item_type
    if body.action is not None:
        # Non-leaf nodes must always use "continue"
        has_children_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.parent_id == item_id).limit(1)
        )
        is_non_leaf = has_children_result.scalar_one_or_none() is not None
        item.action = "continue" if is_non_leaf else body.action
    if body.order is not None:
        item.order = body.order

    await db.commit()
    await db.refresh(item)
    return _item_to_read(item)


class PinnedTagEntry(BaseModel):
    dimension: str
    tag: str


class ContextOverrideBody(BaseModel):
    pinned: bool
    override_note: str | None = None
    pinned_tags: list[PinnedTagEntry] | None = None


@router.patch("/checklist-items/{item_id}/context-override", response_model=ChecklistItemRead)
async def set_context_override(
    item_id: str,
    body: ContextOverrideBody,
    db: AsyncSession = Depends(get_db),
) -> ChecklistItemRead:
    """Pin or unpin a checklist item's context calibration.

    When pinning, `pinned_tags` records which (dimension, tag) bundles justify
    the pin. On context regeneration, a pin whose tags all still exist is
    preserved silently; a pin whose tags have been removed is flagged as orphaned
    so the moderator can decide what to do.
    """
    result = await db.execute(select(ChecklistItem).where(ChecklistItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")

    item.context_pinned = body.pinned
    item.context_override_note = body.override_note
    if body.pinned:
        item.pinned_tags = (
            [t.model_dump() for t in body.pinned_tags]
            if body.pinned_tags is not None else None
        )
    else:
        item.pinned_tags = None
    await db.commit()
    await db.refresh(item)
    return _item_to_read(item)


@router.delete("/checklist-items/{item_id}", status_code=204)
async def delete_checklist_item(
    item_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    from sqlalchemy import delete as sa_delete, update as sa_update
    result = await db.execute(select(ChecklistItem).where(ChecklistItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    # Null out checklist item links before deleting (preserve description for re-resolve)
    child_ids_result = await db.execute(
        select(ChecklistItem.id).where(ChecklistItem.parent_id == item_id)
    )
    child_ids = [r[0] for r in child_ids_result]
    ids_to_null = child_ids + [item_id]
    await db.execute(
        sa_update(ExampleChecklistItemLink)
        .where(ExampleChecklistItemLink.checklist_item_id.in_(ids_to_null))
        .values(checklist_item_id=None)
    )
    await db.execute(sa_delete(ChecklistItem).where(ChecklistItem.parent_id == item_id))
    await db.delete(item)
    await db.commit()


@router.post("/rules/{rule_id}/recompile")
async def recompile_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Recompile rule and return diff (stored as suggestions, not applied)."""
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if rule.rule_type != "actionable":
        raise HTTPException(status_code=400, detail="Only actionable rules can be compiled")

    comm_result = await db.execute(
        select(Community).where(Community.id == rule.community_id)
    )
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    # Fetch existing checklist
    items_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    existing_items = list(items_result.scalars().all())

    # Fetch other rules
    other_rules_result = await db.execute(
        select(Rule).where(
            Rule.community_id == rule.community_id,
            Rule.is_active == True,
            Rule.id != rule_id,
        )
    )
    other_rules = list(other_rules_result.scalars().all())

    compiler = get_compiler()

    if not existing_items:
        # No checklist yet (e.g. rule was just re-triaged to actionable) — full compile
        checklist_items = await compiler.compile_rule(
            rule=rule,
            community=community,
            other_rules=other_rules,
        )
        await _persist_new_items(db, checklist_items, rule_id)
        await db.commit()
        return {"suggestion_id": None, "diff": {"mode": "full_compile"}}

    # Existing checklist — diff only, store as suggestion for review
    operations = await compiler.recompile_with_diff(
        rule=rule,
        community=community,
        other_rules=other_rules,
        existing_items=existing_items,
    )

    # If all operations are "keep", nothing changed — skip creating a suggestion
    if all(op.get("op") == "keep" for op in operations):
        return {"suggestion_id": None, "diff": {"operations": operations, "no_changes": True}}

    suggestion = Suggestion(
        rule_id=rule_id,
        suggestion_type="checklist",
        content={"operations": operations},
        status="pending",
    )
    db.add(suggestion)
    await db.commit()
    await db.refresh(suggestion)

    return {
        "suggestion_id": suggestion.id,
        "diff": {"operations": operations},
    }


@router.post("/rules/{rule_id}/recompile/accept")
async def accept_recompile(
    rule_id: str,
    suggestion_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Accept a pending recompile suggestion — apply the new checklist."""
    # Find the pending suggestion
    sug_result = await db.execute(
        select(Suggestion).where(
            Suggestion.id == suggestion_id,
            Suggestion.rule_id == rule_id,
            Suggestion.status == "pending",
        )
    )
    suggestion = sug_result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Pending suggestion not found")

    operations = suggestion.content.get("operations", [])

    existing_result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
    )
    existing_by_id = {item.id: item for item in existing_result.scalars().all()}

    skipped = await _apply_diff_operations(db, operations, existing_by_id, rule_id)
    await db.flush()
    await _re_resolve_checklist_links(db, rule_id)

    suggestion.status = "accepted"
    await db.commit()

    # Re-evaluate uncovered violations against the updated checklist (debounced).
    # Bumping the generation counter ensures that rapid-fire accepts only trigger
    # one LLM call — the last one wins after the debounce delay.
    gen = _link_generation.get(rule_id, 0) + 1
    _link_generation[rule_id] = gen
    background_tasks.add_task(_link_uncovered_violations, rule_id, gen)

    # Re-evaluate override decisions so health metrics self-correct (debounced).
    reeval_gen = _reeval_generation.get(rule_id, 0) + 1
    _reeval_generation[rule_id] = reeval_gen
    background_tasks.add_task(_reevaluate_error_cases, rule_id, reeval_gen)

    # Re-evaluate PENDING queue decisions so the moderation queue reflects the
    # new logic (debounced).
    pending_gen = schedule_pending_queue_reeval(rule_id)
    background_tasks.add_task(_reevaluate_pending_queue, rule_id, pending_gen)

    return {
        "status": "accepted",
        "operations_applied": len(operations) - len(skipped),
        "skipped_ops": skipped,
    }
