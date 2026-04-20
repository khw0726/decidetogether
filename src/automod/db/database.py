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


async def _migrate_community_atmosphere(conn) -> None:
    """Add atmosphere column to communities table if missing."""
    cols_result = await conn.execute(text("PRAGMA table_info(communities)"))
    col_names = {row[1] for row in cols_result.fetchall()}
    if "atmosphere" not in col_names and col_names:
        await conn.execute(text("ALTER TABLE communities ADD COLUMN atmosphere JSON"))


async def _migrate_checklist_atmosphere_fields(conn) -> None:
    """Add atmosphere_influenced and atmosphere_note to checklist_items if missing."""
    cols_result = await conn.execute(text("PRAGMA table_info(checklist_items)"))
    col_names = {row[1] for row in cols_result.fetchall()}
    if not col_names:
        return  # Table doesn't exist yet
    if "atmosphere_influenced" not in col_names:
        await conn.execute(text(
            "ALTER TABLE checklist_items ADD COLUMN atmosphere_influenced BOOLEAN NOT NULL DEFAULT 0"
        ))
    if "atmosphere_note" not in col_names:
        await conn.execute(text(
            "ALTER TABLE checklist_items ADD COLUMN atmosphere_note TEXT"
        ))


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


async def init_db() -> None:
    """Create all database tables."""
    async with engine.begin() as conn:
        from . import models  # noqa: F401 - ensure models are imported
        await _migrate_example_checklist_item_links(conn)
        await _migrate_community_atmosphere(conn)
        await _migrate_checklist_atmosphere_fields(conn)
        await _migrate_decision_tag_field(conn)
        await _migrate_rule_override_count(conn)
        await _migrate_community_context_field(conn)
        await _migrate_checklist_context_rename(conn)
        await _migrate_community_context_samples(conn)
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
