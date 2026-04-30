"""Checklist tree walker: evaluates each node and combines results."""

import logging
from typing import Any

from ..db.models import ChecklistItem, Rule
from .deterministic import evaluate_deterministic
from .structural import evaluate_structural
from .actions import VERDICT_PRECEDENCE

logger = logging.getLogger(__name__)


class TreeEvaluator:
    """Walks the checklist tree and evaluates each node."""

    def __init__(self, subjective_evaluator: Any):
        self.subjective_evaluator = subjective_evaluator

    def _get_children(
        self, item: ChecklistItem, all_items: list[ChecklistItem]
    ) -> list[ChecklistItem]:
        return sorted(
            [i for i in all_items if i.parent_id == item.id],
            key=lambda x: x.order,
        )

    async def evaluate_rule(
        self,
        rule: Rule,
        checklist: list[ChecklistItem],
        post: dict[str, Any],
        community_name: str,
    ) -> dict[str, Any]:
        """Evaluate all checklist items for a rule, return verdict + reasoning.

        Returns:
        {
            "verdict": "approve" | "remove" | "review",
            "confidence": float,
            "reasoning": {item_id: {triggered, confidence, reasoning}},
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

        # Pre-evaluate all subjective items in a single batch for efficiency
        subjective_items = [i for i in checklist if i.item_type == "subjective"]
        subjective_results: dict[str, dict] = {}

        if subjective_items:
            batch_results = await self.subjective_evaluator.evaluate_batch(
                items=subjective_items,
                post=post,
                community_name=community_name,
            )
            for result in batch_results:
                subjective_results[result["item_id"]] = result

        # Evaluate all items (deterministic + structural locally, subjective from batch)
        all_results: dict[str, dict] = {}
        for item in checklist:
            if item.item_type == "deterministic":
                triggered, reasoning = evaluate_deterministic(item, post)
                confidence = 1.0
            elif item.item_type == "structural":
                triggered, reasoning = evaluate_structural(item, post)
                confidence = 1.0
            elif item.item_type == "subjective":
                sub_result = subjective_results.get(item.id, {
                    "triggered": False,
                    "confidence": 0.5,
                    "reasoning": "No result",
                })
                triggered = sub_result.get("triggered", False)
                confidence = sub_result.get("confidence", 0.5)
                reasoning = sub_result.get("reasoning", "")
            else:
                triggered = False
                confidence = 0.5
                reasoning = f"Unknown item type: {item.item_type}"

            all_results[item.id] = {
                "triggered": triggered,
                "confidence": confidence,
                "reasoning": reasoning,
                "action": item.action,
                "item_type": item.item_type,
                "description": item.description,
                "parent_id": item.parent_id,
            }

        # Walk root items and aggregate
        root_items = sorted(
            [i for i in checklist if i.parent_id is None], key=lambda x: x.order
        )
        verdict, confidence, triggered_ids, visited_ids = self._walk_roots(
            root_items, checklist, all_results
        )

        # Only include items that were actually visited during the walk
        visited_reasoning = {k: v for k, v in all_results.items() if k in visited_ids}

        return {
            "verdict": verdict,
            "confidence": confidence,
            "reasoning": visited_reasoning,
            "triggered_items": triggered_ids,
        }

    def _walk_roots(
        self,
        root_items: list[ChecklistItem],
        all_items: list[ChecklistItem],
        all_results: dict[str, dict],
    ) -> tuple[str, float, list[str], set[str]]:
        """Walk all root items and return worst verdict (OR logic across siblings)."""
        worst_verdict = "approve"
        worst_confidence = 1.0
        all_triggered: list[str] = []
        all_visited: set[str] = set()

        for item in root_items:
            verdict, confidence, triggered, visited = self._evaluate_subtree(item, all_items, all_results)
            all_triggered.extend(triggered)
            all_visited.update(visited)
            if VERDICT_PRECEDENCE.get(verdict, 0) > VERDICT_PRECEDENCE.get(worst_verdict, 0):
                worst_verdict = verdict
                worst_confidence = confidence
            elif VERDICT_PRECEDENCE.get(verdict, 0) == VERDICT_PRECEDENCE.get(worst_verdict, 0):
                worst_confidence = min(worst_confidence, confidence)

        return worst_verdict, worst_confidence, all_triggered, all_visited

    def _evaluate_subtree(
        self,
        item: ChecklistItem,
        all_items: list[ChecklistItem],
        all_results: dict[str, dict],
    ) -> tuple[str, float, list[str], set[str]]:
        """Recursively evaluate an item and its children.

        Returns (verdict, confidence, triggered_item_ids, visited_item_ids).

        Semantics:
        - If item is NOT triggered (answer=NO): return approve, skip children.
        - If item IS triggered (answer=YES):
            - Apply this item's own action as the minimum verdict.
            - Evaluate children; any child can escalate the verdict (OR logic).
        """
        result = all_results.get(item.id, {"triggered": False, "confidence": 0.5})
        triggered = result.get("triggered", False)
        confidence = result.get("confidence", 0.5)
        visited: set[str] = {item.id}

        if not triggered:
            return "approve", confidence, [], visited

        # Item says YES — translate action to verdict
        # "warn" action → "warn" verdict; "continue" is not a verdict on its own
        _ACTION_TO_VERDICT = {"remove": "remove", "warn": "warn", "continue": "approve"}
        self_verdict = _ACTION_TO_VERDICT.get(item.action, "approve")
        worst_verdict = self_verdict
        worst_confidence = confidence
        triggered_ids = [item.id]

        # Evaluate children to potentially escalate
        children = self._get_children(item, all_items)
        for child in children:
            child_verdict, child_confidence, child_triggered, child_visited = self._evaluate_subtree(
                child, all_items, all_results
            )
            triggered_ids.extend(child_triggered)
            visited.update(child_visited)
            if VERDICT_PRECEDENCE.get(child_verdict, 0) > VERDICT_PRECEDENCE.get(worst_verdict, 0):
                worst_verdict = child_verdict
                worst_confidence = child_confidence

        return worst_verdict, worst_confidence, triggered_ids, visited
