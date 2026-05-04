"""Scenario-driven setup endpoints for hypothetical-community user studies.

Endpoints:
  GET  /scenarios                                  — list scenario files
  POST /communities/from-scenario                  — create a fresh community from a scenario
  POST /communities/{id}/scenario-import-next      — import the next batch of queue_posts
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_anthropic_client, settings
from ..core.engine import EvaluationEngine
from ..core.scenario_loader import (
    get_or_crawl_context,
    list_scenarios,
    load_scenario,
)
from ..db.database import AsyncSessionLocal, get_db
from ..db.models import Community, Decision, Rule
from ..models.scenario import ScenarioFile, ScenarioSummary

# How many queue_posts to evaluate per batch (initial setup AND each "load more" click).
SCENARIO_IMPORT_BATCH = 25

logger = logging.getLogger(__name__)
router = APIRouter(tags=["scenarios"])


class FromScenarioRequest(BaseModel):
    filename: str
    community_name: str | None = None  # overrides scenario.community.name when set


class FromScenarioResponse(BaseModel):
    community_id: str
    community_name: str
    scenario_id: str
    rules_inserted: int
    queue_posts_scheduled: int
    context_cached: bool


@router.get("/scenarios", response_model=list[ScenarioSummary])
async def list_scenario_files() -> list[ScenarioSummary]:
    """List all scenario files under scenarios/."""
    return list_scenarios()


class AtmospherePost(BaseModel):
    bucket: str  # "hot" | "top" | "controversial" | "ignored"
    title: str
    body: str
    score: int
    num_comments: int
    upvote_ratio: float | None = None


class AtmosphereComment(BaseModel):
    body: str
    score: int
    post_title: str | None = None


class AtmosphereResponse(BaseModel):
    community_name: str
    description: str
    posts: list[AtmospherePost]
    comments: list[AtmosphereComment]


# Deterministic mix: same selection for every participant, every request.
_ATMOSPHERE_MIX = [("hot", 4), ("top", 2), ("controversial", 2), ("ignored", 1)]
_ATMOSPHERE_COMMENT_COUNT = 15


@router.get("/scenarios/{scenario_id}/atmosphere", response_model=AtmosphereResponse)
async def get_scenario_atmosphere(scenario_id: str) -> AtmosphereResponse:
    """Curated 5-10 post sample so participants can feel the community vibe.

    Reads the cached context for the scenario (data/scenario_contexts/<id>.json)
    and the scenario file itself for name + description. Selection is
    deterministic so every participant sees the same feed.
    """
    import json

    summaries = list_scenarios()
    summary = next((s for s in summaries if s.id == scenario_id), None)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Scenario not found: {scenario_id}")

    scenario = load_scenario(summary.filename)

    from ..core.scenario_loader import _context_cache_path
    cache_path = _context_cache_path(scenario_id)
    if not cache_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No cached context for scenario {scenario_id} — set up a community from this scenario first",
        )
    samples = json.loads(cache_path.read_text())

    posts: list[AtmospherePost] = []
    for bucket, n in _ATMOSPHERE_MIX:
        for entry in (samples.get(bucket) or [])[:n]:
            posts.append(AtmospherePost(
                bucket=bucket,
                title=entry.get("title") or "",
                body=entry.get("body") or "",
                score=int(entry.get("score") or 0),
                num_comments=int(entry.get("num_comments") or 0),
                upvote_ratio=entry.get("upvote_ratio"),
            ))

    comments_raw = samples.get("comments") or []
    comments = [
        AtmosphereComment(
            body=c.get("body") or "",
            score=int(c.get("score") or 0),
            post_title=c.get("post_title"),
        )
        for c in comments_raw[:_ATMOSPHERE_COMMENT_COUNT]
    ]

    return AtmosphereResponse(
        community_name=scenario.community.name,
        description=scenario.community.description or "",
        posts=posts,
        comments=comments,
    )


@router.post(
    "/communities/from-scenario",
    response_model=FromScenarioResponse,
    status_code=201,
)
async def create_community_from_scenario(
    body: FromScenarioRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> FromScenarioResponse:
    """Create a hypothetical community from a scenario file and schedule the rest in the background.

    Synchronous: create community + insert rules (so the response carries IDs).
    Background: load/crawl context → generate context dimensions → triage + compile
    rules → evaluate queue posts. The frontend can poll /communities/{id}/setup-status
    to watch progress.
    """
    try:
        scenario = load_scenario(body.filename)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse scenario: {e}")

    # Synchronous: create the community + insert rules
    name_override = (body.community_name or "").strip()
    community = Community(
        name=name_override or scenario.community.name,
        platform="hypothetical",
        platform_config={
            "public_description": scenario.community.description,
            "scenario_id": scenario.id,
            "scenario_filename": body.filename,
        },
    )
    db.add(community)
    await db.flush()

    rules: list[Rule] = []
    for i, item in enumerate(scenario.rules):
        priority = item.priority if item.priority is not None else i
        rule = Rule(
            community_id=community.id,
            title=item.title,
            text=item.text,
            priority=priority,
        )
        db.add(rule)
        rules.append(rule)
    await db.commit()
    await db.refresh(community)
    for rule in rules:
        await db.refresh(rule)

    community_id = community.id
    rule_ids = [r.id for r in rules]

    # Background: context, compilation, queue evaluation
    from ..core.scenario_loader import _context_cache_path
    context_was_cached = _context_cache_path(scenario.id).exists()

    background_tasks.add_task(_run_scenario_setup, community_id, rule_ids, scenario)

    return FromScenarioResponse(
        community_id=community_id,
        community_name=community.name,
        scenario_id=scenario.id,
        rules_inserted=len(rules),
        queue_posts_scheduled=len(scenario.queue_posts),
        context_cached=context_was_cached,
    )


async def _run_scenario_setup(
    community_id: str,
    rule_ids: list[str],
    scenario: ScenarioFile,
) -> None:
    """Background pipeline: context → triage → compile → evaluate queue.

    Errors at each stage are logged but don't abort downstream stages where
    sensible (e.g., a context-generation failure still lets us compile rules).
    """
    # ── Stage 1: load (or crawl) context samples and persist on the community ──
    try:
        sampled = await get_or_crawl_context(scenario.id, scenario.base_subreddit)
        async with AsyncSessionLocal() as db:
            comm = (await db.execute(
                select(Community).where(Community.id == community_id)
            )).scalar_one_or_none()
            if comm is not None:
                comm.context_samples = sampled
                await db.commit()
    except Exception as e:
        logger.error("Scenario %s: context load failed: %s", scenario.id, e)

    # ── Stage 2: generate community_context dimensions ─────────────────────────
    try:
        await _generate_context_for(community_id)
    except Exception as e:
        logger.error("Scenario %s: context generation failed: %s", scenario.id, e)

    # ── Stage 3: triage rules (concurrent) ─────────────────────────────────────
    try:
        await _triage_rules(community_id, rule_ids)
    except Exception as e:
        logger.error("Scenario %s: rule triage failed: %s", scenario.id, e)

    # ── Stage 4: compile actionable rules (concurrent LLM, serialized persist)
    try:
        await _compile_rules(community_id, rule_ids)
    except Exception as e:
        logger.error("Scenario %s: rule compilation failed: %s", scenario.id, e)

    # ── Stage 5: evaluate queue_posts ──────────────────────────────────────────
    try:
        await _evaluate_queue_posts(community_id, scenario)
    except Exception as e:
        logger.error("Scenario %s: queue evaluation failed: %s", scenario.id, e)

    logger.info("Scenario %s setup complete for community %s", scenario.id, community_id)


async def _generate_context_for(community_id: str) -> None:
    """Run the same compiler.generate_community_context the wizard's step 3 runs."""
    from ..compiler.compiler import RuleCompiler
    from ..db.models import CommunitySamplePost
    from .communities import _load_taxonomy

    compiler = RuleCompiler(get_anthropic_client(), settings)

    async with AsyncSessionLocal() as db:
        comm = (await db.execute(
            select(Community).where(Community.id == community_id)
        )).scalar_one_or_none()
        if comm is None:
            return

        rules_result = await db.execute(
            select(Rule).where(
                Rule.community_id == community_id,
                Rule.is_active == True,  # noqa: E712
            ).order_by(Rule.priority.asc())
        )
        active_rules = list(rules_result.scalars().all())
        rules_summary = "\n".join(f"- {r.title}: {r.text[:150]}" for r in active_rules)

        description = ""
        if comm.platform_config:
            description = comm.platform_config.get("public_description", "")

        sampled_posts = comm.context_samples
        committed_result = await db.execute(
            select(CommunitySamplePost).where(
                CommunitySamplePost.community_id == community_id,
                CommunitySamplePost.status == "committed",
            )
        )
        committed = list(committed_result.scalars().all())
        acceptable = [{"content": s.content, "note": s.note} for s in committed if s.label == "acceptable"]
        unacceptable = [{"content": s.content, "note": s.note} for s in committed if s.label == "unacceptable"]

    taxonomy = _load_taxonomy()

    context = await compiler.generate_community_context(
        community_name=comm.name,
        platform=comm.platform,
        description=description,
        rules_summary=rules_summary,
        subscribers=None,
        sampled_posts=sampled_posts,
        taxonomy=taxonomy,
        acceptable_samples=acceptable or None,
        unacceptable_samples=unacceptable or None,
    )

    async with AsyncSessionLocal() as db:
        comm = (await db.execute(
            select(Community).where(Community.id == community_id)
        )).scalar_one_or_none()
        if comm is None:
            return
        existing = comm.community_context or {}
        for dim in ("purpose", "participants", "stakes", "tone"):
            existing[dim] = context[dim]
        comm.community_context = existing
        comm.context_stale = False
        await db.commit()


