"""Structural (metadata-based) checklist item evaluation."""

from typing import Any

from ..db.models import ChecklistItem


# Single source of truth for the structural-check field schema. The compiler
# prompts and the admin UI both read this so the LLM can't propose unknown
# fields and moderators can only pick from what we actually evaluate.
#
# value_type drives the operator picker in the UI:
#   - "number"  → numeric comparisons (<, <=, >, >=, ==, !=)
#   - "string"  → string comparisons (==, !=, in)
#   - "bool"    → equality only (==, !=)
STRUCTURAL_FIELDS: list[dict[str, str]] = [
    {"field": "account_age_days", "value_type": "number",
     "description": "Days since the author created their account."},
    {"field": "karma", "value_type": "number",
     "description": "Total karma (comment + link) for the author."},
    {"field": "subreddit_karma", "value_type": "number",
     "description": "Author's karma within this specific community."},
    {"field": "post_type", "value_type": "string",
     "description": "Type of submission: 'self', 'link', or 'comment'."},
    {"field": "flair", "value_type": "string",
     "description": "Post flair text, if any."},
    {"field": "channel", "value_type": "string",
     "description": "Community / subreddit name (e.g. 'r/example')."},
    {"field": "is_oc", "value_type": "bool",
     "description": "Whether the post is marked as original content."},
]

STRUCTURAL_FIELD_NAMES: set[str] = {f["field"] for f in STRUCTURAL_FIELDS}


def evaluate_structural(item: ChecklistItem, post: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate structural checks against post metadata.

    Returns (triggered, reasoning) where triggered=True means the item's question
    is answered YES (violation detected).
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
        return False, "No structural checks defined."

    if match_mode == "all":
        triggered = all(check_results)
    else:  # any
        triggered = any(check_results)

    reasoning = f"Structural checks ({match_mode}): {'; '.join(check_descriptions)}"
    return triggered, reasoning


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
