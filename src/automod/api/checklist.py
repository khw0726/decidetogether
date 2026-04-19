"""Checklist item endpoints."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_anthropic_client, settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, Community, Example, ExampleChecklistItemLink, ExampleRuleLink, Rule, Suggestion
from ..models.schemas import ChecklistItemCreate, ChecklistItemRead, ChecklistItemUpdate, SuggestionRead
from .rules import _apply_diff_operations, _persist_new_examples, _persist_new_items, _re_resolve_checklist_links

logger = logging.getLogger(__name__)
router = APIRouter(tags=["checklist"])

# Debounce state: tracks pending link-violation tasks per rule_id.
# Each accept_recompile bumps the generation counter; the background task
# waits a short period then only proceeds if no newer request arrived.
_link_generation: dict[str, int] = {}
_LINK_DEBOUNCE_SECONDS = 5


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

    from ..db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
            rule = rule_result.scalar_one_or_none()
            if not rule:
                return

            # Fetch current checklist items
            items_result = await db.execute(
                select(ChecklistItem)
                .where(ChecklistItem.rule_id == rule_id)
                .order_by(ChecklistItem.order.asc())
            )
            all_items = list(items_result.scalars().all())
            if not all_items:
                return
            items_by_id = {i.id: i for i in all_items}

            # Fetch examples linked to this rule
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

            # Find which examples already have a valid checklist item link
            links_result = await db.execute(
                select(ExampleChecklistItemLink)
                .where(ExampleChecklistItemLink.example_id.in_(example_ids))
            )
            linked_example_ids: set[str] = set()
            for link in links_result.scalars():
                if link.checklist_item_id and link.checklist_item_id in items_by_id:
                    linked_example_ids.add(link.example_id)

            # Collect uncovered violations
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

            # Ask LLM to match violations to checklist items
            compiler = get_compiler()
            proposed_links = await compiler.link_violations_to_items(rule, all_items, violations)

            # Create ExampleChecklistItemLink records for valid matches
            created = 0
            for proposed in proposed_links:
                ex_id = proposed.get("example_id")
                item_id = proposed.get("checklist_item_id")
                item_desc = proposed.get("checklist_item_description", "")

                # Validate both IDs exist
                if ex_id not in examples_by_id or item_id not in items_by_id:
                    continue

                # Check if link already exists
                existing_result = await db.execute(
                    select(ExampleChecklistItemLink).where(
                        ExampleChecklistItemLink.example_id == ex_id,
                    )
                )
                existing = existing_result.scalar_one_or_none()
                if existing:
                    # Update dangling link
                    existing.checklist_item_id = item_id
                    existing.checklist_item_description = item_desc or items_by_id[item_id].description
                else:
                    db.add(ExampleChecklistItemLink(
                        example_id=ex_id,
                        checklist_item_id=item_id,
                        checklist_item_description=item_desc or items_by_id[item_id].description,
                    ))
                created += 1

            await db.commit()
            logger.info(f"Linked {created} uncovered violation(s) to checklist items for rule {rule_id}")

        except Exception as e:
            logger.error(f"Failed to link uncovered violations for rule {rule_id}: {e}")
            await db.rollback()


def get_compiler() -> RuleCompiler:
    client = get_anthropic_client()
    return RuleCompiler(client, settings)


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

    # Infer item_type and logic via Claude
    compiler = get_compiler()
    inferred = await compiler.compile_single_item(
        description=body.description,
        rule=rule,
        community=community,
        existing_items=existing_items,
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

    # When the description changes and no explicit logic is provided,
    # re-infer item_type and logic from the new description so the
    # evaluation behavior actually updates to match the new wording.
    description_changed = body.description is not None and body.description != item.description
    if description_changed and body.logic is None:
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
        inferred = await compiler.compile_single_item(
            description=body.description,
            rule=rule,
            community=community,
            existing_items=existing_items,
        )
        item.description = body.description
        if body.item_type is None:
            item.item_type = inferred["item_type"]
        item.logic = inferred["logic"]
    else:
        if body.description is not None:
            item.description = body.description
        if body.logic is not None:
            item.logic = body.logic

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
        checklist_items, example_dicts = await compiler.compile_rule(
            rule=rule,
            community=community,
            other_rules=other_rules,
        )
        await _persist_new_items(db, checklist_items, rule_id)
        await db.flush()
        items_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
        )
        item_desc_map = {i.description: i.id for i in items_result.scalars()}
        await _persist_new_examples(db, example_dicts, rule_id, item_description_map=item_desc_map)
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

    await _apply_diff_operations(db, operations, existing_by_id, rule_id)
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

    return {"status": "accepted", "operations_applied": len(operations)}
