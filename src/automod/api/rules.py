"""Rule CRUD endpoints with triage + compilation."""

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_anthropic_client, settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, CommunitySamplePost, Community, Decision, Example, ExampleChecklistItemLink, ExampleRuleLink, Rule, Suggestion
from ..models.schemas import (
    ContextPreviewResponse,
    RuleBatchImportRequest,
    RuleBatchImportResponse,
    RuleBatchImportResult,
    RuleCreate,
    RuleRead,
    RulePriorityUpdate,
    RuleTypeOverride,
    RuleUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["rules"])


def get_compiler() -> RuleCompiler:
    client = get_anthropic_client()
    return RuleCompiler(client, settings)


async def _re_resolve_checklist_links(db, rule_id: str) -> None:
    """After recompile, re-link dangling ExampleChecklistItemLink rows back to items by description.

    Links become dangling (checklist_item_id=NULL) when an item is deleted during a diff-recompile.
    We match them against current items by exact description to restore the link.
    """
    items_result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
    )
    desc_to_id = {item.description: item.id for item in items_result.scalars()}
    if not desc_to_id:
        return

    example_ids_result = await db.execute(
        select(ExampleRuleLink.example_id).where(ExampleRuleLink.rule_id == rule_id)
    )
    example_ids = [r[0] for r in example_ids_result]
    if not example_ids:
        return

    dangling_result = await db.execute(
        select(ExampleChecklistItemLink)
        .where(ExampleChecklistItemLink.example_id.in_(example_ids))
        .where(ExampleChecklistItemLink.checklist_item_id == None)  # noqa: E711
        .where(ExampleChecklistItemLink.checklist_item_description != "")
    )
    resolved = 0
    for link in dangling_result.scalars():
        new_id = desc_to_id.get(link.checklist_item_description)
        if new_id:
            link.checklist_item_id = new_id
            resolved += 1
    if resolved:
        logger.info(f"Re-resolved {resolved} checklist item link(s) for rule {rule_id}")


async def _compile_rule_read_and_llm(
    rule_id: str,
    community_id: str,
) -> dict | None:
    """Phase 1: Read DB context and run LLM compilation (parallelizable).

    Returns a dict with compilation results, or None if the rule should be skipped.
    ORM objects remain usable after session close thanks to expire_on_commit=False.
    """
    from ..db.database import AsyncSessionLocal

    # ── Read phase (short-lived session) ────────────────────────────────
    async with AsyncSessionLocal() as db:
        rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
        rule = rule_result.scalar_one_or_none()
        if not rule or rule.rule_type != "actionable":
            return None

        community_result = await db.execute(
            select(Community).where(Community.id == community_id)
        )
        community = community_result.scalar_one_or_none()
        if not community:
            return None

        other_rules_result = await db.execute(
            select(Rule).where(
                Rule.community_id == community_id,
                Rule.is_active == True,
                Rule.id != rule_id,
            )
        )
        other_rules = list(other_rules_result.scalars().all())

        existing_result = await db.execute(
            select(ChecklistItem).where(
                ChecklistItem.rule_id == rule_id,
                ChecklistItem.parent_id == None,  # noqa: E711
            )
        )
        existing_items = list(existing_result.scalars().all())

        community_context = community.community_context

        approved_result = await db.execute(
            select(Decision)
            .where(Decision.community_id == community_id, Decision.moderator_verdict == "approve")
            .order_by(Decision.created_at.desc())
            .limit(5)
        )
        removed_result = await db.execute(
            select(Decision)
            .where(Decision.community_id == community_id, Decision.moderator_verdict == "remove")
            .order_by(Decision.created_at.desc())
            .limit(5)
        )
        sample_result = await db.execute(
            select(CommunitySamplePost)
            .where(CommunitySamplePost.community_id == community_id)
            .order_by(CommunitySamplePost.created_at.desc())
            .limit(20)
        )
        community_posts_sample = [
            {"content": d.post_content, "label": "acceptable"}
            for d in approved_result.scalars().all()
        ] + [
            {"content": d.post_content, "label": "unacceptable"}
            for d in removed_result.scalars().all()
        ] + [
            {"content": p.content, "label": p.label, "note": p.note}
            for p in sample_result.scalars().all()
        ]

    # ── LLM phase (no session held) ────────────────────────────────────
    compiler = get_compiler()

    if not existing_items:
        # Two-pass compilation: base compile then context adjustment
        adjusted_items, example_dicts, base_checklist_dicts, adjustment_summary = \
            await compiler.compile_rule_two_pass(
                rule=rule,
                community=community,
                other_rules=other_rules,
                community_context=community_context,
                community_posts_sample=community_posts_sample or None,
                relevant_context=rule.relevant_context,
                custom_context_notes=rule.custom_context_notes,
            )
        return {
            "mode": "compile",
            "rule_id": rule_id,
            "community_id": community_id,
            "rule": rule,
            "community": community,
            "checklist_items": adjusted_items,
            "example_dicts": example_dicts,
            "base_checklist_json": base_checklist_dicts,
            "context_adjustment_summary": adjustment_summary,
        }
    else:
        operations = await compiler.recompile_with_diff(
            rule=rule,
            community=community,
            other_rules=other_rules,
            existing_items=existing_items,
        )
        return {
            "mode": "recompile",
            "rule_id": rule_id,
            "community_id": community_id,
            "rule": rule,
            "community": community,
            "operations": operations,
            "existing_items": existing_items,
        }


