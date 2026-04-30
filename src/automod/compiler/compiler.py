"""Rule compiler: translates human-readable rules into executable checklist trees."""

import logging
import uuid
from typing import Any, Optional

import anthropic

from ..config import Settings
from ..db.models import ChecklistItem, Community, Rule
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
    - [{dimension, tag, weight?}, ...] → keep only notes whose (dimension, tag) appears in the
      list AND whose weight is non-zero. The weight (default +1.0) is attached to each kept
      note as `weight` so downstream prompts can express "strongly informs" vs. "counter-signal".
    """
    if not community_context:
        return community_context
    if relevant_context is None:
        return community_context
    if not relevant_context:
        return {}

    # (dim, tag) -> weight (last write wins for duplicates).
    weights: dict[tuple[str, str], float] = {}
    for entry in relevant_context:
        if isinstance(entry, dict):
            dim = entry.get("dimension")
            tag = entry.get("tag")
            w = entry.get("weight", 1.0)
        else:
            dim = getattr(entry, "dimension", None)
            tag = getattr(entry, "tag", None)
            w = getattr(entry, "weight", 1.0)
        if not dim or not tag:
            continue
        try:
            wf = float(w) if w is not None else 1.0
        except (TypeError, ValueError):
            wf = 1.0
        # Non-positive weights mean "this dimension is not relevant to this rule" —
        # drop them so they don't reach the prompt at all.
        if wf <= 0.0:
            continue
        weights[(dim, tag)] = min(1.0, wf)

    filtered: dict = {}
    for dim in ["purpose", "participants", "stakes", "tone"]:
        d = community_context.get(dim, {}) or {}
        notes = d.get("notes", []) or []
        kept_notes = []
        for note in notes:
            tag = note.get("tag", "") if isinstance(note, dict) else ""
            if not tag:
                continue
            if (dim, tag) not in weights:
                continue
            kept = dict(note)
            kept["weight"] = weights[(dim, tag)]
            kept_notes.append(kept)
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
        },
        "required": ["checklist_tree"],
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

_DRAFT_RULE_FROM_CONTEXT_TOOL = {
    "name": "draft_rule_from_context",
    "description": "Draft a community rule grounded in the target community's context notes and peer-community rules. Every clause must cite at least one grounding source.",
    "input_schema": {
        "type": "object",
        "properties": {
            "draft_text": {
                "type": "string",
                "description": "The full rule text as it would appear in the community rules.",
            },
            "clauses": {
                "type": "array",
                "description": "The rule decomposed into self-contained clauses, each with citations.",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "citations": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {
                                        "type": "string",
                                        "enum": ["context", "peer_rule"],
                                    },
                                    "dimension": {
                                        "type": "string",
                                        "description": "For kind=context: one of purpose|participants|stakes|tone.",
                                    },
                                    "tag": {
                                        "type": "string",
                                        "description": "For kind=context: the tag of the cited note.",
                                    },
                                    "community_name": {
                                        "type": "string",
                                        "description": "For kind=peer_rule: the name of the source community.",
                                    },
                                    "rule_title": {
                                        "type": "string",
                                        "description": "For kind=peer_rule: the title of the cited peer rule.",
                                    },
                                    "shared_tag": {
                                        "type": "string",
                                        "description": "For kind=peer_rule: a tag shared between the peer community's context and the target community's context that justifies the borrowing.",
                                    },
                                },
                                "required": ["kind"],
                            },
                        },
                    },
                    "required": ["text", "citations"],
                },
            },
            "suggested_relevant_context": {
                "type": "array",
                "description": "Which {dimension, tag} bundles from the target community should be marked relevant for this rule.",
                "items": {
                    "type": "object",
                    "properties": {
                        "dimension": {"type": "string"},
                        "tag": {"type": "string"},
                    },
                    "required": ["dimension", "tag"],
                },
            },
        },
        "required": ["draft_text", "clauses", "suggested_relevant_context"],
    },
}

_MATCH_RELEVANT_CONTEXT_TOOL = {
    "name": "submit_relevant_context",
    "description": "Pick the (dimension, tag) bundles from the community context that actually inform how this rule should be moderated, with a per-tag weight in [-1.0, 1.0].",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant_context": {
                "type": "array",
                "description": "Tags that inform this rule. Omit tags that have no bearing. Use negative weights for counter-signals (the tag explicitly suggests calibrating AWAY).",
                "items": {
                    "type": "object",
                    "properties": {
                        "dimension": {
                            "type": "string",
                            "enum": ["purpose", "participants", "stakes", "tone"],
                        },
                        "tag": {"type": "string"},
                        "weight": {
                            "type": "number",
                            "description": "Strength and direction of influence on this rule. +1 = strongly informs; +0.5 = supports; -0.5 = counter-signal; -1 = strong counter-signal. Avoid 0 — drop the tag instead.",
                            "minimum": -1.0,
                            "maximum": 1.0,
                        },
                        "rationale": {
                            "type": "string",
                            "description": "One short clause explaining why this tag matters for this rule.",
                        },
                    },
                    "required": ["dimension", "tag", "weight"],
                },
            },
        },
        "required": ["relevant_context"],
    },
}

_DIAGNOSE_TOOL = {
    "name": "submit_health_diagnoses",
    "description": "Submit per-item health diagnoses with proposed fixes at the appropriate level (logic / rule_text / context).",
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
                        "proposed_levels": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["logic", "rule_text", "context"]},
                            "description": "Levels to emit suggestions at. Default per action: tighten_rubric/split_item → ['logic','rule_text']; adjust_threshold/promote_to_deterministic → ['logic']. Add 'context' only if an L2 trigger fires.",
                        },
                        "level_reasoning": {
                            "type": "string",
                            "description": "Brief: which action type, and (if 'context' is included) which L2 trigger fired (against_existing_context | cross_rule).",
                        },
                        "text_change": {
                            "type": "object",
                            "description": "Required when proposed_levels contains 'rule_text'. Full proposed rule text, plus rationale.",
                            "properties": {
                                "proposed_text": {"type": "string"},
                                "rationale": {"type": "string"},
                            },
                        },
                        "context_change": {
                            "type": "object",
                            "description": "Required when proposed_levels contains 'context'. Note to add/edit on the rule's custom_context_notes.",
                            "properties": {
                                "proposed_note": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "tag": {"type": "string"},
                                    },
                                },
                                "l2_trigger": {
                                    "type": "string",
                                    "enum": ["against_existing_context", "cross_rule"],
                                },
                                "rationale": {"type": "string"},
                            },
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
                        "proposed_levels": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["logic", "rule_text", "context"]},
                            "description": "Default for add_item: ['rule_text'] when the violation has no anchor in current text (text gap); ['logic'] only if making implicit text explicit.",
                        },
                        "level_reasoning": {"type": "string"},
                        "text_change": {
                            "type": "object",
                            "description": "Required when proposed_levels contains 'rule_text'. Full proposed rule text, plus rationale.",
                            "properties": {
                                "proposed_text": {"type": "string"},
                                "rationale": {"type": "string"},
                            },
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
        },
        "required": ["checklist_tree"],
    },
}

_CONTEXT_ADJUST_TOOL = {
    "name": "submit_context_diff",
    "description": "Submit diff operations that calibrate a checklist for a specific community context",
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
                        "rule_text_anchor": {"type": ["string", "null"]},
                        "item_type": {
                            "type": "string",
                            "enum": ["deterministic", "structural", "subjective"],
                        },
                        "logic": {"type": "object"},
                        "action": {
                            "type": "string",
                            "enum": ["remove", "warn", "continue"],
                        },
                        "context_influenced": {"type": "boolean"},
                        "context_note": {"type": ["string", "null"]},
                        "context_change_types": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["threshold", "rubric", "description", "action", "new_item", "pattern", "check"],
                            },
                        },
                        "children": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["op"],
                },
            },
            "adjustment_summary": {
                "type": "string",
                "description": "Single short sentence (under 15 words) stating the purpose of these adjustments. Empty string if no changes.",
            },
        },
        "required": ["operations", "adjustment_summary"],
    },
}

_INTENT_TRANSLATION_TOOL = {
    "name": "submit_intent_translation",
    "description": "Decide whether a moderator's casual intent message implies a rule_text edit, and if so, propose the minimal new rule text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["propose", "no_change"],
            },
            "proposed_text": {
                "type": "string",
                "description": "Full proposed rule text. Required when decision=propose.",
            },
            "rationale": {
                "type": "string",
                "description": "One short sentence describing what the edit changes and why. Required when decision=propose.",
            },
            "no_change_reason": {
                "type": "string",
                "description": "One short line explaining why no edit is needed. Required when decision=no_change.",
            },
        },
        "required": ["decision"],
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
            "user_edited_logic": bool(item.user_edited_logic),
        }
        if item.parent_id:
            d["parent_id"] = item.parent_id
        return d

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
        community_context: Optional[dict] = None,
    ) -> list[ChecklistItem]:
        """Compile actionable rule into a checklist tree."""
        logger.info(f"Compiling rule '{rule.title}' for community '{community.name}'")

        other_rules_summary = self._make_other_rules_summary(
            [r for r in other_rules if r.id != rule.id]
        )

        existing_checklist_dicts = None
        if existing_items:
            existing_checklist_dicts = [self._checklist_item_to_dict(i) for i in existing_items]

        user_prompt = prompts.build_compile_prompt(
            rule_title=rule.title,
            rule_text=rule.text,
            community_name=community.name,
            platform=community.platform,
            other_rules_summary=other_rules_summary,
            existing_checklist=existing_checklist_dicts,
            community_context=community_context,
        )

        compiled = await self._call_claude(prompts.COMPILE_SYSTEM, user_prompt, tool=_COMPILE_TOOL)

        checklist_items = self._parse_flat_items(
            compiled.get("checklist_tree", []), rule.id
        )

        return checklist_items

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
    ) -> list[ChecklistItem]:
        """Pass 1: Compile rule without community context (context-free baseline)."""
        logger.info(f"Pass 1: Compiling rule '{rule.title}' without context")

        other_rules_summary = self._make_other_rules_summary(
            [r for r in other_rules if r.id != rule.id]
        )

        existing_checklist_dicts = None
        if existing_items:
            existing_checklist_dicts = [self._checklist_item_to_dict(i) for i in existing_items]

        user_prompt = prompts.build_no_context_compile_prompt(
            rule_title=rule.title,
            rule_text=rule.text,
            community_name=community.name,
            platform=community.platform,
            other_rules_summary=other_rules_summary,
            existing_checklist=existing_checklist_dicts,
        )

        compiled = await self._call_claude(
            prompts.NO_CONTEXT_COMPILE_SYSTEM, user_prompt, tool=_NO_CONTEXT_COMPILE_TOOL
        )

        return self._parse_flat_items(compiled.get("checklist_tree", []), rule.id)

    async def adjust_for_context(
        self,
        rule: Rule,
        community: Community,
        current_items: list[ChecklistItem],
        community_context: dict,
        pinned_item_ids: Optional[list[str]] = None,
        relevant_context: Optional[list[dict]] = None,
        custom_context_notes: Optional[list[dict]] = None,
        other_rules: Optional[list[Rule]] = None,
    ) -> tuple[list[ChecklistItem], str, list[dict]]:
        """Pass 2: Calibrate an existing checklist for community context using diff operations.

        Args:
            current_items: The starting-point checklist. For initial compilation this is
                          the Pass 1 base output; for live preview it is the live checklist.
                          Items must have stable ids — operations reference them by id.
            pinned_item_ids: ids of items the LLM must "keep" unconditionally.
            relevant_context: Per-rule filter. If None, all community context bundles
                          apply. If a list of {dimension, tag}, context is narrowed
                          to only those bundles. Empty list = no community context.
            custom_context_notes: Rule-specific calibration notes ([{text, tag}]) appended
                          to the community context for this rule only.

        Returns (adjusted_items, adjustment_summary, operations).
        If no context applies, returns current_items unchanged with empty summary and ops.
        """
        filtered_context = _filter_context_by_relevant(community_context, relevant_context)

        if not filtered_context and not custom_context_notes:
            return list(current_items), "", []

        logger.info(f"Pass 2: Calibrating rule '{rule.title}' for community context")

        current_dicts = self._items_to_diff_dicts(current_items)

        other_rules_summary = self._make_other_rules_summary(
            [r for r in (other_rules or []) if r.id != rule.id]
        )

        user_prompt = prompts.build_context_adjust_prompt(
            rule_title=rule.title,
            rule_text=rule.text,
            community_name=community.name,
            platform=community.platform,
            current_checklist=current_dicts,
            community_context=filtered_context or {},
            pinned_item_ids=pinned_item_ids,
            custom_context_notes=custom_context_notes,
            other_rules_summary=other_rules_summary,
        )

        result = await self._call_claude(
            prompts.CONTEXT_ADJUST_SYSTEM, user_prompt, tool=_CONTEXT_ADJUST_TOOL
        )

        operations = result.get("operations", []) or []
        raw_summary = result.get("adjustment_summary", "")
        if isinstance(raw_summary, list):
            summary = " ".join(s.strip() for s in raw_summary if s and s.strip())
        else:
            summary = (raw_summary or "").strip()

        adjusted_items = self._apply_context_ops(current_items, operations, rule.id)
        return adjusted_items, summary, operations

    def _items_to_diff_dicts(self, items: list[ChecklistItem]) -> list[dict]:
        """Build a nested dict tree (with ids) for the diff prompt input."""
        children_map: dict[Optional[str], list[ChecklistItem]] = {}
        for item in sorted(items, key=lambda x: x.order):
            children_map.setdefault(item.parent_id, []).append(item)

        def build(parent_id: Optional[str]) -> list[dict]:
            return [
                {
                    "id": item.id,
                    "description": item.description,
                    "rule_text_anchor": item.rule_text_anchor,
                    "item_type": item.item_type,
                    "logic": item.logic,
                    "action": item.action,
                    "children": build(item.id),
                }
                for item in children_map.get(parent_id, [])
            ]

        return build(None)

    def _apply_context_ops(
        self,
        current_items: list[ChecklistItem],
        operations: list[dict],
        rule_id: str,
    ) -> list[ChecklistItem]:
        """Apply context-adjustment ops to current items, producing a new flat ChecklistItem list.

        Operations may be nested (children inline) and reference items by existing_id.
        Items without a corresponding op (and not descendants of a deleted op) are kept as-is.
        """
        items_by_id = {it.id: it for it in current_items}
        children_of: dict[Optional[str], list[ChecklistItem]] = {}
        for it in sorted(current_items, key=lambda x: x.order):
            children_of.setdefault(it.parent_id, []).append(it)

        result: list[ChecklistItem] = []
        order_counter = [0]
        # Tracks every existing item id consumed by an op anywhere in the tree.
        # The fallback paths (unreferenced top-level / unreferenced children) consult
        # this so an item that an op moved under a new parent isn't *also* re-emitted
        # in its original position — which would produce a duplicate primary key.
        consumed_ids: set[str] = set()

        def clone_item(
            src: ChecklistItem,
            parent_id: Optional[str],
            overrides: Optional[dict] = None,
        ) -> ChecklistItem:
            o = overrides or {}
            new = ChecklistItem(
                id=src.id,
                rule_id=rule_id,
                order=order_counter[0],
                parent_id=parent_id,
                description=o.get("description", src.description),
                rule_text_anchor=o.get("rule_text_anchor", src.rule_text_anchor),
                item_type=o.get("item_type", src.item_type),
                logic=o.get("logic", src.logic),
                action=o.get("action", src.action),
                context_influenced=o.get("context_influenced", src.context_influenced),
                context_note=o.get("context_note", src.context_note),
                context_change_types=o.get("context_change_types", src.context_change_types),
                base_description=src.base_description,
                context_pinned=src.context_pinned,
                context_override_note=src.context_override_note,
                pinned_tags=src.pinned_tags,
            )
            order_counter[0] += 1
            return new

        def make_added_item(op: dict, parent_id: Optional[str]) -> ChecklistItem:
            new = ChecklistItem(
                id=str(uuid.uuid4()),
                rule_id=rule_id,
                order=order_counter[0],
                parent_id=parent_id,
                description=op.get("description", ""),
                rule_text_anchor=op.get("rule_text_anchor"),
                item_type=op.get("item_type", "subjective"),
                logic=op.get("logic") or {},
                action=op.get("action", "warn"),
                context_influenced=op.get("context_influenced", True),
                context_note=op.get("context_note"),
                context_change_types=op.get("context_change_types") or ["new_item"],
            )
            order_counter[0] += 1
            return new

        def append_subtree_unchanged(item: ChecklistItem, parent_id: Optional[str]) -> None:
            if item.id in consumed_ids:
                return
            consumed_ids.add(item.id)
            cloned = clone_item(item, parent_id)
            result.append(cloned)
            for child in children_of.get(item.id, []):
                append_subtree_unchanged(child, cloned.id)

        def process_ops(ops: list[dict], parent_id: Optional[str]) -> set[str]:
            """Process ops at one level. Returns set of existing_ids that were referenced."""
            referenced: set[str] = set()
            for op in ops:
                kind = op.get("op")
                if kind == "add":
                    new = make_added_item(op, parent_id)
                    result.append(new)
                    process_ops(op.get("children") or [], new.id)
                    continue

                eid = op.get("existing_id")
                if not eid or eid not in items_by_id:
                    continue
                referenced.add(eid)
                # An LLM op may reference the same existing id twice (e.g. moves it
                # somewhere new and a later op re-references it). Take the first claim
                # and skip the rest so we never emit two rows with the same PK.
                if eid in consumed_ids:
                    continue
                consumed_ids.add(eid)
                src = items_by_id[eid]

                if kind == "delete":
                    continue  # drop subtree

                if kind == "update":
                    overrides = {
                        k: op[k] for k in (
                            "description", "rule_text_anchor", "item_type",
                            "logic", "action", "context_influenced",
                            "context_note", "context_change_types",
                        )
                        if k in op
                    }
                    cloned = clone_item(src, parent_id, overrides)
                else:  # keep (or unknown — treat as keep)
                    cloned = clone_item(src, parent_id)
                result.append(cloned)

                child_ops = op.get("children") or []
                child_referenced = process_ops(child_ops, cloned.id)
                # Children of this item not referenced by any op — keep unchanged.
                for child in children_of.get(eid, []):
                    if child.id not in child_referenced:
                        append_subtree_unchanged(child, cloned.id)
            return referenced

        top_referenced = process_ops(operations, None)
        # Top-level items not referenced by any op — keep unchanged at the end.
        for item in children_of.get(None, []):
            if item.id not in top_referenced:
                append_subtree_unchanged(item, None)

        return result

    async def compile_rule_two_pass(
        self,
        rule: Rule,
        community: Community,
        other_rules: list[Rule],
        existing_items: Optional[list[ChecklistItem]] = None,
        community_context: Optional[dict] = None,
        relevant_context: Optional[list[dict]] = None,
        custom_context_notes: Optional[list[dict]] = None,
    ) -> tuple[list[ChecklistItem], list[dict], str]:
        """Two-pass compilation: base compile then context adjustment via diff ops.

        Returns (adjusted_items, base_checklist_dicts, adjustment_summary).
        """
        # Pass 1: context-free
        base_items = await self.compile_rule_base(
            rule, community, other_rules, existing_items,
        )

        base_checklist_dicts = self._items_to_nested_dicts(base_items)

        # Pass 2: calibrate for context via diff ops (filtered per-rule)
        filtered_context = _filter_context_by_relevant(community_context, relevant_context)
        if filtered_context or custom_context_notes:
            adjusted_items, summary, _ops = await self.adjust_for_context(
                rule=rule,
                community=community,
                current_items=base_items,
                community_context=filtered_context or {},
                custom_context_notes=custom_context_notes,
                other_rules=other_rules,
            )
        else:
            adjusted_items = base_items
            summary = ""

        return adjusted_items, base_checklist_dicts, summary

    async def generate_community_context(
        self,
        community_name: str,
        platform: str,
        description: str,
        rules_summary: str,
        subscribers: Optional[int] = None,
        sampled_posts: Optional[dict[str, list[dict]]] = None,
        taxonomy: Optional[dict] = None,
        acceptable_samples: Optional[list[dict]] = None,
        unacceptable_samples: Optional[list[dict]] = None,
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
            acceptable_samples=acceptable_samples,
            unacceptable_samples=unacceptable_samples,
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
        force_item_type: Optional[str] = None,
    ) -> dict:
        """Infer item_type and logic for a manually-added checklist item description.

        When force_item_type is set, the LLM is told to generate logic for that
        specific type (so the moderator's type choice in the UI is honored).
        Otherwise both type and logic are inferred.
        """
        logger.info(
            f"Inferring item logic for: {description!r}"
            + (f" (forced type: {force_item_type})" if force_item_type else "")
        )
        existing_dicts = [self._checklist_item_to_dict(i) for i in existing_items]
        user_prompt = prompts.build_infer_item_prompt(
            description=description,
            rule_text=rule.text,
            community_name=community.name,
            existing_items=existing_dicts,
            force_item_type=force_item_type,
        )
        result = await self._call_claude(
            prompts.INFER_ITEM_SYSTEM, user_prompt, tool=_INFER_ITEM_TOOL
        )
        item_type = force_item_type or result.get("item_type", "subjective")
        return {
            "item_type": item_type,
            "logic": result.get("logic", {}),
        }

    async def diagnose_rule_health(
        self,
        rule: Rule,
        checklist: list[ChecklistItem],
        health_data: dict,
        community_context: Optional[dict] = None,
        sibling_rules: Optional[list[dict]] = None,
    ) -> dict:
        """Diagnose per-item health issues and propose typed fixes at the appropriate level."""
        logger.info(f"Diagnosing rule health for rule '{rule.title}'")
        checklist_dicts = [self._checklist_item_to_dict(i) for i in checklist]
        user_prompt = prompts.build_diagnose_health_prompt(
            rule_text=rule.text,
            checklist_items=checklist_dicts,
            health_data=health_data,
            community_context=community_context,
            sibling_rules=sibling_rules,
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

    async def match_relevant_context(
        self,
        rule_title: str,
        rule_text: str,
        community_name: str,
        community_context: dict,
    ) -> list[dict]:
        """Pick relevant (dimension, tag) tags + weights for a rule via LLM.

        Returns a list of {dimension, tag, weight} dicts. Caller should validate that
        every (dimension, tag) returned actually exists in community_context — fabricated
        pairs are dropped at the API layer.
        """
        # Quick out: no tagged notes at all means nothing to match against.
        has_any_tag = False
        for dim in ("purpose", "participants", "stakes", "tone"):
            d = (community_context or {}).get(dim) or {}
            for note in d.get("notes") or []:
                tag = note.get("tag", "") if isinstance(note, dict) else ""
                if tag:
                    has_any_tag = True
                    break
            if has_any_tag:
                break
        if not has_any_tag:
            return []

        logger.info(f"Matching relevant context tags for rule '{rule_title}' in '{community_name}'")
        user_prompt = prompts.build_match_relevant_context_prompt(
            rule_title=rule_title,
            rule_text=rule_text,
            community_name=community_name,
            community_context=community_context or {},
        )
        result = await self._call_claude(
            prompts.MATCH_RELEVANT_CONTEXT_SYSTEM,
            user_prompt,
            tool=_MATCH_RELEVANT_CONTEXT_TOOL,
            model=self.settings.haiku_model,
        )
        raw = result.get("relevant_context") or []
        out: list[dict] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            dim = entry.get("dimension")
            tag = entry.get("tag")
            if not dim or not tag:
                continue
            try:
                w = float(entry.get("weight", 1.0))
            except (TypeError, ValueError):
                w = 1.0
            w = max(-1.0, min(1.0, w))
            if w == 0.0:
                continue
            out.append({"dimension": dim, "tag": tag, "weight": w})
        return out

    async def draft_rule_from_context(
        self,
        title: str,
        target_community_name: str,
        target_context: dict,
        peer_rules: list[dict],
    ) -> dict:
        """Draft a rule grounded in the target community's context + peer-community rules.

        peer_rules entries: {community_name, rule_title, rule_text, shared_tags: [str]}.
        Returns: {draft_text, clauses[{text, citations[]}], suggested_relevant_context[]}.
        Validation of citations is done by the caller (api/rules.py).
        """
        logger.info(
            f"Drafting rule '{title}' for community '{target_community_name}' "
            f"with {len(peer_rules)} peer rule(s)"
        )
        user_prompt = prompts.build_draft_rule_from_context_prompt(
            title=title,
            target_community_name=target_community_name,
            target_context=target_context or {},
            peer_rules=peer_rules,
        )
        result = await self._call_claude(
            prompts.DRAFT_RULE_FROM_CONTEXT_SYSTEM,
            user_prompt,
            tool=_DRAFT_RULE_FROM_CONTEXT_TOOL,
        )
        return {
            "draft_text": result.get("draft_text", ""),
            "clauses": result.get("clauses", []),
            "suggested_relevant_context": result.get("suggested_relevant_context", []),
        }

    async def translate_intent_to_suggestion(
        self,
        rule: Rule,
        community: Community,
        new_message: str,
        recent_messages: Optional[list[dict]] = None,
        anchored_post: Optional[dict] = None,
    ) -> dict:
        """Translate a casual moderator intent message into either a rule_text suggestion or a no-change reason.

        Returns:
            {"decision": "propose", "proposed_text": str, "rationale": str}
            or {"decision": "no_change", "no_change_reason": str}
        """
        logger.info(f"Translating intent message for rule '{rule.title}'")
        user_prompt = prompts.build_intent_translation_prompt(
            rule_title=rule.title,
            rule_text=rule.text,
            community_name=community.name,
            new_message=new_message,
            recent_messages=recent_messages,
            anchored_post=anchored_post,
        )
        result = await self._call_claude(
            prompts.INTENT_TRANSLATION_SYSTEM,
            user_prompt,
            tool=_INTENT_TRANSLATION_TOOL,
            model=self.settings.sonnet_model,
        )
        decision = result.get("decision", "no_change")
        if decision == "propose":
            proposed = (result.get("proposed_text") or "").strip()
            rationale = (result.get("rationale") or "").strip()
            if not proposed or proposed == rule.text.strip():
                return {
                    "decision": "no_change",
                    "no_change_reason": rationale or "Proposal matched current rule text.",
                }
            return {
                "decision": "propose",
                "proposed_text": proposed,
                "rationale": rationale,
            }
        return {
            "decision": "no_change",
            "no_change_reason": (result.get("no_change_reason") or "No edit implied.").strip(),
        }

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
