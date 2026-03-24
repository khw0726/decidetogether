"""Deterministic (regex-based) checklist item evaluation."""

import re
from typing import Any

from ..db.models import ChecklistItem


def evaluate_deterministic(item: ChecklistItem, post: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate a deterministic checklist item using regex/pattern matching.

    Returns (passes, reasoning) where passes=True means the item's criterion is satisfied
    (i.e., the post does NOT violate this particular check, or the pattern matches and
    negate=False means it IS a violation, so passes=False).

    Convention: passes=True → OK (no violation); passes=False → violation detected.
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
            # Invalid regex — treat as no match, log the error
            import logging
            logging.getLogger(__name__).warning(f"Invalid regex {regex!r}: {e}")

    if match_mode == "any":
        pattern_hit = len(matched_patterns) > 0
    else:  # all
        pattern_hit = len(matched_patterns) == len(patterns)

    # If negate=False: pattern_hit means the pattern IS found → violation (passes=False)
    # If negate=True: pattern_hit means the pattern IS found → actually fine (passes=True)
    # E.g. "post must contain [OC] tag" → negate=True, pattern=[OC], so if NOT found → violation
    if negate:
        passes = not pattern_hit
        if pattern_hit:
            reasoning = f"Pattern found (negate mode — expected NOT to match): {matched_patterns}"
        else:
            reasoning = f"No pattern match found — expected absence confirmed."
    else:
        passes = not pattern_hit  # pattern found → violation → passes=False
        if pattern_hit:
            reasoning = f"Violation detected — matched patterns: {matched_patterns}"
        else:
            reasoning = "No violation patterns matched."

    return passes, reasoning