async def _compile_rule_persist(result: dict) -> None:
    """Phase 2: Persist compilation results to DB (must be serialized for SQLite)."""
    from ..db.database import AsyncSessionLocal

    rule_id = result["rule_id"]
    rule = result["rule"]
    community = result["community"]
    compiler = get_compiler()

    async with AsyncSessionLocal() as db:
        try:
            if result["mode"] == "compile":
                await _persist_new_items(db, result["checklist_items"], rule_id)
                await db.flush()
                items_result = await db.execute(
                    select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
                )
                item_desc_map = {i.description: i.id for i in items_result.scalars()}
                await _persist_new_examples(
                    db, result["example_dicts"], rule_id,
                    item_description_map=item_desc_map, community_id=result["community_id"],
                )
                await db.flush()
                # Save two-pass artifacts on the Rule
                if result.get("base_checklist_json") is not None:
                    rule_obj = (await db.execute(
                        select(Rule).where(Rule.id == rule_id)
                    )).scalar_one_or_none()
                    if rule_obj:
                        rule_obj.base_checklist_json = result["base_checklist_json"]
                        rule_obj.context_adjustment_summary = result.get("context_adjustment_summary", "")
                        await db.flush()
                await _fill_missing_examples(db, rule_id, compiler, rule, community)
            else:
                # Re-attach existing items to this session (merge returns new tracked instances)
                existing_by_id = {}
                for item in result["existing_items"]:
                    merged = await db.merge(item)
                    existing_by_id[merged.id] = merged
                await _apply_diff_operations(db, result["operations"], existing_by_id, rule_id)
                await db.flush()
                await _re_resolve_checklist_links(db, rule_id)
                await _fill_missing_examples(db, rule_id, compiler, rule, community)

            await db.commit()
            logger.info(f"Compilation complete for rule {rule_id}")

        except Exception as e:
            logger.error(f"Compilation failed for rule {rule_id}: {e}")
            await db.rollback()


async def _compile_rule_background(
    rule_id: str,
    community_id: str,
) -> None:
    """Background task to compile (or recompile) a single rule."""
    try:
        result = await _compile_rule_read_and_llm(rule_id, community_id)
        if result:
            await _compile_rule_persist(result)
    except Exception as e:
        logger.error(f"Compilation failed for rule {rule_id}: {e}")


def _serialize_adjusted_items(items: list[ChecklistItem]) -> list[dict]:
    """Serialize Pass 2 output (unsaved ORM instances) to JSON-safe flat dicts.

    parent_id references are preserved by ID so the tree can be reconstructed on commit.
    """
    return [
        {
            "id": item.id,
            "order": item.order,
            "parent_id": item.parent_id,
            "description": item.description,
            "rule_text_anchor": item.rule_text_anchor,
            "item_type": item.item_type,
            "logic": item.logic,
            "action": item.action,
            "context_influenced": item.context_influenced,
            "context_note": item.context_note,
            "context_change_types": item.context_change_types,
            "base_description": item.base_description,
            "context_pinned": item.context_pinned,
            "context_override_note": item.context_override_note,
            "pinned_tags": item.pinned_tags,
        }
        for item in items
    ]


