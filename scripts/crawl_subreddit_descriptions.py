"""
Crawl subreddit descriptions + metadata for community context taxonomy discovery.

Collects diverse subreddits via popular listings + topic-based search, then
fetches full metadata (description, sidebar, rules, subscriber count, etc.)
for each. Output is a JSON array ready for LLooM concept induction.

Usage:
    python scripts/crawl_subreddit_descriptions.py
    python scripts/crawl_subreddit_descriptions.py --target 50 --skip-rules
    python scripts/crawl_subreddit_descriptions.py --output my_output.json

Requires: requests, tqdm (both in requirements.txt)
No Reddit API credentials needed — uses public JSON endpoints.
"""

import argparse
import json
import logging
import time
from pathlib import Path

import requests
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "automod-context-research/0.1 (taxonomy discovery)"}
BASE_URL = "https://www.reddit.com"
REQUEST_DELAY = 2.0  # seconds between requests (Reddit rate-limits unauthenticated at ~30/min)

# Fields to extract from each subreddit listing
EXTRACT_FIELDS = [
    "display_name", "title", "public_description", "description",
    "subscribers", "over18", "subreddit_type", "created_utc",
    "submit_text", "advertiser_category", "submission_type",
    "quarantine", "lang",
]

# Seed topics for diverse search coverage
SEARCH_TOPICS = [
    # Support / crisis
    "mental health", "addiction recovery", "grief support", "domestic violence",
    "suicide prevention", "disability",
    # Professional advice
    "legal advice", "tax help", "career advice", "medical questions",
    "personal finance", "relationship advice",
    # Hobbies / creative
    "woodworking", "painting art", "gardening", "photography",
    "knitting crochet", "cooking recipes", "homebrewing",
    # Gaming / entertainment
    "gaming", "board games", "movies", "anime", "music production",
    "book club", "television",
    # News / politics
    "politics", "news", "worldnews", "economics",
    # Local / geographic
    "city subreddit", "country subreddit", "local community",
    # Education / learning
    "homework help", "learn programming", "science", "history",
    "language learning", "ask experts",
    # Marketplace / trade
    "buy sell trade", "deals", "free stuff",
    # Identity / culture
    "parenting", "lgbtq", "women", "men", "teenagers",
    "religion", "atheism",
    # Fitness / health
    "fitness", "weight loss", "running", "yoga", "nutrition",
    # Technology
    "programming", "linux", "android", "apple", "cybersecurity",
    # Animals / nature
    "dogs", "cats", "aquariums", "birdwatching",
    # Meta / humor
    "memes", "shitposting", "copypasta", "askreddit",
    # NSFW (for over18 context)
    "nsfw", "gonewild",
    # Niche / small
    "obscure hobby", "niche community", "small subreddit",
]


def fetch_json(url: str, params: dict | None = None) -> dict | None:
    """Fetch a Reddit JSON endpoint with rate limiting."""
    time.sleep(REQUEST_DELAY)
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 10))
            logger.warning(f"Rate limited, waiting {retry_after}s")
            time.sleep(retry_after)
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code != 200:
            logger.debug(f"HTTP {resp.status_code} for {url}")
            return None
        return resp.json()
    except Exception as e:
        logger.debug(f"Request failed for {url}: {e}")
        return None


def extract_sub_data(child: dict) -> dict | None:
    """Extract relevant fields from a subreddit listing child."""
    data = child.get("data", {})
    if not data.get("display_name"):
        return None
    return {field: data.get(field) for field in EXTRACT_FIELDS}


def crawl_popular(n_pages: int = 10) -> list[dict]:
    """Crawl /subreddits/popular with pagination."""
    subs = []
    after = None
    for page in range(n_pages):
        params = {"limit": 100}
        if after:
            params["after"] = after
        data = fetch_json(f"{BASE_URL}/subreddits/popular.json", params)
        if not data or "data" not in data:
            break
        children = data["data"].get("children", [])
        if not children:
            break
        for child in children:
            sub = extract_sub_data(child)
            if sub:
                subs.append(sub)
        after = data["data"].get("after")
        if not after:
            break
        logger.info(f"Popular page {page + 1}: {len(children)} subs (total: {len(subs)})")
    return subs


def crawl_search(topics: list[str], pages_per_topic: int = 2) -> list[dict]:
    """Search subreddits by topic for diversity."""
    subs = []
    for topic in tqdm(topics, desc="Searching topics"):
        after = None
        for page in range(pages_per_topic):
            params = {"q": topic, "limit": 25, "sort": "relevance", "type": "sr"}
            if after:
                params["after"] = after
            data = fetch_json(f"{BASE_URL}/subreddits/search.json", params)
            if not data or "data" not in data:
                break
            children = data["data"].get("children", [])
            for child in children:
                sub = extract_sub_data(child)
                if sub:
                    subs.append(sub)
            after = data["data"].get("after")
            if not after:
                break
    return subs


