"""Checklist tree walker: evaluates each node and combines results."""

import logging
from typing import Any, Optional

from ..db.models import ChecklistItem, Example, Rule
from .deterministic import evaluate_deterministic
from .structural import evaluate_structural
from .actions import merge_item_results

logger = logging.getLogger(__name__)


class TreeEvaluator:
    """Walks the checklist tree and evaluates each node."""

    def __init__(self, subjective_evaluator: Any):
        self.subjective_evaluator = subjective_evaluator

    def _build_tree(self, items: list[ChecklistItem]) -> list[ChecklistItem]:
        """Return root-level items; children are available via item.children relationship."""
        return [item for item in items if item.parent_id is None]

    def _get_children(
        self, item: ChecklistItem, all_items: list[ChecklistItem]
    ) -> list[ChecklistItem]:
        """Get direct children of an item from flat list."""
        return [i for i in all_items if i.parent_id == item.id]

    async def evaluate_rule(
        self,
        rule: Rule,
        checklist: list[ChecklistItem],
        post: dict[str, Any],
        community_name: str,
        examples: list[Example],
    ) -> dict[str, Any]:
        """Evaluate all checklist items for a rule, return verdict + reasoning.

        Returns:
        {
            "verdict": "approve" | "remove" | "flag",
            "confidence": float,
            "reasoning": {item_id: {passes, confidence, reasoning}},
            "triggered_items": [item_id, ...]
        }
        """
        if not checklist:
            return {
                "verdict": "approve",
                "confidence": 1.0,
                "reasoning": {},
                "triggered_items": [],
            }

        # Collect all subjective items for batch evaluation
        subjective_items = [i for i in checklist if i.item_type == "subjective"]
        subjective_results: dict[str, dict] = {}

        if subjective_items:
            batch_results = await self.subjective_evaluator.evaluate_batch(
                items=subjective_items,
                post=post,
                community_name=community_name,
                examples=examples,
            )
            for result in batch_results:
                subjective_results[result["item_id"]] = result

        # Evaluate all items
        all_results: dict[str, dict] = {}
        for item in checklist:
            if item.item_type == "deterministic":
                passes, reasoning = evaluate_deterministic(item, post)
                confidence = 1.0  # deterministic is always certain
            elif item.item_type == "structural":
                passes, reasoning = evaluate_structural(item, post)
                confidence = 1.0
            elif item.item_type == "subjective":
                sub_result = subjective_results.get(item.id, {
                    "passes": True,
                    "confidence": 0.5,
                    "reasoning": "No result",
                })
                passes = sub_result.get("passes", True)
                confidence = sub_result.get("confidence", 0.5)
                reasoning = sub_result.get("reasoning", "")
            else:
                passes = True
                confidence = 0.5
                reasoning = f"Unknown item type: {item.item_type}"

            all_results[item.id] = {
                "passes": passes,
                "confidence": confidence,
                "reasoning": reasoning,
                "fail_action": item.fail_action,
                "item_type": item.item_type,
                "description": item.description,
            }

        # Walk tree to compute overall verdict
        root_items = self._build_tree(checklist)
        verdict, confidence, triggered = self._walk_tree(
            root_items, checklist, all_results
        )

        return {
            "verdict": verdict,
            "confidence": confidence,
            "reasoning": all_results,
            "triggered_items": triggered,
        }

    def _walk_tree(
        self,
        root_items: list[ChecklistItem],
        all_items: list[ChecklistItem],
        all_results: dict[str, dict],
    ) -> tuple[str, float, list[str]]:
        """Walk the tree and determine final verdict for a rule.

        Returns (verdict, confidence, triggered_item_ids).
        """
        triggered_items = []
        worst_verdict = "approve"
        worst_confidence = 1.0

        for root_item in sorted(root_items, key=lambda x: x.order):
            verdict, confidence, triggered = self._evaluate_subtree(
                root_item, all_items, all_results
            )
            triggered_items.extend(triggered)

            from .actions import VERDICT_PRECEDENCE
            if VERDICT_PRECEDENCE.get(verdict, 0) > VERDICT_PRECEDENCE.get(worst_verdict, 0):
                worst_verdict = verdict
                worst_confidence = confidence
            elif VERDICT_PRECEDENCE.get(verdict, 0) == VERDICT_PRECEDENCE.get(worst_verdict, 0):
                # Same level — take lower confidence (more uncertain)
                worst_confidence = min(worst_confidence, confidence)

        return worst_verdict, worst_confidence, triggered_items

    def _evaluate_subtree(
        self,
        item: ChecklistItem,
        all_items: list[ChecklistItem],
        all_results: dict[str, dict],
    ) -> tuple[str, float, list[str]]:
        """Recursively evaluate an item and its children.

        Returns (verdict, confidence, triggered_item_ids).
        """
        children = self._get_children(item, all_items)
        item_result = all_results.get(item.id, {"passes": True, "confidence": 0.5})

        triggered = []

        if not children:
            # Leaf node
            passes = item_result.get("passes", True)
            confidence = item_result.get("confidence", 0.5)
            if not passes:
                triggered.append(item.id)
                verdict = item.fail_action if item.fail_action != "continue" else "approve"
            else:
                verdict = "approve"
            return verdict, confidence, triggered

        # Non-leaf: evaluate children and combine
        child_verdicts = []
        child_results_for_combine = []

        for child in sorted(children, key=lambda x: x.order):
            child_verdict, child_confidence, child_triggered = self._evaluate_subtree(
                child, all_items, all_results
            )
            triggered.extend(child_triggered)

            from .actions import VERDICT_PRECEDENCE
            child_passes = VERDICT_PRECEDENCE.get(child_verdict, 1) == VERDICT_PRECEDENCE.get("approve", 1)
            child_results_for_combine.append({
                "passes": child_passes,
                "confidence": child_confidence,
            })
            child_verdicts.append(child_verdict)

        # Apply this node's combine_mode to children
        combined_passes, combined_confidence = merge_item_results(
            child_results_for_combine, item.combine_mode
        )

        # Also consider this node's own evaluation
        self_passes = item_result.get("passes", True)

        # Final: node passes if it passes AND its children pass (for all_must_pass logic)
        final_passes = combined_passes and self_passes

        if not final_passes:
            triggered.append(item.id)
            verdict = item.fail_action if item.fail_action != "continue" else "approve"
            # Escalate to worst child verdict if worse
            from .actions import VERDICT_PRECEDENCE
            for cv in child_verdicts:
                if VERDICT_PRECEDENCE.get(cv, 0) > VERDICT_PRECEDENCE.get(verdict, 0):
                    verdict = cv
        else:
            verdict = "approve"

        return verdict, combined_confidence, triggered