def _rehydrate_checklist_items(dicts: list[dict], rule_id: str) -> list[ChecklistItem]:
    """Create fresh ChecklistItem ORM instances from stashed dicts.

    Generates new IDs and remaps parent_id references so the structure is preserved.
    """
    old_to_new: dict[str, str] = {d["id"]: str(uuid.uuid4()) for d in dicts if d.get("id")}
    items: list[ChecklistItem] = []
    for d in dicts:
        parent_old = d.get("parent_id")
        parent_new = old_to_new.get(parent_old) if parent_old else None
        items.append(ChecklistItem(
            id=old_to_new.get(d.get("id")) or str(uuid.uuid4()),
            rule_id=rule_id,
            order=d.get("order", 0),
            parent_id=parent_new,
            description=d.get("description", ""),
            rule_text_anchor=d.get("rule_text_anchor"),
            item_type=d.get("item_type", "subjective"),
            logic=d.get("logic") or {},
            action=d.get("action", "warn"),
            context_influenced=d.get("context_influenced", False),
            context_note=d.get("context_note"),
            context_change_types=d.get("context_change_types"),
            base_description=d.get("base_description"),
            context_pinned=d.get("context_pinned", False),
            context_override_note=d.get("context_override_note"),
            pinned_tags=d.get("pinned_tags"),
        ))
    return items


def _nest_preview_items(flat: list[dict]) -> list[dict]:
    """Convert a flat list of stashed item dicts into a nested tree for frontend rendering.

    Each node gets a `children` key holding nested child dicts.
    """
    nodes: dict[str, dict] = {}
    for d in flat:
        nodes[d["id"]] = {**d, "children": []}
    roots: list[dict] = []
    for d in sorted(flat, key=lambda x: x.get("order", 0)):
        node = nodes[d["id"]]
        parent_id = d.get("parent_id")
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


async def _run_pass2(rule_id: str) -> Optional[tuple[list[dict], list[str]]]:
    """Run Pass 2 (context adjustment) against the rule's current base checklist and context.

    Returns (serialized items, summary) or None if the rule is not eligible.
    Does not touch the DB — callers decide whether to stash or persist.
    """
    from ..db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
        rule = rule_result.scalar_one_or_none()
        if not rule or not rule.base_checklist_json:
            return None
        community_result = await db.execute(
            select(Community).where(Community.id == rule.community_id)
        )
        community = community_result.scalar_one_or_none()
        if not community or not community.community_context:
            return None

        pinned_result = await db.execute(
            select(ChecklistItem).where(
                ChecklistItem.rule_id == rule.id,
                ChecklistItem.context_pinned == True,  # noqa: E712
            )
        )
        pinned_items = [
            {"description": p.description, "context_override_note": p.context_override_note}
            for p in pinned_result.scalars().all()
        ] or None

    compiler = get_compiler()
    adjusted_items, summary = await compiler.adjust_for_context(
        rule=rule,
        community=community,
        base_checklist_dicts=rule.base_checklist_json,
        community_context=community.community_context,
        pinned_items=pinned_items,
        relevant_context=rule.relevant_context,
        custom_context_notes=rule.custom_context_notes,
    )
    # Compiler returns "" for summary when there's no context to apply; normalize to list.
    if isinstance(summary, str):
        summary = [s.strip() for s in summary.split(". ") if s.strip()]
    return _serialize_adjusted_items(adjusted_items), summary


async def _persist_new_items(db, checklist_items: list, rule_id: str) -> None:
    """Insert a fresh set of checklist items.

    Items arrive as a flat list with parent_id already set by the compiler's
    _parse_items_recursive. Just add them all.
    """
    for item in checklist_items:
        db.add(item)
    await db.flush()