def deduplicate(subs: list[dict]) -> list[dict]:
    """Deduplicate by lowercase display_name, keeping first occurrence."""
    seen = set()
    unique = []
    for sub in subs:
        key = sub["display_name"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(sub)
    return unique


def filter_subs(subs: list[dict]) -> list[dict]:
    """Filter out private, quarantined, and description-less subs."""
    filtered = []
    for sub in subs:
        if sub.get("subreddit_type") not in ("public", "restricted"):
            continue
        if sub.get("quarantine"):
            continue
        desc = (sub.get("description") or "").strip()
        pub_desc = (sub.get("public_description") or "").strip()
        if not desc and not pub_desc:
            continue
        filtered.append(sub)
    return filtered


def fetch_rules(sub_name: str) -> list[dict]:
    """Fetch rules for a single subreddit."""
    data = fetch_json(f"{BASE_URL}/r/{sub_name}/about/rules.json")
    if not data or "rules" not in data:
        return []
    return [
        {"short_name": r.get("short_name", ""), "description": r.get("description", "")}
        for r in data["rules"]
    ]


def enrich_with_rules(subs: list[dict]) -> list[dict]:
    """Add rules to each subreddit entry."""
    for sub in tqdm(subs, desc="Fetching rules"):
        sub["rules"] = fetch_rules(sub["display_name"])
    return subs


def save_checkpoint(subs: list[dict], output_path: Path):
    """Save intermediate results."""
    output_path.write_text(json.dumps(subs, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Crawl subreddit descriptions for taxonomy discovery")
    parser.add_argument("--output", type=str, default="scripts/subreddit_descriptions.json")
    parser.add_argument("--target", type=int, default=1000, help="Target number of unique subs")
    parser.add_argument("--skip-rules", action="store_true", help="Skip per-sub rules fetch")
    args = parser.parse_args()

    output_path = Path(args.output)

    # Phase 1: Crawl popular subs
    logger.info("Phase 1: Crawling popular subreddits...")
    popular_subs = crawl_popular(n_pages=10)
    logger.info(f"Got {len(popular_subs)} from popular listings")

    all_subs = deduplicate(popular_subs)
    all_subs = filter_subs(all_subs)
    logger.info(f"After dedup+filter: {len(all_subs)} from popular")

    # Phase 2: Search by topic for diversity (only if we need more)
    if len(all_subs) < args.target:
        logger.info(f"Phase 2: Searching by topic (need {args.target - len(all_subs)} more)...")
        search_subs = crawl_search(SEARCH_TOPICS)
        logger.info(f"Got {len(search_subs)} from topic searches")
        combined = all_subs + search_subs
        all_subs = deduplicate(combined)
        all_subs = filter_subs(all_subs)
        logger.info(f"After dedup+filter: {len(all_subs)} total")
    else:
        logger.info("Skipping topic search — popular already meets target")

    # Trim to target
    if len(all_subs) > args.target:
        all_subs = all_subs[:args.target]
        logger.info(f"Trimmed to target: {args.target}")

    # Phase 3: Enrich with rules
    if not args.skip_rules:
        logger.info("Phase 3: Fetching rules for each subreddit...")
        # Save checkpoint before the slow rules fetch
        save_checkpoint(all_subs, output_path)
        logger.info(f"Checkpoint saved to {output_path}")
        all_subs = enrich_with_rules(all_subs)
    else:
        for sub in all_subs:
            sub["rules"] = []
        logger.info("Skipping rules fetch")

    # Rename display_name -> name for cleaner output
    for sub in all_subs:
        sub["name"] = sub.pop("display_name")

    # Save final output
    save_checkpoint(all_subs, output_path)

    # Summary stats
    with_desc = sum(1 for s in all_subs if (s.get("description") or "").strip())
    with_rules = sum(1 for s in all_subs if s.get("rules"))
    over18 = sum(1 for s in all_subs if s.get("over18"))
    sub_counts = [s.get("subscribers", 0) or 0 for s in all_subs]
    logger.info(
        f"\nDone. {len(all_subs)} subreddits saved to {output_path}\n"
        f"  With sidebar description: {with_desc}\n"
        f"  With rules: {with_rules}\n"
        f"  Over 18: {over18}\n"
        f"  Subscriber range: {min(sub_counts):,} – {max(sub_counts):,}"
    )


if __name__ == "__main__":
    main()
