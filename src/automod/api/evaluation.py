"""Post evaluation endpoints."""

import logging

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.engine import EvaluationEngine
from ..db.database import get_db
from ..models.schemas import (
    BatchEvaluateRequest,
    BatchEvaluateResponse,
    DecisionRead,
    EvaluateRequest,
    EvaluateResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["evaluation"])


def get_engine(db: AsyncSession = Depends(get_db)) -> EvaluationEngine:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return EvaluationEngine(db=db, client=client, settings=settings)


@router.post("/communities/{community_id}/evaluate", response_model=EvaluateResponse)
async def evaluate_post(
    community_id: str,
    body: EvaluateRequest,
    engine: EvaluationEngine = Depends(get_engine),
) -> EvaluateResponse:
    """Evaluate a single post against all community rules."""
    try:
        post_dict = body.post_content.model_dump()
        decision = await engine.evaluate_post(community_id=community_id, post=post_dict)
        return EvaluateResponse(decision=DecisionRead.model_validate(decision))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Evaluation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {e}")


@router.post("/communities/{community_id}/evaluate/batch", response_model=BatchEvaluateResponse)
async def evaluate_posts_batch(
    community_id: str,
    body: BatchEvaluateRequest,
    engine: EvaluationEngine = Depends(get_engine),
) -> BatchEvaluateResponse:
    """Evaluate multiple posts against all community rules."""
    if len(body.posts) > 20:
        raise HTTPException(status_code=422, detail="Batch size limited to 20 posts")

    decisions = []
    errors = []

    for i, post_content in enumerate(body.posts):
        try:
            post_dict = post_content.model_dump()
            decision = await engine.evaluate_post(community_id=community_id, post=post_dict)
            decisions.append(DecisionRead.model_validate(decision))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"Batch evaluation failed for post {i}: {e}")
            errors.append({"index": i, "error": str(e)})

    if errors and not decisions:
        raise HTTPException(status_code=500, detail=f"All evaluations failed: {errors}")

    return BatchEvaluateResponse(decisions=decisions)
