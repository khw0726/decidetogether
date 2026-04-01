from sqlalchemy import text
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


async def init_db() -> None:
    """Create all database tables."""
    async with engine.begin() as conn:
        from . import models  # noqa: F401 - ensure models are imported
        await _migrate_example_checklist_item_links(conn)
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
