"""
Attribute moderated comments to specific rule(s) using rule text alone.

For each ModBench entry with ground_truth_verdict="remove", ask an LLM
which rule(s) the comment most plausibly violates. The LLM is told the
comment was definitely removed, so at least one rule applies. Rules
are identified by their rule_text key (first 60 chars of rule_text),
matching the same key format used elsewhere in the pipeline.

Output: an augmented ModBench JSON where "remove" entries gain a
  `violated_rules` field — a list of rule_text_keys the LLM attributed.

Usage:
    python scripts/attribute_violations.py \\
        --modbench scripts/modbench_eval5.json \\
        --rules scripts/modbench_rules_2016_2017.json \\
        --output scripts/modbench_eval5_attributed.json
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.config import Settings
from scripts.evaluate_output import _make_anthropic_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent
MAX_CONCURRENT = 20


_ATTRIBUTE_TOOL = {
    "name": "submit_attribution",
    "description": "Submit the rule indices that the comment violates",
    "input_schema": {
        "type": "object",
        "properties": {
            "violated_rule_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "1-based indices of the rules (from the provided list) that this comment violates. Include ALL rules that plausibly apply, in decreasing order of relevance. At least one index is required since the comment was removed by moderators.",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of why the selected rules apply.",
            },
        },
        "required": ["violated_rule_indices", "reasoning"],
    },
}

_SYSTEM = """You are an expert content moderator classifying which community rule(s) a removed comment violates.

Context: The comment was removed by moderators, so you can assume at least one rule was violated. Your task is to identify which specific rule(s) most plausibly apply.

Rules for attribution:
- Consider ONLY the provided rules (do not invent new ones).
- Return 1-based indices into the rules list.
- A comment can violate multiple rules simultaneously — return all that apply.
- Return the most-likely rule first, then additional rules in decreasing relevance.
- If uncertain, prefer specific content rules (e.g. "No personal attacks") over generic ones (e.g. "Be civil") when both could apply.
- If no rule obviously applies, return the best guess anyway (the comment WAS removed)."""


def _build_prompt(subreddit: str, rules: list[dict], post: dict) -> str:
    rules_text = ""
    for i, r in enumerate(rules, 1):
        title = r.get("title", "")
        desc = r.get("description", "")
        line = f"{i}. {title}"
        if desc.strip():
            line += f"\n   {desc[:300]}"
        rules_text += line + "\n"

    content = post.get("content", {})
    title = content.get("title", "")
    body = content.get("body", "")
    author = post.get("author", {})
    username = author.get("username", "unknown") if isinstance(author, dict) else "unknown"

    post_block = f"Author: u/{username}\n"
    if title:
        post_block += f"Title: {title}\n"
    if body:
        post_block += f"Body: {body}\n"

    return f"""## Community: r/{subreddit}

## Rules
{rules_text}
## Removed comment
{post_block}

Which rule(s) does this comment most plausibly violate? Return rule indices (1-based) via the submit_attribution tool."""


async def attribute_one(
    client, model: str,
    mb_entry: dict,
    rules: list[dict],
    semaphore: asyncio.Semaphore,
) -> dict:
    """Attribute one modbench entry to rule(s)."""
    async with semaphore:
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=512,
                system=_SYSTEM,
                messages=[{"role": "user", "content": _build_prompt(
                    mb_entry["subreddit"], rules, mb_entry["post"]
                )}],
                tools=[_ATTRIBUTE_TOOL],
                tool_choice={"type": "tool", "name": "submit_attribution"},
            )
            result = response.content[0].input
            indices = result.get("violated_rule_indices", [])
            # Convert 1-based indices to rule_text_keys
            violated_keys = []
            for idx in indices:
                if 1 <= idx <= len(rules):
                    r = rules[idx - 1]
                    rule_text = r.get("title", "")
                    desc = r.get("description", "")
                    if desc.strip():
                        rule_text = f"{rule_text}\n\n{desc}"
                    violated_keys.append(rule_text[:60])  # matches index key format
            return {
                "id": mb_entry["id"],
                "violated_rules": violated_keys,
                "violated_rule_indices": indices,
                "reasoning": result.get("reasoning", ""),
            }
        except Exception as e:
            logger.error(f"Attribution failed for {mb_entry['id']}: {e}")
            return {
                "id": mb_entry["id"],
                "violated_rules": [],
                "violated_rule_indices": [],
                "reasoning": f"ERROR: {e}",
            }


async def main():
    parser = argparse.ArgumentParser(description="Attribute moderated comments to rules")
    parser.add_argument("--modbench", type=Path, required=True)
    parser.add_argument("--rules", type=Path, default=SCRIPTS_DIR / "modbench_rules_2016_2017.json",
                        help="Rules JSON file (subreddit → rules list)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None, help="Process first N entries only")
    args = parser.parse_args()

    client, model = _make_anthropic_client()
    if "bedrock" in type(client).__name__.lower():
        model = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

    with open(args.modbench) as f:
        modbench = json.load(f)
    with open(args.rules) as f:
        all_rules = json.load(f)

    if args.limit:
        modbench = modbench[:args.limit]

    # Find entries to attribute: only "remove" ground truth
    to_attribute = [
        (i, mb) for i, mb in enumerate(modbench)
        if mb.get("ground_truth_verdict") == "remove"
    ]
    logger.info(f"{len(to_attribute)} removed entries to attribute (of {len(modbench)} total)")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = []
    for idx, mb in to_attribute:
        sub = mb["subreddit"]
        rules = all_rules.get(sub, [])
        if not rules:
            continue
        tasks.append(attribute_one(client, model, mb, rules, semaphore))

    results = await asyncio.gather(*tasks)

    # Index results by modbench id
    attribution_by_id = {r["id"]: r for r in results}

    # Augment modbench entries
    for mb in modbench:
        if mb.get("ground_truth_verdict") == "remove":
            attrib = attribution_by_id.get(mb["id"])
            if attrib:
                mb["violated_rules"] = attrib["violated_rules"]
                mb["attribution_reasoning"] = attrib["reasoning"]

    with open(args.output, "w") as f:
        json.dump(modbench, f, indent=2)

    # Print summary
    total_removed = sum(1 for mb in modbench if mb.get("ground_truth_verdict") == "remove")
    attributed = sum(1 for mb in modbench if mb.get("violated_rules"))
    avg_rules_per_entry = (
        sum(len(mb.get("violated_rules", [])) for mb in modbench if mb.get("violated_rules"))
        / attributed if attributed else 0
    )

    print(f"\n{'='*60}")
    print(f"Violation Attribution Summary")
    print(f"{'='*60}")
    print(f"Total removed entries: {total_removed}")
    print(f"Successfully attributed: {attributed}")
    print(f"Avg rules per attributed entry: {avg_rules_per_entry:.2f}")
    print(f"\nWrote augmented modbench to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
