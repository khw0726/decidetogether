"""
Sample N rules from rule_descriptions.json, compile each using title only,
and write results to compiler_test_sampled.json.

Usage (from repo root):
    python scripts/test_compiler_sampled.py
    python scripts/test_compiler_sampled.py --n 30 --seed 7

Output: scripts/compiler_test_sampled.json
  [
    {
      "subreddit": "MMA",
      "title": "Be Civil",
      "description": "...",   <- kept for evaluation, NOT fed to compiler
      "triage": {...},
      "checklist": [...],
      "examples": [...]
    },
    ...
  ]
"""

import argparse
import asyncio
import json
import logging
import random
import sys
import types
import uuid
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.compiler.compiler import RuleCompiler
from src.automod.config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DESCRIPTIONS_PATH = Path(__file__).parent / "rule_descriptions.json"
OUTPUT_PATH = Path(__file__).parent / "compiler_test_sampled.json"

DEFAULT_N = 30
DEFAULT_SEED = 1
DEFAULT_OUTPUT = Path(__file__).parent / "compiler_test_sampled.json"
AUGMENTED_OUTPUT = Path(__file__).parent / "compiler_test_sampled_augmented.json"


def make_rule(title: str, rule_type: str = "actionable") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=str(uuid.uuid4()),
        title=title[:60],
        text=title,
        rule_type=rule_type,
    )


def make_community(name: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(name=name, platform="reddit")


def item_to_dict(item) -> dict:
    return {
        "description": item.description,
        "item_type": item.item_type,
        "action": item.action,
        "rule_text_anchor": item.rule_text_anchor,
        "logic": item.logic,
        "children": [_raw_node_to_dict(c) for c in (getattr(item, "_children_data", None) or [])],
    }


def _raw_node_to_dict(raw: dict) -> dict:
    return {
        "description": raw.get("description", ""),
        "item_type": raw.get("item_type", "subjective"),
        "action": raw.get("action", "flag"),
        "rule_text_anchor": raw.get("rule_text_anchor"),
        "logic": raw.get("logic", {}),
        "children": [_raw_node_to_dict(c) for c in raw.get("children", [])],
    }


async def process_rule(
    compiler: RuleCompiler,
    entry: dict,
    idx: int,
    total: int,
    augmented: bool = False,
) -> dict:
    subreddit = entry["subreddit"]
    title = entry["title"]
    description = entry["description"]
    compiler_input = f"{title}\n\n{description}" if augmented else title

    logger.info(f"[{idx}/{total}] r/{subreddit}: {title[:60]!r}" + (" [+desc]" if augmented else ""))

    result = {
        "subreddit": subreddit,
        "title": title,
        "description": description,
        "augmented": augmented,
        "triage": None,
        "checklist": None,
        "examples": None,
    }

    try:
        triage = await compiler.triage_rule(compiler_input, subreddit, "reddit")
        result["triage"] = triage
    except Exception as e:
        logger.error(f"  Triage failed: {e}")
        result["triage"] = {"rule_type": "error", "reasoning": str(e)}
        return result

    if triage["rule_type"] == "actionable":
        try:
            rule_obj = make_rule(compiler_input, "actionable")
            community_obj = make_community(subreddit)
            checklist_items, examples = await compiler.compile_rule(
                rule_obj, community_obj, other_rules=[]
            )
            result["checklist"] = [item_to_dict(item) for item in checklist_items]
            result["examples"] = examples
        except Exception as e:
            logger.error(f"  Compile failed: {e}")
            result["checklist"] = [{"error": str(e)}]
            result["examples"] = []

    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="Number of rules to sample")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--augmented", action="store_true", help="Feed title + description to compiler")
    args = parser.parse_args()

    settings = Settings()
    if not settings.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY is not set.")
        sys.exit(1)

    if not DESCRIPTIONS_PATH.exists():
        logger.error(f"Not found: {DESCRIPTIONS_PATH}. Run crawl_descriptions.py first.")
        sys.exit(1)

    with open(DESCRIPTIONS_PATH) as f:
        all_rules: list[dict] = json.load(f)

    rng = random.Random(args.seed)
    sampled = rng.sample(all_rules, min(args.n, len(all_rules)))
    logger.info(f"Sampled {len(sampled)} rules (seed={args.seed}) from {len(all_rules)} available")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    compiler = RuleCompiler(client, settings)

    output_path = AUGMENTED_OUTPUT if args.augmented else DEFAULT_OUTPUT

    results = []
    for idx, entry in enumerate(sampled, 1):
        result = await process_rule(compiler, entry, idx, len(sampled), augmented=args.augmented)
        results.append(result)
        output_path.write_text(json.dumps(results, indent=2, default=str))

    logger.info(f"Done. Output: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
