"""
Cluster extracted community context tags into a clean taxonomy.

Two-pass approach:
1. Send frequent tags (count >= 2) to LLM, ask it to group into canonical categories
2. Map all original tags to canonical categories

Usage:
    python scripts/cluster_context_tags.py
    python scripts/cluster_context_tags.py --input scripts/community_contexts_extracted.jsonl

Output:
    scripts/context_taxonomy.json        — the taxonomy (categories + member tags)
    scripts/context_tag_mapping.json     — full tag → canonical category mapping
    scripts/community_contexts_clustered.jsonl — original data with tags replaced by canonical categories
"""

import argparse
import asyncio
import json
import logging
import collections
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.automod.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DIMENSIONS = ["purpose", "participants", "stakes", "tone"]

# Step 1: Discover canonical categories from top tags
DISCOVER_PROMPT = """\
You are building a taxonomy for community context analysis. Below are the most common tags \
extracted from ~1000 subreddit descriptions for the "{dimension}" dimension, with frequency counts.

Your job: define 10-25 canonical categories that cover these tags. Each category should be:
- Distinct (minimal overlap with other categories)
- Meaningful for moderation calibration
- Named with a clear lowercase_with_underscores identifier

Top tags (tag: count):
{tags_with_counts}

Respond with a JSON object mapping each canonical category name to a one-line description.
Include an "other" category for anything that doesn't fit.

Example format:
{{"category_name": "description of what this covers", ...}}

JSON:"""

# Step 2: Map tags to discovered categories
MAP_PROMPT = """\
You have canonical categories for the "{dimension}" dimension of community context:

{categories_desc}

Map each tag below to the single best-fitting category. Respond with a JSON object: {{"tag": "category", ...}}
Only use category names from the list above.

Tags:
{tags_to_map}

JSON:"""


def collect_tags(data: list[dict], dimension: str) -> collections.Counter:
    counter = collections.Counter()
    for d in data:
        counter.update(d["extracted"][dimension]["tags"])
    return counter


def parse_json_response(text: str) -> dict:
    """Robustly parse JSON from LLM response."""
    # Try direct parse first
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError(f"No JSON object found in response")
    candidate = text[start:end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Try fixing common issues: trailing commas
        import re
        fixed = re.sub(r',\s*}', '}', candidate)
        fixed = re.sub(r',\s*]', ']', fixed)
        return json.loads(fixed)


async def discover_categories(
    client: anthropic.AsyncAnthropicBedrock,
    model: str,
    dimension: str,
    tag_counts: dict[str, int],
) -> dict:
    """Send top tags to LLM, get canonical category definitions."""
    # Only send top 150 tags — enough to discover categories
    sorted_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:150]
    tags_text = "\n".join(f"  {tag}: {count}" for tag, count in sorted_tags)

    prompt = DISCOVER_PROMPT.format(dimension=dimension, tags_with_counts=tags_text)

    resp = await client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return parse_json_response(resp.content[0].text)


async def map_long_tail(
    client: anthropic.AsyncAnthropicBedrock,
    model: str,
    dimension: str,
    categories: dict,
    tags_to_map: list[str],
    batch_size: int = 200,
) -> dict[str, str]:
    """Map long-tail tags to canonical categories in batches."""
    categories_desc = "\n".join(
        f"  {name}: {info['description']}"
        for name, info in categories.items()
    )

    mapping = {}
    for i in range(0, len(tags_to_map), batch_size):
        batch = tags_to_map[i:i + batch_size]
        tags_text = "\n".join(f"  {tag}" for tag in batch)

        prompt = MAP_PROMPT.format(
            dimension=dimension,
            categories_desc=categories_desc,
            tags_to_map=tags_text,
        )

        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            batch_mapping = parse_json_response(resp.content[0].text)
            mapping.update(batch_mapping)
            logger.info(f"  Mapped batch {i//batch_size + 1}: {len(batch_mapping)} tags")
        except Exception as e:
            logger.warning(f"  Batch {i//batch_size + 1} failed: {e}, assigning 'other'"  )
            for tag in batch:
                mapping[tag] = "other"

    return mapping


