"""Decision queue endpoints."""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.database import get_db
from ..db.models import Community, Decision, Example, ExampleRuleLink, Rule
from ..models.schemas import DecisionRead, DecisionResolve, DecisionStats
from .rules import _compile_rule_background

logger = logging.getLogger(__name__)
router = APIRouter(tags=["decisions"])


@router.get("/communities/{community_id}/decisions", response_model=list[DecisionRead])
async def list_decisions(
    community_id: str,
    status: str | None = None,
    rule_id: str | None = None,
    verdict: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> list[DecisionRead]:
    comm_result = await db.execute(
        select(Community).where(Community.id == community_id)
    )
    if not comm_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Community not found")

    query = (
        select(Decision)
        .where(Decision.community_id == community_id)
        .order_by(Decision.agent_confidence.asc())  # Lowest confidence first
        .limit(limit)
        .offset(offset)
    )

    if status == "pending":
        query = query.where(Decision.moderator_verdict == "pending")
    elif status == "resolved":
        query = query.where(Decision.moderator_verdict != "pending")

    if verdict:
        query = query.where(Decision.agent_verdict == verdict)

    # Filter by rule_id (check JSON column)
    if rule_id:
        # SQLite JSON: filter decisions where triggered_rules contains rule_id
        query = query.where(Decision.triggered_rules.contains([rule_id]))

    result = await db.execute(query)
    decisions = result.scalars().all()
    return [DecisionRead.model_validate(d) for d in decisions]


@router.put("/decisions/{decision_id}/resolve", response_model=DecisionRead)
async def resolve_decision(
    decision_id: str,
    body: DecisionResolve,
    db: AsyncSession = Depends(get_db),
) -> DecisionRead:
    valid_verdicts = {"approve", "remove", "review"}
    if body.verdict not in valid_verdicts:
        raise HTTPException(status_code=422, detail=f"verdict must be one of {valid_verdicts}")

    result = await db.execute(select(Decision).where(Decision.id == decision_id))
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")

    if decision.moderator_verdict != "pending":
        raise HTTPException(status_code=400, detail="Decision already resolved")

    decision.moderator_verdict = body.verdict
    decision.moderator_reasoning_category = body.reasoning_category
    decision.moderator_notes = body.notes
    decision.was_override = decision.agent_verdict != body.verdict
    decision.resolved_at = datetime.utcnow()

    await db.flush()

    # Auto-create example from this decision based on the four cases:
    #   agent=approve + mod=approve + no triggered rules → skip (no useful signal)
    #   agent=approve + mod=remove/review               → negative/borderline, link to mod-specified rule_ids
    #   agent=remove  + mod=approve                     → positive, link to agent's triggered_rules
    #   agent=remove  + mod=remove/review               → negative/borderline, link to agent's triggered_rules
    agent_approved = decision.agent_verdict == "approve"
    mod_approved = body.verdict == "approve"
    agent_triggered = decision.triggered_rules or []

    skip_example = agent_approved and mod_approved and not agent_triggered

    if not skip_example:
        if body.verdict == "approve":
            example_label = "positive"
        elif body.verdict == "remove":
            example_label = "negative"
        else:
            example_label = "borderline"

        example = Example(
            content=decision.post_content,
            label=example_label,
            source="moderator_decision",
            moderator_reasoning=body.notes,
        )
        db.add(example)
        await db.flush()

        # Determine which rules to link:
        # - Agent missed the violation (approved but mod disagrees) → use moderator-specified rule_ids
        # - Agent was correct or conservative → use agent's triggered_rules
        if agent_approved and not mod_approved:
            rule_ids_to_link = body.rule_ids or []
        else:
            rule_ids_to_link = agent_triggered

        for rule_id in rule_ids_to_link:
            rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
            if rule_result.scalar_one_or_none():
                link = ExampleRuleLink(
                    example_id=example.id,
                    rule_id=rule_id,
                    relevance_note=f"Auto-created from moderator decision ({body.verdict})",
                )
                db.add(link)

    await db.commit()
    await db.refresh(decision)
    return DecisionRead.model_validate(decision)


@router.get("/communities/{community_id}/decisions/stats", response_model=DecisionStats)
async def get_decision_stats(
    community_id: str, db: AsyncSession = Depends(get_db)
) -> DecisionStats:
    comm_result = await db.execute(
        select(Community).where(Community.id == community_id)
    )
    if not comm_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Community not found")

    # Total decisions
    total_result = await db.execute(
        select(func.count(Decision.id)).where(Decision.community_id == community_id)
    )
    total = total_result.scalar() or 0

    # Pending decisions
    pending_result = await db.execute(
        select(func.count(Decision.id)).where(
            Decision.community_id == community_id,
            Decision.moderator_verdict == "pending",
        )
    )
    pending = pending_result.scalar() or 0

    resolved = total - pending

    # Override count
    override_result = await db.execute(
        select(func.count(Decision.id)).where(
            Decision.community_id == community_id,
            Decision.was_override == True,
        )
    )
    override_count = override_result.scalar() or 0
    override_rate = override_count / resolved if resolved > 0 else 0.0

    # Verdict breakdown (agent)
    all_decisions_result = await db.execute(
        select(Decision).where(Decision.community_id == community_id)
    )
    all_decisions = list(all_decisions_result.scalars().all())

    verdicts_breakdown: dict[str, int] = {"approve": 0, "remove": 0, "review": 0}
    override_categories: dict[str, int] = {}

    for d in all_decisions:
        v = d.agent_verdict
        if v in verdicts_breakdown:
            verdicts_breakdown[v] += 1

        if d.moderator_reasoning_category:
            cat = d.moderator_reasoning_category
            override_categories[cat] = override_categories.get(cat, 0) + 1

    return DecisionStats(
        total_decisions=total,
        pending_decisions=pending,
        resolved_decisions=resolved,
        override_rate=round(override_rate, 3),
        verdicts_breakdown=verdicts_breakdown,
        override_categories=override_categories,
    )