async def _triage_rules(community_id: str, rule_ids: list[str]) -> None:
    from ..compiler.compiler import RuleCompiler
    from ..db.database import write_session

    compiler = RuleCompiler(get_anthropic_client(), settings)

    async with AsyncSessionLocal() as db:
        comm = (await db.execute(
            select(Community).where(Community.id == community_id)
        )).scalar_one_or_none()
        rules = list((await db.execute(
            select(Rule).where(Rule.id.in_(rule_ids))
        )).scalars().all())
        if not comm or not rules:
            return

    async def _triage_one(rule: Rule) -> tuple[str, dict | None, str | None]:
        try:
            result = await compiler.triage_rule(rule.title, rule.text, comm.name, comm.platform)
            return rule.id, result, None
        except Exception as e:
            logger.warning("Triage failed for rule %s: %s", rule.id, e)
            return rule.id, None, str(e)

    triaged = await asyncio.gather(*[_triage_one(r) for r in rules])

    async with write_session() as db:
        for rid, result, _err in triaged:
            if result is None:
                continue
            rule_obj = (await db.execute(
                select(Rule).where(Rule.id == rid)
            )).scalar_one_or_none()
            if rule_obj is None:
                continue
            rule_obj.rule_type = result["rule_type"]
            rule_obj.rule_type_reasoning = result.get("reasoning")
            rule_obj.applies_to = result.get("applies_to", "both")
        await db.commit()


