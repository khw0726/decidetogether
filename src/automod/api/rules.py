"""Rule CRUD endpoints with triage + compilation."""

import asyncio
import logging
from typing import Optional

import anthropic
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, Community, Example, ExampleRuleLink, Rule
from ..models.schemas import (
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
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return RuleCompiler(client, settings)


async def _compile_rule_background(
    rule_id: str,
    community_id: str,
) -> None:
    """Background task to compile (or recompile) a rule.

    First compile: runs compile_rule() and inserts all items fresh.
    Recompile: runs recompile_with_diff() and applies keep/update/add/delete ops
    against the existing checklist rows, preserving as much as possible.
    """
    from ..db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
            rule = rule_result.scalar_one_or_none()
            if not rule or rule.rule_type != "actionable":
                return

            community_result = await db.execute(
                select(Community).where(Community.id == community_id)
            )
            community = community_result.scalar_one_or_none()
            if not community:
                return

            other_rules_result = await db.execute(
                select(Rule).where(
                    Rule.community_id == community_id,
                    Rule.is_active == True,
                    Rule.id != rule_id,
                )
            )
            other_rules = list(other_rules_result.scalars().all())

            # Load existing top-level checklist items (parent_id IS NULL)
            existing_result = await db.execute(
                select(ChecklistItem).where(
                    ChecklistItem.rule_id == rule_id,
                    ChecklistItem.parent_id == None,  # noqa: E711
                )
            )
            existing_items = list(existing_result.scalars().all())

            compiler = get_compiler()

            if not existing_items:
                # ── First compile ────────────────────────────────────────────
                checklist_items, example_dicts = await compiler.compile_rule(
                    rule=rule,
                    community=community,
                    other_rules=other_rules,
                )
                await _persist_new_items(db, checklist_items, rule_id)
                await _persist_new_examples(db, example_dicts, rule_id)
            else:
                # ── Recompile with diff ──────────────────────────────────────
                operations = await compiler.recompile_with_diff(
                    rule=rule,
                    community=community,
                    other_rules=other_rules,
                    existing_items=existing_items,
                )
                existing_by_id = {item.id: item for item in existing_items}
                await _apply_diff_operations(db, operations, existing_by_id, rule_id)

            await db.commit()
            logger.info(f"Compilation complete for rule {rule_id}")

        except Exception as e:
            logger.error(f"Compilation failed for rule {rule_id}: {e}")
            await db.rollback()


async def _persist_new_items(db, checklist_items: list, rule_id: str) -> None:
    """Insert a fresh set of checklist items, handling parent_id linking for children."""
    added_items = []
    for item in checklist_items:
        db.add(item)
        added_items.append(item)

    await db.flush()

    for parent_item in added_items:
        children_data = getattr(parent_item, "_children_data", [])
        for i, child_data in enumerate(children_data):
            child = ChecklistItem(
                rule_id=rule_id,
                order=i,
                parent_id=parent_item.id,
                description=child_data.get("description", ""),
                rule_text_anchor=child_data.get("rule_text_anchor"),
                item_type=child_data.get("item_type", "subjective"),
                logic=child_data.get("logic", {}),
                action=child_data.get("action", "flag"),
            )
            db.add(child)


async def _persist_new_examples(db, example_dicts: list, rule_id: str) -> None:
    """Insert generated examples and link them to the rule."""
    for ex_dict in example_dicts:
        example = Example(
            content=ex_dict.get("content", {}),
            label=ex_dict.get("label", "positive"),
            source="generated",
        )
        db.add(example)
        await db.flush()
        db.add(ExampleRuleLink(
            example_id=example.id,
            rule_id=rule_id,
            relevance_note=ex_dict.get("relevance_note", ""),
        ))


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
            # Replace children: delete old child rows, insert new ones
            if "children" in op:
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
                        action=child_data.get("action", "flag"),
                    ))

        elif kind == "delete":
            item = existing_by_id.get(op.get("existing_id"))
            if item is None:
                logger.warning(f"recompile delete: unknown id {op.get('existing_id')!r}")
                continue
            # Explicitly delete children first (async ORM cascade requires loaded relationships)
            await db.execute(
                sa_delete(ChecklistItem).where(ChecklistItem.parent_id == item.id)
            )
            await db.delete(item)

        elif kind == "add":
            new_item = ChecklistItem(
                rule_id=rule_id,
                order=op.get("order", 0),
                parent_id=None,
                description=op.get("description", ""),
                rule_text_anchor=op.get("rule_text_anchor"),
                item_type=op.get("item_type", "subjective"),
                logic=op.get("logic", {}),
                action=op.get("action", "flag"),
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
                    action=child_data.get("action", "flag"),
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
            return rule, None
        except Exception as e:
            logger.error(f"Triage failed for rule {rule.id}: {e}")
            return rule, str(e)

    triage_results = await asyncio.gather(*[_triage(r) for r in rules])
    await db.commit()
    for rule in rules:
        await db.refresh(rule)

    # Schedule background compilation for actionable rules
    results: list[RuleBatchImportResult] = []
    actionable_count = 0
    for rule, triage_error in triage_results:
        if rule.rule_type == "actionable":
            background_tasks.add_task(_compile_rule_background, rule.id, community_id)
            actionable_count += 1
        results.append(RuleBatchImportResult(rule=RuleRead.model_validate(rule), triage_error=triage_error))

    return RuleBatchImportResponse(
        imported=results,
        total=len(results),
        actionable_count=actionable_count,
        skipped_count=len(results) - actionable_count,
    )


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
            except Exception as e:
                logger.error(f"Re-triage failed: {e}")

        if rule.rule_type == "actionable":
            background_tasks.add_task(_compile_rule_background, rule.id, rule.community_id)

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
