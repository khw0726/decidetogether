"""Rule compiler: translates human-readable rules into executable checklist trees."""

import logging
from typing import Any, Optional

import anthropic

from ..config import Settings
from ..db.models import ChecklistItem, Community, Example, Rule
from . import prompts

logger = logging.getLogger(__name__)

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
            "reasoning": {"type": "string"},
        },
        "required": ["rule_type", "reasoning"],
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
                        "rule_text_anchor": {"type": ["string", "null"]},
                        "item_type": {
                            "type": "string",
                            "enum": ["deterministic", "structural", "subjective"],
                        },
                        "logic": {"type": "object"},
                        "action": {
                            "type": "string",
                            "enum": ["remove", "flag", "continue"],
                        },
                        "children": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": [
                        "description", "item_type", "logic",
                        "action", "children",
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
                            "type": ["string", "null"],
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
                        "rule_text_anchor": {"type": ["string", "null"]},
                        "item_type": {
                            "type": "string",
                            "enum": ["deterministic", "structural", "subjective"],
                        },
                        "logic": {"type": "object"},
                        "action": {
                            "type": "string",
                            "enum": ["remove", "flag", "continue"],
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

_SUGGEST_FROM_EXAMPLES_TOOL = {
    "name": "submit_suggestions",
    "description": "Submit checklist and rule text improvement suggestions",
    "input_schema": {
        "type": "object",
        "properties": {
            "suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "suggestion_type": {
                            "type": "string",
                            "enum": ["checklist", "rule_text"],
                        },
                        "target": {"type": ["string", "null"]},
                        "description": {"type": "string"},
                        "proposed_change": {"type": "object"},
                        "reasoning": {"type": "string"},
                    },
                    "required": [
                        "suggestion_type", "target", "description",
                        "proposed_change", "reasoning",
                    ],
                },
            },
        },
        "required": ["suggestions"],
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
                        "related_checklist_item_description": {"type": "string"},
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

_SUGGEST_FROM_CHECKLIST_TOOL = {
    "name": "submit_checklist_suggestions",
    "description": "Submit suggested examples and optional rule text updates",
    "input_schema": {
        "type": "object",
        "properties": {
            "suggested_examples": {
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
                    },
                    "required": ["label", "content", "relevance_note"],
                },
            },
            "rule_text_suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "proposed_text": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["description", "proposed_text", "reasoning"],
                },
            },
        },
        "required": ["suggested_examples", "rule_text_suggestions"],
    },
}


class RuleCompiler:
    def __init__(self, client: anthropic.AsyncAnthropic, settings: Settings):
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
        return {
            "id": item.id,
            "description": item.description,
            "rule_text_anchor": item.rule_text_anchor,
            "item_type": item.item_type,
            "logic": item.logic,
            "action": item.action,
            "order": item.order,
        }

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
        result = []
        for i, item_data in enumerate(items_data):
            item = ChecklistItem(
                rule_id=rule_id,
                order=order_offset + i,
                parent_id=parent_id,
                description=item_data.get("description", ""),
                rule_text_anchor=item_data.get("rule_text_anchor"),
                item_type=item_data.get("item_type", "subjective"),
                logic=item_data.get("logic", {}),
                action=item_data.get("action", "flag"),
            )
            result.append(item)

            # Process children - they'll be linked after IDs are assigned
            children_data = item_data.get("children", [])
            if children_data:
                item._pending_children = children_data  # type: ignore
            else:
                item._pending_children = []  # type: ignore

        return result

    async def compile_rule(
        self,
        rule: Rule,
        community: Community,
        other_rules: list[Rule],
        existing_items: Optional[list[ChecklistItem]] = None,
        existing_examples: Optional[list[Example]] = None,
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
            rule_text=rule.text,
            community_name=community.name,
            platform=community.platform,
            other_rules_summary=other_rules_summary,
            existing_checklist=existing_checklist_dicts,
            existing_examples=existing_example_dicts,
        )

        compiled = await self._call_claude(prompts.COMPILE_SYSTEM, user_prompt, tool=_COMPILE_TOOL)

        # Parse checklist tree
        checklist_items = self._parse_flat_items(
            compiled.get("checklist_tree", []), rule.id
        )

        # Return examples as raw dicts (caller persists them)
        examples = compiled.get("examples", [])

        return checklist_items, examples

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
        for item_data in items_data:
            item = ChecklistItem(
                rule_id=rule_id,
                order=order,
                parent_id=parent_id,
                description=item_data.get("description", ""),
                rule_text_anchor=item_data.get("rule_text_anchor"),
                item_type=item_data.get("item_type", "subjective"),
                logic=item_data.get("logic", {}),
                action=item_data.get("action", "flag"),
            )
            result.append(item)
            order += 1

            children_data = item_data.get("children", [])
            if children_data:
                # Store reference so caller can link parent_id after DB flush
                item._children_data = children_data  # type: ignore
            else:
                item._children_data = []  # type: ignore

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

    async def suggest_from_examples(
        self,
        rule: Rule,
        checklist: list[ChecklistItem],
        examples: list[Example],
    ) -> list[dict]:
        """Generate checklist/rule text suggestions from examples."""
        logger.info(f"Generating suggestions from examples for rule '{rule.title}'")

        checklist_dicts = [self._checklist_item_to_dict(i) for i in checklist]
        example_dicts = [self._example_to_dict(e) for e in examples]

        # Get community name (we use rule's community_id as fallback)
        community_name = f"community ({rule.community_id})"

        user_prompt = prompts.build_suggest_from_examples_prompt(
            rule_text=rule.text,
            checklist_items=checklist_dicts,
            examples=example_dicts,
            community_name=community_name,
        )

        result = await self._call_claude(
            prompts.SUGGEST_FROM_EXAMPLES_SYSTEM, user_prompt, tool=_SUGGEST_FROM_EXAMPLES_TOOL
        )
        return result.get("suggestions", [])

    async def suggest_from_checklist(
        self,
        rule: Rule,
        checklist: list[ChecklistItem],
        examples: list[Example],
        community_name: str = "",
    ) -> list[dict]:
        """Generate example/rule text suggestions from checklist changes."""
        logger.info(f"Generating suggestions from checklist for rule '{rule.title}'")

        checklist_dicts = [self._checklist_item_to_dict(i) for i in checklist]
        example_dicts = [self._example_to_dict(e) for e in examples]

        user_prompt = prompts.build_suggest_from_checklist_prompt(
            rule_text=rule.text,
            checklist_items=checklist_dicts,
            existing_examples=example_dicts,
            community_name=community_name or f"community ({rule.community_id})",
        )

        result = await self._call_claude(
            prompts.SUGGEST_FROM_CHECKLIST_SYSTEM, user_prompt, tool=_SUGGEST_FROM_CHECKLIST_TOOL
        )

        suggestions = []
        for ex in result.get("suggested_examples", []):
            suggestions.append({
                "suggestion_type": "example",
                "content": ex,
                "reasoning": ex.get("relevance_note", ""),
            })
        for rt in result.get("rule_text_suggestions", []):
            suggestions.append({
                "suggestion_type": "rule_text",
                "content": rt,
                "reasoning": rt.get("reasoning", ""),
            })
        return suggestions

    async def generate_examples_for_items(
        self,
        rule: Rule,
        community: Community,
        items: list[ChecklistItem],
        existing_examples: Optional[list] = None,
    ) -> list[dict]:
        """Generate one violating example per checklist item in the given list."""
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