async def _compile_rules(community_id: str, rule_ids: list[str]) -> None:
    """Mirror the rules-batch compile pipeline for actionable rules."""
    from .rules import (
        _compile_rule_persist,
        _compile_rule_read_and_llm,
        _set_compile_status,
    )

    async with AsyncSessionLocal() as db:
        actionable = list((await db.execute(
            select(Rule).where(
                Rule.id.in_(rule_ids),
                Rule.rule_type == "actionable",
            )
        )).scalars().all())
    actionable_ids = [r.id for r in actionable]
    if not actionable_ids:
        return

    for rid in actionable_ids:
        await _set_compile_status(rid, "pending", None)

    llm_results = await asyncio.gather(
        *[_compile_rule_read_and_llm(rid, community_id) for rid in actionable_ids],
        return_exceptions=True,
    )

    for rid, result in zip(actionable_ids, llm_results):
        if isinstance(result, Exception):
            logger.error("Compile LLM phase failed for %s: %s", rid, result)
            await _set_compile_status(rid, "failed", str(result))
            continue
        if result is None:
            await _set_compile_status(rid, "ok", None)
            continue
        try:
            await _compile_rule_persist(result)
            await _set_compile_status(rid, "ok", None)
        except Exception as e:
            await _set_compile_status(rid, "failed", str(e))


