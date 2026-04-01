"""
Augmented compiler test: re-run compilation on the same 20 subreddits but with
rule_text = title + description (where a description was crawled and is substantive).

Reads rule_descriptions.json (from crawl_descriptions.py) and the same CSV used
by test_compiler.py. Writes compiler_test_output_augmented.json.

Usage (from repo root):
    python scripts/test_compiler_augmented.py

Output: scripts/compiler_test_output_augmented.json
"""

import asyncio
import json
import logging
import sys
import types
import uuid
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))  # make scripts/ importable

from src.automod.compiler.compiler import RuleCompiler
from src.automod.config import Settings

# Reuse helpers from test_compiler
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("test_compiler", Path(__file__).parent / "test_compiler.py")
_tc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_tc)

load_latest_rulesets = _tc.load_latest_rulesets
sample_subreddits = _tc.sample_subreddits
item_to_dict = _tc.item_to_dict
CSV_PATH = _tc.CSV_PATH
SAMPLE_SIZE = _tc.SAMPLE_SIZE
RANDOM_SEED = _tc.RANDOM_SEED
MIN_RULES = _tc.MIN_RULES
MAX_RULES = _tc.MAX_RULES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DESCRIPTIONS_PATH = Path(__file__).parent / "rule_descriptions.json"
OUTPUT_PATH = Path(__file__).parent / "compiler_test_output_augmented.json"


def make_rule(rule_text: str, rule_type: str = "actionable") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=str(uuid.uuid4()),
        title=rule_text[:60],
        text=rule_text,
        rule_type=rule_type,
    )


def make_community(name: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(name=name, platform="reddit")


def build_augmented_rule_text(csv_title: str, descriptions: list[dict]) -> str:
    """Return title + description if a substantive description was found, else just title."""
    for entry in descriptions:
        if entry["csv_title"] == csv_title and entry.get("matched") and entry.get("description"):
            return f"{csv_title}\n\n{entry['description']}"
    return csv_title


async def process_subreddit(
    compiler: RuleCompiler,
    subreddit: str,
    rules: list[str],
    descriptions: list[dict],
    idx: int,
    total: int,
) -> dict:
    logger.info(f"[{idx}/{total}] Processing r/{subreddit} ({len(rules)} rules)")
    community = make_community(subreddit)
    result_rules = []

    for rule_idx, rule_text in enumerate(rules):
        augmented_text = build_augmented_rule_text(rule_text, descriptions)
        augmented = augmented_text != rule_text
        logger.info(
            f"  Rule {rule_idx + 1}/{len(rules)}: {rule_text[:50]!r}"
            + (" [+description]" if augmented else " [title-only]")
        )

        rule_entry: dict = {
            "rule_text": rule_text,
            "augmented": augmented,
            "triage": None,
            "checklist": None,
            "examples": None,
        }

        try:
            triage = await compiler.triage_rule(augmented_text, subreddit, "reddit")
            rule_entry["triage"] = triage
        except Exception as e:
            logger.error(f"  Triage failed: {e}")
            rule_entry["triage"] = {"rule_type": "error", "reasoning": str(e)}
            result_rules.append(rule_entry)
            continue

        if triage["rule_type"] == "actionable":
            try:
                rule_obj = make_rule(augmented_text, "actionable")
                other_rule_objs = [
                    make_rule(build_augmented_rule_text(rt, descriptions), "actionable")
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

    if not DESCRIPTIONS_PATH.exists():
        logger.error(f"Descriptions file not found: {DESCRIPTIONS_PATH}")
        logger.error("Run scripts/crawl_descriptions.py first.")
        sys.exit(1)

    with open(DESCRIPTIONS_PATH) as f:
        all_descriptions: dict[str, list[dict]] = json.load(f)

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    compiler = RuleCompiler(client, settings)

    logger.info(f"Loading CSV from {CSV_PATH}")
    rulesets = load_latest_rulesets(CSV_PATH)
    logger.info(f"Found {len(rulesets)} eligible subreddits")

    sampled = sample_subreddits(rulesets, SAMPLE_SIZE, RANDOM_SEED)
    logger.info(f"Sampled {len(sampled)} subreddits: {sampled}")

    results = []
    for idx, subreddit in enumerate(sampled, 1):
        descriptions = all_descriptions.get(subreddit, [])
        entry = await process_subreddit(
            compiler, subreddit, rulesets[subreddit], descriptions, idx, len(sampled)
        )
        results.append(entry)
        OUTPUT_PATH.write_text(json.dumps(results, indent=2, default=str))
        logger.info(f"  Saved progress to {OUTPUT_PATH}")

    logger.info(f"Done. Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
