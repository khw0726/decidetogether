"""Community CRUD endpoints."""

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..compiler.compiler import RuleCompiler
from ..config import get_anthropic_client, settings
from ..core.engine import EvaluationEngine
from ..core.reddit_crawler import crawl_subreddit_top_posts
from ..db.database import AsyncSessionLocal, get_db
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
from ..core.reddit_crawler import sample_subreddit_for_context
from ..models.schemas import (
    CommunityContextData,
    CommunityContextUpdate,
    CommunityCreate,
    CommunityRead,
    CommunitySamplePostCreate,
    CommunitySamplePostRead,
    DecisionRead,
    RedditImportRequest,
    RedditImportResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["communities"])


def _get_engine(db: AsyncSession = Depends(get_db)) -> EvaluationEngine:
    client = get_anthropic_client()
    return EvaluationEngine(db=db, client=client, settings=settings)


def get_compiler() -> RuleCompiler:
    client = get_anthropic_client()
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


@router.get("/communities/context-taxonomy")
async def get_context_taxonomy():
    """Return the available context tags per dimension for the tag picker UI."""
    taxonomy = _load_taxonomy()
    if not taxonomy:
        return {}
    return taxonomy


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


# ── Setup Status ──────────────────────────────────────────────────────────────


class BorderlineItem(BaseModel):
    suggestion_id: str
    rule_id: str
    rule_title: str
    content: dict
    relevance_note: str


class SetupStatusResponse(BaseModel):
    actionable_total: int
    compiled_count: int
    borderline_examples: list[BorderlineItem]


@router.get(
    "/communities/{community_id}/setup-status",
    response_model=SetupStatusResponse,
)
async def get_setup_status(
    community_id: str, db: AsyncSession = Depends(get_db)
) -> SetupStatusResponse:
    """Return compilation progress and pending borderline examples for the setup wizard."""
    # Get all actionable rules
    rules_result = await db.execute(
        select(Rule).where(
            Rule.community_id == community_id,
            Rule.is_active == True,  # noqa: E712
            Rule.rule_type == "actionable",
        )
    )
    actionable_rules = list(rules_result.scalars().all())
    rule_map = {r.id: r.title for r in actionable_rules}

    # Count how many have at least one checklist item (= compiled)
    if actionable_rules:
        from sqlalchemy import func
        compiled_result = await db.execute(
            select(func.count(func.distinct(ChecklistItem.rule_id)))
            .where(ChecklistItem.rule_id.in_([r.id for r in actionable_rules]))
        )
        compiled = compiled_result.scalar() or 0
    else:
        compiled = 0

    # Fetch pending borderline example suggestions across all rules
    if rule_map:
        suggestions_result = await db.execute(
            select(Suggestion).where(
                Suggestion.rule_id.in_(rule_map.keys()),
                Suggestion.suggestion_type == "example",
                Suggestion.status == "pending",
            )
        )
        suggestions = list(suggestions_result.scalars().all())
    else:
        suggestions = []

    borderline_items = []
    for s in suggestions:
        label = s.content.get("label", "")
        if label != "borderline":
            continue
        borderline_items.append(BorderlineItem(
            suggestion_id=s.id,
            rule_id=s.rule_id,
            rule_title=rule_map.get(s.rule_id, ""),
            content=s.content.get("content", {}),
            relevance_note=s.content.get("relevance_note", ""),
        ))

    return SetupStatusResponse(
        actionable_total=len(actionable_rules),
        compiled_count=compiled,
        borderline_examples=borderline_items,
    )


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


class CrawlSamplePostsResponse(BaseModel):
    posts: list[CommunitySamplePostRead]
    crawled_count: int


