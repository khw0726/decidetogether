"""Alignment endpoints: suggest-from-examples, suggest-from-checklist, suggestions CRUD."""

import logging
import uuid
from typing import Any

import anthropic
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, Community, Example, ExampleChecklistItemLink, ExampleRuleLink, Rule, Suggestion
from ..models.schemas import SuggestionRead
from ..core.subjective import SubjectiveEvaluator
from ..core.tree_evaluator import TreeEvaluator
from .rules import _compile_rule_background
from .examples import _generate_suggestions_from_example

logger = logging.getLogger(__name__)
router = APIRouter(tags=["alignment"])


class AcceptSuggestionBody(BaseModel):
    label_override: str | None = None


def get_compiler() -> RuleCompiler:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return RuleCompiler(client, settings)


async def _get_rule_checklist_examples(
    rule_id: str, db: AsyncSession
) -> tuple[Rule, list[ChecklistItem], list[Example]]:
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    checklist_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    checklist = list(checklist_result.scalars().all())

    examples_result = await db.execute(
        select(Example)
        .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
        .where(ExampleRuleLink.rule_id == rule_id)
        .order_by(Example.created_at.desc())
    )
    examples = list(examples_result.scalars().all())

    return rule, checklist, examples


@router.post("/rules/{rule_id}/suggest-from-examples", response_model=list[SuggestionRead])
async def suggest_from_examples(
    rule_id: str, db: AsyncSession = Depends(get_db)
) -> list[SuggestionRead]:
    """Generate checklist/rule text suggestions from current examples."""
    rule, checklist, examples = await _get_rule_checklist_examples(rule_id, db)

    if not examples:
        raise HTTPException(status_code=400, detail="No examples found for this rule")

    # Get community name
    comm_result = await db.execute(
        select(Community).where(Community.id == rule.community_id)
    )
    community = comm_result.scalar_one_or_none()

    compiler = get_compiler()
    suggestion_dicts = await compiler.suggest_from_examples(rule, checklist, examples)

    checklist_by_id = {i.id: i for i in checklist}

    created = []
    for sug in suggestion_dicts:
        sug_type = sug.get("suggestion_type", "checklist")

        if sug_type == "checklist":
            target = sug.get("target")
            parent_id = sug.get("parent_id")
            proposed = sug.get("proposed_change") or {}

            if target and target in checklist_by_id:
                op = {"op": "update", "existing_id": target}
                op.update({k: v for k, v in proposed.items() if k != "id"})
            else:
                op = {"op": "add", **proposed}
                if parent_id and parent_id in checklist_by_id:
                    op["parent_id"] = parent_id
                if "children" not in op:
                    op["children"] = []

            content = {
                "operations": [op],
                "description": sug.get("description", ""),
                "reasoning": sug.get("reasoning", ""),
            }
        else:
            content = sug

        suggestion = Suggestion(
            rule_id=rule_id,
            suggestion_type=sug_type,
            content=content,
            status="pending",
        )
        db.add(suggestion)
        created.append(suggestion)

    await db.commit()
    for s in created:
        await db.refresh(s)

    return [SuggestionRead.model_validate(s) for s in created]


@router.post("/rules/{rule_id}/suggest-from-checklist", response_model=list[SuggestionRead])
async def suggest_from_checklist(
    rule_id: str, db: AsyncSession = Depends(get_db)
) -> list[SuggestionRead]:
    """Generate example/rule text suggestions from current checklist."""
    rule, checklist, examples = await _get_rule_checklist_examples(rule_id, db)

    if not checklist:
        raise HTTPException(status_code=400, detail="No checklist items found for this rule")

    comm_result = await db.execute(
        select(Community).where(Community.id == rule.community_id)
    )
    community = comm_result.scalar_one_or_none()
    community_name = community.name if community else ""

    compiler = get_compiler()
    suggestion_dicts = await compiler.suggest_from_checklist(
        rule, checklist, examples, community_name
    )

    created = []
    for sug in suggestion_dicts:
        suggestion = Suggestion(
            rule_id=rule_id,
            suggestion_type=sug.get("suggestion_type", "example"),
            content=sug,
            status="pending",
        )
        db.add(suggestion)
        created.append(suggestion)

    await db.commit()
    for s in created:
        await db.refresh(s)

    return [SuggestionRead.model_validate(s) for s in created]


