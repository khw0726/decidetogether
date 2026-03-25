"""Validation of compiled rules and checklist items."""

from typing import Any


class ValidationError(Exception):
    pass


VALID_ITEM_TYPES = {"deterministic", "structural", "subjective"}
VALID_ACTIONS = {"remove", "flag", "continue"}
VALID_RULE_TYPES = {"actionable", "procedural", "meta", "informational"}


def validate_checklist_item(item_data: dict[str, Any]) -> list[str]:
    """Validate a checklist item dict. Returns list of error messages."""
    errors = []

    if not item_data.get("description"):
        errors.append("description is required")

    item_type = item_data.get("item_type")
    if item_type not in VALID_ITEM_TYPES:
        errors.append(f"item_type must be one of {VALID_ITEM_TYPES}, got: {item_type!r}")

    action = item_data.get("action", "flag")
    if action not in VALID_ACTIONS:
        errors.append(f"action must be one of {VALID_ACTIONS}")

    logic = item_data.get("logic", {})
    if item_type == "deterministic":
        logic_errors = validate_deterministic_logic(logic)
        errors.extend(logic_errors)
    elif item_type == "structural":
        logic_errors = validate_structural_logic(logic)
        errors.extend(logic_errors)
    elif item_type == "subjective":
        logic_errors = validate_subjective_logic(logic)
        errors.extend(logic_errors)

    return errors


def validate_deterministic_logic(logic: dict) -> list[str]:
    errors = []
    if not isinstance(logic.get("patterns"), list):
        errors.append("deterministic logic must have 'patterns' list")
    else:
        for i, p in enumerate(logic["patterns"]):
            if not isinstance(p.get("regex"), str):
                errors.append(f"pattern[{i}] must have a 'regex' string")
    match_mode = logic.get("match_mode", "any")
    if match_mode not in {"any", "all"}:
        errors.append("deterministic match_mode must be 'any' or 'all'")
    return errors


def validate_structural_logic(logic: dict) -> list[str]:
    errors = []
    if not isinstance(logic.get("checks"), list):
        errors.append("structural logic must have 'checks' list")
    else:
        valid_operators = {"<", ">", "<=", ">=", "==", "!=", "in"}
        for i, check in enumerate(logic["checks"]):
            if not check.get("field"):
                errors.append(f"check[{i}] must have a 'field'")
            if check.get("operator") not in valid_operators:
                errors.append(f"check[{i}] operator must be one of {valid_operators}")
            if "value" not in check:
                errors.append(f"check[{i}] must have a 'value'")
    return errors


def validate_subjective_logic(logic: dict) -> list[str]:
    errors = []
    if not logic.get("prompt_template"):
        errors.append("subjective logic must have 'prompt_template'")
    if not logic.get("rubric"):
        errors.append("subjective logic must have 'rubric'")
    threshold = logic.get("threshold", 0.7)
    if not isinstance(threshold, (int, float)) or not (0.0 <= threshold <= 1.0):
        errors.append("subjective threshold must be a float between 0.0 and 1.0")
    return errors


def validate_rule_type(rule_type: str) -> list[str]:
    if rule_type not in VALID_RULE_TYPES:
        return [f"rule_type must be one of {VALID_RULE_TYPES}"]
    return []


def validate_compiled_output(compiled: dict) -> list[str]:
    """Validate the full compiler output."""
    errors = []

    checklist_tree = compiled.get("checklist_tree")
    if not isinstance(checklist_tree, list):
        errors.append("checklist_tree must be a list")
        return errors

    for i, item in enumerate(checklist_tree):
        item_errors = validate_checklist_item(item)
        for err in item_errors:
            errors.append(f"checklist_tree[{i}]: {err}")

    examples = compiled.get("examples", [])
    valid_labels = {"positive", "negative", "borderline"}
    for i, ex in enumerate(examples):
        if ex.get("label") not in valid_labels:
            errors.append(f"examples[{i}]: label must be one of {valid_labels}")
        if not isinstance(ex.get("content"), dict):
            errors.append(f"examples[{i}]: content must be a dict")

    return errors
