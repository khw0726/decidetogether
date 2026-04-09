"""Main evaluation engine: orchestrates rule evaluation for a post."""

import logging
from datetime import datetime
from typing import Any, Optional

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import Settings
from ..compiler.compiler import RuleCompiler
from ..compiler.prompts import (
    COMMUNITY_NORMS_SYSTEM,
    build_community_norms_prompt,
)
from ..db.models import ChecklistItem, Community, Decision, Example, ExampleRuleLink, Rule
from .actions import resolve_verdict
from .subjective import SubjectiveEvaluator
from .tree_evaluator import TreeEvaluator

logger = logging.getLogger(__name__)


class EvaluationEngine:
    def __init__(
        self,
        db: AsyncSession,
        client: anthropic.AsyncAnthropic,
        settings: Settings,
    ):
        self.db = db
        self.client = client
        self.settings = settings
        self.subjective_evaluator = SubjectiveEvaluator(client, settings)
        self.tree_evaluator = TreeEvaluator(self.subjective_evaluator)

    async def evaluate_post(
        self, community_id: str, post: dict[str, Any]
    ) -> Decision:
        """Main entry point: evaluate a post against all community rules.

        Returns a persisted Decision record.
        """
        # 1. Fetch community
        community_result = await self.db.execute(
            select(Community).where(Community.id == community_id)
        )
        community = community_result.scalar_one_or_none()
        if not community:
            raise ValueError(f"Community {community_id} not found")

        # 2. Fetch active rules ordered by priority
        rules_result = await self.db.execute(
            select(Rule)
            .where(Rule.community_id == community_id, Rule.is_active == True)
            .order_by(Rule.priority.asc())
        )
        all_rules = list(rules_result.scalars().all())
        actionable_rules = [r for r in all_rules if r.rule_type == "actionable"]

        # 3. Build rules summary for context
        non_actionable_rules = [r for r in all_rules if r.rule_type != "actionable"]
        rules_summary = self._build_rules_summary(all_rules)

        # 4. Evaluate each actionable rule
        rule_results = []
        triggered_rule_ids = []
        full_reasoning: dict[str, Any] = {}

        for rule in actionable_rules:
            # Fetch checklist items for this rule
            items_result = await self.db.execute(
                select(ChecklistItem)
                .where(ChecklistItem.rule_id == rule.id)
                .order_by(ChecklistItem.order.asc())
            )
            checklist = list(items_result.scalars().all())

            # Fetch examples for this rule
            examples = await self._fetch_rule_examples(rule.id)

            if not checklist:
                # No checklist compiled yet — skip evaluation
                continue

            rule_result = await self.tree_evaluator.evaluate_rule(
                rule=rule,
                checklist=checklist,
                post=post,
                community_name=community.name,
                examples=examples,
            )

            rule_verdict = rule_result["verdict"]
            rule_confidence = rule_result["confidence"]

            rule_results.append({
                "rule_id": rule.id,
                "rule_title": rule.title,
                "verdict": rule_verdict,
                "confidence": rule_confidence,
            })

            full_reasoning[rule.id] = {
                "rule_title": rule.title,
                "verdict": rule_verdict,
                "confidence": rule_confidence,
                "item_reasoning": rule_result["reasoning"],
                "triggered_items": rule_result["triggered_items"],
            }

            if rule_verdict in ("remove", "review"):
                triggered_rule_ids.append(rule.id)

        # 5. Aggregate verdict
        if rule_results:
            agent_verdict, agent_confidence = resolve_verdict(rule_results)
        else:
            agent_verdict, agent_confidence = "approve", 1.0

        # 6. Community norms check (only if no remove verdict already)
        if agent_verdict != "remove":
            norms_result = await self._check_community_norms(
                post=post,
                community_name=community.name,
                rules_summary=rules_summary,
                community_atmosphere = community.atmosphere,
            )
            if norms_result.get("violates_norms"):
                norms_confidence = norms_result.get("confidence", 0.5)
                full_reasoning["__community_norms__"] = {
                    "rule_title": "Community norms (inferred)",
                    "verdict": "review",
                    "confidence": norms_confidence,
                    "item_reasoning": {
                        "community_norms": {
                            "triggered": True,
                            "description": "Community norms check",
                            "reasoning": norms_result.get("reasoning", ""),
                            "confidence": norms_confidence,
                            "action": "flag",
                            "item_type": "subjective",
                        }
                    },
                    "triggered_items": ["community_norms"],
                }
                if agent_verdict == "approve":
                    agent_verdict = "review"
                    agent_confidence = norms_confidence

        # 7. Create Decision record
        post_id = post.get("id", "unknown")
        decision = Decision(
            community_id=community_id,
            post_content=post,
            post_platform_id=str(post_id),
            agent_verdict=agent_verdict,
            agent_confidence=agent_confidence,
            agent_reasoning=full_reasoning,
            triggered_rules=triggered_rule_ids,
            moderator_verdict="pending",
            was_override=False,
        )
        self.db.add(decision)
        await self.db.commit()
        await self.db.refresh(decision)

        return decision

    async def _fetch_rule_examples(self, rule_id: str) -> list[Example]:
        """Fetch examples linked to a rule."""
        result = await self.db.execute(
            select(Example)
            .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
            .where(ExampleRuleLink.rule_id == rule_id)
        )
        return list(result.scalars().all())

    def _build_rules_summary(self, rules: list[Rule]) -> str:
        lines = []
        for rule in rules:
            lines.append(f"[{rule.rule_type.upper()}] {rule.title}: {rule.text[:100]}")
        return "\n".join(lines) if lines else "No rules defined."

    async def _check_community_norms(
        self,
        post: dict[str, Any],
        community_name: str,
        rules_summary: str,
        community_atmosphere: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the community norms check."""
        _norms_tool = {
            "name": "submit_norms_evaluation",
            "description": "Submit community norms evaluation result",
            "input_schema": {
                "type": "object",
                "properties": {
                    "violates_norms": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "reasoning": {"type": "string"},
                },
                "required": ["violates_norms", "confidence", "reasoning"],
            },
        }
        try:
            user_prompt = build_community_norms_prompt(
                post_content=post,
                community_name=community_name,
                rules_summary=rules_summary,
                recent_decisions=[],
                community_atmosphere=community_atmosphere,
            )
            response = await self.client.messages.create(
                model=self.settings.haiku_model,
                max_tokens=1024,
                system=COMMUNITY_NORMS_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[_norms_tool],
                tool_choice={"type": "tool", "name": _norms_tool["name"]},
            )
            return response.content[0].input
        except Exception as e:
            logger.warning(f"Community norms check failed: {e}")
            return {"violates_norms": False, "confidence": 0.0, "reasoning": str(e)}