@router.get("/rules/{rule_id}/suggestions", response_model=list[SuggestionRead])
async def list_suggestions(
    rule_id: str,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[SuggestionRead]:
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    if not rule_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Rule not found")

    query = (
        select(Suggestion)
        .where(Suggestion.rule_id == rule_id)
        .order_by(Suggestion.created_at.desc())
    )
    if status:
        query = query.where(Suggestion.status == status)

    result = await db.execute(query)
    suggestions = result.scalars().all()
    return [SuggestionRead.model_validate(s) for s in suggestions]


@router.post("/suggestions/{suggestion_id}/accept", response_model=SuggestionRead)
async def accept_suggestion(
    suggestion_id: str,
    background_tasks: BackgroundTasks,
    body: AcceptSuggestionBody = Body(default=AcceptSuggestionBody()),
    db: AsyncSession = Depends(get_db),
) -> SuggestionRead:
    result = await db.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail=f"Suggestion is already {suggestion.status}")

    suggestion.status = "accepted"

    # Apply the suggestion if it's a rule_text update
    if suggestion.suggestion_type == "rule_text" and suggestion.rule_id:
        rule_result = await db.execute(select(Rule).where(Rule.id == suggestion.rule_id))
        rule = rule_result.scalar_one_or_none()
        if rule:
            c = suggestion.content
            proposed = (
                c.get("proposed_text")
                or c.get("proposed_change", {}).get("text")
            )
            if proposed:
                rule.text = proposed

    # Apply if it's an example suggestion
    if suggestion.suggestion_type == "example" and suggestion.rule_id:
        rule_result = await db.execute(select(Rule).where(Rule.id == suggestion.rule_id))
        rule = rule_result.scalar_one_or_none()
        ex_content = suggestion.content.get("content", {})
        # Use label_override if provided (moderator decision on borderline examples)
        ex_label = body.label_override or suggestion.content.get("label", "compliant")
        relevance = suggestion.content.get("relevance_note", "")
        if ex_content:
            example = Example(
                community_id=rule.community_id if rule else None,
                content=ex_content,
                label=ex_label,
                source="generated",
            )
            db.add(example)
            await db.flush()
            link = ExampleRuleLink(
                example_id=example.id,
                rule_id=suggestion.rule_id,
                relevance_note=relevance,
            )
            db.add(link)
            related_desc = suggestion.content.get("related_checklist_item_description")
            if related_desc and suggestion.rule_id:
                item_result = await db.execute(
                    select(ChecklistItem)
                    .where(ChecklistItem.rule_id == suggestion.rule_id)
                    .where(ChecklistItem.description == related_desc)
                    .limit(1)
                )
                item = item_result.scalar_one_or_none()
                db.add(ExampleChecklistItemLink(
                    example_id=example.id,
                    checklist_item_id=item.id if item else None,
                    checklist_item_description=related_desc,
                ))
            # Trigger tuning when a borderline example is resolved to a clear label
            original_label = suggestion.content.get("label", "compliant")
            if original_label == "borderline" and ex_label in ("compliant", "violating"):
                background_tasks.add_task(_generate_suggestions_from_example, suggestion.rule_id)

    # Create a new rule from synthesized suggestion
    if suggestion.suggestion_type == "new_rule":
        content = suggestion.content
        community_id = content.get("community_id")
        if not community_id:
            raise HTTPException(status_code=400, detail="Suggestion missing community_id")

        comm_result = await db.execute(select(Community).where(Community.id == community_id))
        community = comm_result.scalar_one_or_none()
        if not community:
            raise HTTPException(status_code=404, detail="Community not found")

        # Assign priority after existing rules
        last_result = await db.execute(
            select(Rule)
            .where(Rule.community_id == community_id)
            .order_by(Rule.priority.desc())
            .limit(1)
        )
        last_rule = last_result.scalar_one_or_none()
        next_priority = (last_rule.priority + 1) if last_rule else 0

        new_rule = Rule(
            community_id=community_id,
            title=content["title"],
            text=content["text"],
            priority=next_priority,
        )
        db.add(new_rule)
        await db.flush()

        # Triage the new rule
        compiler = get_compiler()
        triage = await compiler.triage_rule(new_rule.text, community.name, community.platform)
        new_rule.rule_type = triage["rule_type"]
        new_rule.rule_type_reasoning = triage.get("reasoning")

        # Link the orphaned examples to the new rule
        for example_id in content.get("example_ids", []):
            db.add(ExampleRuleLink(
                example_id=example_id,
                rule_id=new_rule.id,
                relevance_note="Auto-linked from rule synthesis",
            ))

        # Enqueue background compilation if actionable
        if new_rule.rule_type == "actionable":
            background_tasks.add_task(_compile_rule_background, str(new_rule.id), community_id)

    await db.commit()
    await db.refresh(suggestion)
    return SuggestionRead.model_validate(suggestion)


class PreviewRecompileRequest(BaseModel):
    rule_text: str


