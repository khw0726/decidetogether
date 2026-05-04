"""Scenario file discovery + context caching for hypothetical-community setup.

Scenarios live as JSON files under SCENARIOS_DIR. Each scenario's community
context is sampled from its `base_subreddit` ONCE and persisted under
CONTEXT_CACHE_DIR/<id>.json — repeat setup runs for the same scenario reuse
the cached sample so every study session sees identical context.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import settings
from ..models.scenario import ScenarioFile, ScenarioSummary
from .reddit_crawler import sample_subreddit_for_context

logger = logging.getLogger(__name__)

# Project root is three levels up from this file: src/automod/core/ -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = _REPO_ROOT / "scenarios"
CONTEXT_CACHE_DIR = _REPO_ROOT / "data" / "scenario_contexts"


def _context_cache_path(scenario_id: str) -> Path:
    safe = scenario_id.replace("/", "_").replace("..", "_")
    return CONTEXT_CACHE_DIR / f"{safe}.json"


def list_scenarios() -> list[ScenarioSummary]:
    """List every parseable scenario file under SCENARIOS_DIR."""
    if not SCENARIOS_DIR.exists():
        return []
    out: list[ScenarioSummary] = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            sc = ScenarioFile.model_validate(data)
        except Exception as e:
            logger.warning("Skipping malformed scenario file %s: %s", path.name, e)
            continue
        out.append(ScenarioSummary(
            id=sc.id,
            filename=path.name,
            community_name=sc.community.name,
            base_subreddit=sc.base_subreddit,
            rule_count=len(sc.rules),
            queue_post_count=len(sc.queue_posts),
            context_cached=_context_cache_path(sc.id).exists(),
        ))
    return out


def load_scenario(filename: str) -> ScenarioFile:
    """Load a scenario by filename (e.g., 'example_askscience.json')."""
    if "/" in filename or ".." in filename:
        raise ValueError("Invalid scenario filename")
    path = SCENARIOS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {filename}")
    data = json.loads(path.read_text())
    return ScenarioFile.model_validate(data)


async def get_or_crawl_context(scenario_id: str, base_subreddit: str) -> dict:
    """Return the cached context sample for `scenario_id`, crawling once if absent.

    Result shape matches `sample_subreddit_for_context`: a dict with keys
    hot/top/controversial/ignored/comments. Persisted as JSON keyed by id so
    every setup run for the same scenario sees identical context.
    """
    cache_path = _context_cache_path(scenario_id)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            logger.info("Loaded cached context for scenario %s", scenario_id)
            return cached
        except Exception as e:
            logger.warning("Cache for %s is corrupt, re-crawling: %s", scenario_id, e)

    if not settings.reddit_client_id:
        logger.warning("Reddit credentials not configured; returning empty context for %s", scenario_id)
        return {"hot": [], "top": [], "controversial": [], "ignored": [], "comments": []}

    sampled = await sample_subreddit_for_context(
        subreddit=base_subreddit,
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
        username=settings.reddit_username,
        password=settings.reddit_password,
    )
    CONTEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(sampled, indent=2))
    logger.info("Cached context for scenario %s (base r/%s)", scenario_id, base_subreddit)
    return sampled
