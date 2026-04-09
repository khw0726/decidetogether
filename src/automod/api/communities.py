"""Community CRUD endpoints."""

import re
from datetime import datetime, timezone

import anthropic
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..compiler.compiler import RuleCompiler
from ..config import settings
from ..core.reddit_crawler import crawl_subreddit_top_posts
from ..db.database import get_db
from ..db.models import (
    ChecklistItem,
    Community,
    CommunitySamplePost,
    Decision,
    Example,
    ExampleChecklistItemLink,
    ExampleRuleLink,
    Rule,
    Suggestion,
)
from ..models.schemas import (
    CommunityCreate,
    CommunityRead,
    CommunitySamplePostCreate,
    CommunitySamplePostRead,
)

router = APIRouter(tags=["communities"])


class AtmosphereGenerateResponse(BaseModel):
    community: CommunityRead
    crawled_count: int


def get_compiler() -> RuleCompiler:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return RuleCompiler(client, settings)


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


@router.delete("/communities/{community_id}", status_code=204)
async def delete_community(
    community_id: str, db: AsyncSession = Depends(get_db)
) -> None:
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    # Fetch rule IDs for this community
    rules_result = await db.execute(select(Rule.id).where(Rule.community_id == community_id))
    rule_ids = [r[0] for r in rules_result.all()]

    if rule_ids:
        # Checklist items linked to these rules
        ci_result = await db.execute(select(ChecklistItem.id).where(ChecklistItem.rule_id.in_(rule_ids)))
        ci_ids = [r[0] for r in ci_result.all()]
        if ci_ids:
            await db.execute(delete(ExampleChecklistItemLink).where(ExampleChecklistItemLink.checklist_item_id.in_(ci_ids)))
        await db.execute(delete(ChecklistItem).where(ChecklistItem.rule_id.in_(rule_ids)))

        # Examples linked to these rules
        erl_result = await db.execute(select(ExampleRuleLink.example_id).where(ExampleRuleLink.rule_id.in_(rule_ids)))
        example_ids = [r[0] for r in erl_result.all()]
        if example_ids:
            await db.execute(delete(ExampleChecklistItemLink).where(ExampleChecklistItemLink.example_id.in_(example_ids)))
            await db.execute(delete(Example).where(Example.id.in_(example_ids)))
        await db.execute(delete(ExampleRuleLink).where(ExampleRuleLink.rule_id.in_(rule_ids)))

        await db.execute(delete(Suggestion).where(Suggestion.rule_id.in_(rule_ids)))
        await db.execute(delete(Rule).where(Rule.community_id == community_id))

    await db.execute(delete(Decision).where(Decision.community_id == community_id))
    await db.execute(delete(CommunitySamplePost).where(CommunitySamplePost.community_id == community_id))
    await db.delete(community)
    await db.commit()


# ── Sample Posts ───────────────────────────────────────────────────────────────

@router.get("/communities/{community_id}/sample-posts", response_model=list[CommunitySamplePostRead])
async def list_sample_posts(
    community_id: str, db: AsyncSession = Depends(get_db)
) -> list[CommunitySamplePostRead]:
    result = await db.execute(
        select(CommunitySamplePost)
        .where(CommunitySamplePost.community_id == community_id)
        .order_by(CommunitySamplePost.created_at.asc())
    )
    posts = result.scalars().all()
    return [CommunitySamplePostRead.model_validate(p) for p in posts]