@router.post(
    "/communities/{community_id}/sample-posts/crawl",
    response_model=CrawlSamplePostsResponse,
    status_code=201,
)
async def crawl_sample_posts(
    community_id: str,
    db: AsyncSession = Depends(get_db),
) -> CrawlSamplePostsResponse:
    """Auto-crawl top posts from a Reddit community and save them as sample posts."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    if community.platform != "reddit":
        raise HTTPException(status_code=422, detail="Auto-crawl is only available for Reddit communities")

    if not settings.reddit_client_id:
        raise HTTPException(status_code=422, detail="Reddit credentials are not configured")

    m = re.match(r"^r/(.+)$", community.name.strip(), re.IGNORECASE)
    if not m:
        raise HTTPException(status_code=422, detail="Community name must be in r/subreddit format for auto-crawl")

    crawled_posts = await crawl_subreddit_top_posts(
        m.group(1),
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
        username=settings.reddit_username,
        password=settings.reddit_password,
        limit=15,
        time_filter="month",
    )

    saved_posts = []
    for post_data in crawled_posts:
        post = CommunitySamplePost(
            community_id=community_id,
            content=post_data,
            label="acceptable",
            note="Auto-crawled from subreddit",
        )
        db.add(post)
        await db.flush()
        await db.refresh(post)
        saved_posts.append(CommunitySamplePostRead.model_validate(post))

    await db.commit()
    return CrawlSamplePostsResponse(
        posts=saved_posts,
        crawled_count=len(crawled_posts),
    )


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


# ── Context Samples ───────────────────────────────────────────────────────────


class ContextSamplesResponse(BaseModel):
    context_samples: dict


@router.get("/communities/{community_id}/context-samples", response_model=ContextSamplesResponse)
async def get_context_samples(
    community_id: str, db: AsyncSession = Depends(get_db)
) -> ContextSamplesResponse:
    """Return stored context samples."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")
    return ContextSamplesResponse(context_samples=community.context_samples or {})


@router.post(
    "/communities/{community_id}/context-samples/crawl",
    response_model=ContextSamplesResponse,
    status_code=201,
)
async def crawl_context_samples(
    community_id: str,
    db: AsyncSession = Depends(get_db),
) -> ContextSamplesResponse:
    """Crawl activity-based post samples (hot/top/controversial/ignored/comments) for context generation."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    if community.platform != "reddit":
        raise HTTPException(status_code=422, detail="Context sampling is only available for Reddit communities")

    if not settings.reddit_client_id:
        raise HTTPException(status_code=422, detail="Reddit credentials are not configured")

    m = re.match(r"^r/(.+)$", community.name.strip(), re.IGNORECASE)
    if not m:
        raise HTTPException(status_code=422, detail="Community name must be in r/subreddit format")

    sampled = await sample_subreddit_for_context(
        subreddit=m.group(1),
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
        username=settings.reddit_username,
        password=settings.reddit_password,
    )

    community.context_samples = sampled
    await db.commit()
    await db.refresh(community)
    return ContextSamplesResponse(context_samples=community.context_samples)


# ── Community Context ─────────────────────────────────────────────────────────


class ContextGenerateResponse(BaseModel):
    community_context: dict


@router.get("/communities/{community_id}/context", response_model=dict)
async def get_community_context(
    community_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    """Return the full community context."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")
    return community.community_context or {}