async def _wait_until_rules_compiled(
    community_id: str, timeout_seconds: float = 600.0, poll_seconds: float = 2.0
) -> bool:
    """Block until no actionable rule for this community has compile_status='pending'.

    Returns True if all rules settled before the timeout, False otherwise. We do
    not error on timeout — the caller proceeds and the few-rules-evaluated
    behavior re-emerges, but at least we tried.
    """
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while True:
        async with AsyncSessionLocal() as db:
            pending = (await db.execute(
                select(Rule.id).where(
                    Rule.community_id == community_id,
                    Rule.is_active == True,  # noqa: E712
                    Rule.rule_type == "actionable",
                    Rule.compile_status == "pending",
                )
            )).scalars().all()
        if not pending:
            return True
        if asyncio.get_event_loop().time() >= deadline:
            logger.warning(
                "Scenario import: %d actionable rule(s) still pending compile after %ss; "
                "proceeding anyway. Their checklists are empty so they will be skipped.",
                len(pending), timeout_seconds,
            )
            return False
        await asyncio.sleep(poll_seconds)


async def _evaluate_queue_posts(community_id: str, scenario: ScenarioFile) -> int:
    """Evaluate the next batch (≤ SCENARIO_IMPORT_BATCH) of unimported queue posts.

    Posts already represented by a Decision row (matched on post_platform_id)
    are skipped. Returns the number of posts evaluated this call.

    Waits for any in-flight rule compilation to finish first — otherwise rules
    with empty checklists are silently skipped during evaluation, leaving newly
    imported posts evaluated against an incomplete ruleset.
    """
    if not scenario.queue_posts:
        return 0

    await _wait_until_rules_compiled(community_id)

    all_posts = [qp.content.model_dump() for qp in scenario.queue_posts]
    all_ids = [p["id"] for p in all_posts if p.get("id")]

    async with AsyncSessionLocal() as db:
        existing = set(
            (await db.execute(
                select(Decision.post_platform_id).where(
                    Decision.community_id == community_id,
                    Decision.post_platform_id.in_(all_ids),
                )
            )).scalars().all()
        ) if all_ids else set()

    pending = [p for p in all_posts if p.get("id") not in existing]
    batch = pending[:SCENARIO_IMPORT_BATCH]
    if not batch:
        logger.info("Scenario %s: no more queue posts to import", scenario.id)
        return 0

    sem = asyncio.Semaphore(5)

    async def _eval_one(post_dict: dict) -> None:
        async with sem, AsyncSessionLocal() as session:
            eng = EvaluationEngine(
                db=session,
                client=get_anthropic_client(),
                settings=settings,
            )
            try:
                await eng.evaluate_post(community_id=community_id, post=post_dict)
            except Exception as e:
                logger.warning("Failed to evaluate scenario post %s: %s", post_dict.get("id"), e)

    await asyncio.gather(*[_eval_one(p) for p in batch], return_exceptions=True)
    logger.info(
        "Scenario %s: evaluated %d queue posts (%d remaining)",
        scenario.id, len(batch), len(pending) - len(batch),
    )
    return len(batch)


