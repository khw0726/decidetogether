"""Rule compiler: translates human-readable rules into executable checklist trees."""

import logging
from typing import Any, Optional

import anthropic

from ..config import Settings
from ..db.models import ChecklistItem, Community, Example, Rule
from . import prompts

logger = logging.getLogger(__name__)


def _filter_context_by_relevant(
    community_context: Optional[dict],
    relevant_context: Optional[list[dict]],
) -> Optional[dict]:
    """Filter a community context dict to only the (dimension, tag) bundles selected for a rule.

    relevant_context semantics:
    - None → return context unchanged (all bundles apply, the default).
    - [] → return empty dict (moderator explicitly opted out of all community context).
    - [{dimension, tag}, ...] → keep only notes whose (dimension, tag) appears in the list.
    """
    if not community_context:
        return community_context
    if relevant_context is None:
        return community_context
    if not relevant_context:
        return {}

    allowed: dict[str, set[str]] = {}
    for entry in relevant_context:
        dim = entry.get("dimension") if isinstance(entry, dict) else getattr(entry, "dimension", None)
        tag = entry.get("tag") if isinstance(entry, dict) else getattr(entry, "tag", None)
        if dim and tag:
            allowed.setdefault(dim, set()).add(tag)

    filtered: dict = {}
    for dim in ["purpose", "participants", "stakes", "tone"]:
        d = community_context.get(dim, {}) or {}
        notes = d.get("notes", []) or []
        kept_notes = []
        for note in notes:
            tag = note.get("tag", "") if isinstance(note, dict) else ""
            if tag and tag in allowed.get(dim, set()):
                kept_notes.append(note)
        if kept_notes:
            filtered[dim] = {"notes": kept_notes}
            if "manually_edited" in d:
                filtered[dim]["manually_edited"] = d["manually_edited"]
    return filtered


_TRIAGE_TOOL = {
    "name": "submit_triage",
    "description": "Submit rule classification result",
    "input_schema": {
        "type": "object",
        "properties": {
            "rule_type": {
                "type": "string",
                "enum": ["actionable", "procedural", "meta", "informational"],
            },
            "applies_to": {
                "type": "string",
                "enum": ["posts", "comments", "both"],
                "description": "What content type this rule applies to: posts (submissions only), comments (replies only), or both",
            },
            "reasoning": {"type": "string"},
        },
        "required": ["rule_type", "applies_to", "reasoning"],
    },
}

_COMPILE_TOOL = {
    "name": "submit_compiled_rule",
    "description": "Submit the compiled checklist tree and examples",
    "input_schema": {
        "type": "object",
        "properties": {
            "checklist_tree": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "rule_text_anchor": {
                            "type": ["string", "null"],
                            "description": "The specific portion of the rule text that this item corresponds to, if applicable. Should be an exact substring of the rule text or null if no specific anchor can be identified.",
                        },
                        "item_type": {
                            "type": "string",
                            "enum": ["deterministic", "structural", "subjective"],
                        },
                        "logic": {"type": "object"},
                        "action": {
                            "type": "string",
                            "enum": ["remove", "warn", "continue"],
                        },
                        "children": {"type": "array", "items": {"type": "object"}},
                        "context_influenced": {
                            "type": "boolean",
                            "description": "True if community context shaped how this item was framed or calibrated",
                        },
                        "context_note": {
                            "type": ["string", "null"],
                            "description": "Brief explanation of how community context influenced this item. Trace the reasoning: '[situational fact] → [calibration decision]'",
                        },
                    },
                    "required": [
                        "description", "item_type", "logic",
                        "action", "children", "context_influenced",
                    ],
                },
            },
            "examples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "enum": ["compliant", "violating", "borderline"],
                        },
                        "content": {"type": "object"},
                        "relevance_note": {"type": "string"},
                        "related_checklist_item_description": {
                            "type": ["string"],
                            "description": "Exact description of the checklist item this example primarily tests",
                        },
                    },
                    "required": ["label", "content", "relevance_note"],
                },
            },
        },
        "required": ["checklist_tree", "examples"],
    },
}