@router.put("/communities/{community_id}/context", response_model=dict)
async def update_community_context(
    community_id: str,
    body: CommunityContextUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update community context (partial — only provided dimensions are updated)."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    # Deep copy so SQLAlchemy detects the mutation on the JSON column
    import copy
    updated = copy.deepcopy(community.community_context or {})
    for dim in ["purpose", "participants", "stakes", "tone"]:
        update_val = getattr(body, dim, None)
        if update_val is not None:
            dim_data = update_val.model_dump()
            dim_data["manually_edited"] = True
            updated[dim] = dim_data

    community.community_context = updated
    await db.commit()
    await db.refresh(community)
    return community.community_context or {}


@router.post(
    "/communities/{community_id}/context/generate",
    response_model=ContextGenerateResponse,
)
async def generate_community_context(
    community_id: str,
    db: AsyncSession = Depends(get_db),
    compiler: RuleCompiler = Depends(get_compiler),
) -> ContextGenerateResponse:
    """Auto-generate community context using metadata + activity-sampled posts from Reddit."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    # Gather community description from platform config or name
    description = ""
    if community.platform_config:
        description = community.platform_config.get("public_description", "")
        if not description:
            description = community.platform_config.get("description", "")

    # Gather rules summary
    rules_result = await db.execute(
        select(Rule)
        .where(Rule.community_id == community_id, Rule.is_active == True)  # noqa: E712
        .order_by(Rule.priority.asc())
    )
    active_rules = list(rules_result.scalars().all())
    rules_summary = "\n".join(f"- {r.title}: {r.text[:150]}" for r in active_rules) if active_rules else ""

    # Get subscriber count
    subscribers = None
    if community.platform_config:
        subscribers = community.platform_config.get("subscribers")

    # Always crawl fresh posts when regenerating context
    sampled_posts = None
    if community.platform == "reddit":
        m = re.match(r"^r/(.+)$", community.name.strip(), re.IGNORECASE)
        if m and settings.reddit_client_id:
            sampled_posts = await sample_subreddit_for_context(
                subreddit=m.group(1),
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                user_agent=settings.reddit_user_agent,
                username=settings.reddit_username,
                password=settings.reddit_password,
            )
            community.context_samples = sampled_posts

    # Fall back to stored samples if crawl didn't produce results
    if not sampled_posts:
        sampled_posts = community.context_samples

    # Load taxonomy for tag constraint
    taxonomy = _load_taxonomy()

    # Generate context via LLM
    context = await compiler.generate_community_context(
        community_name=community.name,
        platform=community.platform,
        description=description,
        rules_summary=rules_summary,
        subscribers=subscribers,
        sampled_posts=sampled_posts,
        taxonomy=taxonomy,
    )

    # Merge with existing context (preserve manually-edited dimensions)
    existing = community.community_context or {}
    for dim in ["purpose", "participants", "stakes", "tone"]:
        if dim not in existing:
            existing[dim] = context[dim]
        elif existing[dim].get("manually_edited"):
            # Skip regeneration for dimensions the moderator has hand-edited
            continue
        else:
            existing[dim] = context[dim]

    community.community_context = existing
    await db.commit()
    await db.refresh(community)
    return ContextGenerateResponse(community_context=community.community_context)


class ContextPreviewImpactItem(BaseModel):
    rule_id: str
    rule_title: str
    adjustment_summary: list[str]


class ContextPreviewImpactResponse(BaseModel):
    rules_affected: int
    impacts: list[ContextPreviewImpactItem]


@router.post(
    "/communities/{community_id}/context/preview-impact",
    response_model=ContextPreviewImpactResponse,
)
async def preview_context_impact(
    community_id: str,
    body: CommunityContextUpdate,
    db: AsyncSession = Depends(get_db),
    compiler: RuleCompiler = Depends(get_compiler),
) -> ContextPreviewImpactResponse:
    """Preview how a draft context change would affect rules with stored base checklists."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    # Build draft context by merging current context with the update
    draft_context = dict(community.community_context or {})
    for dim in ["purpose", "participants", "stakes", "tone"]:
        update_val = getattr(body, dim, None)
        if update_val is not None:
            draft_context[dim] = update_val.model_dump()

    # Find actionable rules with stored base checklists
    rules_result = await db.execute(
        select(Rule).where(
            Rule.community_id == community_id,
            Rule.is_active == True,  # noqa: E712
            Rule.rule_type == "actionable",
            Rule.base_checklist_json.isnot(None),
        )
    )
    rules = list(rules_result.scalars().all())

    # Fetch current live checklist items for all rules so the LLM can
    # describe changes relative to what moderators see today.
    rule_ids = [r.id for r in rules]
    items_result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.rule_id.in_(rule_ids))
    )
    all_items = list(items_result.scalars().all())
    items_by_rule: dict[str, list[ChecklistItem]] = {}
    for item in all_items:
        items_by_rule.setdefault(item.rule_id, []).append(item)

    impacts = []
    for rule in rules:
        try:
            current_items = items_by_rule.get(rule.id, [])
            current_dicts = (
                compiler._items_to_nested_dicts(current_items)
                if current_items else None
            )
            _, summary = await compiler.adjust_for_context(
                rule=rule,
                community=community,
                base_checklist_dicts=rule.base_checklist_json,
                community_context=draft_context,
                current_checklist_dicts=current_dicts,
                relevant_context=rule.relevant_context,
                custom_context_notes=rule.custom_context_notes,
            )
            if summary:
                impacts.append(ContextPreviewImpactItem(
                    rule_id=rule.id,
                    rule_title=rule.title,
                    adjustment_summary=summary,
                ))
        except Exception as e:
            logger.warning(f"Preview failed for rule '{rule.title}': {e}")

    return ContextPreviewImpactResponse(
        rules_affected=len(impacts),
        impacts=impacts,
    )


