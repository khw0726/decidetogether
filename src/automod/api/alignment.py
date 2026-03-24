"""Alignment endpoints: suggest-from-examples, suggest-from-checklist, suggestions CRUD."""

import logging
from typing import Any

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, Community, Example, ExampleRuleLink, Rule, Suggestion
from ..models.schemas import SuggestionRead

logger = logging.getLogger(__name__)
router = APIRouter(tags=["alignment"])


def get_compiler() -> RuleCompiler:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return RuleCompiler(client, settings)


async def _get_rule_checklist_examples(
    rule_id: str, db: AsyncSession
) -> tuple[Rule, list[ChecklistItem], list[Example]]:
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

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
        .order_by(Example.created_at.desc())
    )
    examples = list(examples_result.scalars().all())

    return rule, checklist, examples


@router.post("/rules/{rule_id}/suggest-from-examples", response_model=list[SuggestionRead])
async def suggest_from_examples(
    rule_id: str, db: AsyncSession = Depends(get_db)
) -> list[SuggestionRead]:
    """Generate checklist/rule text suggestions from current examples."""
    rule, checklist, examples = await _get_rule_checklist_examples(rule_id, db)

    if not examples:
        raise HTTPException(status_code=400, detail="No examples found for this rule")

    # Get community name
    comm_result = await db.execute(
        select(Community).where(Community.id == rule.community_id)
    )
    community = comm_result.scalar_one_or_none()

    compiler = get_compiler()
    suggestion_dicts = await compiler.suggest_from_examples(rule, checklist, examples)

    created = []
    for sug in suggestion_dicts:
        suggestion = Suggestion(
            rule_id=rule_id,
            suggestion_type=sug.get("suggestion_type", "checklist"),
            content=sug,
            status="pending",
        )
        db.add(suggestion)
        created.append(suggestion)

    await db.commit()
    for s in created:
        await db.refresh(s)

    return [SuggestionRead.model_validate(s) for s in created]


@router.post("/rules/{rule_id}/suggest-from-checklist", response_model=list[SuggestionRead])
async def suggest_from_checklist(
    rule_id: str, db: AsyncSession = Depends(get_db)
) -> list[SuggestionRead]:
    """Generate example/rule text suggestions from current checklist."""
    rule, checklist, examples = await _get_rule_checklist_examples(rule_id, db)

    if not checklist:
        raise HTTPException(status_code=400, detail="No checklist items found for this rule")

    comm_result = await db.execute(
        select(Community).where(Community.id == rule.community_id)
    )
    community = comm_result.scalar_one_or_none()
    community_name = community.name if community else ""

    compiler = get_compiler()
    suggestion_dicts = await compiler.suggest_from_checklist(
        rule, checklist, examples, community_name
    )

    created = []
    for sug in suggestion_dicts:
        suggestion = Suggestion(
            rule_id=rule_id,
            suggestion_type=sug.get("suggestion_type", "example"),
            content=sug,
            status="pending",
        )
        db.add(suggestion)
        created.append(suggestion)

    await db.commit()
    for s in created:
        await db.refresh(s)

    return [SuggestionRead.model_validate(s) for s in created]


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

    suggestion.status = "accepted"

    # Apply the suggestion if it's a rule_text update
    if suggestion.suggestion_type == "rule_text" and suggestion.rule_id:
        rule_result = await db.execute(select(Rule).where(Rule.id == suggestion.rule_id))
        rule = rule_result.scalar_one_or_none()
        if rule:
            proposed = suggestion.content.get("proposed_text")
            if proposed:
                rule.text = proposed

    # Apply if it's an example suggestion
    if suggestion.suggestion_type == "example" and suggestion.rule_id:
        ex_content = suggestion.content.get("content", {})
        ex_label = suggestion.content.get("label", "positive")
        relevance = suggestion.content.get("relevance_note", "")
        if ex_content:
            example = Example(
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