_RECOMPILE_TOOL = {
    "name": "submit_recompile_diff",
    "description": "Submit diff operations to update an existing checklist tree",
    "input_schema": {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": ["keep", "update", "delete", "add"],
                        },
                        "existing_id": {"type": "string"},
                        "description": {"type": "string"},
                        "rule_text_anchor": {
                            "type": ["string", "null"],
                            "description": "The specific portion of the rule text that this item corresponds to, if applicable. Should be an exact substring of the rule text or null if no specific anchor can be identified.",
                        },
                        "item_type": {
                            "type": "string",
                            "enum": ["deterministic", "structural", "subjective"],
                        },
                        "logic": {"type": "object"},
                        "action": {
                            "type": "string",
                            "enum": ["remove", "warn", "continue"],
                        },
                        "children": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["op"],
                },
            },
        },
        "required": ["operations"],
    },
}

_INFER_ITEM_TOOL = {
    "name": "submit_inferred_item",
    "description": "Submit the inferred item type and logic for a checklist item description",
    "input_schema": {
        "type": "object",
        "properties": {
            "item_type": {
                "type": "string",
                "enum": ["deterministic", "structural", "subjective"],
            },
            "logic": {"type": "object"},
        },
        "required": ["item_type", "logic"],
    },
}

_SYNTHESIZE_RULE_TOOL = {
    "name": "synthesize_rule",
    "description": "Propose a new community rule based on moderator override examples",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short rule title, ≤ 10 words"},
            "text": {"type": "string", "description": "Full rule text as it would appear in community rules"},
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "How confident you are that these examples reflect a real recurring pattern",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of the inferred pattern and what the examples have in common",
            },
        },
        "required": ["title", "text", "confidence", "reasoning"],
    },
}

_FILL_EXAMPLES_TOOL = {
    "name": "submit_fill_examples",
    "description": "Submit one violating example per checklist item that is missing one",
    "input_schema": {
        "type": "object",
        "properties": {
            "examples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "enum": ["violating", "borderline"],
                        },
                        "content": {"type": "object"},
                        "relevance_note": {"type": "string"},
                        "related_checklist_item_description": {
                            "type": "string",
                            "description": "Exact description of the checklist item this example primarily tests",
                        },
                    },
                    "required": [
                        "label", "content", "relevance_note",
                        "related_checklist_item_description",
                    ],
                },
            },
        },
        "required": ["examples"],
    },
}

_DIAGNOSE_TOOL = {
    "name": "submit_health_diagnoses",
    "description": "Submit per-item health diagnoses with proposed fixes",
    "input_schema": {
        "type": "object",
        "properties": {
            "diagnoses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "action": {
                            "type": "string",
                            "enum": ["tighten_rubric", "adjust_threshold", "promote_to_deterministic", "split_item"],
                        },
                        "reasoning": {"type": "string"},
                        "proposed_change": {"type": "object"},
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": ["item_id", "action", "reasoning", "proposed_change", "confidence"],
                },
            },
            "new_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["add_item"]},
                        "reasoning": {"type": "string"},
                        "proposed_item": {"type": "object"},
                        "motivated_by": {"type": "array", "items": {"type": "string"}},
                        "split_from": {
                            "type": ["string", "null"],
                            "description": "For split_item: the item_id this was split from, so both halves are applied together.",
                        },
                    },
                    "required": ["action", "reasoning", "proposed_item"],
                },
            },
        },
        "required": ["diagnoses", "new_items"],
    },
}

_NOTE_ITEM_SCHEMA = {
    "type": "object",
    "description": "One tagged calibration note. The tag (from the taxonomy) is primary; the text is a short per-tag explanation of how that tag applies to this community.",
    "properties": {
        "tag": {
            "type": "string",
            "description": "Taxonomy tag for this dimension (primary field).",
        },
        "text": {
            "type": "string",
            "description": "Brief moderator-readable explanation (≤15 words, single short clause) of how this tag applies here. Be terse — no preamble, no hedging.",
        },
    },
    "required": ["tag", "text"],
    "additionalProperties": False,
}