class ReapplyContextResponse(BaseModel):
    rules_updated: int
    summaries: dict  # rule_id -> adjustment_summary


@router.post(
    "/communities/{community_id}/reapply-context",
    response_model=ReapplyContextResponse,
)
async def reapply_context(
    community_id: str,
    db: AsyncSession = Depends(get_db),
    compiler: RuleCompiler = Depends(get_compiler),
) -> ReapplyContextResponse:
    """Re-run context adjustment (Pass 2) on all rules that have a stored base checklist."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    if not community.community_context:
        raise HTTPException(status_code=422, detail="No community context to apply")

    # Find actionable rules with stored base checklists
    rules_result = await db.execute(
        select(Rule).where(
            Rule.community_id == community_id,
            Rule.is_active == True,  # noqa: E712
            Rule.rule_type == "actionable",
            Rule.base_checklist_json.isnot(None),
        )
    )
    rules = list(rules_result.scalars().all())

    summaries = {}
    updated = 0

    for rule in rules:
        try:
            # Collect pinned items before replacement
            pinned_result = await db.execute(
                select(ChecklistItem).where(
                    ChecklistItem.rule_id == rule.id,
                    ChecklistItem.context_pinned == True,  # noqa: E712
                )
            )
            pinned_items = [
                {"description": p.description, "context_override_note": p.context_override_note}
                for p in pinned_result.scalars().all()
            ] or None

            adjusted_items, summary = await compiler.adjust_for_context(
                rule=rule,
                community=community,
                base_checklist_dicts=rule.base_checklist_json,
                community_context=community.community_context,
                pinned_items=pinned_items,
                relevant_context=rule.relevant_context,
                custom_context_notes=rule.custom_context_notes,
            )

            # Replace existing checklist items
            await db.execute(
                delete(ChecklistItem).where(ChecklistItem.rule_id == rule.id)
            )
            for item in adjusted_items:
                item.rule_id = rule.id
                db.add(item)

            rule.context_adjustment_summary = summary
            summaries[rule.id] = summary
            updated += 1
        except Exception as e:
            logger.warning(f"Failed to reapply context for rule '{rule.title}': {e}")

    await db.commit()
    return ReapplyContextResponse(rules_updated=updated, summaries=summaries)


def _load_taxonomy() -> dict | None:
    """Load the context taxonomy from scripts/context_taxonomy.json if available."""
    import json
    from pathlib import Path

    taxonomy_path = Path(__file__).parent.parent.parent.parent / "scripts" / "context_taxonomy.json"
    if taxonomy_path.exists():
        try:
            data = json.loads(taxonomy_path.read_text())
            # Simplify to just {dim: {tag: description}}
            simplified = {}
            for dim in ["purpose", "participants", "stakes", "tone"]:
                cats = data.get(dim, {})
                simplified[dim] = {name: info.get("description", "") for name, info in cats.items()}
            return simplified
        except Exception:
            pass
    return None


class PopulateQueueResponse(BaseModel):
    message: str
    task_started: bool


@router.post(
    "/communities/{community_id}/populate-queue",
    response_model=PopulateQueueResponse,
)
async def populate_decision_queue(
    community_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> PopulateQueueResponse:
    """Crawl recent Reddit posts and evaluate them to populate the decision queue (runs in background)."""
    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    if community.platform != "reddit":
        return PopulateQueueResponse(message="Auto-populate only available for Reddit communities", task_started=False)

    if not settings.reddit_client_id:
        return PopulateQueueResponse(message="Reddit credentials not configured", task_started=False)

    m = re.match(r"^r/(.+)$", community.name.strip(), re.IGNORECASE)
    if not m:
        return PopulateQueueResponse(message="Community name must be in r/subreddit format", task_started=False)

    subreddit = m.group(1)
    background_tasks.add_task(_populate_queue_background, community_id, subreddit)
    return PopulateQueueResponse(message="Decision queue population started", task_started=True)


async def _populate_queue_background(community_id: str, subreddit: str) -> None:
    """Background task: crawl recent posts and evaluate them."""
    from ..db.database import AsyncSessionLocal

    try:
        raw_posts = await crawl_subreddit_top_posts(
            subreddit=subreddit,
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
            username=settings.reddit_username,
            password=settings.reddit_password,
            limit=25,
            time_filter="week",
        )

        async with AsyncSessionLocal() as db:
            platform_ids = [p["id"] for p in raw_posts]
            if platform_ids:
                existing = set(
                    (await db.execute(
                        select(Decision.post_platform_id).where(
                            Decision.community_id == community_id,
                            Decision.post_platform_id.in_(platform_ids),
                        )
                    )).scalars().all()
                )
            else:
                existing = set()

        new_posts = [p for p in raw_posts if p["id"] not in existing]

        sem = asyncio.Semaphore(5)

        async def _eval_one(post: dict) -> None:
            async with sem, AsyncSessionLocal() as session:
                eng = EvaluationEngine(
                    db=session,
                    client=get_anthropic_client(),
                    settings=settings,
                )
                await eng.evaluate_post(community_id=community_id, post=post)

        await asyncio.gather(
            *[_eval_one(p) for p in new_posts],
            return_exceptions=True,
        )
        logger.info(f"Populated queue for community {community_id}: {len(new_posts)} posts evaluated")

    except Exception as e:
        logger.error(f"Failed to populate queue for community {community_id}: {e}")


@router.post("/communities/{community_id}/import-reddit", response_model=RedditImportResponse)
async def import_reddit_posts(
    community_id: str,
    body: RedditImportRequest,
    db: AsyncSession = Depends(get_db),
    engine: EvaluationEngine = Depends(_get_engine),
) -> RedditImportResponse:
    """Crawl recent posts from a subreddit and run moderation on each."""
    comm = (await db.execute(select(Community).where(Community.id == community_id))).scalar_one_or_none()
    if not comm:
        raise HTTPException(status_code=404, detail="Community not found")

    if not settings.reddit_client_id:
        raise HTTPException(status_code=422, detail="Reddit credentials are not configured")

    raw_posts = await crawl_subreddit_top_posts(
        subreddit=body.subreddit,
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
        username=settings.reddit_username,
        password=settings.reddit_password,
        limit=body.limit,
        time_filter=body.time_filter,
    )

    platform_ids = [p["id"] for p in raw_posts]
    if platform_ids:
        existing = set(
            (await db.execute(
                select(Decision.post_platform_id).where(
                    Decision.community_id == community_id,
                    Decision.post_platform_id.in_(platform_ids),
                )
            )).scalars().all()
        )
    else:
        existing = set()

    new_posts = [p for p in raw_posts if p["id"] not in existing]
    skipped_count = len(raw_posts) - len(new_posts)

    sem = asyncio.Semaphore(5)

    async def _eval_one(post: dict) -> Decision:
        async with sem, AsyncSessionLocal() as session:
            eng = EvaluationEngine(
                db=session,
                client=get_anthropic_client(),
                settings=settings,
            )
            return await eng.evaluate_post(community_id=community_id, post=post)

    results = await asyncio.gather(
        *[_eval_one(p) for p in new_posts],
        return_exceptions=True,
    )

    decisions = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.warning("Failed to evaluate post %s: %s", new_posts[i].get("id"), r)
        else:
            decisions.append(DecisionRead.model_validate(r))

    return RedditImportResponse(
        decisions=decisions,
        crawled_count=len(raw_posts),
        evaluated_count=len(decisions),
        skipped_count=skipped_count,
    )
