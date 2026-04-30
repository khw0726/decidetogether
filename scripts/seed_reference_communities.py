"""Seed the reference-community corpus used for grounded rule-text suggestions.

Reads `scripts/reference_corpus.jsonl` (output of `airules_sample.py`), generates a
4-dimension community context for each row, applies clustered-tag remapping for
cross-community tag alignment, embeds each rule's short_name, and upserts the
results into the live SQLite DB as `Community(is_reference=True)` + child Rule rows.

Idempotent: re-runs upsert by community name — existing reference communities are
deleted and re-inserted so the corpus stays in sync with the input file.

Source dataset: sTechLab/AIRules (CHI25). Cite the paper if publishing results
that depend on this corpus.

Usage:
  python scripts/seed_reference_communities.py \\
      --input scripts/reference_corpus.jsonl \\
      --tag-mapping scripts/context_tag_mapping.json
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select  # noqa: E402

from src.automod.config import get_anthropic_client, settings  # noqa: E402
from src.automod.compiler.compiler import RuleCompiler  # noqa: E402
from src.automod.db.database import AsyncSessionLocal, init_db  # noqa: E402
from src.automod.db.models import Community, Rule  # noqa: E402
from src.automod.embeddings import embed_text, pack_vector  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_tag_mapping(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        logger.warning(f"Tag mapping not found at {path} — skipping clustering")
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def remap_context_tags(context: dict, mapping: dict[str, dict[str, str]]) -> dict:
    """Replace each note's `tag` with its clustered category from `mapping`."""
    if not context or not mapping:
        return context
    for dim in ("purpose", "participants", "stakes", "tone"):
        d = (context.get(dim) or {})
        notes = d.get("notes") or []
        dim_map = mapping.get(dim, {})
        for note in notes:
            if not isinstance(note, dict):
                continue
            old_tag = note.get("tag", "")
            if old_tag and old_tag in dim_map:
                note["tag"] = dim_map[old_tag]
        d["notes"] = notes
        context[dim] = d
    return context


async def _generate_context_for_row(
    compiler: RuleCompiler, row: dict, taxonomy: dict | None
) -> dict:
    rules_summary = "\n".join(
        f"- {r['short_name']}: {r.get('description', '')}"
        for r in row.get("rules", [])[:20]
    )
    return await compiler.generate_community_context(
        community_name=row["name"],
        platform="reddit",
        description=row.get("public_description") or "",
        rules_summary=rules_summary,
        subscribers=row.get("subscribers"),
        sampled_posts=None,
        taxonomy=taxonomy,
    )


async def _seed_one(session, compiler: RuleCompiler, row: dict, tag_mapping: dict, taxonomy: dict | None) -> None:
    name = row["name"]

    # Idempotent upsert: drop any existing reference community with this name.
    existing_q = await session.execute(
        select(Community).where(Community.name == name).where(Community.is_reference.is_(True))
    )
    existing = existing_q.scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.commit()

    try:
        context = await _generate_context_for_row(compiler, row, taxonomy)
    except Exception as e:
        logger.error(f"[{name}] context generation failed: {e}")
        return

    context = remap_context_tags(context, tag_mapping)

    community = Community(
        name=name,
        platform="reddit",
        is_reference=True,
        public_description=row.get("public_description"),
        community_context=context,
    )
    session.add(community)
    await session.flush()  # assign id

    for i, raw_rule in enumerate(row.get("rules", [])):
        title = raw_rule.get("short_name") or ""
        if not title:
            continue
        try:
            vec = await embed_text(title)
            blob = pack_vector(vec)
        except Exception as e:
            logger.warning(f"[{name}] embed failed for '{title}': {e}")
            blob = None
        rule = Rule(
            community_id=community.id,
            title=title,
            text=raw_rule.get("description") or title,
            priority=i,
            rule_type="actionable",
            applies_to="both",
            title_embedding=blob,
        )
        session.add(rule)

    await session.commit()
    logger.info(f"[{name}] seeded with {len(row.get('rules', []))} rules")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=Path("scripts/reference_corpus.jsonl"))
    ap.add_argument("--tag-mapping", type=Path, default=Path("scripts/context_tag_mapping.json"))
    ap.add_argument("--taxonomy", type=Path, default=Path("scripts/context_taxonomy.json"),
                    help="Optional taxonomy passed to context generation for tag alignment.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process only first N rows (0 = all).")
    args = ap.parse_args()

    if not args.input.exists():
        logger.error(f"Input not found: {args.input} — run airules_sample.py first")
        return 1

    tag_mapping = load_tag_mapping(args.tag_mapping)
    taxonomy: dict | None = None
    if args.taxonomy.exists():
        with args.taxonomy.open("r", encoding="utf-8") as fh:
            taxonomy = json.load(fh)

    rows: list[dict] = []
    with args.input.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if args.limit > 0:
        rows = rows[: args.limit]
    logger.info(f"Seeding {len(rows)} reference communities")

    await init_db()

    client = get_anthropic_client()
    compiler = RuleCompiler(client, settings)

    async with AsyncSessionLocal() as session:
        for row in rows:
            try:
                await _seed_one(session, compiler, row, tag_mapping, taxonomy)
            except Exception as e:
                logger.error(f"[{row.get('name')}] seed failed: {e}")
                await session.rollback()

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
