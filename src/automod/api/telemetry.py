"""Frontend telemetry sink. Appends UI events to a JSONL log."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

LOG_DIR = Path(os.environ.get("TELEMETRY_DIR", "data/telemetry"))
LOG_DIR.mkdir(parents=True, exist_ok=True)


class UIEvent(BaseModel):
    # Client-assigned
    ts: str                     # ISO8601 from browser
    session_id: str             # per-tab uuid
    kind: str                   # "click" | "input" | "nav" | "custom"
    route: str                  # window.location.pathname + search
    target_tag: str | None = None      # "BUTTON", "A", ...
    target_role: str | None = None     # aria-role
    target_text: str | None = None     # truncated innerText
    target_id: str | None = None
    target_classes: str | None = None
    data_log: str | None = None        # nearest [data-log] ancestor value
    log_context: dict[str, Any] | None = None  # JSON from data-log-context
    name: str | None = None            # for explicit logEvent() calls
    payload: dict[str, Any] | None = None
    context: dict[str, Any] | None = None  # session context (community, etc.)


class EventBatch(BaseModel):
    events: list[UIEvent]


def _logfile_for_today() -> Path:
    return LOG_DIR / f"ui-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"


@router.post("/events")
async def ingest_events(batch: EventBatch) -> dict[str, int]:
    server_ts = datetime.now(timezone.utc).isoformat()
    path = _logfile_for_today()
    with path.open("a", encoding="utf-8") as f:
        for ev in batch.events:
            row = ev.model_dump()
            row["server_ts"] = server_ts
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"received": len(batch.events)}