async def _persist_new_examples(
    db,
    example_dicts: list,
    rule_id: str,
    item_description_map: dict[str, str] | None = None,
    community_id: str | None = None,
) -> None:
    """Insert generated examples and link them to the rule.

    item_description_map maps checklist item description → item ID, used to
    create ExampleChecklistItemLink records when the compiler provides
    related_checklist_item_description on an example.
    """
    for ex_dict in example_dicts:
        label = ex_dict.get("label", "compliant")
        if label == "borderline":
            # Route borderline examples through the suggestion pipeline so
            # moderators must make an explicit compliant/violating decision.
            db.add(Suggestion(
                rule_id=rule_id,
                suggestion_type="example",
                content={
                    "label": "borderline",
                    "content": ex_dict.get("content", {}),
                    "relevance_note": ex_dict.get("relevance_note", ""),
                    "related_checklist_item_description": ex_dict.get("related_checklist_item_description"),
                },
                status="pending",
            ))
            continue

        example = Example(
            community_id=community_id,
            content=ex_dict.get("content", {}),
            label=label,
            source="generated",
        )
        db.add(example)
        await db.flush()
        db.add(ExampleRuleLink(
            example_id=example.id,
            rule_id=rule_id,
            relevance_note=ex_dict.get("relevance_note", ""),
        ))
        related_desc = ex_dict.get("related_checklist_item_description")
        if related_desc and item_description_map:
            item_id = item_description_map.get(related_desc)
            if item_id:
                db.add(ExampleChecklistItemLink(
                    example_id=example.id,
                    checklist_item_id=item_id,
                    checklist_item_description=related_desc,
                ))


async def _fill_missing_examples(db, rule_id: str, compiler, rule, community) -> None:
    """Generate one violating example for each top-level checklist item that doesn't have one."""
    items_result = await db.execute(
        select(ChecklistItem).where(
            ChecklistItem.rule_id == rule_id,
            ChecklistItem.parent_id == None,  # noqa: E711
        )
    )
    all_items = list(items_result.scalars())
    if not all_items:
        return

    covered_result = await db.execute(
        select(ExampleChecklistItemLink.checklist_item_id)
        .join(Example, Example.id == ExampleChecklistItemLink.example_id)
        .where(
            ExampleChecklistItemLink.checklist_item_id.in_([i.id for i in all_items]),
            Example.label.in_(["violating", "borderline"]),
        )
        .distinct()
    )
    covered_ids = {r[0] for r in covered_result}

    items_needing = [i for i in all_items if i.id not in covered_ids]
    if not items_needing:
        return

    # Limit to 3 items per rule to avoid overwhelming the calibration step.
    # Prioritize: subjective > context-influenced > lower thresholds (more ambiguous).
    if len(items_needing) > 3:
        def _ambiguity_score(item: ChecklistItem) -> tuple:
            type_rank = 0 if item.item_type == "subjective" else 1
            context_rank = 0 if item.context_influenced else 1
            threshold = (item.logic or {}).get("threshold", 0.7) if item.item_type == "subjective" else 1.0
            return (type_rank, context_rank, threshold)

        items_needing.sort(key=_ambiguity_score)
        items_needing = items_needing[:3]

    example_ids_result = await db.execute(
        select(ExampleRuleLink.example_id).where(ExampleRuleLink.rule_id == rule_id)
    )
    example_ids = [r[0] for r in example_ids_result]
    existing_examples = []
    if example_ids:
        examples_result = await db.execute(
            select(Example).where(Example.id.in_(example_ids))
        )
        existing_examples = list(examples_result.scalars())

    new_examples = await compiler.generate_examples_for_items(
        rule=rule,
        community=community,
        items=items_needing,
        existing_examples=existing_examples or None,
    )

    item_desc_map = {i.description: i.id for i in all_items}
    await _persist_new_examples(db, new_examples, rule_id, item_description_map=item_desc_map, community_id=rule.community_id)
    logger.info(f"Filled {len(new_examples)} missing example(s) for rule {rule_id}")


