"""Checklist item endpoints."""

import logging
from typing import Any

import anthropic
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, Community, Example, ExampleRuleLink, Rule, Suggestion
from ..models.schemas import ChecklistItemRead, ChecklistItemUpdate, SuggestionRead

logger = logging.getLogger(__name__)
router = APIRouter(tags=["checklist"])


def get_compiler() -> RuleCompiler:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
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

    if body.description is not None:
        item.description = body.description
    if body.rule_text_anchor is not None:
        item.rule_text_anchor = body.rule_text_anchor
    if body.item_type is not None:
        item.item_type = body.item_type
    if body.logic is not None:
        item.logic = body.logic
    if body.action is not None:
        item.action = body.action
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
    result = await db.execute(select(ChecklistItem).where(ChecklistItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")
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

    # Fetch existing examples
    examples_result = await db.execute(
        select(Example)
        .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
        .where(ExampleRuleLink.rule_id == rule_id)
    )
    existing_examples = list(examples_result.scalars().all())

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
    diff = await compiler.recompile_rule(
        rule=rule,
        community=community,
        other_rules=other_rules,
        existing_items=existing_items,
        existing_examples=existing_examples,
    )

    # Store diff as a suggestion
    suggestion = Suggestion(
        rule_id=rule_id,
        suggestion_type="checklist",
        content=diff,
        status="pending",
    )
    db.add(suggestion)
    await db.commit()
    await db.refresh(suggestion)

    return {
        "suggestion_id": suggestion.id,
        "diff": diff,
    }


@router.post("/rules/{rule_id}/recompile/accept")
async def accept_recompile(
    rule_id: str,
    suggestion_id: str,
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

    diff = suggestion.content
    new_items_raw = diff.get("new_items_raw", [])

    # Delete existing checklist items
    existing_result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
    )
    for item in existing_result.scalars().all():
        await db.delete(item)
    await db.flush()

    # Create new items
    for i, item_data in enumerate(new_items_raw):
        item = ChecklistItem(
            rule_id=rule_id,
            order=item_data.get("order", i),
            parent_id=None,  # Simplified: no parent linking in accept
            description=item_data.get("description", ""),
            rule_text_anchor=item_data.get("rule_text_anchor"),
            item_type=item_data.get("item_type", "subjective"),
            logic=item_data.get("logic", {}),
            action=item_data.get("action", "flag"),
        )
        db.add(item)

    # Mark suggestion as accepted
    suggestion.status = "accepted"
    await db.commit()

    return {"status": "accepted", "items_created": len(new_items_raw)}