@router.post("/communities/{community_id}/sample-posts", response_model=CommunitySamplePostRead, status_code=201)
async def add_sample_post(
    community_id: str,
    body: CommunitySamplePostCreate,
    db: AsyncSession = Depends(get_db),
) -> CommunitySamplePostRead:
    result = await db.execute(select(Community).where(Community.id == community_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Community not found")

    valid_labels = {"acceptable", "unacceptable"}
    if body.label not in valid_labels:
        raise HTTPException(status_code=422, detail=f"label must be one of {valid_labels}")

    post = CommunitySamplePost(
        community_id=community_id,
        content=body.content,
        label=body.label,
        note=body.note,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return CommunitySamplePostRead.model_validate(post)


@router.delete("/communities/{community_id}/sample-posts/{post_id}", status_code=204)
async def delete_sample_post(
    community_id: str,
    post_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(CommunitySamplePost).where(
            CommunitySamplePost.id == post_id,
            CommunitySamplePost.community_id == community_id,
        )
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Sample post not found")
    await db.delete(post)
    await db.commit()


# ── Reddit URL Import ─────────────────────────────────────────────────────────

class ImportFromUrlRequest(BaseModel):
    url: str
    label: str  # acceptable | unacceptable
    note: str | None = None


_REDDIT_POST_RE = re.compile(
    r"https?://(?:www\.|old\.|new\.)?reddit\.com/r/[^/]+/comments/([a-z0-9]+)",
    re.IGNORECASE,
)
_REDDIT_SHORT_RE = re.compile(r"https?://redd\.it/([a-z0-9]+)", re.IGNORECASE)

_REDDIT_HEADERS = {
    "User-Agent": "automod-agent/2.0 (community moderation tool)",
}


def _reddit_json_url(url: str) -> str:
    """Convert any Reddit post URL to its .json API equivalent."""
    # Strip trailing slash and any query/fragment, then add .json
    clean = url.split("?")[0].split("#")[0].rstrip("/")
    if not clean.endswith(".json"):
        clean += ".json"
    # Always use www.reddit.com (old/new redirects may not return JSON)
    clean = re.sub(r"https?://(?:old\.|new\.)?reddit\.com", "https://www.reddit.com", clean)
    return clean


def _map_reddit_post(data: dict) -> dict:
    """Map a Reddit post's data dict to the PostContent schema."""
    created_utc = data.get("created_utc", 0)
    author_created_utc = data.get("author_created_utc")

    account_age_days: int | None = None
    if author_created_utc and created_utc:
        account_age_days = max(0, int((created_utc - author_created_utc) / 86400))

    is_self = data.get("is_self", True)
    links = []
    if not is_self and data.get("url"):
        links = [data["url"]]

    return {
        "id": data.get("name", ""),
        "platform": "reddit",
        "author": {
            "username": data.get("author", ""),
            "account_age_days": account_age_days,
            "platform_metadata": {
                "karma": data.get("author_karma"),
            },
        },
        "content": {
            "title": data.get("title", ""),
            "body": data.get("selftext", ""),
            "media": [],
            "links": links,
        },
        "context": {
            "channel": data.get("subreddit_name_prefixed", ""),
            "post_type": "self" if is_self else "link",
            "flair": data.get("link_flair_text") or None,
            "platform_metadata": {
                "score": data.get("score", 0),
                "permalink": data.get("permalink", ""),
            },
        },
        "timestamp": datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()
        if created_utc
        else None,
    }


@router.post(
    "/communities/{community_id}/sample-posts/import-url",
    response_model=CommunitySamplePostRead,
    status_code=201,
)
async def import_sample_post_from_url(
    community_id: str,
    body: ImportFromUrlRequest,
    db: AsyncSession = Depends(get_db),
) -> CommunitySamplePostRead:
    """Fetch a Reddit post by URL and add it as a community sample post."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Community not found")

    valid_labels = {"acceptable", "unacceptable"}
    if body.label not in valid_labels:
        raise HTTPException(status_code=422, detail=f"label must be one of {valid_labels}")

    # Validate it looks like a Reddit post URL
    if not (_REDDIT_POST_RE.search(body.url) or _REDDIT_SHORT_RE.search(body.url)):
        raise HTTPException(
            status_code=422,
            detail="URL must be a Reddit post URL (reddit.com/r/.../comments/... or redd.it/...)",
        )

    json_url = _reddit_json_url(body.url)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            response = await client.get(json_url, headers=_REDDIT_HEADERS)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Reddit returned {e.response.status_code}. The post may be private or deleted.",
        )
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to fetch post from Reddit.")

    # Reddit JSON API returns [post_listing, comments_listing]
    try:
        post_data = payload[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        raise HTTPException(status_code=502, detail="Unexpected response format from Reddit.")

    content = _map_reddit_post(post_data)

    post = CommunitySamplePost(
        community_id=community_id,
        content=content,
        label=body.label,
        note=body.note,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return CommunitySamplePostRead.model_validate(post)


# ── Atmosphere Generation ──────────────────────────────────────────────────────

@router.post("/communities/{community_id}/atmosphere/generate", response_model=AtmosphereGenerateResponse)
async def generate_atmosphere(
    community_id: str,
    db: AsyncSession = Depends(get_db),
    compiler: RuleCompiler = Depends(get_compiler),
) -> AtmosphereGenerateResponse:
    """Generate a community atmosphere profile from existing decisions, sample posts, and crawled Reddit posts."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    # Collect approved decisions
    approved_result = await db.execute(
        select(Decision)
        .where(
            Decision.community_id == community_id,
            Decision.moderator_verdict == "approve",
        )
        .order_by(Decision.created_at.desc())
        .limit(10)
    )
    approved_decisions = approved_result.scalars().all()

    # Collect removed decisions
    removed_result = await db.execute(
        select(Decision)
        .where(
            Decision.community_id == community_id,
            Decision.moderator_verdict == "remove",
        )
        .order_by(Decision.created_at.desc())
        .limit(10)
    )
    removed_decisions = removed_result.scalars().all()

    # Collect user-supplied sample posts
    sample_result = await db.execute(
        select(CommunitySamplePost)
        .where(CommunitySamplePost.community_id == community_id)
        .order_by(CommunitySamplePost.created_at.desc())
        .limit(20)
    )
    sample_posts = sample_result.scalars().all()

    # Crawl top posts from Reddit if credentials are configured
    crawled_posts: list[dict] = []
    if community.platform == "reddit" and settings.reddit_client_id:
        m = re.match(r"^r/(.+)$", community.name.strip(), re.IGNORECASE)
        if m:
            crawled_posts = await crawl_subreddit_top_posts(
                m.group(1),
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                user_agent=settings.reddit_user_agent,
                username=settings.reddit_username,
                password=settings.reddit_password,
            )

    acceptable_posts = (
        [{"content": p["content"], "label": "acceptable"} for p in crawled_posts]
        + [{"content": d.post_content, "label": "acceptable"} for d in approved_decisions]
        + [{"content": p.content, "label": "acceptable", "note": p.note} for p in sample_posts if p.label == "acceptable"]
    )

    unacceptable_posts = [
        {"content": d.post_content, "label": "unacceptable"}
        for d in removed_decisions
    ] + [
        {"content": p.content, "label": "unacceptable", "note": p.note}
        for p in sample_posts if p.label == "unacceptable"
    ]

    if not acceptable_posts and not unacceptable_posts:
        raise HTTPException(
            status_code=422,
            detail="No posts available. Add sample posts or resolve some moderation decisions first.",
        )

    # Fetch active rules to give the atmosphere generator context on what the community enforces
    rules_result = await db.execute(
        select(Rule)
        .where(Rule.community_id == community_id, Rule.is_active == True)  # noqa: E712
        .order_by(Rule.priority.asc())
    )
    active_rules = list(rules_result.scalars().all())

    atmosphere = await compiler.generate_community_atmosphere(
        community=community,
        acceptable_posts=acceptable_posts,
        unacceptable_posts=unacceptable_posts,
        other_rules=active_rules or None,
    )
    community.atmosphere = atmosphere
    await db.commit()
    await db.refresh(community)
    return AtmosphereGenerateResponse(
        community=CommunityRead.model_validate(community),
        crawled_count=len(crawled_posts),
    )