async def _apply_diff_operations(
    db,
    operations: list[dict],
    existing_by_id: dict,
    rule_id: str,
) -> None:
    """Apply keep/update/add/delete operations from recompile_with_diff()."""
    for op in operations:
        kind = op.get("op")

        if kind == "keep":
            # Nothing to do — row stays as-is
            pass

        elif kind == "update":
            item = existing_by_id.get(op.get("existing_id"))
            if item is None:
                logger.warning(f"recompile update: unknown id {op.get('existing_id')!r}")
                continue
            if "description" in op:
                item.description = op["description"]
            if "rule_text_anchor" in op:
                item.rule_text_anchor = op["rule_text_anchor"]
            if "item_type" in op:
                item.item_type = op["item_type"]
            if "logic" in op:
                item.logic = op["logic"]
            if "action" in op:
                item.action = op["action"]
            # Replace children: null out links, delete old child rows, insert new ones
            if "children" in op:
                old_child_ids_result = await db.execute(
                    select(ChecklistItem.id).where(ChecklistItem.parent_id == item.id)
                )
                old_child_ids = [r[0] for r in old_child_ids_result]
                if old_child_ids:
                    await db.execute(
                        sa_update(ExampleChecklistItemLink)
                        .where(ExampleChecklistItemLink.checklist_item_id.in_(old_child_ids))
                        .values(checklist_item_id=None)
                    )
                await db.execute(
                    sa_delete(ChecklistItem).where(ChecklistItem.parent_id == item.id)
                )
                await db.flush()
                for i, child_data in enumerate(op["children"]):
                    db.add(ChecklistItem(
                        rule_id=rule_id,
                        order=i,
                        parent_id=item.id,
                        description=child_data.get("description", ""),
                        rule_text_anchor=child_data.get("rule_text_anchor"),
                        item_type=child_data.get("item_type", "subjective"),
                        logic=child_data.get("logic", {}),
                        action=child_data.get("action", "warn"),
                        context_influenced=child_data.get("context_influenced", False),
                        context_note=child_data.get("context_note"),
                        context_change_types=child_data.get("context_change_types"),
                    ))

        elif kind == "delete":
            item = existing_by_id.get(op.get("existing_id"))
            if item is None:
                logger.warning(f"recompile delete: unknown id {op.get('existing_id')!r}")
                continue
            # Collect child IDs, null out all links before deletion to preserve description
            child_ids_result = await db.execute(
                select(ChecklistItem.id).where(ChecklistItem.parent_id == item.id)
            )
            child_ids = [r[0] for r in child_ids_result]
            ids_to_null = child_ids + [item.id]
            await db.execute(
                sa_update(ExampleChecklistItemLink)
                .where(ExampleChecklistItemLink.checklist_item_id.in_(ids_to_null))
                .values(checklist_item_id=None)
            )
            await db.execute(
                sa_delete(ChecklistItem).where(ChecklistItem.parent_id == item.id)
            )
            await db.delete(item)

        elif kind == "add":
            parent_id = op.get("parent_id")

            # If adding under a parent, ensure parent action is "continue"
            if parent_id:
                parent_item = existing_by_id.get(parent_id)
                if parent_item and parent_item.action != "continue":
                    parent_item.action = "continue"

            # Place after existing siblings
            order = op.get("order", 0)
            if parent_id:
                sibling_result = await db.execute(
                    select(ChecklistItem)
                    .where(ChecklistItem.parent_id == parent_id, ChecklistItem.rule_id == rule_id)
                )
                siblings = list(sibling_result.scalars())
                if siblings:
                    order = max(s.order for s in siblings) + 1

            new_item = ChecklistItem(
                rule_id=rule_id,
                order=order,
                parent_id=parent_id,
                description=op.get("description", ""),
                rule_text_anchor=op.get("rule_text_anchor"),
                item_type=op.get("item_type", "subjective"),
                logic=op.get("logic", {}),
                action=op.get("action", "warn"),
                context_influenced=op.get("context_influenced", False),
                context_note=op.get("context_note"),
                context_change_types=op.get("context_change_types"),
            )
            db.add(new_item)
            await db.flush()
            for i, child_data in enumerate(op.get("children", [])):
                db.add(ChecklistItem(
                    rule_id=rule_id,
                    order=i,
                    parent_id=new_item.id,
                    description=child_data.get("description", ""),
                    rule_text_anchor=child_data.get("rule_text_anchor"),
                    item_type=child_data.get("item_type", "subjective"),
                    logic=child_data.get("logic", {}),
                    action=child_data.get("action", "warn"),
                    context_influenced=child_data.get("context_influenced", False),
                    context_note=child_data.get("context_note"),
                    context_change_types=child_data.get("context_change_types"),
                ))

        else:
            logger.warning(f"recompile: unknown op {kind!r}, skipping")


