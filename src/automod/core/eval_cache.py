"""In-memory TTL cache for evaluation results.

Used by the fluid rule editor: when the moderator types and we re-score the
same posts against a draft checklist, most subjective items are unchanged
across keystrokes — caching their results by (post, logic) avoids redundant
LLM calls.

Process-local. Lost on restart. Single-worker dev assumption.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

_TTL_SECONDS = 600  # 10 minutes
_MAX_ENTRIES = 5000

# key (str hash) -> (expires_at, value)
_store: dict[str, tuple[float, Any]] = {}


def _make_key(post: dict[str, Any], item_config: dict[str, Any]) -> str:
    payload = json.dumps([post, item_config], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evict_expired(now: float) -> None:
    if len(_store) < _MAX_ENTRIES:
        return
    expired = [k for k, (exp, _) in _store.items() if exp <= now]
    for k in expired:
        _store.pop(k, None)
    # If still over the cap, drop the oldest entries.
    if len(_store) >= _MAX_ENTRIES:
        oldest = sorted(_store.items(), key=lambda kv: kv[1][0])[: len(_store) - _MAX_ENTRIES + 1]
        for k, _ in oldest:
            _store.pop(k, None)


def get(post: dict[str, Any], item_config: dict[str, Any]) -> Any | None:
    key = _make_key(post, item_config)
    entry = _store.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if expires_at <= time.time():
        _store.pop(key, None)
        return None
    return value


def set_(post: dict[str, Any], item_config: dict[str, Any], value: Any) -> None:
    now = time.time()
    _evict_expired(now)
    key = _make_key(post, item_config)
    _store[key] = (now + _TTL_SECONDS, value)


def clear() -> None:
    _store.clear()
