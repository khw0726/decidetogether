"""Structural (metadata-based) checklist item evaluation."""

from typing import Any

from ..db.models import ChecklistItem


def evaluate_structural(item: ChecklistItem, post: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate structural checks against post metadata.

    Returns (passes, reasoning) where passes=True means the criterion is met
    (no structural violation for this item).
    """
    logic = item.logic
    checks = logic.get("checks", [])
    match_mode = logic.get("match_mode", "all")

    # Extract metadata from post
    author = post.get("author", {})
    if not isinstance(author, dict):
        author = {}

    context = post.get("context", {})
    if not isinstance(context, dict):
        context = {}

    # Supported fields
    field_map: dict[str, Any] = {
        "account_age_days": author.get("account_age_days"),
        "post_type": context.get("post_type"),
        "flair": context.get("flair"),
        "karma": (author.get("platform_metadata") or {}).get("karma"),
        "subreddit_karma": (author.get("platform_metadata") or {}).get("subreddit_karma"),
        "is_oc": (context.get("platform_metadata") or {}).get("is_oc"),
        "channel": context.get("channel"),
    }

    check_results = []
    check_descriptions = []

    for check in checks:
        field = check.get("field", "")
        operator = check.get("operator", "==")
        value = check.get("value")
        actual = field_map.get(field)

        if actual is None:
            # Missing field — treat as check failing (unknown = can't verify)
            check_results.append(False)
            check_descriptions.append(f"{field} is missing (treating as fail)")
            continue

        passed = _apply_operator(actual, operator, value)
        check_results.append(passed)
        check_descriptions.append(f"{field}={actual!r} {operator} {value!r} → {'✓' if passed else '✗'}")

    if not check_results:
        return True, "No structural checks defined."

    if match_mode == "all":
        passes = all(check_results)
    else:  # any
        passes = any(check_results)

    # For structural items: failing the check = violation (passes=False)
    # E.g. "account_age_days < 30" → young account → violation detected
    # So if the check passes (condition is true), that means violation is detected → passes=False
    # EXCEPT: this depends on whether the logic is framed as "detect violation" or "confirm OK"
    # Convention: structural logic checks detect the violation condition (when True = bad)
    # So we negate: passes = NOT check_passed
    # Wait — need to be consistent with the spec examples:
    # {"field": "account_age_days", "operator": "<", "value": 30} in a spam check
    # means "account is new" → this IS a violation signal → check is True → item fails
    # So: structural check True → violation → passes = False
    violation_detected = passes  # checks fired = violation
    passes = not violation_detected

    reasoning = f"Structural checks ({match_mode}): {'; '.join(check_descriptions)}"
    return passes, reasoning


def _apply_operator(actual: Any, operator: str, value: Any) -> bool:
    """Apply a comparison operator."""
    try:
        if operator == "<":
            return actual < value
        elif operator == ">":
            return actual > value
        elif operator == "<=":
            return actual <= value
        elif operator == ">=":
            return actual >= value
        elif operator == "==":
            return actual == value
        elif operator == "!=":
            return actual != value
        elif operator == "in":
            if isinstance(value, list):
                return actual in value
            return str(actual) in str(value)
        else:
            return False
    except (TypeError, ValueError):
        return False