_DIMENSION_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "array",
            "description": "2-4 tagged notes (prefer fewer, sharper notes over more verbose ones). Do NOT include a separate 'tags' field — tags live inside each note.",
            "items": _NOTE_ITEM_SCHEMA,
        },
    },
    "required": ["notes"],
    "additionalProperties": False,
}

_GENERATE_CONTEXT_TOOL = {
    "name": "submit_community_context",
    "description": "Submit a structured community context profile with four dimensions. Each dimension's notes are tag-primary: each note = one taxonomy tag + a short explanation. Do NOT emit a separate tags array.",
    "input_schema": {
        "type": "object",
        "properties": {
            "purpose": _DIMENSION_SCHEMA,
            "participants": _DIMENSION_SCHEMA,
            "stakes": _DIMENSION_SCHEMA,
            "tone": _DIMENSION_SCHEMA,
        },
        "required": ["purpose", "participants", "stakes", "tone"],
    },
}

_NO_CONTEXT_COMPILE_TOOL = {
    "name": "submit_compiled_rule",
    "description": "Submit the compiled checklist tree and examples (no context)",
    "input_schema": {
        "type": "object",
        "properties": {
            "checklist_tree": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "rule_text_anchor": {"type": ["string", "null"]},
                        "item_type": {"type": "string", "enum": ["deterministic", "structural", "subjective"]},
                        "logic": {"type": "object"},
                        "action": {"type": "string", "enum": ["remove", "warn", "continue"]},
                        "children": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["description", "item_type", "logic", "action", "children"],
                },
            },
            "examples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "enum": ["compliant", "violating", "borderline"]},
                        "content": {"type": "object"},
                        "relevance_note": {"type": "string"},
                        "related_checklist_item_description": {"type": ["string", "null"]},
                    },
                    "required": ["label", "content", "relevance_note"],
                },
            },
        },
        "required": ["checklist_tree", "examples"],
    },
}

_CONTEXT_ADJUST_TOOL = {
    "name": "submit_adjusted_checklist",
    "description": "Submit the context-adjusted checklist tree and adjustment summary",
    "input_schema": {
        "type": "object",
        "properties": {
            "checklist_tree": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "rule_text_anchor": {"type": ["string", "null"]},
                        "item_type": {"type": "string", "enum": ["deterministic", "structural", "subjective"]},
                        "logic": {"type": "object"},
                        "action": {"type": "string", "enum": ["remove", "warn", "continue"]},
                        "children": {"type": "array", "items": {"type": "object"}},
                        "context_influenced": {"type": "boolean"},
                        "context_note": {"type": ["string", "null"]},
                        "context_change_types": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["threshold", "rubric", "description", "action", "new_item", "pattern", "check"],
                            },
                            "description": "What was changed by context: threshold, rubric, description, action, new_item (added by context), pattern (regex), check (structural)",
                        },
                        "base_description": {
                            "type": ["string", "null"],
                            "description": "If this item was derived from a base checklist item, the EXACT description of that base item (copy verbatim from the base checklist input). Null only for items with context_change_types=['new_item'].",
                        },
                    },
                    "required": ["description", "item_type", "logic", "action", "children", "context_influenced"],
                },
            },
            "adjustment_summary": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short bullet points summarizing each adjustment (one bullet per change, under 20 words each)",
            },
        },
        "required": ["checklist_tree", "adjustment_summary"],
    },
}

_LINK_VIOLATIONS_TOOL = {
    "name": "submit_violation_links",
    "description": "Submit links between uncovered violations and checklist items",
    "input_schema": {
        "type": "object",
        "properties": {
            "links": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "example_id": {
                            "type": "string",
                            "description": "ID of the violating example",
                        },
                        "checklist_item_id": {
                            "type": "string",
                            "description": "ID of the checklist item this violation matches",
                        },
                        "checklist_item_description": {
                            "type": "string",
                            "description": "Description of the checklist item (for stable re-linking)",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Brief explanation of why this violation matches this item",
                        },
                    },
                    "required": ["example_id", "checklist_item_id", "checklist_item_description", "reasoning"],
                },
            },
        },
        "required": ["links"],
    },
}