@router.post("/rules/{rule_id}/preview-recompile")
async def preview_recompile(
    rule_id: str,
    body: PreviewRecompileRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Preview how a draft rule text change would affect the checklist and existing examples.

    Does NOT save anything. Returns:
    - operations: the diff (keep/update/add/delete) that would be applied
    - example_verdicts: for each labeled example, whether the new checklist would change the verdict
    """
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    comm_result = await db.execute(select(Community).where(Community.id == rule.community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    other_rules_result = await db.execute(
        select(Rule).where(
            Rule.community_id == rule.community_id,
            Rule.is_active == True,
            Rule.id != rule_id,
        )
    )
    other_rules = list(other_rules_result.scalars().all())

    # Fetch existing top-level checklist items
    existing_result = await db.execute(
        select(ChecklistItem).where(
            ChecklistItem.rule_id == rule_id,
            ChecklistItem.parent_id == None,  # noqa: E711
        )
    )
    existing_items = list(existing_result.scalars().all())

    # Build a draft Rule object with the preview text (not persisted)
    draft_rule = Rule(
        id=rule.id,
        community_id=rule.community_id,
        title=rule.title,
        text=body.rule_text,
        priority=rule.priority,
        rule_type=rule.rule_type,
    )

    compiler = get_compiler()
    operations = await compiler.recompile_with_diff(
        rule=draft_rule,
        community=community,
        other_rules=other_rules,
        existing_items=existing_items,
    )

    # Fetch up to 20 labeled examples for re-evaluation preview
    examples_result = await db.execute(
        select(Example)
        .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
        .where(ExampleRuleLink.rule_id == rule_id)
        .where(Example.label.in_(["compliant", "violating", "borderline"]))
        .order_by(Example.created_at.desc())
        .limit(20)
    )
    examples = list(examples_result.scalars().all())

    # Build hypothetical new checklist by applying ops to a copy of existing items' descriptions
    # We just report which items would change, not run actual evaluation (that would require LLM calls per example)
    existing_by_id = {item.id: item for item in existing_items}
    item_changes: dict[str, str] = {}  # item_id → op type
    added_descriptions: list[str] = []
    for op in operations:
        kind = op.get("op")
        if kind == "update":
            item_changes[op["existing_id"]] = "update"
        elif kind == "delete":
            item_changes[op["existing_id"]] = "delete"
        elif kind == "add":
            added_descriptions.append(op.get("description", ""))

    # For each example, determine if any of its linked checklist items would change
    example_verdicts = []
    for ex in examples:
        links_result = await db.execute(
            select(ExampleChecklistItemLink)
            .where(ExampleChecklistItemLink.example_id == ex.id)
        )
        linked_item_ids = {
            link.checklist_item_id
            for link in links_result.scalars()
            if link.checklist_item_id
        }
        affected_items = [
            existing_by_id[iid].description
            for iid in linked_item_ids
            if iid in item_changes
        ]
        may_change = bool(affected_items) or bool(added_descriptions)
        example_verdicts.append({
            "example_id": ex.id,
            "label": ex.label,
            "content_title": (ex.content or {}).get("content", {}).get("title", ""),
            "may_change": may_change,
            "affected_checklist_items": affected_items,
        })

    return {
        "operations": operations,
        "example_verdicts": example_verdicts,
        "summary": {
            "keep": sum(1 for op in operations if op.get("op") == "keep"),
            "update": sum(1 for op in operations if op.get("op") == "update"),
            "delete": sum(1 for op in operations if op.get("op") == "delete"),
            "add": sum(1 for op in operations if op.get("op") == "add"),
            "examples_may_change": sum(1 for ev in example_verdicts if ev["may_change"]),
        },
    }


def _apply_diff_to_checklist(
    all_existing: list[ChecklistItem],
    operations: list[dict],
    rule_id: str,
) -> list[ChecklistItem]:
    """Apply diff operations to produce a hypothetical in-memory checklist."""
    op_by_existing_id: dict[str, dict] = {
        op["existing_id"]: op
        for op in operations
        if op.get("existing_id")
    }
    deleted_root_ids: set[str] = {
        op["existing_id"]
        for op in operations
        if op.get("op") == "delete" and op.get("existing_id")
    }

    # Find all descendants of deleted root items
    def get_descendants(item_id: str) -> set[str]:
        result: set[str] = set()
        for item in all_existing:
            if item.parent_id == item_id:
                result.add(item.id)
                result |= get_descendants(item.id)
        return result

    excluded_ids: set[str] = set(deleted_root_ids)
    for did in deleted_root_ids:
        excluded_ids |= get_descendants(did)

    hypothetical: list[ChecklistItem] = []
    for item in all_existing:
        if item.id in excluded_ids:
            continue
        op = op_by_existing_id.get(item.id)
        if op and op.get("op") == "update":
            hypothetical.append(ChecklistItem(
                id=item.id,
                rule_id=item.rule_id,
                parent_id=item.parent_id,
                order=item.order,
                description=op.get("description") or item.description,
                rule_text_anchor=op.get("rule_text_anchor", item.rule_text_anchor),
                item_type=op.get("item_type") or item.item_type,
                logic=op.get("logic") or item.logic,
                action=op.get("action") or item.action,
                atmosphere_influenced=op.get("atmosphere_influenced", item.atmosphere_influenced),
                atmosphere_note=op.get("atmosphere_note", item.atmosphere_note),
            ))
        else:
            hypothetical.append(item)

    # Append new items from "add" ops
    for i, op in enumerate(operations):
        if op.get("op") == "add":
            hypothetical.append(ChecklistItem(
                id=str(uuid.uuid4()),
                rule_id=rule_id,
                parent_id=None,
                order=1000 + i,
                description=op.get("description", ""),
                rule_text_anchor=op.get("rule_text_anchor"),
                item_type=op.get("item_type", "subjective"),
                logic=op.get("logic") or {},
                action=op.get("action", "flag"),
                atmosphere_influenced=op.get("atmosphere_influenced", False),
                atmosphere_note=op.get("atmosphere_note"),
            ))

    return hypothetical


@router.post("/rules/{rule_id}/evaluate-examples-with-draft")
async def evaluate_examples_with_draft(
    rule_id: str,
    body: PreviewRecompileRequest,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Evaluate linked examples against a hypothetical checklist built from the draft rule text.

    Returns per-example: old label and new verdict, so the UI can highlight verdict flips.
    Does NOT save anything to the database.
    """
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    comm_result = await db.execute(select(Community).where(Community.id == rule.community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    other_rules_result = await db.execute(
        select(Rule).where(
            Rule.community_id == rule.community_id,
            Rule.is_active == True,  # noqa: E712
            Rule.id != rule_id,
        )
    )
    other_rules = list(other_rules_result.scalars().all())

    # Fetch root items for the compiler diff
    root_result = await db.execute(
        select(ChecklistItem).where(
            ChecklistItem.rule_id == rule_id,
            ChecklistItem.parent_id == None,  # noqa: E711
        )
    )
    root_items = list(root_result.scalars().all())

    # Fetch ALL checklist items (including children)
    all_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.rule_id == rule_id)
        .order_by(ChecklistItem.order.asc())
    )
    all_existing = list(all_result.scalars().all())

    draft_rule = Rule(
        id=rule.id,
        community_id=rule.community_id,
        title=rule.title,
        text=body.rule_text,
        priority=rule.priority,
        rule_type=rule.rule_type,
    )

    compiler = get_compiler()
    operations = await compiler.recompile_with_diff(
        rule=draft_rule,
        community=community,
        other_rules=other_rules,
        existing_items=root_items,
    )

    hypothetical = _apply_diff_to_checklist(all_existing, operations, rule_id)
    if not hypothetical:
        return []

    # Fetch up to 20 labeled examples linked to this rule
    examples_result = await db.execute(
        select(Example)
        .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
        .where(ExampleRuleLink.rule_id == rule_id)
        .where(Example.label.in_(["compliant", "violating", "borderline"]))
        .order_by(Example.created_at.desc())
        .limit(20)
    )
    examples = list(examples_result.scalars().all())
    if not examples:
        return []

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    subjective_evaluator = SubjectiveEvaluator(client, settings)
    tree_evaluator = TreeEvaluator(subjective_evaluator)

    results: list[dict[str, Any]] = []
    for ex in examples:
        try:
            rule_result_data = await tree_evaluator.evaluate_rule(
                rule=draft_rule,
                checklist=hypothetical,
                post=ex.content,
                community_name=community.name,
                examples=[],
            )
            new_verdict = rule_result_data["verdict"]
            new_confidence = rule_result_data["confidence"]
        except Exception as e:
            logger.warning(f"Draft evaluation failed for example {ex.id}: {e}")
            new_verdict = "error"
            new_confidence = 0.0

        results.append({
            "example_id": ex.id,
            "old_label": ex.label,
            "new_verdict": new_verdict,
            "new_confidence": new_confidence,
        })

    return results


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuggestionRead)
async def dismiss_suggestion(
    suggestion_id: str, db: AsyncSession = Depends(get_db)
) -> SuggestionRead:
    result = await db.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail=f"Suggestion is already {suggestion.status}")

    suggestion.status = "dismissed"
    await db.commit()
    await db.refresh(suggestion)
    return SuggestionRead.model_validate(suggestion)
