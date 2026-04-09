"""Subjective (LLM-based) checklist item evaluation with Haiku→Sonnet escalation."""

import logging
from typing import Any

import anthropic

from ..config import Settings
from ..db.models import ChecklistItem, Example
from ..compiler.prompts import SUBJECTIVE_EVAL_SYSTEM, build_subjective_eval_prompt

logger = logging.getLogger(__name__)

_EVAL_TOOL = {
    "name": "submit_evaluations",
    "description": "Submit batch moderation evaluation results",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "triggered": {"type": "boolean"},
                        "confidence": {"type": "number"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["item_id", "triggered", "confidence", "reasoning"],
                },
            },
        },
        "required": ["results"],
    },
}


class SubjectiveEvaluator:
    def __init__(self, client: anthropic.AsyncAnthropic, settings: Settings):
        self.client = client
        self.settings = settings

    async def _call_model(self, content: str | list[dict[str, Any]], model: str) -> dict[str, Any]:
        response = await self.client.messages.create(
            model=model,
            max_tokens=4096,
            system=SUBJECTIVE_EVAL_SYSTEM,
            messages=[{"role": "user", "content": content}],
            tools=[_EVAL_TOOL],
            tool_choice={"type": "tool", "name": _EVAL_TOOL["name"]},
        )
        return response.content[0].input

    def _build_content(self, post: dict[str, Any], text_prompt: str) -> str | list[dict[str, Any]]:
        """Return a multimodal content list if the post has image URLs, otherwise plain text."""
        media = post.get("content", {}).get("media", [])
        image_urls = [m for m in media if isinstance(m, str) and m.startswith("http")][:10]
        if not image_urls:
            return text_prompt
        blocks: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "url", "url": url}}
            for url in image_urls
        ]
        blocks.append({"type": "text", "text": text_prompt})
        return blocks

    def _prepare_item_dict(self, item: ChecklistItem) -> dict[str, Any]:
        """Convert a checklist item to the dict format for the evaluation prompt."""
        return {
            "item_id": item.id,
            "description": item.description,
            "prompt_template": item.logic.get("prompt_template", ""),
            "rubric": item.logic.get("rubric", ""),
            "threshold": item.logic.get("threshold", 0.7),
        }

    def _prepare_example_dicts(self, examples: list[Example]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split examples into primary (clear) and borderline (calibration) dicts."""
        compliant = [
            {"label": ex.label, "content": ex.content}
            for ex in examples
            if ex.label == "compliant"
        ]
        violating = [
            {"label": ex.label, "content": ex.content}
            for ex in examples
            if ex.label == "violating"
        ]

        # Keep equal counts of compliant/violating examples (up to 4 total => 2 each)
        n = min(len(compliant), len(violating), 2)
        primary = compliant[:n] + violating[:n]
        borderline = [
            {"label": ex.label, "content": ex.content}
            for ex in examples
            if ex.label == "borderline"
        ][:8]
        return primary, borderline

    async def evaluate_batch(
        self,
        items: list[ChecklistItem],
        post: dict[str, Any],
        community_name: str,
        examples: list[Example],
    ) -> list[dict[str, Any]]:
        """Batch evaluate multiple subjective items in a single LLM call.

        Returns list of {item_id, triggered, confidence, reasoning}.
        Uses Haiku first; escalates low-confidence items to Sonnet.
        triggered=True means the item's question is answered YES (violation detected).
        """
        if not items:
            return []

        items_dicts = [self._prepare_item_dict(item) for item in items]
        primary_dicts, borderline_dicts = self._prepare_example_dicts(examples)

        user_prompt = build_subjective_eval_prompt(
            post_content=post,
            items_with_rubrics=items_dicts,
            community_name=community_name,
            examples=primary_dicts,
            borderline_examples=borderline_dicts,
        )
        content = self._build_content(post, user_prompt)

        # First pass: Haiku (fast, cheap)
        logger.info(f"Batch evaluating {len(items)} subjective items with Haiku")
        try:
            haiku_response = await self._call_model(content, self.settings.haiku_model)
            haiku_results = haiku_response.get("results", [])
        except Exception as e:
            logger.error(f"Haiku evaluation failed: {e}")
            return [
                {
                    "item_id": item.id,
                    "triggered": False,
                    "confidence": 0.5,
                    "reasoning": f"Evaluation failed: {e}",
                }
                for item in items
            ]

        # Identify low-confidence results that need escalation
        threshold = self.settings.escalation_confidence_threshold
        low_confidence = [r for r in haiku_results if r.get("confidence", 1.0) < threshold]

        if not low_confidence:
            return haiku_results

        # Escalate low-confidence items to Sonnet
        logger.info(f"Escalating {len(low_confidence)} items to Sonnet")
        low_conf_ids = {r["item_id"] for r in low_confidence}
        escalated_items = [item for item in items if item.id in low_conf_ids]

        if escalated_items:
            escalated_items_dicts = [self._prepare_item_dict(item) for item in escalated_items]
            escalation_prompt = build_subjective_eval_prompt(
                post_content=post,
                items_with_rubrics=escalated_items_dicts,
                community_name=community_name,
                examples=primary_dicts,
                borderline_examples=borderline_dicts,
            )
            escalation_content = self._build_content(post, escalation_prompt)
            try:
                sonnet_response = await self._call_model(escalation_content, self.settings.sonnet_model)
                sonnet_results = sonnet_response.get("results", [])
                sonnet_by_id = {r["item_id"]: r for r in sonnet_results}
            except Exception as e:
                logger.error(f"Sonnet escalation failed: {e}")
                sonnet_by_id = {}

            # Merge: use Sonnet results for escalated items
            final_results = []
            haiku_by_id = {r["item_id"]: r for r in haiku_results}
            for item in items:
                if item.id in low_conf_ids and item.id in sonnet_by_id:
                    result = sonnet_by_id[item.id]
                    result["escalated"] = True
                    final_results.append(result)
                else:
                    final_results.append(haiku_by_id.get(item.id, {
                        "item_id": item.id,
                        "triggered": False,
                        "confidence": 0.5,
                        "reasoning": "No result returned",
                    }))
            return final_results

        return haiku_results