@router.post("/communities/{community_id}/rules", response_model=RuleRead, status_code=201)
async def create_rule(
    community_id: str,
    body: RuleCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    # Verify community exists
    comm_result = await db.execute(select(Community).where(Community.id == community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    # Create the rule first
    rule = Rule(
        community_id=community_id,
        title=body.title,
        text=body.text,
        priority=body.priority,
        relevant_context=(
            [e.model_dump() for e in body.relevant_context]
            if body.relevant_context is not None else None
        ),
        custom_context_notes=[n.model_dump() for n in body.custom_context_notes],
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    # Triage the rule (blocking — user needs to see the type)
    try:
        compiler = get_compiler()
        triage = await compiler.triage_rule(rule.text, community.name, community.platform)
        rule.rule_type = triage["rule_type"]
        rule.rule_type_reasoning = triage["reasoning"]
        rule.applies_to = triage.get("applies_to", "both")
        await db.commit()
        await db.refresh(rule)
    except Exception as e:
        logger.error(f"Triage failed for rule {rule.id}: {e}")

    # If actionable, compile in background
    if rule.rule_type == "actionable":
        background_tasks.add_task(_compile_rule_background, rule.id, community_id)

    return RuleRead.model_validate(rule)


@router.post("/communities/{community_id}/rules/batch", response_model=RuleBatchImportResponse, status_code=201)
async def batch_import_rules(
    community_id: str,
    body: RuleBatchImportRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RuleBatchImportResponse:
    """Import multiple rules at once. Triages all concurrently, then compiles actionable ones in the background."""
    comm_result = await db.execute(select(Community).where(Community.id == community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    # Create all Rule records first (assign auto-priority if not provided)
    rules: list[Rule] = []
    for i, item in enumerate(body.rules):
        priority = item.priority if item.priority is not None else i
        rule = Rule(
            community_id=community_id,
            title=item.title,
            text=item.text,
            priority=priority,
        )
        db.add(rule)
        rules.append(rule)
    await db.commit()
    for rule in rules:
        await db.refresh(rule)

    # Triage all rules concurrently
    compiler = get_compiler()

    async def _triage(rule: Rule) -> tuple[Rule, str | None]:
        try:
            result = await compiler.triage_rule(rule.text, community.name, community.platform)
            rule.rule_type = result["rule_type"]
            rule.rule_type_reasoning = result["reasoning"]
            rule.applies_to = result.get("applies_to", "both")
            return rule, None
        except Exception as e:
            logger.error(f"Triage failed for rule {rule.id}: {e}")
            return rule, str(e)

    triage_results = await asyncio.gather(*[_triage(r) for r in rules])
    await db.commit()
    for rule in rules:
        await db.refresh(rule)

    # Schedule background compilation for actionable rules (concurrently)
    results: list[RuleBatchImportResult] = []
    actionable_ids: list[int] = []
    for rule, triage_error in triage_results:
        if rule.rule_type == "actionable":
            actionable_ids.append(rule.id)
        results.append(RuleBatchImportResult(rule=RuleRead.model_validate(rule), triage_error=triage_error))

    async def _compile_batch() -> None:
        # Phase 1: Run all LLM compilations in parallel
        llm_results = await asyncio.gather(
            *[_compile_rule_read_and_llm(rid, community_id) for rid in actionable_ids],
            return_exceptions=True,
        )
        # Phase 2: Persist results sequentially (SQLite single-writer)
        for rid, result in zip(actionable_ids, llm_results):
            if isinstance(result, Exception):
                logger.error(f"Compilation LLM phase failed for rule {rid}: {result}")
                continue
            if result is None:
                continue
            await _compile_rule_persist(result)

    if actionable_ids:
        background_tasks.add_task(_compile_batch)
    actionable_count = len(actionable_ids)

    return RuleBatchImportResponse(
        imported=results,
        total=len(results),
        actionable_count=actionable_count,
        skipped_count=len(results) - actionable_count,
    )


_REDDIT_HEADERS = {
    "User-Agent": "automod-agent/2.0 (community moderation tool)",
}


class RedditRuleItem(BaseModel):
    title: str
    text: str


class RedditRulesResponse(BaseModel):
    rules: list[RedditRuleItem]
    subreddit: str


@router.get("/reddit-rules/{subreddit}", response_model=RedditRulesResponse)
async def fetch_reddit_rules(subreddit: str) -> RedditRulesResponse:
    """Fetch rules from a subreddit's rules.json endpoint."""
    # Sanitize subreddit name
    sub = re.sub(r"^r/", "", subreddit.strip(), flags=re.IGNORECASE)
    if not re.match(r"^[A-Za-z0-9_]+$", sub):
        raise HTTPException(status_code=422, detail="Invalid subreddit name")

    url = f"https://www.reddit.com/r/{sub}/about/rules.json"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(url, headers=_REDDIT_HEADERS)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Reddit returned {e.response.status_code} for r/{sub}",
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach Reddit: {e}")

    data = resp.json()
    raw_rules = data.get("rules", [])

    rules = []
    for r in raw_rules:
        title = r.get("short_name", "").strip()
        text = r.get("description", "").strip()
        if title:
            rules.append(RedditRuleItem(title=title, text=text or title))

    return RedditRulesResponse(rules=rules, subreddit=sub)


@router.get("/communities/{community_id}/rules", response_model=list[RuleRead])
async def list_rules(
    community_id: str,
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[RuleRead]:
    query = select(Rule).where(Rule.community_id == community_id)
    if not include_inactive:
        query = query.where(Rule.is_active == True)
    query = query.order_by(Rule.priority.asc())
    result = await db.execute(query)
    rules = result.scalars().all()
    return [RuleRead.model_validate(r) for r in rules]


@router.put("/rules/{rule_id}", response_model=RuleRead)
async def update_rule(
    rule_id: str,
    body: RuleUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if body.title is not None:
        rule.title = body.title
    if body.text is not None:
        rule.text = body.text
    if body.priority is not None:
        rule.priority = body.priority
    if body.is_active is not None:
        rule.is_active = body.is_active
    if body.applies_to is not None:
        rule.applies_to = body.applies_to

    context_changed = False
    fields_set = body.model_fields_set
    if "relevant_context" in fields_set:
        rule.relevant_context = (
            [e.model_dump() for e in body.relevant_context]
            if body.relevant_context is not None else None
        )
        context_changed = True
    if "custom_context_notes" in fields_set:
        rule.custom_context_notes = (
            [n.model_dump() for n in body.custom_context_notes]
            if body.custom_context_notes is not None else []
        )
        context_changed = True

    # Any text or context change invalidates a pending preview — clear it so the
    # moderator has to regenerate before committing.
    if body.text is not None or context_changed:
        rule.pending_checklist_json = None
        rule.pending_context_adjustment_summary = None
        rule.pending_relevant_context = None
        rule.pending_custom_context_notes = None
        rule.pending_generated_at = None

    # If text changed, re-triage and queue recompile
    if body.text is not None:
        comm_result = await db.execute(
            select(Community).where(Community.id == rule.community_id)
        )
        community = comm_result.scalar_one_or_none()
        if community:
            try:
                compiler = get_compiler()
                triage = await compiler.triage_rule(rule.text, community.name, community.platform)
                rule.rule_type = triage["rule_type"]
                rule.rule_type_reasoning = triage["reasoning"]
                rule.applies_to = triage.get("applies_to", "both")
            except Exception as e:
                logger.error(f"Re-triage failed: {e}")

        if rule.rule_type == "actionable":
            background_tasks.add_task(_compile_rule_background, rule.id, rule.community_id)

    await db.commit()
    await db.refresh(rule)
    return RuleRead.model_validate(rule)


def _current_context_inputs(rule: Rule) -> tuple[dict, list]:
    """Snapshot the rule's current context selection for staleness detection."""
    return (
        {"value": rule.relevant_context},  # None-vs-empty-list distinguishable
        list(rule.custom_context_notes or []),
    )


@router.post("/rules/{rule_id}/context-preview", response_model=ContextPreviewResponse)
async def preview_context_adjustment(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> ContextPreviewResponse:
    """Run Pass 2 synchronously and stash the result on the rule without persisting
    checklist changes. Moderator reviews the stash, then commits or discards it.
    """
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.rule_type != "actionable":
        raise HTTPException(status_code=400, detail="Only actionable rules support context adjustment")
    if not rule.base_checklist_json:
        raise HTTPException(status_code=400, detail="Rule has no base checklist to adjust")

    pass2 = await _run_pass2(rule_id)
    if pass2 is None:
        raise HTTPException(status_code=400, detail="Rule is not eligible for context adjustment")
    preview_flat, summary = pass2

    rel_snap, notes_snap = _current_context_inputs(rule)
    rule.pending_checklist_json = preview_flat
    rule.pending_context_adjustment_summary = summary
    rule.pending_relevant_context = rel_snap
    rule.pending_custom_context_notes = notes_snap
    rule.pending_generated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(rule)

    # Fetch current (live) checklist for side-by-side display.
    current_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    current_items = list(current_result.scalars().all())

    # Import locally to avoid a circular import with checklist.py → rules helpers.
    from .checklist import _build_tree

    return ContextPreviewResponse(
        preview_items=_nest_preview_items(preview_flat),
        summary=summary,
        generated_at=rule.pending_generated_at,
        current_items=_build_tree(current_items),
    )


@router.post("/rules/{rule_id}/context-commit", response_model=RuleRead)
async def commit_context_adjustment(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    """Apply the stashed Pass 2 preview: replace checklist items, move summary, clear stash."""
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if not rule.pending_checklist_json:
        raise HTTPException(status_code=400, detail="No pending preview to commit")

    # Staleness check: current context must match what was used to generate the preview.
    current_rel, current_notes = _current_context_inputs(rule)
    if rule.pending_relevant_context != current_rel or rule.pending_custom_context_notes != current_notes:
        raise HTTPException(
            status_code=409,
            detail="Preview is stale — context selection has changed since it was generated. Regenerate the preview.",
        )

    stashed_items = rule.pending_checklist_json
    stashed_summary = rule.pending_context_adjustment_summary

    try:
        await db.execute(sa_delete(ChecklistItem).where(ChecklistItem.rule_id == rule_id))
        for item in _rehydrate_checklist_items(stashed_items, rule_id):
            db.add(item)
        rule.context_adjustment_summary = stashed_summary
        rule.pending_checklist_json = None
        rule.pending_context_adjustment_summary = None
        rule.pending_relevant_context = None
        rule.pending_custom_context_notes = None
        rule.pending_generated_at = None
        await db.commit()
        await _re_resolve_checklist_links(db, rule_id)
        await db.commit()
    except Exception as e:
        logger.error(f"Commit preview failed for rule {rule_id}: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to commit preview")

    await db.refresh(rule)
    return RuleRead.model_validate(rule)


@router.delete("/rules/{rule_id}/context-preview", response_model=RuleRead)
async def discard_context_preview(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    """Clear any stashed preview without applying it."""
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    rule.pending_checklist_json = None
    rule.pending_context_adjustment_summary = None
    rule.pending_relevant_context = None
    rule.pending_custom_context_notes = None
    rule.pending_generated_at = None
    await db.commit()
    await db.refresh(rule)
    return RuleRead.model_validate(rule)


@router.put("/rules/{rule_id}/priority", response_model=RuleRead)
async def update_rule_priority(
    rule_id: str,
    body: RulePriorityUpdate,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    rule.priority = body.priority
    await db.commit()
    await db.refresh(rule)
    return RuleRead.model_validate(rule)


@router.put("/rules/{rule_id}/rule-type", response_model=RuleRead)
async def override_rule_type(
    rule_id: str,
    body: RuleTypeOverride,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    valid_types = {"actionable", "procedural", "meta", "informational"}
    if body.rule_type not in valid_types:
        raise HTTPException(status_code=422, detail=f"rule_type must be one of {valid_types}")

    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    rule.rule_type = body.rule_type
    if body.reasoning:
        rule.rule_type_reasoning = body.reasoning

    await db.commit()
    await db.refresh(rule)

    # If overridden to actionable and no checklist exists, compile it
    if body.rule_type == "actionable":
        items_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.rule_id == rule_id).limit(1)
        )
        if not items_result.scalar_one_or_none():
            background_tasks.add_task(_compile_rule_background, rule.id, rule.community_id)

    return RuleRead.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def deactivate_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.is_active = False
    await db.commit()
