"""Re-crawl scenario context caches so each comment carries its post_title.

Backs up the existing cache to <name>.bak.json and overwrites with a fresh sample.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from automod.config import settings
from automod.core.reddit_crawler import sample_subreddit_for_context
from automod.core.scenario_loader import (
    CONTEXT_CACHE_DIR,
    list_scenarios,
    load_scenario,
    _context_cache_path,
)


async def recrawl_one(scenario_id: str, base_subreddit: str) -> None:
    cache_path = _context_cache_path(scenario_id)
    if cache_path.exists():
        backup = cache_path.with_suffix(".bak.json")
        shutil.copy2(cache_path, backup)
        print(f"  backed up → {backup.name}")

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
    print(f"  wrote {len(sampled.get('comments', []))} comments")


async def main() -> None:
    if not settings.reddit_client_id:
        print("ERROR: reddit_client_id not set in .env")
        sys.exit(1)

    scenarios = list_scenarios()
    target_ids = sys.argv[1:] or [s.id for s in scenarios]

    for s in scenarios:
        if s.id not in target_ids:
            continue
        print(f"[{s.id}] re-crawling r/{s.base_subreddit}")
        try:
            await recrawl_one(s.id, s.base_subreddit)
        except Exception as e:
            print(f"  FAILED: {e}")


if __name__ == "__main__":
    asyncio.run(main())
