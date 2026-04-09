"""Example management endpoints."""

import logging
from collections import defaultdict

import anthropic
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..compiler.compiler import RuleCompiler
from ..db.database import get_db
from ..db.models import ChecklistItem, Community, Example, ExampleChecklistItemLink, ExampleRuleLink, Rule, Suggestion
from ..models.schemas import CommunityExampleRead, ExampleCreate, ExampleRead, ExampleUpdate

logger = logging.getLogger(__name__)
router = APIRouter(tags=["examples"])


def get_compiler() -> RuleCompiler:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return RuleCompiler(client, settings)


async def _generate_suggestions_from_example(rule_id: str) -> None:
    """Background task to generate suggestions after a new example is added."""
    from ..db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
            rule = rule_result.scalar_one_or_none()
            if not rule:
                return

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
            )
            examples = list(examples_result.scalars().all())

            if not checklist or not examples:
                return

            # Count violating examples per checklist item for deterministic regex threshold
            checklist_ids = [i.id for i in checklist]
            links_result = await db.execute(
                select(ExampleChecklistItemLink)
                .join(Example, ExampleChecklistItemLink.example_id == Example.id)
                .where(
                    and_(
                        ExampleChecklistItemLink.checklist_item_id.in_(checklist_ids),
                        Example.label == "violating",
                    )
                )
            )
            violating_counts: dict[str, int] = {}
            for link in links_result.scalars():
                cid = link.checklist_item_id
                if cid:
                    violating_counts[cid] = violating_counts.get(cid, 0) + 1

            compiler = get_compiler()
            suggestions = await compiler.suggest_from_examples(rule, checklist, examples, violating_counts)

            checklist_by_id = {i.id: i for i in checklist}

            for sug in suggestions:
                sug_type = sug.get("suggestion_type", "checklist")

                if sug_type == "checklist":
                    # Convert to operations format for accept_recompile
                    target = sug.get("target")
                    parent_id = sug.get("parent_id")
                    proposed = sug.get("proposed_change") or {}

                    if target and target in checklist_by_id:
                        # Update existing item
                        op = {"op": "update", "existing_id": target}
                        op.update({k: v for k, v in proposed.items() if k != "id"})
                    else:
                        # Add new item (root or child)
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
                    # rule_text suggestions — store as-is
                    content = sug

                suggestion = Suggestion(
                    rule_id=rule_id,
                    suggestion_type=sug_type,
                    content=content,
                    status="pending",
                )
                db.add(suggestion)

            await db.commit()
            logger.info(f"Generated {len(suggestions)} suggestions for rule {rule_id}")

        except Exception as e:
            logger.error(f"Suggestion generation failed for rule {rule_id}: {e}")
            await db.rollback()


@router.get("/communities/{community_id}/examples", response_model=list[CommunityExampleRead])
async def list_community_examples(
    community_id: str,
    rule_id: str | None = None,  # "unlinked" | <uuid> | None (all)
    label: str | None = None,
    source: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[CommunityExampleRead]:
    """Return all examples for a community, grouped by rule linkage."""
    comm_result = await db.execute(select(Community).where(Community.id == community_id))
    if not comm_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Community not found")

    # 1. Get all rule IDs + titles for this community
    rule_result = await db.execute(
        select(Rule.id, Rule.title).where(Rule.community_id == community_id)
    )
    community_rules: dict[str, str] = {row.id: row.title for row in rule_result}

    # 2. Get all ExampleRuleLinks for community rules → build example → [(rule_id, title)] map
    example_to_rules: dict[str, list[tuple[str, str]]] = defaultdict(list)
    linked_example_ids: set[str] = set()
    if community_rules:
        link_result = await db.execute(
            select(ExampleRuleLink).where(ExampleRuleLink.rule_id.in_(community_rules.keys()))
        )
        for link in link_result.scalars():
            example_to_rules[link.example_id].append((link.rule_id, community_rules[link.rule_id]))
            linked_example_ids.add(link.example_id)

    # 3. Fetch examples based on rule_id filter
    linked_examples: list[Example] = []
    unlinked_examples: list[Example] = []

    fetch_linked = rule_id != "unlinked"
    fetch_unlinked = rule_id is None or rule_id == "unlinked"

    if fetch_linked and linked_example_ids:
        ids_to_fetch = linked_example_ids
        if rule_id and rule_id != "unlinked":
            # Filter to only examples linked to the specified rule
            ids_to_fetch = {
                eid for eid in linked_example_ids
                if any(r[0] == rule_id for r in example_to_rules.get(eid, []))
            }
        if ids_to_fetch:
            q = select(Example).where(Example.id.in_(ids_to_fetch))
            if label:
                q = q.where(Example.label == label)
            if source:
                q = q.where(Example.source == source)
            q = q.order_by(Example.created_at.desc())
            res = await db.execute(q)
            linked_examples = list(res.scalars().all())

    if fetch_unlinked:
        # Unlinked = moderator_decision examples with no rule link, scoped to this community
        q = (
            select(Example)
            .outerjoin(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
            .where(Example.community_id == community_id)
            .where(Example.source == "moderator_decision")
            .where(Example.label.in_(["violating", "borderline"]))
            .where(ExampleRuleLink.example_id.is_(None))
        )
        if label:
            q = q.where(Example.label == label)
        if source:
            q = q.where(Example.source == source)
        q = q.order_by(Example.created_at.desc())
        res = await db.execute(q)
        unlinked_examples = list(res.scalars().all())

    all_examples = linked_examples + unlinked_examples

    return [
        CommunityExampleRead(
            id=e.id,
            content=e.content,
            label=e.label,
            source=e.source,
            moderator_reasoning=e.moderator_reasoning,
            created_at=e.created_at,
            updated_at=e.updated_at,
            rule_ids=[p[0] for p in example_to_rules.get(e.id, [])],
            rule_titles=[p[1] for p in example_to_rules.get(e.id, [])],
        )
        for e in all_examples
    ]


@router.post("/rules/{rule_id}/examples", response_model=ExampleRead, status_code=201)
async def add_example(
    rule_id: str,
    body: ExampleCreate,
    db: AsyncSession = Depends(get_db),
) -> ExampleRead:
    # Verify rule exists
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    valid_labels = {"compliant", "violating", "borderline"}
    if body.label not in valid_labels:
        raise HTTPException(status_code=422, detail=f"label must be one of {valid_labels}")

    # Create example
    example = Example(
        community_id=rule.community_id,
        content=body.content,
        label=body.label,
        source=body.source,
        moderator_reasoning=body.moderator_reasoning,
    )
    db.add(example)
    await db.flush()

    # Link to rule
    link = ExampleRuleLink(
        example_id=example.id,
        rule_id=rule_id,
        relevance_note=body.relevance_note,
    )
    db.add(link)
    if body.checklist_item_id:
        item_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.id == body.checklist_item_id)
        )
        item = item_result.scalar_one_or_none()
        if item:
            db.add(ExampleChecklistItemLink(
                example_id=example.id,
                checklist_item_id=item.id,
                checklist_item_description=item.description,
            ))
    await db.commit()
    await db.refresh(example)

    return ExampleRead.model_validate(example)


