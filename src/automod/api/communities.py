"""Community CRUD endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.database import get_db
from ..db.models import Community
from ..models.schemas import CommunityCreate, CommunityRead

router = APIRouter(tags=["communities"])


@router.get("/communities", response_model=list[CommunityRead])
async def list_communities(db: AsyncSession = Depends(get_db)) -> list[CommunityRead]:
    result = await db.execute(select(Community).order_by(Community.created_at.asc()))
    communities = result.scalars().all()
    return [CommunityRead.model_validate(c) for c in communities]


@router.post("/communities", response_model=CommunityRead, status_code=201)
async def create_community(
    body: CommunityCreate, db: AsyncSession = Depends(get_db)
) -> CommunityRead:
    valid_platforms = {"reddit", "chatroom", "forum"}
    if body.platform not in valid_platforms:
        raise HTTPException(
            status_code=422,
            detail=f"platform must be one of {valid_platforms}",
        )
    community = Community(
        name=body.name,
        platform=body.platform,
        platform_config=body.platform_config,
    )
    db.add(community)
    await db.commit()
    await db.refresh(community)
    return CommunityRead.model_validate(community)


@router.get("/communities/{community_id}", response_model=CommunityRead)
async def get_community(
    community_id: str, db: AsyncSession = Depends(get_db)
) -> CommunityRead:
    result = await db.execute(
        select(Community).where(Community.id == community_id)
    )
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")
    return CommunityRead.model_validate(community)