async def process_dimension(
    client: anthropic.AsyncAnthropicBedrock,
    model: str,
    dimension: str,
    tag_counter: collections.Counter,
) -> tuple[dict, dict[str, str]]:
    """Full pipeline for one dimension: discover categories, then map all tags."""
    logger.info(f"\n{'='*60}")
    logger.info(f"Processing dimension: {dimension.upper()}")
    logger.info(f"{'='*60}")

    # Step 1: Discover categories from top tags
    frequent = {tag: count for tag, count in tag_counter.items() if count >= 2}
    logger.info(f"  {len(frequent)} frequent tags (count>=2), {len(tag_counter)} total unique")
    logger.info("  Step 1: Discovering canonical categories from top tags...")
    categories = await discover_categories(client, model, dimension, frequent)
    logger.info(f"  Found {len(categories)} categories")

    # Step 2: Map ALL tags to categories (in batches)
    all_tags = list(tag_counter.keys())
    # Wrap categories for the map prompt: categories is {name: description}
    categories_for_map = {name: {"description": desc} for name, desc in categories.items()}
    logger.info(f"  Step 2: Mapping all {len(all_tags)} tags to categories...")
    tag_mapping = await map_long_tail(
        client, model, dimension, categories_for_map, all_tags
    )

    mapped_count = len(tag_mapping)
    logger.info(f"  Mapped {mapped_count}/{len(all_tags)} tags to {len(categories)} categories")

    return categories, tag_mapping


async def main():
    parser = argparse.ArgumentParser(description="Cluster context tags into taxonomy")
    parser.add_argument("--input", type=str, default="scripts/community_contexts_extracted.jsonl")
    parser.add_argument("--model", type=str, default=settings.sonnet_model,
                        help="Model for clustering (default: Sonnet for quality)")
    args = parser.parse_args()

    load_dotenv()

    data = [json.loads(line) for line in open(args.input)]
    logger.info(f"Loaded {len(data)} extractions")

    client = anthropic.AsyncAnthropicBedrock(
        aws_access_key=settings.aws_access_key,
        aws_secret_key=settings.aws_secret_key,
        aws_region=settings.aws_region,
    )

    taxonomy = {}
    full_mapping = {}

    for dim in DIMENSIONS:
        tag_counter = collect_tags(data, dim)
        categories, tag_mapping = await process_dimension(
            client, args.model, dim, tag_counter
        )
        # categories is {name: description_string}
        # Collect example tags from the mapping
        cat_examples = collections.defaultdict(list)
        for tag, cat in tag_mapping.items():
            if len(cat_examples[cat]) < 10:
                cat_examples[cat].append(tag)
        taxonomy[dim] = {
            name: {
                "description": desc,
                "example_tags": cat_examples.get(name, []),
            }
            for name, desc in categories.items()
        }
        full_mapping[dim] = tag_mapping

    # Save taxonomy
    taxonomy_path = Path("scripts/context_taxonomy.json")
    taxonomy_path.write_text(json.dumps(taxonomy, indent=2, ensure_ascii=False))
    logger.info(f"\nTaxonomy saved to {taxonomy_path}")

    # Save full mapping
    mapping_path = Path("scripts/context_tag_mapping.json")
    mapping_path.write_text(json.dumps(full_mapping, indent=2, ensure_ascii=False))
    logger.info(f"Tag mapping saved to {mapping_path}")

    # Rewrite data with canonical tags
    clustered_path = Path("scripts/community_contexts_clustered.jsonl")
    with open(clustered_path, "w") as f:
        for d in data:
            clustered = dict(d)
            clustered["clustered"] = {}
            for dim in DIMENSIONS:
                orig_tags = d["extracted"][dim]["tags"]
                canonical = [full_mapping[dim].get(t, "other") for t in orig_tags]
                # Deduplicate while preserving order
                seen = set()
                deduped = []
                for c in canonical:
                    if c not in seen:
                        seen.add(c)
                        deduped.append(c)
                clustered["clustered"][dim] = deduped
            f.write(json.dumps(clustered, ensure_ascii=False) + "\n")
    logger.info(f"Clustered data saved to {clustered_path}")

    # Print summary
    print("\n" + "=" * 60)
    print("TAXONOMY SUMMARY")
    print("=" * 60)
    for dim in DIMENSIONS:
        cats = taxonomy[dim]
        print(f"\n{dim.upper()} ({len(cats)} categories):")
        for name, info in cats.items():
            print(f"  {name}: {info['description']}")


if __name__ == "__main__":
    asyncio.run(main())
