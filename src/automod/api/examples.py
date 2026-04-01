"""Example management endpoints."""

import logging

import anthropic
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, Example, ExampleChecklistItemLink, ExampleRuleLink, Rule, Suggestion
from ..models.schemas import ExampleCreate, ExampleRead, ExampleUpdate

logger = logging.getLogger(__name__)
router = APIRouter(tags=["examples"])


def get_compiler() -> RuleCompiler:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return RuleCompiler(client, settings)


async def _generate_suggestions_from_example(rule_id: str) -> None:
    """Background task to generate suggestions after a new example is added."""
    from ..db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
            rule = rule_result.scalar_one_or_none()
            if not rule:
                return

            checklist_result = await db.execute(
                select(ChecklistItem)
                .where(ChecklistItem.rule_id == rule_id)
                .order_by(ChecklistItem.order.asc())
            )
            checklist = list(checklist_result.scalars().all())

            examples_result = await db.execute(
                select(Example)
                .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
                .where(ExampleRuleLink.rule_id == rule_id)
            )
            examples = list(examples_result.scalars().all())

            if not checklist or not examples:
                return

            compiler = get_compiler()
            suggestions = await compiler.suggest_from_examples(rule, checklist, examples)

            for sug in suggestions:
                suggestion = Suggestion(
                    rule_id=rule_id,
                    suggestion_type=sug.get("suggestion_type", "checklist"),
                    content=sug,
                    status="pending",
                )
                db.add(suggestion)

            await db.commit()
            logger.info(f"Generated {len(suggestions)} suggestions for rule {rule_id}")

        except Exception as e:
            logger.error(f"Suggestion generation failed for rule {rule_id}: {e}")
            await db.rollback()


@router.post("/rules/{rule_id}/examples", response_model=ExampleRead, status_code=201)
async def add_example(
    rule_id: str,
    body: ExampleCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ExampleRead:
    # Verify rule exists
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    valid_labels = {"compliant", "violating", "borderline"}
    if body.label not in valid_labels:
        raise HTTPException(status_code=422, detail=f"label must be one of {valid_labels}")

    # Create example
    example = Example(
        content=body.content,
        label=body.label,
        source=body.source,
        moderator_reasoning=body.moderator_reasoning,
    )
    db.add(example)
    await db.flush()

    # Link to rule
    link = ExampleRuleLink(
        example_id=example.id,
        rule_id=rule_id,
        relevance_note=body.relevance_note,
    )
    db.add(link)
    await db.commit()
    await db.refresh(example)

    # Trigger suggestion generation in background
    background_tasks.add_task(_generate_suggestions_from_example, rule_id)

    return ExampleRead.model_validate(example)


@router.get("/rules/{rule_id}/examples", response_model=list[ExampleRead])
async def list_examples(
    rule_id: str,
    label: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[ExampleRead]:
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    if not rule_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Rule not found")

    query = (
        select(Example)
        .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
        .where(ExampleRuleLink.rule_id == rule_id)
        .order_by(Example.created_at.desc())
    )
    if label:
        query = query.where(Example.label == label)

    result = await db.execute(query)
    examples = list(result.scalars().all())

    # Fetch checklist item links for all examples in one query.
    # checklist_item_id may be NULL for links that survived a recompile delete;
    # checklist_item_description is always populated and used as the stable display value.
    example_ids = [e.id for e in examples]
    item_link_result = await db.execute(
        select(ExampleChecklistItemLink)
        .where(ExampleChecklistItemLink.example_id.in_(example_ids))
    )
    item_by_example: dict[str, tuple[str | None, str | None]] = {}
    for link in item_link_result.scalars():
        item_by_example[link.example_id] = (link.checklist_item_id, link.checklist_item_description or None)

    reads = []
    for e in examples:
        item_id, item_desc = item_by_example.get(e.id, (None, None))
        reads.append(ExampleRead(
            id=e.id,
            content=e.content,
            label=e.label,
            source=e.source,
            moderator_reasoning=e.moderator_reasoning,
            checklist_item_id=item_id,
            checklist_item_description=item_desc,
            created_at=e.created_at,
            updated_at=e.updated_at,
        ))
    return reads


@router.put("/examples/{example_id}", response_model=ExampleRead)
async def update_example(
    example_id: str,
    body: ExampleUpdate,
    db: AsyncSession = Depends(get_db),
) -> ExampleRead:
    result = await db.execute(select(Example).where(Example.id == example_id))
    example = result.scalar_one_or_none()
    if not example:
        raise HTTPException(status_code=404, detail="Example not found")

    if body.content is not None:
        example.content = body.content
    if body.label is not None:
        valid_labels = {"compliant", "violating", "borderline"}
        if body.label not in valid_labels:
            raise HTTPException(status_code=422, detail=f"label must be one of {valid_labels}")
        example.label = body.label
    if body.moderator_reasoning is not None:
        example.moderator_reasoning = body.moderator_reasoning

    await db.commit()
    await db.refresh(example)
    return ExampleRead.model_validate(example)


@router.delete("/examples/{example_id}", status_code=204)
async def delete_example(
    example_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Example).where(Example.id == example_id))
    example = result.scalar_one_or_none()
    if not example:
        raise HTTPException(status_code=404, detail="Example not found")
    await db.delete(example)
    await db.commit()
