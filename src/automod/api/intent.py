"""Rule-intent chat endpoints.

Casual moderator messages translate (via LLM) into pending `Suggestion` rows of
type `rule_text`. The thread itself is the messages; suggestions ride on the
existing alignment pipeline (accept_suggestion → _recompile_after_text_accept).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_anthropic_client, settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import Community, Decision, Rule, RuleIntentMessage, Suggestion
from ..models.schemas import (
    RuleIntentMessageCreate,
    RuleIntentMessageRead,
    RuleIntentMessageResponse,
    SuggestionRead,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["intent"])


# How many recent messages to feed back into the translator as conversational context.
_RECENT_THREAD_WINDOW = 8


def _get_compiler() -> RuleCompiler:
    return RuleCompiler(get_anthropic_client(), settings)


def _to_read(
    msg: RuleIntentMessage, suggestion: Optional[Suggestion] = None
) -> RuleIntentMessageRead:
    return RuleIntentMessageRead(
        id=msg.id,
        rule_id=msg.rule_id,
        body=msg.body,
        author=msg.author,
        decision_id=msg.decision_id,
        suggestion_id=msg.suggestion_id,
        no_suggestion_reason=msg.no_suggestion_reason,
        created_at=msg.created_at,
        suggestion_status=suggestion.status if suggestion else None,
        suggestion_content=suggestion.content if suggestion else None,
    )


@router.get(
    "/rules/{rule_id}/intent-messages",
    response_model=list[RuleIntentMessageRead],
)
async def list_intent_messages(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[RuleIntentMessageRead]:
    rule_res = await db.execute(select(Rule).where(Rule.id == rule_id))
    if not rule_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Rule not found")

    msg_res = await db.execute(
        select(RuleIntentMessage)
        .where(RuleIntentMessage.rule_id == rule_id)
        .order_by(RuleIntentMessage.created_at.asc())
    )
    messages = list(msg_res.scalars().all())
    if not messages:
        return []

    suggestion_ids = [m.suggestion_id for m in messages if m.suggestion_id]
    suggestions_by_id: dict[str, Suggestion] = {}
    if suggestion_ids:
        sug_res = await db.execute(
            select(Suggestion).where(Suggestion.id.in_(suggestion_ids))
        )
        for s in sug_res.scalars().all():
            suggestions_by_id[s.id] = s

    return [
        _to_read(m, suggestions_by_id.get(m.suggestion_id) if m.suggestion_id else None)
        for m in messages
    ]


@router.post(
    "/rules/{rule_id}/intent-messages",
    response_model=RuleIntentMessageResponse,
)
async def create_intent_message(
    rule_id: str,
    body: RuleIntentMessageCreate,
    db: AsyncSession = Depends(get_db),
) -> RuleIntentMessageResponse:
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message text is required")

    rule_res = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_res.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    comm_res = await db.execute(select(Community).where(Community.id == rule.community_id))
    community = comm_res.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    anchored_post: Optional[dict] = None
    decision_id: Optional[str] = None
    if body.decision_id:
        dec_res = await db.execute(select(Decision).where(Decision.id == body.decision_id))
        decision = dec_res.scalar_one_or_none()
        if decision is None:
            raise HTTPException(status_code=404, detail="Anchored decision not found")
        if decision.community_id != rule.community_id:
            raise HTTPException(
                status_code=400,
                detail="Anchored decision does not belong to this rule's community",
            )
        anchored_post = decision.post_content
        decision_id = decision.id

    # Pull the recent thread for translator context.
    recent_res = await db.execute(
        select(RuleIntentMessage)
        .where(RuleIntentMessage.rule_id == rule_id)
        .order_by(RuleIntentMessage.created_at.desc())
        .limit(_RECENT_THREAD_WINDOW)
    )
    recent = list(recent_res.scalars().all())
    recent.reverse()  # oldest first
    recent_dicts = [{"body": m.body, "author": m.author} for m in recent]

    # ── LLM phase ─────────────────────────────────────────────────────
    try:
        compiler = _get_compiler()
        translation = await compiler.translate_intent_to_suggestion(
            rule=rule,
            community=community,
            new_message=text,
            recent_messages=recent_dicts,
            anchored_post=anchored_post,
        )
    except Exception:
        logger.exception(f"Intent translation failed for rule {rule_id}")
        translation = {
            "decision": "no_change",
            "no_change_reason": "Translator failed; message saved without a suggestion.",
        }

    # ── Persist ───────────────────────────────────────────────────────
    suggestion: Optional[Suggestion] = None
    no_suggestion_reason: Optional[str] = None
    if translation.get("decision") == "propose":
        suggestion = Suggestion(
            rule_id=rule_id,
            suggestion_type="rule_text",
            content={
                "proposed_text": translation["proposed_text"],
                "rationale": translation.get("rationale", ""),
                "source": "intent_chat",
                "decision_id": decision_id,
            },
            status="pending",
        )
        db.add(suggestion)
        await db.flush()
    else:
        no_suggestion_reason = translation.get("no_change_reason") or "No edit implied."

    msg = RuleIntentMessage(
        rule_id=rule_id,
        body=text,
        author="moderator",
        decision_id=decision_id,
        suggestion_id=suggestion.id if suggestion else None,
        no_suggestion_reason=no_suggestion_reason,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    if suggestion:
        await db.refresh(suggestion)

    return RuleIntentMessageResponse(
        message=_to_read(msg, suggestion),
        suggestion=SuggestionRead.model_validate(suggestion) if suggestion else None,
    )