class RuleCompiler:
    def __init__(self, client: anthropic.AsyncAnthropicBedrock, settings: Settings):
        self.client = client
        self.settings = settings

    async def _call_claude(
        self,
        system: str,
        user: str,
        tool: dict,
        model: Optional[str] = None,
    ) -> Any:
        """Make a Claude API call using structured output and return the parsed result."""
        if model is None:
            model = self.settings.compiler_model
        response = await self.client.messages.create(
            model=model,
            max_tokens=8192,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
        )
        return response.content[0].input

    async def triage_rule(self, rule_text: str, community_name: str, platform: str) -> dict[str, str]:
        """Classify rule as actionable/procedural/meta/informational."""
        logger.info(f"Triaging rule for community '{community_name}'")
        user_prompt = prompts.build_triage_prompt(rule_text, community_name, platform)
        result = await self._call_claude(prompts.TRIAGE_SYSTEM, user_prompt, tool=_TRIAGE_TOOL)
        return {
            "rule_type": result.get("rule_type", "informational"),
            "applies_to": result.get("applies_to", "both"),
            "reasoning": result.get("reasoning", ""),
        }

    def _make_other_rules_summary(self, other_rules: list[Rule]) -> str:
        if not other_rules:
            return "No other rules."
        lines = []
        for r in other_rules:
            lines.append(f"- [{r.rule_type.upper()}] {r.title}: {r.text[:100]}...")
        return "\n".join(lines)

    def _checklist_item_to_dict(self, item: ChecklistItem) -> dict:
        d: dict = {
            "id": item.id,
            "description": item.description,
            "rule_text_anchor": item.rule_text_anchor,
            "item_type": item.item_type,
            "logic": item.logic,
            "action": item.action,
            "order": item.order,
        }
        if item.parent_id:
            d["parent_id"] = item.parent_id
        return d

    def _example_to_dict(self, example: Example) -> dict:
        return {
            "id": example.id,
            "label": example.label,
            "content": example.content,
        }

    def _parse_checklist_items(
        self, items_data: list[dict], rule_id: str, parent_id: Optional[str] = None, order_offset: int = 0
    ) -> list[ChecklistItem]:
        """Recursively parse checklist items from compiler output."""
        import uuid
        result = []
        for i, item_data in enumerate(items_data):
            item = ChecklistItem(
                id=str(uuid.uuid4()),
                rule_id=rule_id,
                order=order_offset + i,
                parent_id=parent_id,
                description=item_data.get("description", ""),
                rule_text_anchor=item_data.get("rule_text_anchor"),
                item_type=item_data.get("item_type", "subjective"),
                logic=item_data.get("logic", {}),
                action=item_data.get("action", "warn"),
            )
            result.append(item)

            children_data = item_data.get("children", [])
            if children_data:
                result.extend(self._parse_checklist_items(
                    children_data, rule_id, parent_id=item.id, order_offset=0
                ))

        return result

    async def compile_rule(
        self,
        rule: Rule,
        community: Community,
        other_rules: list[Rule],
        existing_items: Optional[list[ChecklistItem]] = None,
        existing_examples: Optional[list[Example]] = None,
        community_context: Optional[dict] = None,
        community_posts_sample: Optional[list[dict]] = None,
    ) -> tuple[list[ChecklistItem], list[dict]]:
        """Compile actionable rule into checklist tree + examples.

        Returns (checklist_items, example_dicts) to be persisted by caller.
        """
        logger.info(f"Compiling rule '{rule.title}' for community '{community.name}'")

        other_rules_summary = self._make_other_rules_summary(
            [r for r in other_rules if r.id != rule.id]
        )

        existing_checklist_dicts = None
        if existing_items:
            existing_checklist_dicts = [self._checklist_item_to_dict(i) for i in existing_items]

        existing_example_dicts = None
        if existing_examples:
            existing_example_dicts = [self._example_to_dict(e) for e in existing_examples]

        user_prompt = prompts.build_compile_prompt(
            rule_title=rule.title,
            rule_text=rule.text,
            community_name=community.name,
            platform=community.platform,
            other_rules_summary=other_rules_summary,
            existing_checklist=existing_checklist_dicts,
            existing_examples=existing_example_dicts,
            community_context=community_context,
            community_posts_sample=community_posts_sample,
        )

        compiled = await self._call_claude(prompts.COMPILE_SYSTEM, user_prompt, tool=_COMPILE_TOOL)

        # Parse checklist tree
        checklist_items = self._parse_flat_items(
            compiled.get("checklist_tree", []), rule.id
        )

        # Return examples as raw dicts (caller persists them)
        examples = compiled.get("examples", [])

        return checklist_items, examples

    def _items_to_nested_dicts(self, items: list[ChecklistItem]) -> list[dict]:
        """Convert flat ChecklistItem list to nested dict tree for serialization."""
        items_by_id = {item.id: item for item in items}
        roots = []
        children_map: dict[str, list[dict]] = {}

        for item in sorted(items, key=lambda x: x.order):
            d = {
                "description": item.description,
                "rule_text_anchor": item.rule_text_anchor,
                "item_type": item.item_type,
                "logic": item.logic,
                "action": item.action,
                "context_influenced": item.context_influenced,
                "context_note": item.context_note,
                "children": [],
            }
            if item.parent_id:
                children_map.setdefault(item.parent_id, []).append(d)
            else:
                roots.append(d)

        # Attach children recursively
        def _attach(node_dict: dict, item_id: str) -> None:
            for child_d in children_map.get(item_id, []):
                node_dict["children"].append(child_d)
                # Find child item to get its ID for further nesting
                for it in items:
                    if it.description == child_d["description"] and it.parent_id == item_id:
                        _attach(child_d, it.id)
                        break

        for root_d in roots:
            for it in items:
                if it.description == root_d["description"] and it.parent_id is None:
                    _attach(root_d, it.id)
                    break

        return roots

    async def compile_rule_base(
        self,
        rule: Rule,
        community: Community,
        other_rules: list[Rule],
        existing_items: Optional[list[ChecklistItem]] = None,
        existing_examples: Optional[list[Example]] = None,
    ) -> tuple[list[ChecklistItem], list[dict]]:
        """Pass 1: Compile rule without community context (context-free baseline).

        Returns (checklist_items, example_dicts).
        """
        logger.info(f"Pass 1: Compiling rule '{rule.title}' without context")

        other_rules_summary = self._make_other_rules_summary(
            [r for r in other_rules if r.id != rule.id]
        )

        existing_checklist_dicts = None
        if existing_items:
            existing_checklist_dicts = [self._checklist_item_to_dict(i) for i in existing_items]

        existing_example_dicts = None
        if existing_examples:
            existing_example_dicts = [self._example_to_dict(e) for e in existing_examples]

        user_prompt = prompts.build_no_context_compile_prompt(
            rule_title=rule.title,
            rule_text=rule.text,
            community_name=community.name,
            platform=community.platform,
            other_rules_summary=other_rules_summary,
            existing_checklist=existing_checklist_dicts,
            existing_examples=existing_example_dicts,
        )

        compiled = await self._call_claude(
            prompts.NO_CONTEXT_COMPILE_SYSTEM, user_prompt, tool=_NO_CONTEXT_COMPILE_TOOL
        )

        checklist_items = self._parse_flat_items(
            compiled.get("checklist_tree", []), rule.id
        )
        examples = compiled.get("examples", [])

        return checklist_items, examples

    async def adjust_for_context(
        self,
        rule: Rule,
        community: Community,
        base_checklist_dicts: list[dict],
        community_context: dict,
        community_posts_sample: Optional[list] = None,
        pinned_items: Optional[list[dict]] = None,
        current_checklist_dicts: Optional[list[dict]] = None,
        relevant_context: Optional[list[dict]] = None,
        custom_context_notes: Optional[list[dict]] = None,
    ) -> tuple[list[ChecklistItem], str]:
        """Pass 2: Adjust a base checklist using community context.

        Args:
            pinned_items: List of dicts with keys: description, context_override_note.
                          These items' calibration must be preserved as-is.
            current_checklist_dicts: If provided, the LLM will describe changes
                          relative to these (the live checklist) instead of the base.
            relevant_context: Per-rule filter. If None, all community context bundles
                          apply. If a list of {dimension, tag}, context is narrowed
                          to only those bundles. Empty list = no community context.
            custom_context_notes: Rule-specific calibration notes ([{text, tag}]) appended
                          to the community context for this rule only.

        Returns (adjusted_items, adjustment_summary).
        If no community_context is provided, returns base items unchanged.
        """
        filtered_context = _filter_context_by_relevant(community_context, relevant_context)

        if not filtered_context and not custom_context_notes:
            items = self._parse_flat_items(base_checklist_dicts, rule.id)
            return items, ""

        logger.info(f"Pass 2: Adjusting rule '{rule.title}' for community context")

        user_prompt = prompts.build_context_adjust_prompt(
            rule_title=rule.title,
            rule_text=rule.text,
            community_name=community.name,
            platform=community.platform,
            base_checklist=base_checklist_dicts,
            community_context=filtered_context or {},
            community_posts_sample=community_posts_sample,
            pinned_items=pinned_items,
            current_checklist=current_checklist_dicts,
            custom_context_notes=custom_context_notes,
        )

        result = await self._call_claude(
            prompts.CONTEXT_ADJUST_SYSTEM, user_prompt, tool=_CONTEXT_ADJUST_TOOL
        )

        adjusted_items = self._parse_flat_items(
            result.get("checklist_tree", []), rule.id
        )
        raw_summary = result.get("adjustment_summary", [])
        # Normalize: accept both list and legacy string
        if isinstance(raw_summary, str):
            summary = [s.strip() for s in raw_summary.split(". ") if s.strip()]
        else:
            summary = list(raw_summary)

        return adjusted_items, summary

    async def compile_rule_two_pass(
        self,
        rule: Rule,
        community: Community,
        other_rules: list[Rule],
        existing_items: Optional[list[ChecklistItem]] = None,
        existing_examples: Optional[list[Example]] = None,
        community_context: Optional[dict] = None,
        community_posts_sample: Optional[list] = None,
        relevant_context: Optional[list[dict]] = None,
        custom_context_notes: Optional[list[dict]] = None,
    ) -> tuple[list[ChecklistItem], list[dict], list[dict], str]:
        """Two-pass compilation: base compile then context adjustment.

        Returns (adjusted_items, example_dicts, base_checklist_dicts, adjustment_summary).
        """
        # Pass 1: context-free
        base_items, examples = await self.compile_rule_base(
            rule, community, other_rules, existing_items, existing_examples,
        )

        base_checklist_dicts = self._items_to_nested_dicts(base_items)

        # Pass 2: adjust for context (filtered per-rule)
        filtered_context = _filter_context_by_relevant(community_context, relevant_context)
        if filtered_context or custom_context_notes:
            adjusted_items, summary = await self.adjust_for_context(
                rule, community, base_checklist_dicts, filtered_context or {},
                community_posts_sample,
                custom_context_notes=custom_context_notes,
            )
        else:
            adjusted_items = base_items
            summary = ""

        return adjusted_items, examples, base_checklist_dicts, summary

    async def generate_community_context(
        self,
        community_name: str,
        platform: str,
        description: str,
        rules_summary: str,
        subscribers: Optional[int] = None,
        sampled_posts: Optional[dict[str, list[dict]]] = None,
        taxonomy: Optional[dict] = None,
    ) -> dict:
        """Generate structured community context (purpose/participants/stakes/tone) from metadata + sampled posts."""
        logger.info(f"Generating community context for '{community_name}'")
        user_prompt = prompts.build_generate_context_prompt(
            community_name=community_name,
            platform=platform,
            description=description,
            rules_summary=rules_summary,
            subscribers=subscribers,
            sampled_posts=sampled_posts,
            taxonomy=taxonomy,
        )
        result = await self._call_claude(
            prompts.GENERATE_CONTEXT_SYSTEM,
            user_prompt,
            tool=_GENERATE_CONTEXT_TOOL,
        )
        # Normalize output
        context = {}
        for dim in ["purpose", "participants", "stakes", "tone"]:
            d = result.get(dim, {})
            notes_raw = d.get("notes", [])
            notes = []
            for n in notes_raw:
                if isinstance(n, str):
                    notes.append({"text": n, "tag": ""})
                elif isinstance(n, dict):
                    notes.append({"text": n.get("text", ""), "tag": n.get("tag", "")})
            context[dim] = {"notes": notes}
        return context

    def _parse_flat_items(
        self, items_data: list[dict], rule_id: str
    ) -> list[ChecklistItem]:
        """Parse items recursively, returning a flat list with parent_id set correctly."""
        result = []
        self._parse_items_recursive(items_data, rule_id, None, result, 0)
        return result

    def _parse_items_recursive(
        self,
        items_data: list[dict],
        rule_id: str,
        parent_id: Optional[str],
        result: list[ChecklistItem],
        order: int,
    ) -> int:
        import uuid
        for item_data in items_data:
            item = ChecklistItem(
                id=str(uuid.uuid4()),  # set explicitly so parent_id links work in memory (before DB flush)
                rule_id=rule_id,
                order=order,
                parent_id=parent_id,
                description=item_data.get("description", ""),
                rule_text_anchor=item_data.get("rule_text_anchor"),
                item_type=item_data.get("item_type", "subjective"),
                logic=item_data.get("logic", {}),
                action=item_data.get("action", "warn"),
                context_influenced=item_data.get("context_influenced", False),
                context_note=item_data.get("context_note"),
                context_change_types=item_data.get("context_change_types"),
                base_description=item_data.get("base_description"),
            )
            result.append(item)
            order += 1

            children_data = item_data.get("children", [])
            if children_data:
                order = self._parse_items_recursive(
                    children_data, rule_id, item.id, result, order
                )

        return order

    async def recompile_with_diff(
        self,
        rule: Rule,
        community: Community,
        other_rules: list[Rule],
        existing_items: list[ChecklistItem],
    ) -> list[dict]:
        """Recompile an existing checklist using diff operations (keep/update/add/delete).

        Returns a list of operation dicts to be applied by the caller.
        """
        logger.info(f"Recompiling rule '{rule.title}' with diff")

        other_rules_summary = self._make_other_rules_summary(
            [r for r in other_rules if r.id != rule.id]
        )
        existing_dicts = [self._checklist_item_to_dict(i) for i in existing_items]

        user_prompt = prompts.build_recompile_prompt(
            rule_text=rule.text,
            community_name=community.name,
            platform=community.platform,
            other_rules_summary=other_rules_summary,
            existing_items=existing_dicts,
        )

        result = await self._call_claude(
            prompts.RECOMPILE_SYSTEM, user_prompt, tool=_RECOMPILE_TOOL
        )
        return result.get("operations", [])

    async def compile_single_item(
        self,
        description: str,
        rule: Rule,
        community: Community,
        existing_items: list[ChecklistItem],
    ) -> dict:
        """Infer item_type and logic for a manually-added checklist item description."""
        logger.info(f"Inferring item type/logic for: {description!r}")
        existing_dicts = [self._checklist_item_to_dict(i) for i in existing_items]
        user_prompt = prompts.build_infer_item_prompt(
            description=description,
            rule_text=rule.text,
            community_name=community.name,
            existing_items=existing_dicts,
        )
        result = await self._call_claude(
            prompts.INFER_ITEM_SYSTEM, user_prompt, tool=_INFER_ITEM_TOOL
        )
        return {
            "item_type": result.get("item_type", "subjective"),
            "logic": result.get("logic", {}),
        }

    async def generate_examples_for_items(
        self,
        rule: Rule,
        community: Community,
        items: list[ChecklistItem],
        existing_examples: Optional[list] = None,
    ) -> list[dict]:
        """Generate one violating example and one borderline example per checklist item in the given list."""
        logger.info(
            f"Generating {len(items)} missing violating example(s) for rule '{rule.title}'"
        )
        item_dicts = [self._checklist_item_to_dict(i) for i in items]
        existing_dicts = (
            [self._example_to_dict(e) for e in existing_examples]
            if existing_examples
            else None
        )
        user_prompt = prompts.build_fill_examples_prompt(
            rule_text=rule.text,
            community_name=community.name,
            platform=community.platform,
            items_needing_examples=item_dicts,
            existing_examples=existing_dicts,
        )
        result = await self._call_claude(
            prompts.FILL_EXAMPLES_SYSTEM, user_prompt, tool=_FILL_EXAMPLES_TOOL
        )
        return result.get("examples", [])

    async def diagnose_rule_health(
        self,
        rule: Rule,
        checklist: list[ChecklistItem],
        health_data: dict,
    ) -> dict:
        """Diagnose per-item health issues and propose typed fixes."""
        logger.info(f"Diagnosing rule health for rule '{rule.title}'")
        checklist_dicts = [self._checklist_item_to_dict(i) for i in checklist]
        user_prompt = prompts.build_diagnose_health_prompt(
            rule_text=rule.text,
            checklist_items=checklist_dicts,
            health_data=health_data,
        )
        result = await self._call_claude(
            prompts.DIAGNOSE_HEALTH_SYSTEM, user_prompt, tool=_DIAGNOSE_TOOL
        )
        return {
            "diagnoses": result.get("diagnoses", []),
            "new_items": result.get("new_items", []),
        }

    async def link_violations_to_items(
        self,
        rule: Rule,
        checklist: list[ChecklistItem],
        violations: list[dict],
    ) -> list[dict]:
        """Match uncovered violations to checklist items via LLM.

        Args:
            rule: The rule these violations belong to.
            checklist: Current checklist items for the rule.
            violations: List of dicts with keys: example_id, label, title, content.

        Returns:
            List of dicts with keys: example_id, checklist_item_id,
            checklist_item_description, reasoning.
        """
        if not violations or not checklist:
            return []

        logger.info(
            f"Linking {len(violations)} uncovered violation(s) to checklist items "
            f"for rule '{rule.title}'"
        )

        checklist_dicts = [self._checklist_item_to_dict(i) for i in checklist]
        user_prompt = prompts.build_link_violations_prompt(
            rule_text=rule.text,
            checklist_items=checklist_dicts,
            violations=violations,
        )

        result = await self._call_claude(
            prompts.LINK_VIOLATIONS_SYSTEM,
            user_prompt,
            tool=_LINK_VIOLATIONS_TOOL,
            model=self.settings.haiku_model,
        )
        return result.get("links", [])

    async def synthesize_rule_from_examples(
        self,
        example_dicts: list[dict],
        community: Community,
    ) -> dict:
        """Infer a new rule from a list of example dicts with keys: label, content, moderator_reasoning."""
        logger.info(
            f"Synthesizing rule from {len(example_dicts)} examples "
            f"for community '{community.name}'"
        )
        user_prompt = prompts.build_synthesize_rule_prompt(
            examples=example_dicts,
            community_name=community.name,
            platform=community.platform,
        )
        result = await self._call_claude(
            prompts.SYNTHESIZE_RULE_SYSTEM, user_prompt, tool=_SYNTHESIZE_RULE_TOOL
        )
        return {
            "title": result.get("title", ""),
            "text": result.get("text", ""),
            "confidence": result.get("confidence", "low"),
            "reasoning": result.get("reasoning", ""),
        }
