import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator

from ..config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA busy_timeout=5000")

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# Single-writer guard for SQLite. Background tasks (compile, triage, queue
# re-eval) routinely run in parallel; without this lock they race on the
# single writer slot and surface as "database is locked" once busy_timeout
# expires. Any code path that opens a session intending to write should use
# write_session() so writers serialize at the asyncio layer instead. Code
# that needs to commit on an already-open session (e.g. an evaluation that
# threads its session through several phases) can `async with db_write_lock:`
# around the commit itself.
db_write_lock = asyncio.Lock()


@asynccontextmanager
async def write_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session while holding the global write lock.

    The lock is held for the lifetime of the session (including commit), so
    only one writer can have a transaction open at a time. Don't do LLM
    calls inside this block — split read/LLM/write phases instead.
    """
    async with db_write_lock:
        async with AsyncSessionLocal() as session:
            yield session


async def _migrate_example_checklist_item_links(conn) -> None:
    """Migrate example_checklist_item_links to new schema (surrogate PK + description column).

    Old schema: composite PK (example_id, checklist_item_id), no description.
    New schema: surrogate PK id, nullable checklist_item_id, checklist_item_description TEXT.

    SQLite doesn't support ALTER TABLE for PK changes, so we recreate the table.
    """
    import uuid as _uuid

    # Check whether migration is needed by inspecting existing columns
    cols_result = await conn.execute(
        text("PRAGMA table_info(example_checklist_item_links)")
    )
    cols = cols_result.fetchall()
    col_names = {row[1] for row in cols}

    if "id" in col_names:
        return  # Already migrated

    if not col_names:
        return  # Table doesn't exist yet; create_all will handle it

    # Recreate with new schema
    await conn.execute(text(
        "ALTER TABLE example_checklist_item_links RENAME TO _ecil_old"
    ))
    await conn.execute(text("""
        CREATE TABLE example_checklist_item_links (
            id TEXT PRIMARY KEY NOT NULL,
            example_id TEXT NOT NULL REFERENCES examples(id),
            checklist_item_id TEXT REFERENCES checklist_items(id),
            checklist_item_description TEXT NOT NULL DEFAULT ''
        )
    """))
    # Copy existing rows (description will be empty — acceptable for legacy data)
    old_rows_result = await conn.execute(
        text("SELECT example_id, checklist_item_id FROM _ecil_old")
    )
    old_rows = old_rows_result.fetchall()
    for example_id, checklist_item_id in old_rows:
        await conn.execute(
            text("INSERT INTO example_checklist_item_links (id, example_id, checklist_item_id) VALUES (:id, :eid, :cid)"),
            {"id": str(_uuid.uuid4()), "eid": example_id, "cid": checklist_item_id},
        )
    await conn.execute(text("DROP TABLE _ecil_old"))


async def _migrate_community_context_field(conn) -> None:
    """Add community_context column to communities table if missing."""
    cols_result = await conn.execute(text("PRAGMA table_info(communities)"))
    col_names = {row[1] for row in cols_result.fetchall()}
    if not col_names:
        return
    if "community_context" not in col_names:
        await conn.execute(text("ALTER TABLE communities ADD COLUMN community_context JSON"))


async def _migrate_checklist_context_rename(conn) -> None:
    """Rename atmosphere_influenced→context_influenced, atmosphere_note→context_note on checklist_items."""
    cols_result = await conn.execute(text("PRAGMA table_info(checklist_items)"))
    col_names = {row[1] for row in cols_result.fetchall()}
    if not col_names:
        return
    # Add new columns if they don't exist
    if "context_influenced" not in col_names and "atmosphere_influenced" in col_names:
        await conn.execute(text(
            "ALTER TABLE checklist_items ADD COLUMN context_influenced BOOLEAN NOT NULL DEFAULT 0"
        ))
        await conn.execute(text(
            "UPDATE checklist_items SET context_influenced = atmosphere_influenced"
        ))
    if "context_note" not in col_names and "atmosphere_note" in col_names:
        await conn.execute(text(
            "ALTER TABLE checklist_items ADD COLUMN context_note TEXT"
        ))
        await conn.execute(text(
            "UPDATE checklist_items SET context_note = atmosphere_note"
        ))


async def _migrate_decision_tag_field(conn) -> None:
    """Add moderator_tag to decisions if missing."""
    cols_result = await conn.execute(text("PRAGMA table_info(decisions)"))
    col_names = {row[1] for row in cols_result.fetchall()}
    if not col_names:
        return
    if "moderator_tag" not in col_names:
        await conn.execute(text(
            "ALTER TABLE decisions ADD COLUMN moderator_tag VARCHAR"
        ))


async def _migrate_rule_override_count(conn) -> None:
    """Add override_count to rules if missing."""
    cols_result = await conn.execute(text("PRAGMA table_info(rules)"))
    col_names = {row[1] for row in cols_result.fetchall()}
    if not col_names:
        return
    if "override_count" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN override_count INTEGER NOT NULL DEFAULT 0"
        ))


async def _migrate_community_context_samples(conn) -> None:
    """Add context_samples column to communities table if missing."""
    cols_result = await conn.execute(text("PRAGMA table_info(communities)"))
    col_names = {row[1] for row in cols_result.fetchall()}
    if not col_names:
        return
    if "context_samples" not in col_names:
        await conn.execute(text("ALTER TABLE communities ADD COLUMN context_samples JSON"))


async def _migrate_rule_two_pass_fields(conn) -> None:
    """Add base_checklist_json and context_adjustment_summary to rules if missing."""
    cols_result = await conn.execute(text("PRAGMA table_info(rules)"))
    col_names = {row[1] for row in cols_result.fetchall()}
    if not col_names:
        return
    if "base_checklist_json" not in col_names:
        await conn.execute(text("ALTER TABLE rules ADD COLUMN base_checklist_json JSON"))
    if "context_adjustment_summary" not in col_names:
        await conn.execute(text("ALTER TABLE rules ADD COLUMN context_adjustment_summary TEXT"))


async def _migrate_context_summary_to_json(conn) -> None:
    """Convert legacy string context_adjustment_summary to JSON array."""
    import json as _json
    rows = await conn.execute(text(
        "SELECT id, context_adjustment_summary FROM rules "
        "WHERE context_adjustment_summary IS NOT NULL AND context_adjustment_summary != ''"
    ))
    for row in rows.fetchall():
        rule_id, raw = row[0], row[1]
        # Already a JSON array?
        if isinstance(raw, list):
            continue
        if isinstance(raw, str):
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, list):
                    continue  # already valid JSON array
            except (ValueError, TypeError):
                pass
            # Split prose into sentences as bullets
            bullets = [s.strip().rstrip(".") for s in raw.split(". ") if s.strip()]
            await conn.execute(
                text("UPDATE rules SET context_adjustment_summary = :val WHERE id = :id"),
                {"val": _json.dumps(bullets), "id": rule_id},
            )


async def _migrate_checklist_context_pin_fields(conn) -> None:
    """Add context_pinned and context_override_note to checklist_items if missing."""
    cols_result = await conn.execute(text("PRAGMA table_info(checklist_items)"))
    col_names = {row[1] for row in cols_result.fetchall()}
    if not col_names:
        return
    if "context_pinned" not in col_names:
        await conn.execute(text(
            "ALTER TABLE checklist_items ADD COLUMN context_pinned BOOLEAN NOT NULL DEFAULT 0"
        ))
    if "context_override_note" not in col_names:
        await conn.execute(text(
            "ALTER TABLE checklist_items ADD COLUMN context_override_note TEXT"
        ))


async def _migrate_community_context_prose_to_notes(conn) -> None:
    """Migrate community_context from prose (string) to notes (list of strings).

    Splits prose sentences into individual note items.
    """
    import json as _json
    import re as _re

    rows = await conn.execute(
        text("SELECT id, community_context FROM communities WHERE community_context IS NOT NULL")
    )
    for row in rows.fetchall():
        community_id, ctx_raw = row
        if not ctx_raw:
            continue
        ctx = _json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
        changed = False
        for dim in ["purpose", "participants", "stakes", "tone"]:
            d = ctx.get(dim, {})
            if "prose" in d and "notes" not in d:
                prose = d.pop("prose", "")
                # Split prose into sentences
                if prose:
                    sentences = [s.strip() for s in _re.split(r'(?<=[.!?])\s+', prose) if s.strip()]
                    d["notes"] = sentences
                else:
                    d["notes"] = []
                ctx[dim] = d
                changed = True
        if changed:
            await conn.execute(
                text("UPDATE communities SET community_context = :ctx WHERE id = :id"),
                {"ctx": _json.dumps(ctx), "id": community_id},
            )


async def _migrate_checklist_context_change_types(conn) -> None:
    """Add context_change_types column to checklist_items."""
    cols = await conn.execute(text("PRAGMA table_info(checklist_items)"))
    col_names = {r[1] for r in cols.fetchall()}
    if "context_change_types" not in col_names and col_names:
        await conn.execute(text(
            "ALTER TABLE checklist_items ADD COLUMN context_change_types TEXT DEFAULT NULL"
        ))


async def _migrate_checklist_base_description(conn) -> None:
    """Add base_description column — links a context-adjusted item back to its entry in base_checklist_json."""
    cols = await conn.execute(text("PRAGMA table_info(checklist_items)"))
    col_names = {r[1] for r in cols.fetchall()}
    if "base_description" not in col_names and col_names:
        await conn.execute(text(
            "ALTER TABLE checklist_items ADD COLUMN base_description TEXT DEFAULT NULL"
        ))


async def _migrate_drop_atmosphere(conn) -> None:
    """Drop legacy atmosphere columns: communities.atmosphere and the renamed
    checklist_items.atmosphere_influenced / atmosphere_note (data already moved
    to context_influenced / context_note by _migrate_checklist_context_rename)."""
    # Communities: drop atmosphere column if it exists
    cols = await conn.execute(text("PRAGMA table_info(communities)"))
    col_names = {r[1] for r in cols.fetchall()}
    if "atmosphere" in col_names:
        await conn.execute(text("ALTER TABLE communities DROP COLUMN atmosphere"))

    # Checklist items: drop the old atmosphere_* columns if both they and their context_* replacements exist
    cols = await conn.execute(text("PRAGMA table_info(checklist_items)"))
    col_names = {r[1] for r in cols.fetchall()}
    if "atmosphere_influenced" in col_names and "context_influenced" in col_names:
        await conn.execute(text("ALTER TABLE checklist_items DROP COLUMN atmosphere_influenced"))
    if "atmosphere_note" in col_names and "context_note" in col_names:
        await conn.execute(text("ALTER TABLE checklist_items DROP COLUMN atmosphere_note"))


async def _migrate_rule_relevant_context(conn) -> None:
    """Add relevant_context and custom_context_notes columns to rules if missing."""
    cols = await conn.execute(text("PRAGMA table_info(rules)"))
    col_names = {r[1] for r in cols.fetchall()}
    if not col_names:
        return
    if "relevant_context" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN relevant_context JSON DEFAULT NULL"
        ))
    if "custom_context_notes" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN custom_context_notes JSON DEFAULT NULL"
        ))


async def _migrate_checklist_pinned_tags(conn) -> None:
    """Add pinned_tags column to checklist_items if missing."""
    cols = await conn.execute(text("PRAGMA table_info(checklist_items)"))
    col_names = {r[1] for r in cols.fetchall()}
    if "pinned_tags" not in col_names and col_names:
        await conn.execute(text(
            "ALTER TABLE checklist_items ADD COLUMN pinned_tags JSON DEFAULT NULL"
        ))


async def _migrate_rule_pending_preview(conn) -> None:
    """Add pending_* preview columns to rules if missing."""
    cols = await conn.execute(text("PRAGMA table_info(rules)"))
    col_names = {r[1] for r in cols.fetchall()}
    if not col_names:
        return
    if "pending_checklist_json" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN pending_checklist_json JSON DEFAULT NULL"
        ))
    if "pending_context_adjustment_summary" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN pending_context_adjustment_summary JSON DEFAULT NULL"
        ))
    if "pending_relevant_context" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN pending_relevant_context JSON DEFAULT NULL"
        ))
    if "pending_custom_context_notes" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN pending_custom_context_notes JSON DEFAULT NULL"
        ))
    if "pending_generated_at" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN pending_generated_at DATETIME DEFAULT NULL"
        ))


async def _migrate_reference_corpus_fields(conn) -> None:
    """Add is_reference + public_description to communities and title_embedding to rules."""
    cols = await conn.execute(text("PRAGMA table_info(communities)"))
    col_names = {r[1] for r in cols.fetchall()}
    if col_names:
        if "is_reference" not in col_names:
            await conn.execute(text(
                "ALTER TABLE communities ADD COLUMN is_reference BOOLEAN NOT NULL DEFAULT 0"
            ))
        if "public_description" not in col_names:
            await conn.execute(text(
                "ALTER TABLE communities ADD COLUMN public_description TEXT DEFAULT NULL"
            ))
    cols = await conn.execute(text("PRAGMA table_info(rules)"))
    col_names = {r[1] for r in cols.fetchall()}
    if col_names and "title_embedding" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN title_embedding BLOB DEFAULT NULL"
        ))


async def _migrate_flag_to_warn(conn) -> None:
    """Rename action='flag' to 'warn' in checklist_items and verdict='review' to 'warn' in decisions."""
    # Checklist items
    await conn.execute(text(
        "UPDATE checklist_items SET action = 'warn' WHERE action = 'flag'"
    ))
    # Decisions: agent_verdict 'review' from rule-based path should become 'warn'
    # (keep 'review' only for community norms — those have '__community_norms__' in reasoning,
    # but we can't easily filter JSON here, so leave existing decisions as-is for now)


async def _migrate_sample_post_modqueue_fields(conn) -> None:
    """Add status/source/source_metadata to community_sample_posts and context_stale to communities."""
    cols = await conn.execute(text("PRAGMA table_info(community_sample_posts)"))
    col_names = {r[1] for r in cols.fetchall()}
    if col_names:
        if "status" not in col_names:
            await conn.execute(text(
                "ALTER TABLE community_sample_posts ADD COLUMN status VARCHAR NOT NULL DEFAULT 'committed'"
            ))
        if "source" not in col_names:
            await conn.execute(text(
                "ALTER TABLE community_sample_posts ADD COLUMN source VARCHAR NOT NULL DEFAULT 'manual'"
            ))
        if "source_metadata" not in col_names:
            await conn.execute(text(
                "ALTER TABLE community_sample_posts ADD COLUMN source_metadata JSON DEFAULT NULL"
            ))
    cols = await conn.execute(text("PRAGMA table_info(communities)"))
    col_names = {r[1] for r in cols.fetchall()}
    if col_names and "context_stale" not in col_names:
        await conn.execute(text(
            "ALTER TABLE communities ADD COLUMN context_stale BOOLEAN NOT NULL DEFAULT 0"
        ))


async def _migrate_rule_compile_status(conn) -> None:
    """Add compile_status and compile_error columns to rules if missing."""
    cols = await conn.execute(text("PRAGMA table_info(rules)"))
    col_names = {r[1] for r in cols.fetchall()}
    if not col_names:
        return
    if "compile_status" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN compile_status VARCHAR NOT NULL DEFAULT 'idle'"
        ))
    if "compile_error" not in col_names:
        await conn.execute(text(
            "ALTER TABLE rules ADD COLUMN compile_error TEXT DEFAULT NULL"
        ))


async def init_db() -> None:
    """Create all database tables."""
    async with engine.begin() as conn:
        from . import models  # noqa: F401 - ensure models are imported
        await _migrate_example_checklist_item_links(conn)
        await _migrate_decision_tag_field(conn)
        await _migrate_rule_override_count(conn)
        await _migrate_community_context_field(conn)
        await _migrate_checklist_context_rename(conn)
        await _migrate_drop_atmosphere(conn)
        await _migrate_community_context_samples(conn)
        await _migrate_rule_two_pass_fields(conn)
        await _migrate_checklist_context_pin_fields(conn)
        await _migrate_context_summary_to_json(conn)
        await _migrate_community_context_prose_to_notes(conn)
        await _migrate_checklist_context_change_types(conn)
        await _migrate_checklist_base_description(conn)
        await _migrate_rule_relevant_context(conn)
        await _migrate_checklist_pinned_tags(conn)
        await _migrate_rule_pending_preview(conn)
        await _migrate_reference_corpus_fields(conn)
        await _migrate_flag_to_warn(conn)
        await _migrate_sample_post_modqueue_fields(conn)
        await _migrate_rule_compile_status(conn)
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
