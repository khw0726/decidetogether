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
                        "combine_mode": {
                            "type": "string",
                            "enum": ["all_must_pass", "any_must_pass"],
                        },
                        "fail_action": {
                            "type": "string",
                            "enum": ["remove", "flag", "continue"],
                        },
                        "children": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": [
                        "description", "item_type", "logic",
                        "combine_mode", "fail_action", "children",
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
                            "enum": ["positive", "negative", "borderline"],
                        },
                        "content": {"type": "object"},
                        "relevance_note": {"type": "string"},
                    },
                    "required": ["label", "content", "relevance_note"],
                },
            },
        },
        "required": ["checklist_tree", "examples"],
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
                            "enum": ["positive", "negative", "borderline"],
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
            "combine_mode": item.combine_mode,
            "fail_action": item.fail_action,
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
                combine_mode=item_data.get("combine_mode", "all_must_pass"),
                fail_action=item_data.get("fail_action", "flag"),
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
                combine_mode=item_data.get("combine_mode", "all_must_pass"),
                fail_action=item_data.get("fail_action", "flag"),
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

    async def recompile_rule(
        self,
        rule: Rule,
        community: Community,
        other_rules: list[Rule],
        existing_items: list[ChecklistItem],
        existing_examples: list[Example],
    ) -> dict[str, Any]:
        """Recompile with diff - returns suggested changes, not applied."""
        logger.info(f"Recompiling rule '{rule.title}' with diff")

        new_items, new_examples = await self.compile_rule(
            rule, community, other_rules, existing_items, existing_examples
        )

        # Build diff between existing and new
        existing_descriptions = {item.description: item for item in existing_items}
        new_descriptions = {item.description: item for item in new_items}

        added = [
            self._checklist_item_to_dict(item)
            for desc, item in new_descriptions.items()
            if desc not in existing_descriptions
        ]
        removed = [
            self._checklist_item_to_dict(item)
            for desc, item in existing_descriptions.items()
            if desc not in new_descriptions
        ]
        modified = []
        for desc in set(existing_descriptions) & set(new_descriptions):
            old = existing_descriptions[desc]
            new = new_descriptions[desc]
            if (
                old.logic != new.logic
                or old.fail_action != new.fail_action
                or old.combine_mode != new.combine_mode
            ):
                modified.append({
                    "old": self._checklist_item_to_dict(old),
                    "new": self._checklist_item_to_dict(new),
                })

        return {
            "added": added,
            "removed": removed,
            "modified": modified,
            "new_examples": new_examples,
            "new_items_raw": [self._checklist_item_to_dict(i) for i in new_items],
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