class ScenarioImportNextResponse(BaseModel):
    imported_count: int
    remaining_count: int
    total_count: int


async def _compute_scenario_import_state(
    community_id: str, db: AsyncSession
) -> tuple[ScenarioFile, int, int]:
    """Shared between the GET status endpoint and the POST import endpoint.

    Returns (scenario, pending_count, total_count). Raises HTTPException for
    invalid communities or missing scenario files.
    """
    comm = (await db.execute(
        select(Community).where(Community.id == community_id)
    )).scalar_one_or_none()
    if comm is None:
        raise HTTPException(status_code=404, detail="Community not found")
    if comm.platform != "hypothetical":
        raise HTTPException(status_code=422, detail="Only hypothetical communities support scenario imports")

    cfg = comm.platform_config or {}
    filename = cfg.get("scenario_filename")
    if not filename:
        raise HTTPException(status_code=422, detail="Community has no scenario_filename in platform_config")

    try:
        scenario = load_scenario(filename)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse scenario: {e}")

    all_posts = [qp.content.model_dump() for qp in scenario.queue_posts]
    all_ids = [p["id"] for p in all_posts if p.get("id")]
    existing = set(
        (await db.execute(
            select(Decision.post_platform_id).where(
                Decision.community_id == community_id,
                Decision.post_platform_id.in_(all_ids),
            )
        )).scalars().all()
    ) if all_ids else set()
    pending_count = sum(1 for p in all_posts if p.get("id") not in existing)
    return scenario, pending_count, len(all_posts)


@router.get(
    "/communities/{community_id}/scenario-import-status",
    response_model=ScenarioImportNextResponse,
)
async def scenario_import_status(
    community_id: str, db: AsyncSession = Depends(get_db),
) -> ScenarioImportNextResponse:
    """Read-only counts so the UI can disable Load-More once the queue is exhausted."""
    _, pending_count, total = await _compute_scenario_import_state(community_id, db)
    return ScenarioImportNextResponse(
        imported_count=0, remaining_count=pending_count, total_count=total,
    )


@router.post(
    "/communities/{community_id}/scenario-import-next",
    response_model=ScenarioImportNextResponse,
)
async def scenario_import_next(
    community_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ScenarioImportNextResponse:
    """Import the next SCENARIO_IMPORT_BATCH unimported queue_posts for this community.

    Schedules evaluation in the background and returns counts immediately so the
    UI can show progress as decisions appear. Only valid for hypothetical
    communities created via /communities/from-scenario.
    """
    comm = (await db.execute(
        select(Community).where(Community.id == community_id)
    )).scalar_one_or_none()
    if comm is None:
        raise HTTPException(status_code=404, detail="Community not found")
    if comm.platform != "hypothetical":
        raise HTTPException(status_code=422, detail="Only hypothetical communities support scenario imports")

    cfg = comm.platform_config or {}
    filename = cfg.get("scenario_filename")
    if not filename:
        raise HTTPException(status_code=422, detail="Community has no scenario_filename in platform_config")

    try:
        scenario = load_scenario(filename)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse scenario: {e}")

    # Compute counts up front for the response. The actual evaluation runs in
    # the background so the UI is not blocked.
    all_posts = [qp.content.model_dump() for qp in scenario.queue_posts]
    all_ids = [p["id"] for p in all_posts if p.get("id")]
    existing = set(
        (await db.execute(
            select(Decision.post_platform_id).where(
                Decision.community_id == community_id,
                Decision.post_platform_id.in_(all_ids),
            )
        )).scalars().all()
    ) if all_ids else set()

    pending = [p for p in all_posts if p.get("id") not in existing]
    to_import = min(len(pending), SCENARIO_IMPORT_BATCH)

    if to_import > 0:
        background_tasks.add_task(_evaluate_queue_posts, community_id, scenario)

    return ScenarioImportNextResponse(
        imported_count=to_import,
        remaining_count=max(0, len(pending) - to_import),
        total_count=len(all_posts),
    )
