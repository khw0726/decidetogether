"""
Standalone compiler test: sample 20 subreddits from the historical rules CSV,
run triage + compile on each rule, and write results to compiler_test_output.json.

Usage (from repo root):
    python scripts/test_compiler.py

Output: scripts/compiler_test_output.json
"""

import asyncio
import json
import logging
import random
import sys
import types
import uuid
from pathlib import Path

import pandas as pd

# Make sure src/ is on the path when running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.compiler.compiler import RuleCompiler
from src.automod.config import Settings

import anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = Path("/hdd/khw/home/reddit_data/moderator_perceptions_public/data/rules_APR-2018-JUN-2024.csv")
OUTPUT_PATH = Path(__file__).parent / "compiler_test_output.json"

SAMPLE_SIZE = 20
RANDOM_SEED = 42
MIN_RULES = 2
MAX_RULES = 12
LATEST_WINDOW_DAYS = 30
LATEST_WINDOW_FALLBACK_DAYS = 90


def load_latest_rulesets(csv_path: Path) -> dict[str, list[str]]:
    """Return {subreddit: [rule_text, ...]} for each subreddit's latest ruleset."""
    df = pd.read_csv(csv_path, parse_dates=["latest_start", "earliest_start", "latest_end", "earliest_end"])

    # Drop rows flagged as "Not a Rule"
    df = df[df["Not a Rule"] != True]  # noqa: E712

    # Drop rows with no rule text
    df = df.dropna(subset=["Rule Text"])
    df["Rule Text"] = df["Rule Text"].str.strip()
    df = df[df["Rule Text"] != ""]

    rulesets: dict[str, list[str]] = {}

    for subreddit, group in df.groupby("subreddit"):
        max_start = group["latest_start"].max()
        window = pd.Timedelta(days=LATEST_WINDOW_DAYS)
        recent = group[group["latest_start"] >= (max_start - window)]

        # Broaden window if we got too few rules
        if len(recent) < MIN_RULES:
            window = pd.Timedelta(days=LATEST_WINDOW_FALLBACK_DAYS)
            recent = group[group["latest_start"] >= (max_start - window)]

        rules = recent["Rule Text"].dropna().unique().tolist()
        if MIN_RULES <= len(rules) <= MAX_RULES:
            rulesets[subreddit] = rules

    return rulesets


def sample_subreddits(rulesets: dict[str, list[str]], n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    candidates = sorted(rulesets.keys())
    return rng.sample(candidates, min(n, len(candidates)))


def make_rule(rule_text: str, rule_type: str = "actionable") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=str(uuid.uuid4()),
        title=rule_text[:60],
        text=rule_text,
        rule_type=rule_type,
    )


def make_community(name: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(name=name, platform="reddit")


def item_to_dict(item) -> dict:
    """Serialize a ChecklistItem ORM object (with _children_data) to a plain dict."""
    return {
        "description": item.description,
        "item_type": item.item_type,
        "action": item.action,
        "rule_text_anchor": item.rule_text_anchor,
        "logic": item.logic,
        "children": [_raw_node_to_dict(c) for c in (getattr(item, "_children_data", None) or [])],
    }


def _raw_node_to_dict(raw: dict) -> dict:
    """Recursively convert a raw compiler-output node to a plain dict."""
    return {
        "description": raw.get("description", ""),
        "item_type": raw.get("item_type", "subjective"),
        "action": raw.get("action", "flag"),
        "rule_text_anchor": raw.get("rule_text_anchor"),
        "logic": raw.get("logic", {}),
        "children": [_raw_node_to_dict(c) for c in raw.get("children", [])],
    }


async def process_subreddit(
    compiler: RuleCompiler,
    subreddit: str,
    rules: list[str],
    idx: int,
    total: int,
) -> dict:
    logger.info(f"[{idx}/{total}] Processing r/{subreddit} ({len(rules)} rules)")
    community = make_community(subreddit)
    result_rules = []

    for rule_idx, rule_text in enumerate(rules):
        logger.info(f"  Rule {rule_idx + 1}/{len(rules)}: {rule_text[:60]!r}")

        rule_entry: dict = {"rule_text": rule_text, "triage": None, "checklist": None, "examples": None}

        try:
            triage = await compiler.triage_rule(rule_text, subreddit, "reddit")
            rule_entry["triage"] = triage
        except Exception as e:
            logger.error(f"  Triage failed: {e}")
            rule_entry["triage"] = {"rule_type": "error", "reasoning": str(e)}
            result_rules.append(rule_entry)
            continue

        if triage["rule_type"] == "actionable":
            try:
                rule_obj = make_rule(rule_text, "actionable")
                # Build sibling rules as context (already-triaged ones where possible)
                other_rule_objs = [
                    make_rule(rt, "actionable")
                    for rt in rules
                    if rt != rule_text
                ]
                checklist_items, examples = await compiler.compile_rule(
                    rule_obj, community, other_rule_objs
                )
                rule_entry["checklist"] = [item_to_dict(item) for item in checklist_items]
                rule_entry["examples"] = examples
            except Exception as e:
                logger.error(f"  Compile failed: {e}")
                rule_entry["checklist"] = [{"error": str(e)}]
                rule_entry["examples"] = []

        result_rules.append(rule_entry)

    return {
        "subreddit": subreddit,
        "rule_count": len(rules),
        "rules": result_rules,
    }


async def main():
    settings = Settings()
    if not settings.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY is not set. Check your .env file.")
        sys.exit(1)

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    compiler = RuleCompiler(client, settings)

    logger.info(f"Loading CSV from {CSV_PATH}")
    rulesets = load_latest_rulesets(CSV_PATH)
    logger.info(f"Found {len(rulesets)} eligible subreddits (rules {MIN_RULES}–{MAX_RULES})")

    sampled = sample_subreddits(rulesets, SAMPLE_SIZE, RANDOM_SEED)
    logger.info(f"Sampled {len(sampled)} subreddits: {sampled}")

    results = []
    for idx, subreddit in enumerate(sampled, 1):
        entry = await process_subreddit(compiler, subreddit, rulesets[subreddit], idx, len(sampled))
        results.append(entry)

        # Write incrementally so partial results are saved on interruption
        OUTPUT_PATH.write_text(json.dumps(results, indent=2, default=str))
        logger.info(f"  Saved progress to {OUTPUT_PATH}")

    logger.info(f"Done. Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