@router.get("/rules/{rule_id}/examples", response_model=list[ExampleRead])
async def list_examples(
    rule_id: str,
    label: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[ExampleRead]:
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    if not rule_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Rule not found")

    query = (
        select(Example)
        .join(ExampleRuleLink, Example.id == ExampleRuleLink.example_id)
        .where(ExampleRuleLink.rule_id == rule_id)
        .order_by(Example.created_at.desc())
    )
    if label:
        query = query.where(Example.label == label)

    result = await db.execute(query)
    examples = list(result.scalars().all())

    # Fetch checklist item links for all examples in one query.
    # checklist_item_id may be NULL for links that survived a recompile delete;
    # checklist_item_description is always populated and used as the stable display value.
    example_ids = [e.id for e in examples]
    item_link_result = await db.execute(
        select(ExampleChecklistItemLink)
        .where(ExampleChecklistItemLink.example_id.in_(example_ids))
    )
    item_by_example: dict[str, tuple[str | None, str | None]] = {}
    for link in item_link_result.scalars():
        item_by_example[link.example_id] = (link.checklist_item_id, link.checklist_item_description or None)

    reads = []
    for e in examples:
        item_id, item_desc = item_by_example.get(e.id, (None, None))
        reads.append(ExampleRead(
            id=e.id,
            content=e.content,
            label=e.label,
            source=e.source,
            moderator_reasoning=e.moderator_reasoning,
            checklist_item_id=item_id,
            checklist_item_description=item_desc,
            created_at=e.created_at,
            updated_at=e.updated_at,
        ))
    return reads


@router.put("/examples/{example_id}", response_model=ExampleRead)
async def update_example(
    example_id: str,
    body: ExampleUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ExampleRead:
    result = await db.execute(select(Example).where(Example.id == example_id))
    example = result.scalar_one_or_none()
    if not example:
        raise HTTPException(status_code=404, detail="Example not found")

    if body.content is not None:
        example.content = body.content
    if body.label is not None:
        valid_labels = {"compliant", "violating", "borderline"}
        if body.label not in valid_labels:
            raise HTTPException(status_code=422, detail=f"label must be one of {valid_labels}")
        old_label = example.label
        example.label = body.label
        # Trigger suggestion/tuning generation when a borderline example is resolved
        if old_label == "borderline" and body.label in ("compliant", "violating"):
            link_result = await db.execute(
                select(ExampleRuleLink).where(ExampleRuleLink.example_id == example_id)
            )
            rule_link = link_result.scalar_one_or_none()
            if rule_link:
                background_tasks.add_task(_generate_suggestions_from_example, rule_link.rule_id)
    if body.moderator_reasoning is not None:
        example.moderator_reasoning = body.moderator_reasoning

    await db.commit()
    await db.refresh(example)
    return ExampleRead.model_validate(example)


@router.delete("/examples/{example_id}", status_code=204)
async def delete_example(
    example_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Example).where(Example.id == example_id))
    example = result.scalar_one_or_none()
    if not example:
        raise HTTPException(status_code=404, detail="Example not found")
    await db.delete(example)
    await db.commit()
