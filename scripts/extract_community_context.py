"""
Extract community context (purpose, participants, stakes, tone) from crawled
subreddit descriptions using an LLM.

Reads scripts/subreddit_descriptions.json, calls Haiku for each subreddit,
outputs structured context extractions as JSONL (one per line, streamable).

Usage:
    python scripts/extract_community_context.py
    python scripts/extract_community_context.py --limit 50
    python scripts/extract_community_context.py --resume  # skip already-extracted subs
    python scripts/extract_community_context.py --model global.anthropic.claude-sonnet-4-6

Requires AWS_ACCESS_KEY, AWS_SECRET_KEY, AWS_REGION in .env (uses Bedrock).
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from tqdm.asyncio import tqdm_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.automod.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

INPUT_PATH = Path("scripts/subreddit_descriptions.json")
OUTPUT_PATH = Path("scripts/community_contexts_extracted.jsonl")

EXTRACT_PROMPT = """\
You are analyzing a subreddit to extract structured community context. Given the subreddit's metadata below, extract the following dimensions.

SUBREDDIT: r/{name}
TITLE: {title}
SUBSCRIBERS: {subscribers:,}
PUBLIC DESCRIPTION: {public_description}

SIDEBAR/DESCRIPTION:
{description}

RULES:
{rules_text}

---

Extract the following. For each dimension, write:
1. A short prose summary (2-3 sentences, specific to this community)
2. A set of categorical tags (3-5 short tags per dimension, lowercase_with_underscores)

Dimensions:

PURPOSE: What is this space for? What do people come here to do? What kinds of interactions happen?
- Tags should capture: primary function, interaction mode, content type, content actionability

PARTICIPANTS: Who is here? What expertise levels exist? Any vulnerability factors?
- Tags should capture: audience type, expertise asymmetry, vulnerability factors, age demographics

STAKES: What could go wrong with harmful/bad content? What could go wrong with over-moderation?
- Tags should capture: harm types from bad content, harm types from over-moderation, content permanence

TONE: What is the communication style? How formal/informal? What kind of language is typical?
- Tags should capture: formality level, emotional register, humor presence, conflict level

Respond in this exact JSON format:
{{
  "purpose": {{
    "prose": "...",
    "tags": ["...", "..."]
  }},
  "participants": {{
    "prose": "...",
    "tags": ["...", "..."]
  }},
  "stakes": {{
    "prose": "...",
    "tags": ["...", "..."]
  }},
  "tone": {{
    "prose": "...",
    "tags": ["...", "..."]
  }}
}}"""


def format_rules(rules: list[dict]) -> str:
    if not rules:
        return "(no rules available)"
    lines = []
    for i, r in enumerate(rules, 1):
        name = r.get("short_name", "").strip()
        desc = r.get("description", "").strip()
        if desc:
            lines.append(f"{i}. {name}: {desc[:300]}")
        elif name:
            lines.append(f"{i}. {name}")
    return "\n".join(lines) if lines else "(no rules available)"


def build_prompt(sub: dict) -> str:
    desc = (sub.get("description") or "").strip()
    # Truncate very long sidebars
    if len(desc) > 3000:
        desc = desc[:3000] + "\n...(truncated)"
    return EXTRACT_PROMPT.format(
        name=sub["name"],
        title=sub.get("title", ""),
        subscribers=sub.get("subscribers", 0) or 0,
        public_description=(sub.get("public_description") or "").strip(),
        description=desc or "(empty)",
        rules_text=format_rules(sub.get("rules", [])),
    )


async def extract_one(
    client: anthropic.AsyncAnthropicBedrock,
    sub: dict,
    model: str,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    prompt = build_prompt(sub)
    async with semaphore:
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            # Parse JSON from response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                extracted = json.loads(text[start:end])
                return {
                    "name": sub["name"],
                    "subscribers": sub.get("subscribers", 0),
                    "over18": sub.get("over18", False),
                    "extracted": extracted,
                }
        except json.JSONDecodeError:
            logger.warning(f"r/{sub['name']}: failed to parse JSON")
        except Exception as e:
            logger.warning(f"r/{sub['name']}: {e}")
    return None


async def main():
    parser = argparse.ArgumentParser(description="Extract community context via LLM")
    parser.add_argument("--input", type=str, default=str(INPUT_PATH))
    parser.add_argument("--output", type=str, default=str(OUTPUT_PATH))
    parser.add_argument("--limit", type=int, default=None, help="Process only first N subs")
    parser.add_argument("--concurrency", type=int, default=5, help="Max concurrent LLM calls")
    parser.add_argument("--model", type=str, default=settings.haiku_model, help="Bedrock model ID")
    parser.add_argument("--resume", action="store_true", help="Skip already-extracted subs")
    args = parser.parse_args()

    load_dotenv()

    subs = json.loads(Path(args.input).read_text())
    logger.info(f"Loaded {len(subs)} subreddits from {args.input}")

    # Resume support: load already-extracted names
    output_path = Path(args.output)
    done_names = set()
    if args.resume and output_path.exists():
        for line in output_path.read_text().splitlines():
            if line.strip():
                try:
                    done_names.add(json.loads(line)["name"])
                except (json.JSONDecodeError, KeyError):
                    pass
        logger.info(f"Resuming: {len(done_names)} already extracted")
        subs = [s for s in subs if s["name"] not in done_names]

    if args.limit:
        subs = subs[:args.limit]
    logger.info(f"Will extract {len(subs)} subreddits using {args.model}")

    client = anthropic.AsyncAnthropicBedrock(
        aws_access_key=settings.aws_access_key,
        aws_secret_key=settings.aws_secret_key,
        aws_region=settings.aws_region,
    )
    semaphore = asyncio.Semaphore(args.concurrency)

    # Process in batches, append results as we go
    mode = "a" if args.resume and done_names else "w"
    with open(output_path, mode) as f:
        tasks = [extract_one(client, sub, args.model, semaphore) for sub in subs]
        results = await tqdm_asyncio.gather(*tasks, desc="Extracting")
        extracted = 0
        for result in results:
            if result:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
                extracted += 1

    total = len(done_names) + extracted
    logger.info(f"Done. {extracted} new extractions ({total} total) saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
