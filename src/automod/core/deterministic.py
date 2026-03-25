"""Deterministic (regex-based) checklist item evaluation."""

import re
from typing import Any

from ..db.models import ChecklistItem


def evaluate_deterministic(item: ChecklistItem, post: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate a deterministic checklist item using regex/pattern matching.

    Returns (triggered, reasoning) where triggered=True means the item's question
    is answered YES (violation detected).

    Convention: triggered=True → violation detected; triggered=False → no violation.
    """
    logic = item.logic
    patterns = logic.get("patterns", [])
    match_mode = logic.get("match_mode", "any")
    negate = logic.get("negate", False)

    # Get text to match against (title + body)
    content = post.get("content", {})
    if isinstance(content, dict):
        title = content.get("title", "")
        body = content.get("body", "")
    else:
        title = ""
        body = ""

    text = f"{title} {body}"

    matched_patterns = []
    for pattern in patterns:
        regex = pattern.get("regex", "")
        case_sensitive = pattern.get("case_sensitive", False)
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            if re.search(regex, text, flags):
                matched_patterns.append(regex)
        except re.error as e:
            import logging
            logging.getLogger(__name__).warning(f"Invalid regex {regex!r}: {e}")

    if match_mode == "any":
        pattern_hit = len(matched_patterns) > 0
    else:  # all
        pattern_hit = len(matched_patterns) == len(patterns)

    # negate=False: pattern found → triggered (violation)
    # negate=True: pattern NOT found → triggered (e.g. required tag is missing)
    if negate:
        triggered = not pattern_hit
        if triggered:
            reasoning = "Required pattern not found — violation detected."
        else:
            reasoning = f"Required pattern present — no violation: {matched_patterns}"
    else:
        triggered = pattern_hit
        if triggered:
            reasoning = f"Violation detected — matched patterns: {matched_patterns}"
        else:
            reasoning = "No violation patterns matched."

    return triggered, reasoning
