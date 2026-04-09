"""Decision queue endpoints."""

import logging
from datetime import datetime
from typing import Any

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, Community, CommunitySamplePost, Decision, Example, ExampleChecklistItemLink, ExampleRuleLink, Rule, Suggestion
from ..models.schemas import (
    DecisionRead, DecisionResolve, DecisionStats,
    ExampleRead, SuggestRuleFromDecisionsRequest, SuggestRuleFromOverridesRequest, SuggestionRead,
)
from .rules import _compile_rule_background

logger = logging.getLogger(__name__)
router = APIRouter(tags=["decisions"])


def get_compiler() -> RuleCompiler:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return RuleCompiler(client, settings)


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
    decision.moderator_tag = body.tag
    decision.was_override = decision.agent_verdict != body.verdict
    decision.resolved_at = datetime.utcnow()

    await db.flush()

    # Auto-create example from this decision based on the four cases:
    #   agent=approve + mod=approve + no triggered rules → skip (no useful signal)
    #   agent=approve + mod=remove/review               → violating/borderline, link to mod-specified rule_ids
    #   agent=remove  + mod=approve                     → compliant, link to agent's triggered_rules
    #   agent=remove  + mod=remove/review               → violating/borderline, link to agent's triggered_rules
    agent_approved = decision.agent_verdict == "approve"
    mod_approved = body.verdict == "approve"
    agent_triggered = decision.triggered_rules or []
    agent_reasoning = decision.agent_reasoning or {}

    skip_example = agent_approved and mod_approved and not agent_triggered

    if not skip_example:
        if body.verdict == "approve":
            example_label = "compliant"
        elif body.verdict == "remove":
            example_label = "violating"
        else:
            example_label = "borderline"

        example = Example(
            community_id=decision.community_id,
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
            rule = rule_result.scalar_one_or_none()
            if rule:
                link = ExampleRuleLink(
                    example_id=example.id,
                    rule_id=rule_id,
                    relevance_note=f"Auto-created from moderator decision ({body.verdict})",
                )
                db.add(link)

                # Increment override_count when moderator removes a post linked to this rule
                # but agent approved it (agent missed the violation)
                if not mod_approved and agent_approved:
                    rule.override_count = (rule.override_count or 0) + 1

            # Link to specific checklist items the agent triggered (empty for agent=approve cases)
            rule_reasoning = agent_reasoning.get(rule_id, {})
            for item_id in rule_reasoning.get("triggered_items", []):
                item_result = await db.execute(
                    select(ChecklistItem).where(ChecklistItem.id == item_id)
                )
                item = item_result.scalar_one_or_none()
                if item:
                    db.add(ExampleChecklistItemLink(
                        example_id=example.id,
                        checklist_item_id=item_id,
                        checklist_item_description=item.description,
                    ))

    # When the agent flagged a community norm violation but the moderator approved the post,
    # auto-add the post as an acceptable sample post to community settings.
    agent_cited_norm_violation = "__community_norms__" in agent_reasoning
    if mod_approved and not agent_approved and agent_cited_norm_violation:
        sample_post = CommunitySamplePost(
            community_id=decision.community_id,
            content=decision.post_content,
            label="acceptable",
            note="Auto-added: moderator approved post that agent flagged for community norm violation",
        )
        db.add(sample_post)

    # After a remove-override with no rule linked (unlinked remove), check if we've hit
    # the M=3 threshold and auto-trigger a new-rule suggestion.
    if not mod_approved and agent_approved and not (body.rule_ids or []):
        await _maybe_auto_cluster_unlinked_removes(db, decision.community_id)

    await db.commit()
    await db.refresh(decision)
    return DecisionRead.model_validate(decision)


_UNLINKED_REMOVE_THRESHOLD = 3


async def _maybe_auto_cluster_unlinked_removes(db: AsyncSession, community_id: str) -> None:
    """Auto-cluster unlinked removes into a new rule suggestion when threshold is reached."""
    # Count decisions where agent approved, moderator removed, and no rule was linked
    # (identified by the moderator not providing rule_ids — this function is only called in that case)
    unlinked_count_result = await db.execute(
        select(func.count(Decision.id))
        .where(
            Decision.community_id == community_id,
            Decision.moderator_verdict == "remove",
            Decision.agent_verdict == "approve",
            Decision.was_override == True,
        )
    )
    unlinked_count = unlinked_count_result.scalar() or 0

    if unlinked_count < _UNLINKED_REMOVE_THRESHOLD:
        return

    # Check if we already have a pending auto-generated suggestion for this community
    # to avoid spamming — fetch all pending new_rule suggestions and check in Python
    existing_suggestions_result = await db.execute(
        select(Suggestion)
        .where(
            Suggestion.rule_id == None,  # noqa: E711
            Suggestion.suggestion_type == "new_rule",
            Suggestion.status == "pending",
        )
    )
    for s in existing_suggestions_result.scalars():
        if s.content.get("community_id") == community_id and s.content.get("auto_generated"):
            return  # Already have a pending auto-generated suggestion

    # Fetch recent unlinked remove decisions
    decisions_result = await db.execute(
        select(Decision)
        .where(
            Decision.community_id == community_id,
            Decision.moderator_verdict == "remove",
            Decision.agent_verdict == "approve",
            Decision.was_override == True,
        )
        .order_by(Decision.resolved_at.desc())
        .limit(20)
    )
    decisions = list(decisions_result.scalars().all())
    if not decisions:
        return

    comm_result = await db.execute(select(Community).where(Community.id == community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        return

    compiler = get_compiler()
    example_dicts = [
        {
            "label": "violating",
            "content": d.post_content,
            "moderator_reasoning": (
                f"[{d.moderator_tag}] {d.moderator_notes or ''}".strip()
                if d.moderator_tag
                else d.moderator_notes or ""
            ),
        }
        for d in decisions
    ]
    try:
        synthesis = await compiler.synthesize_rule_from_examples(example_dicts, community)
    except Exception as e:
        logger.error(f"Auto-cluster synthesis failed for community {community_id}: {e}")
        return

    suggestion = Suggestion(
        rule_id=None,
        suggestion_type="new_rule",
        content={
            "title": synthesis["title"],
            "text": synthesis["text"],
            "confidence": synthesis["confidence"],
            "reasoning": synthesis["reasoning"],
            "community_id": community_id,
            "auto_generated": True,
            "unlinked_remove_count": unlinked_count,
        },
        status="pending",
    )
    db.add(suggestion)
    logger.info(
        f"Auto-generated new rule suggestion for community {community_id} "
        f"from {unlinked_count} unlinked removes"
    )


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


@router.get("/communities/{community_id}/unlinked-overrides", response_model=list[ExampleRead])
async def list_unlinked_overrides(
    community_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[ExampleRead]:
    """Return moderator-decision examples that have no rule link (orphaned overrides)."""
    comm_result = await db.execute(select(Community).where(Community.id == community_id))
    if not comm_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Community not found")

    # LEFT JOIN to find violating/borderline examples with no ExampleRuleLink row.
    # Compliant overrides (agent over-triggered, mod approved) are rule-refinement signals,
    # not new-rule candidates — exclude them here.
    query = (
        select(Example)
        .outerjoin(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
        .where(Example.community_id == community_id)
        .where(Example.source == "moderator_decision")
        .where(Example.label.in_(["violating", "borderline"]))
        .where(ExampleRuleLink.example_id == None)  # noqa: E711
        .order_by(Example.created_at.desc())
    )
    result = await db.execute(query)
    examples = list(result.scalars().all())
    return [ExampleRead.model_validate(e) for e in examples]


@router.post("/communities/{community_id}/suggest-rule-from-overrides")
async def suggest_rule_from_overrides(
    community_id: str,
    body: SuggestRuleFromOverridesRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Synthesize a candidate new rule from selected orphaned override examples."""
    comm_result = await db.execute(select(Community).where(Community.id == community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    if not body.example_ids:
        raise HTTPException(status_code=400, detail="At least one example_id is required")

    # Fetch and validate examples
    examples = []
    for example_id in body.example_ids:
        ex_result = await db.execute(select(Example).where(Example.id == example_id))
        ex = ex_result.scalar_one_or_none()
        if not ex:
            raise HTTPException(status_code=404, detail=f"Example {example_id} not found")
        if ex.source != "moderator_decision":
            raise HTTPException(
                status_code=400,
                detail=f"Example {example_id} is not a moderator decision override",
            )
        if ex.label == "compliant":
            raise HTTPException(
                status_code=400,
                detail=f"Example {example_id} is a compliant override (agent over-triggered) — "
                       "these indicate a rule needs to be coarser, not a new rule",
            )
        # Verify it has no rule link
        link_result = await db.execute(
            select(ExampleRuleLink).where(ExampleRuleLink.example_id == example_id)
        )
        if link_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Example {example_id} is already linked to a rule",
            )
        examples.append(ex)

    compiler = get_compiler()
    example_dicts = [
        {"label": e.label, "content": e.content, "moderator_reasoning": e.moderator_reasoning}
        for e in examples
    ]
    synthesis = await compiler.synthesize_rule_from_examples(example_dicts, community)

    suggestion = Suggestion(
        rule_id=None,
        suggestion_type="new_rule",
        content={
            "title": synthesis["title"],
            "text": synthesis["text"],
            "confidence": synthesis["confidence"],
            "reasoning": synthesis["reasoning"],
            "example_ids": body.example_ids,
            "community_id": community_id,
        },
        status="pending",
    )
    db.add(suggestion)
    await db.commit()
    await db.refresh(suggestion)

    response: dict[str, Any] = {"suggestion": SuggestionRead.model_validate(suggestion)}
    if len(body.example_ids) < 3:
        response["warning"] = (
            f"Only {len(body.example_ids)} example(s) selected — "
            "the suggestion may over-fit to a one-off. Consider collecting more overrides first."
        )
    return response


@router.post("/communities/{community_id}/suggest-rule-from-decisions")
async def suggest_rule_from_decisions(
    community_id: str,
    body: SuggestRuleFromDecisionsRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Synthesize a candidate new rule from selected decisions in the queue."""
    comm_result = await db.execute(select(Community).where(Community.id == community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    if not body.decision_ids:
        raise HTTPException(status_code=400, detail="At least one decision_id is required")

    example_dicts = []
    for decision_id in body.decision_ids:
        dec_result = await db.execute(
            select(Decision).where(
                Decision.id == decision_id,
                Decision.community_id == community_id,
            )
        )
        decision = dec_result.scalar_one_or_none()
        if not decision:
            raise HTTPException(status_code=404, detail=f"Decision {decision_id} not found")

        # Derive label: prefer moderator verdict if resolved, else agent verdict
        if decision.moderator_verdict not in (None, "pending"):
            verdict = decision.moderator_verdict
        else:
            verdict = decision.agent_verdict
        label = "violating" if verdict == "remove" else "compliant" if verdict == "approve" else "borderline"

        example_dicts.append({
            "label": label,
            "content": decision.post_content,
            "moderator_reasoning": decision.moderator_notes,
        })

    compiler = get_compiler()
    synthesis = await compiler.synthesize_rule_from_examples(example_dicts, community)

    suggestion = Suggestion(
        rule_id=None,
        suggestion_type="new_rule",
        content={
            "title": synthesis["title"],
            "text": synthesis["text"],
            "confidence": synthesis["confidence"],
            "reasoning": synthesis["reasoning"],
            "community_id": community_id,
        },
        status="pending",
    )
    db.add(suggestion)
    await db.commit()
    await db.refresh(suggestion)

    response: dict[str, Any] = {"suggestion": SuggestionRead.model_validate(suggestion)}
    if len(body.decision_ids) < 3:
        response["warning"] = (
            f"Only {len(body.decision_ids)} decision(s) selected — "
            "the suggestion may over-fit to a one-off."
        )
    return response
