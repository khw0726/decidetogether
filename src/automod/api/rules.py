"""Rule CRUD endpoints with triage + compilation."""

import asyncio
import logging
from typing import Optional

import anthropic
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete as sa_delete, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, CommunitySamplePost, Community, Decision, Example, ExampleChecklistItemLink, ExampleRuleLink, Rule, Suggestion
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

            # Fetch community atmosphere and representative posts for compilation context
            community_atmosphere = community.atmosphere

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

            compiler = get_compiler()

            if not existing_items:
                # ── First compile ────────────────────────────────────────────
                checklist_items, example_dicts = await compiler.compile_rule(
                    rule=rule,
                    community=community,
                    other_rules=other_rules,
                    community_atmosphere=community_atmosphere,
                    community_posts_sample=community_posts_sample or None,
                )
                await _persist_new_items(db, checklist_items, rule_id)
                await db.flush()
                items_result = await db.execute(
                    select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
                )
                item_desc_map = {i.description: i.id for i in items_result.scalars()}
                await _persist_new_examples(db, example_dicts, rule_id, item_description_map=item_desc_map)
                await db.flush()
                await _fill_missing_examples(db, rule_id, compiler, rule, community)
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
                await db.flush()
                await _re_resolve_checklist_links(db, rule_id)
                await _fill_missing_examples(db, rule_id, compiler, rule, community)

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
                atmosphere_influenced=child_data.get("atmosphere_influenced", False),
                atmosphere_note=child_data.get("atmosphere_note"),
            )
            db.add(child)


async def _persist_new_examples(
    db,
    example_dicts: list,
    rule_id: str,
    item_description_map: dict[str, str] | None = None,
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
    await _persist_new_examples(db, new_examples, rule_id, item_description_map=item_desc_map)
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
                        action=child_data.get("action", "flag"),
                        atmosphere_influenced=child_data.get("atmosphere_influenced", False),
                        atmosphere_note=child_data.get("atmosphere_note"),
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
            new_item = ChecklistItem(
                rule_id=rule_id,
                order=op.get("order", 0),
                parent_id=None,
                description=op.get("description", ""),
                rule_text_anchor=op.get("rule_text_anchor"),
                item_type=op.get("item_type", "subjective"),
                logic=op.get("logic", {}),
                action=op.get("action", "flag"),
                atmosphere_influenced=op.get("atmosphere_influenced", False),
                atmosphere_note=op.get("atmosphere_note"),
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
                    atmosphere_influenced=child_data.get("atmosphere_influenced", False),
                    atmosphere_note=child_data.get("atmosphere_note"),
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
