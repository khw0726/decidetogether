"""
Crawl rule titles + descriptions from Reddit for subreddits in the CSV.

For each subreddit, fetches current rules via PRAW and keeps rules that have
a substantive description (not just a restatement of the title).

Usage (from repo root):
    python scripts/crawl_descriptions.py --n 20 --seed 1
    python scripts/crawl_descriptions.py          # crawl all subreddits in CSV

Requires in .env:
    REDDIT_CLIENT_ID=...
    REDDIT_CLIENT_SECRET=...
    REDDIT_USER_AGENT=...  (optional, has default)

Output: scripts/rule_descriptions.json
  [
    {"subreddit": "MMA", "title": "Be Civil", "description": "A bit of banter..."},
    ...
  ]
"""

import argparse
import json
import logging
import os
import random
import sys
from pathlib import Path

import pandas as pd
import praw
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = Path("/hdd/khw/home/reddit_data/moderator_perceptions_public/data/rules_APR-2018-JUN-2024.csv")
OUTPUT_PATH = Path(__file__).parent / "rule_descriptions.json"

MIN_DESCRIPTION_LEN = 80  # chars — shorter descriptions add no signal


def load_subreddit_names(csv_path: Path) -> list[str]:
    df = pd.read_csv(csv_path, usecols=["subreddit"])
    return sorted(df["subreddit"].dropna().unique().tolist())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=None, help="Number of subreddits to sample (default: all)")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    load_dotenv()

    client_id = os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "automod-eval/0.1")

    if not client_id or not client_secret:
        logger.error("REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set in .env")
        sys.exit(1)

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )

    all_subreddits = load_subreddit_names(CSV_PATH)
    if args.n:
        rng = random.Random(args.seed)
        subreddits = rng.sample(all_subreddits, min(args.n, len(all_subreddits)))
        logger.info(f"Sampled {len(subreddits)} subreddits (seed={args.seed}) from {len(all_subreddits)} in CSV")
    else:
        subreddits = all_subreddits
        logger.info(f"Crawling all {len(subreddits)} subreddits in CSV")

    results = []
    for idx, sub_name in enumerate(subreddits, 1):
        try:
            sub = reddit.subreddit(sub_name)
            rules = list(sub.rules)
        except Exception as e:
            logger.warning(f"[{idx}/{len(subreddits)}] r/{sub_name}: {e}")
            continue

        kept = 0
        for r in rules:
            desc = (r.description or "").strip()
            if len(desc) >= MIN_DESCRIPTION_LEN:
                results.append({
                    "subreddit": sub_name,
                    "title": r.short_name,
                    "description": desc,
                })
                kept += 1

        logger.info(f"[{idx}/{len(subreddits)}] r/{sub_name}: {kept}/{len(rules)} rules with descriptions")

    OUTPUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    logger.info(f"\nDone. {len(results)} rules with descriptions across {len(subreddits)} subreddits.")
    logger.info(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
