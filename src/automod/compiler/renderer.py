"""Human-readable rendering of checklist trees."""

from ..db.models import ChecklistItem


def render_checklist_tree(items: list[ChecklistItem], indent: int = 0) -> str:
    """Render a flat list of checklist items as a human-readable tree."""
    # Build tree structure
    root_items = [i for i in items if i.parent_id is None]
    children_map: dict[str, list[ChecklistItem]] = {}
    for item in items:
        if item.parent_id:
            if item.parent_id not in children_map:
                children_map[item.parent_id] = []
            children_map[item.parent_id].append(item)

    lines = []
    _render_items(root_items, children_map, lines, indent)
    return "\n".join(lines)


def _render_items(
    items: list[ChecklistItem],
    children_map: dict[str, list[ChecklistItem]],
    lines: list[str],
    indent: int,
) -> None:
    prefix = "  " * indent
    for item in sorted(items, key=lambda x: x.order):
        type_badge = {
            "deterministic": "[DET]",
            "structural": "[STR]",
            "subjective": "[SUB]",
        }.get(item.item_type, "[???]")

        action_badge = {
            "remove": "→REMOVE",
            "flag": "→FLAG",
            "continue": "→continue",
        }.get(item.fail_action, "")

        lines.append(f"{prefix}{type_badge} {item.description} {action_badge}")
        if item.rule_text_anchor:
            lines.append(f"{prefix}    Anchor: \"{item.rule_text_anchor}\"")
        lines.append(f"{prefix}    Combine: {item.combine_mode}")

        children = children_map.get(item.id, [])
        if children:
            _render_items(children, children_map, lines, indent + 1)


def render_logic(logic: dict) -> str:
    """Render checklist item logic as human-readable text."""
    item_type = logic.get("type", "unknown")

    if item_type == "deterministic":
        patterns = logic.get("patterns", [])
        mode = logic.get("match_mode", "any")
        negate = logic.get("negate", False)
        pattern_strs = [p.get("regex", "") for p in patterns]
        result = f"Match {mode} of: {', '.join(pattern_strs)}"
        if negate:
            result = f"NOT ({result})"
        return result

    elif item_type == "structural":
        checks = logic.get("checks", [])
        mode = logic.get("match_mode", "all")
        check_strs = [f"{c['field']} {c['operator']} {c['value']}" for c in checks]
        return f"Structural check ({mode}): {' AND '.join(check_strs) if mode == 'all' else ' OR '.join(check_strs)}"

    elif item_type == "subjective":
        threshold = logic.get("threshold", 0.7)
        prompt = logic.get("prompt_template", "")[:80]
        return f"LLM evaluation (threshold={threshold}): {prompt}..."

    return str(logic)
