"""Rule CRUD endpoints with triage + compilation."""

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Optional

import asyncpraw
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_anthropic_client, settings
from ..compiler.compiler import RuleCompiler, _filter_context_by_relevant
from ..db.database import get_db
from ..db.models import ChecklistItem, Community, Decision, Example, ExampleChecklistItemLink, ExampleRuleLink, Rule, Suggestion
from ..embeddings import embed_text, unpack_vector, cosine
from ..models.schemas import (
    CommunityContextNote,
    RuleBatchImportRequest,
    RuleBatchImportResponse,
    RuleBatchImportResult,
    RuleContextTag,
    RuleCreate,
    RuleRead,
    RulePriorityUpdate,
    RuleTypeOverride,
    RuleUpdate,
    RuleTextCitation,
    RuleTextClause,
    PeerRuleOption,
    SuggestedContextBundle,
    SuggestRuleTextRequest,
    SuggestRuleTextResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["rules"])


def get_compiler() -> RuleCompiler:
    client = get_anthropic_client()
    return RuleCompiler(client, settings)


async def _re_resolve_checklist_links(db, rule_id: str) -> None:
    """After recompile, re-link dangling ExampleChecklistItemLink rows back to items by description.

    Links become dangling (checklist_item_id=NULL) when an item is deleted during a diff-recompile.
    We match them against current items by exact description to restore the link.
    """
    items_result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
    )
    desc_to_id = {item.description: item.id for item in items_result.scalars()}
    if not desc_to_id:
        return

    example_ids_result = await db.execute(
        select(ExampleRuleLink.example_id).where(ExampleRuleLink.rule_id == rule_id)
    )
    example_ids = [r[0] for r in example_ids_result]
    if not example_ids:
        return

    dangling_result = await db.execute(
        select(ExampleChecklistItemLink)
        .where(ExampleChecklistItemLink.example_id.in_(example_ids))
        .where(ExampleChecklistItemLink.checklist_item_id == None)  # noqa: E711
        .where(ExampleChecklistItemLink.checklist_item_description != "")
    )
    resolved = 0
    for link in dangling_result.scalars():
        new_id = desc_to_id.get(link.checklist_item_description)
        if new_id:
            link.checklist_item_id = new_id
            resolved += 1
    if resolved:
        logger.info(f"Re-resolved {resolved} checklist item link(s) for rule {rule_id}")


async def _compile_rule_read_and_llm(
    rule_id: str,
    community_id: str,
) -> dict | None:
    """Phase 1: Read DB context and run LLM compilation (parallelizable).

    Returns a dict with compilation results, or None if the rule should be skipped.
    ORM objects remain usable after session close thanks to expire_on_commit=False.
    """
    from ..db.database import AsyncSessionLocal

    # ── Read phase (short-lived session) ────────────────────────────────
    async with AsyncSessionLocal() as db:
        rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
        rule = rule_result.scalar_one_or_none()
        if not rule or rule.rule_type != "actionable":
            return None

        community_result = await db.execute(
            select(Community).where(Community.id == community_id)
        )
        community = community_result.scalar_one_or_none()
        if not community:
            return None

        other_rules_result = await db.execute(
            select(Rule).where(
                Rule.community_id == community_id,
                Rule.is_active == True,
                Rule.id != rule_id,
            )
        )
        other_rules = list(other_rules_result.scalars().all())

        existing_result = await db.execute(
            select(ChecklistItem).where(
                ChecklistItem.rule_id == rule_id,
                ChecklistItem.parent_id == None,  # noqa: E711
            )
        )
        existing_items = list(existing_result.scalars().all())

        community_context = community.community_context

    # ── LLM phase (no session held) ────────────────────────────────────
    compiler = get_compiler()

    # Auto-match relevant_context on first compile when the rule hasn't been calibrated yet.
    # `None` is the unmatched sentinel; `[]` means the moderator explicitly opted out and we
    # respect that. Once we match, we'll persist the result back onto the rule below.
    matched_relevant_context: Optional[list[dict]] = None
    effective_relevant_context = rule.relevant_context
    if rule.relevant_context is None and community_context:
        try:
            matched_relevant_context = await compiler.match_relevant_context(
                rule_title=rule.title,
                rule_text=rule.text,
                community_name=community.name,
                community_context=community_context,
            )
            # Validate against the actual context — drop fabricated (dimension, tag) pairs.
            valid_pairs: set[tuple[str, str]] = set()
            for dim in ("purpose", "participants", "stakes", "tone"):
                d = (community_context or {}).get(dim) or {}
                for note in d.get("notes") or []:
                    tag = note.get("tag", "") if isinstance(note, dict) else ""
                    if tag:
                        valid_pairs.add((dim, tag))
            matched_relevant_context = [
                e for e in matched_relevant_context
                if (e.get("dimension"), e.get("tag")) in valid_pairs
            ]
            effective_relevant_context = matched_relevant_context
        except Exception as e:
            logger.warning(f"match_relevant_context failed for rule {rule_id}: {e} — falling back to all context")
            matched_relevant_context = None
            effective_relevant_context = None

    if not existing_items:
        # Two-pass compilation: base compile then context adjustment.
        # Sample posts are not passed here — they shape the community context,
        # which the compiler reads directly.
        adjusted_items, example_dicts, base_checklist_dicts, adjustment_summary = \
            await compiler.compile_rule_two_pass(
                rule=rule,
                community=community,
                other_rules=other_rules,
                community_context=community_context,
                relevant_context=effective_relevant_context,
                custom_context_notes=rule.custom_context_notes,
            )
        return {
            "mode": "compile",
            "rule_id": rule_id,
            "community_id": community_id,
            "rule": rule,
            "community": community,
            "checklist_items": adjusted_items,
            "example_dicts": example_dicts,
            "base_checklist_json": base_checklist_dicts,
            "context_adjustment_summary": adjustment_summary,
            "matched_relevant_context": matched_relevant_context,
        }
    else:
        operations = await compiler.recompile_with_diff(
            rule=rule,
            community=community,
            other_rules=other_rules,
            existing_items=existing_items,
        )
        return {
            "mode": "recompile",
            "rule_id": rule_id,
            "community_id": community_id,
            "rule": rule,
            "community": community,
            "operations": operations,
            "existing_items": existing_items,
            "matched_relevant_context": matched_relevant_context,
        }


async def _compile_rule_persist(result: dict) -> None:
    """Phase 2: Persist compilation results to DB (must be serialized for SQLite)."""
    from ..db.database import AsyncSessionLocal

    rule_id = result["rule_id"]
    rule = result["rule"]
    community = result["community"]
    compiler = get_compiler()

    async with AsyncSessionLocal() as db:
        try:
            if result["mode"] == "compile":
                await _persist_new_items(db, result["checklist_items"], rule_id)
                await db.flush()
                items_result = await db.execute(
                    select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
                )
                item_desc_map = {i.description: i.id for i in items_result.scalars()}
                await _persist_new_examples(
                    db, result["example_dicts"], rule_id,
                    item_description_map=item_desc_map, community_id=result["community_id"],
                )
                await db.flush()
                # Save two-pass artifacts on the Rule, plus auto-matched relevant_context.
                base_json = result.get("base_checklist_json")
                matched = result.get("matched_relevant_context")
                if base_json is not None or matched is not None:
                    rule_obj = (await db.execute(
                        select(Rule).where(Rule.id == rule_id)
                    )).scalar_one_or_none()
                    if rule_obj:
                        if base_json is not None:
                            rule_obj.base_checklist_json = base_json
                            rule_obj.context_adjustment_summary = result.get("context_adjustment_summary", "")
                        if matched is not None:
                            rule_obj.relevant_context = matched
                        await db.flush()
                await _fill_missing_examples(db, rule_id, compiler, rule, community)
            else:
                # Re-attach existing items to this session (merge returns new tracked instances)
                existing_by_id = {}
                for item in result["existing_items"]:
                    merged = await db.merge(item)
                    existing_by_id[merged.id] = merged
                await _apply_diff_operations(db, result["operations"], existing_by_id, rule_id)
                await db.flush()
                matched = result.get("matched_relevant_context")
                if matched is not None:
                    rule_obj = (await db.execute(
                        select(Rule).where(Rule.id == rule_id)
                    )).scalar_one_or_none()
                    if rule_obj:
                        rule_obj.relevant_context = matched
                        await db.flush()
                await _re_resolve_checklist_links(db, rule_id)
                await _fill_missing_examples(db, rule_id, compiler, rule, community)

            await db.commit()
            logger.info(f"Compilation complete for rule {rule_id}")

        except Exception as e:
            logger.error(f"Compilation failed for rule {rule_id}: {e}")
            await db.rollback()


async def _compile_rule_background(
    rule_id: str,
    community_id: str,
) -> None:
    """Background task to compile (or recompile) a single rule.

    On a successful recompile (mode == "recompile"), spawn a debounced re-eval
    of pending queue items so the moderation queue reflects the new logic. New
    rules (mode == "compile") have no prior pending decisions referencing them,
    so we skip the re-eval there.
    """
    try:
        result = await _compile_rule_read_and_llm(rule_id, community_id)
        if result:
            await _compile_rule_persist(result)
            if result.get("mode") == "recompile":
                from .checklist import spawn_pending_queue_reeval
                spawn_pending_queue_reeval(rule_id)
    except Exception as e:
        logger.error(f"Compilation failed for rule {rule_id}: {e}")


async def _persist_new_items(db, checklist_items: list, rule_id: str) -> None:
    """Insert a fresh set of checklist items.

    Items arrive as a flat list with parent_id already set by the compiler's
    _parse_items_recursive. Just add them all.
    """
    for item in checklist_items:
        db.add(item)
    await db.flush()


async def _persist_new_examples(
    db,
    example_dicts: list,
    rule_id: str,
    item_description_map: dict[str, str] | None = None,
    community_id: str | None = None,
) -> None:
    """Insert generated examples and link them to the rule.

    item_description_map maps checklist item description → item ID, used to
    create ExampleChecklistItemLink records when the compiler provides
    related_checklist_item_description on an example.
    """
    for ex_dict in example_dicts:
        label = ex_dict.get("label", "compliant")
        if label == "borderline":
            # Route borderline examples through the suggestion pipeline so
            # moderators must make an explicit compliant/violating decision.
            db.add(Suggestion(
                rule_id=rule_id,
                suggestion_type="example",
                content={
                    "label": "borderline",
                    "content": ex_dict.get("content", {}),
                    "relevance_note": ex_dict.get("relevance_note", ""),
                    "related_checklist_item_description": ex_dict.get("related_checklist_item_description"),
                },
                status="pending",
            ))
            continue

        example = Example(
            community_id=community_id,
            content=ex_dict.get("content", {}),
            label=label,
            source="generated",
        )
        db.add(example)
        await db.flush()
        db.add(ExampleRuleLink(
            example_id=example.id,
            rule_id=rule_id,
            relevance_note=ex_dict.get("relevance_note", ""),
        ))
        related_desc = ex_dict.get("related_checklist_item_description")
        if related_desc and item_description_map:
            item_id = item_description_map.get(related_desc)
            if item_id:
                db.add(ExampleChecklistItemLink(
                    example_id=example.id,
                    checklist_item_id=item_id,
                    checklist_item_description=related_desc,
                ))


async def _fill_missing_examples(db, rule_id: str, compiler, rule, community) -> None:
    """Generate one violating example for each top-level checklist item that doesn't have one."""
    items_result = await db.execute(
        select(ChecklistItem).where(
            ChecklistItem.rule_id == rule_id,
            ChecklistItem.parent_id == None,  # noqa: E711
        )
    )
    all_items = list(items_result.scalars())
    if not all_items:
        return

    covered_result = await db.execute(
        select(ExampleChecklistItemLink.checklist_item_id)
        .join(Example, Example.id == ExampleChecklistItemLink.example_id)
        .where(
            ExampleChecklistItemLink.checklist_item_id.in_([i.id for i in all_items]),
            Example.label.in_(["violating", "borderline"]),
        )
        .distinct()
    )
    covered_ids = {r[0] for r in covered_result}

    items_needing = [i for i in all_items if i.id not in covered_ids]
    if not items_needing:
        return

    # Limit to 3 items per rule to avoid overwhelming the calibration step.
    # Prioritize: subjective > context-influenced > lower thresholds (more ambiguous).
    if len(items_needing) > 3:
        def _ambiguity_score(item: ChecklistItem) -> tuple:
            type_rank = 0 if item.item_type == "subjective" else 1
            context_rank = 0 if item.context_influenced else 1
            threshold = (item.logic or {}).get("threshold", 0.7) if item.item_type == "subjective" else 1.0
            return (type_rank, context_rank, threshold)

        items_needing.sort(key=_ambiguity_score)
        items_needing = items_needing[:3]

    example_ids_result = await db.execute(
        select(ExampleRuleLink.example_id).where(ExampleRuleLink.rule_id == rule_id)
    )
    example_ids = [r[0] for r in example_ids_result]
    existing_examples = []
    if example_ids:
        examples_result = await db.execute(
            select(Example).where(Example.id.in_(example_ids))
        )
        existing_examples = list(examples_result.scalars())

    new_examples = await compiler.generate_examples_for_items(
        rule=rule,
        community=community,
        items=items_needing,
        existing_examples=existing_examples or None,
    )

    item_desc_map = {i.description: i.id for i in all_items}
    await _persist_new_examples(db, new_examples, rule_id, item_description_map=item_desc_map, community_id=rule.community_id)
    logger.info(f"Filled {len(new_examples)} missing example(s) for rule {rule_id}")


async def _apply_diff_operations(
    db,
    operations: list[dict],
    existing_by_id: dict,
    rule_id: str,
) -> None:
    """Apply keep/update/add/delete operations from recompile_with_diff()."""
    for op in operations:
        kind = op.get("op")

        if kind == "keep":
            # Nothing to do — row stays as-is
            pass

        elif kind == "update":
            item = existing_by_id.get(op.get("existing_id"))
            if item is None:
                logger.warning(f"recompile update: unknown id {op.get('existing_id')!r}")
                continue
            if "description" in op:
                item.description = op["description"]
            if "rule_text_anchor" in op:
                item.rule_text_anchor = op["rule_text_anchor"]
            if "item_type" in op:
                item.item_type = op["item_type"]
            if "logic" in op:
                item.logic = op["logic"]
            if "action" in op:
                item.action = op["action"]
            if "context_influenced" in op:
                item.context_influenced = op["context_influenced"]
            if "context_note" in op:
                item.context_note = op["context_note"]
            if "context_change_types" in op:
                item.context_change_types = op["context_change_types"]
            # Replace children: null out links, delete old child rows, insert new ones
            if "children" in op:
                old_child_ids_result = await db.execute(
                    select(ChecklistItem.id).where(ChecklistItem.parent_id == item.id)
                )
                old_child_ids = [r[0] for r in old_child_ids_result]
                if old_child_ids:
                    await db.execute(
                        sa_update(ExampleChecklistItemLink)
                        .where(ExampleChecklistItemLink.checklist_item_id.in_(old_child_ids))
                        .values(checklist_item_id=None)
                    )
                await db.execute(
                    sa_delete(ChecklistItem).where(ChecklistItem.parent_id == item.id)
                )
                await db.flush()
                for i, child_data in enumerate(op["children"]):
                    db.add(ChecklistItem(
                        rule_id=rule_id,
                        order=i,
                        parent_id=item.id,
                        description=child_data.get("description", ""),
                        rule_text_anchor=child_data.get("rule_text_anchor"),
                        item_type=child_data.get("item_type", "subjective"),
                        logic=child_data.get("logic", {}),
                        action=child_data.get("action", "warn"),
                        context_influenced=child_data.get("context_influenced", False),
                        context_note=child_data.get("context_note"),
                        context_change_types=child_data.get("context_change_types"),
                    ))

        elif kind == "delete":
            item = existing_by_id.get(op.get("existing_id"))
            if item is None:
                logger.warning(f"recompile delete: unknown id {op.get('existing_id')!r}")
                continue
            # Collect child IDs, null out all links before deletion to preserve description
            child_ids_result = await db.execute(
                select(ChecklistItem.id).where(ChecklistItem.parent_id == item.id)
            )
            child_ids = [r[0] for r in child_ids_result]
            ids_to_null = child_ids + [item.id]
            await db.execute(
                sa_update(ExampleChecklistItemLink)
                .where(ExampleChecklistItemLink.checklist_item_id.in_(ids_to_null))
                .values(checklist_item_id=None)
            )
            await db.execute(
                sa_delete(ChecklistItem).where(ChecklistItem.parent_id == item.id)
            )
            await db.delete(item)

        elif kind == "add":
            parent_id = op.get("parent_id")

            # If adding under a parent, ensure parent action is "continue"
            if parent_id:
                parent_item = existing_by_id.get(parent_id)
                if parent_item and parent_item.action != "continue":
                    parent_item.action = "continue"

            # Place after existing siblings
            order = op.get("order", 0)
            if parent_id:
                sibling_result = await db.execute(
                    select(ChecklistItem)
                    .where(ChecklistItem.parent_id == parent_id, ChecklistItem.rule_id == rule_id)
                )
                siblings = list(sibling_result.scalars())
                if siblings:
                    order = max(s.order for s in siblings) + 1

            new_item = ChecklistItem(
                rule_id=rule_id,
                order=order,
                parent_id=parent_id,
                description=op.get("description", ""),
                rule_text_anchor=op.get("rule_text_anchor"),
                item_type=op.get("item_type", "subjective"),
                logic=op.get("logic", {}),
                action=op.get("action", "warn"),
                context_influenced=op.get("context_influenced", False),
                context_note=op.get("context_note"),
                context_change_types=op.get("context_change_types"),
            )
            db.add(new_item)
            await db.flush()
            for i, child_data in enumerate(op.get("children", [])):
                db.add(ChecklistItem(
                    rule_id=rule_id,
                    order=i,
                    parent_id=new_item.id,
                    description=child_data.get("description", ""),
                    rule_text_anchor=child_data.get("rule_text_anchor"),
                    item_type=child_data.get("item_type", "subjective"),
                    logic=child_data.get("logic", {}),
                    action=child_data.get("action", "warn"),
                    context_influenced=child_data.get("context_influenced", False),
                    context_note=child_data.get("context_note"),
                    context_change_types=child_data.get("context_change_types"),
                ))

        else:
            logger.warning(f"recompile: unknown op {kind!r}, skipping")


def _community_tag_set(community_context: Optional[dict]) -> set[tuple[str, str]]:
    """Flatten a community_context dict into a set of (dimension, tag) pairs."""
    out: set[tuple[str, str]] = set()
    if not community_context:
        return out
    for dim in ("purpose", "participants", "stakes", "tone"):
        d = (community_context or {}).get(dim) or {}
        for note in d.get("notes") or []:
            tag = note.get("tag", "") if isinstance(note, dict) else ""
            if tag:
                out.add((dim, tag))
    return out


def _community_tag_text_map(community_context: Optional[dict]) -> dict[tuple[str, str], str]:
    """Map (dimension, tag) → note text for citation hydration."""
    out: dict[tuple[str, str], str] = {}
    if not community_context:
        return out
    for dim in ("purpose", "participants", "stakes", "tone"):
        d = (community_context or {}).get(dim) or {}
        for note in d.get("notes") or []:
            if not isinstance(note, dict):
                continue
            tag = note.get("tag", "")
            if tag and (dim, tag) not in out:
                out[(dim, tag)] = note.get("text", "")
    return out


@router.post(
    "/communities/{community_id}/rules/suggest-text",
    response_model=SuggestRuleTextResponse,
)
async def suggest_rule_text(
    community_id: str,
    body: SuggestRuleTextRequest,
    db: AsyncSession = Depends(get_db),
) -> SuggestRuleTextResponse:
    """Draft a rule text grounded in this community's context + peer-community rules.

    A9 + A8 grounding: every clause cites either a {dimension, tag} note from this
    community's context or a peer rule from a community with overlapping context tags.
    """
    title = (body.title or "").strip()
    if len(title) < 3:
        raise HTTPException(status_code=400, detail="title must be at least 3 characters")

    comm_result = await db.execute(
        select(Community).where(Community.id == community_id)
    )
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    full_target_context = community.community_context or {}
    full_target_tags = _community_tag_set(full_target_context)

    # If the caller has pre-selected context tags (from the NewRuleModal picker),
    # restrict matching + drafting to that subset. Otherwise fall back to the full
    # community context.
    selected_pairs: set[tuple[str, str]] = set()
    for entry in body.relevant_context or []:
        if entry.dimension and entry.tag and (entry.dimension, entry.tag) in full_target_tags:
            selected_pairs.add((entry.dimension, entry.tag))

    if selected_pairs:
        target_tags = selected_pairs
        # Filter the context dict down to the selected (dim, tag) bundles for the LLM prompt.
        relevant_for_filter = [
            {"dimension": d, "tag": t, "weight": 1.0} for (d, t) in selected_pairs
        ]
        target_context = _filter_context_by_relevant(full_target_context, relevant_for_filter) or {}
    else:
        target_tags = full_target_tags
        target_context = full_target_context

    # Embed the user's title
    try:
        query_vec = await embed_text(title)
    except Exception as e:
        logger.error(f"embed_text failed: {e}")
        raise HTTPException(status_code=502, detail="embedding service unavailable")

    # Pull all reference rules + their parent communities. Brute-force scan is fine
    # at corpus sizes up to ~10k. Keep this on the DB side as a single join.
    ref_rules_result = await db.execute(
        select(Rule, Community)
        .join(Community, Rule.community_id == Community.id)
        .where(Community.is_reference.is_(True))
        .where(Rule.title_embedding.is_not(None))
    )
    candidates: list[tuple[Rule, Community, float]] = []
    for rule, ref_comm in ref_rules_result.all():
        vec = unpack_vector(rule.title_embedding)
        if vec is None or vec.size == 0:
            continue
        score = cosine(query_vec, vec)
        candidates.append((rule, ref_comm, score))

    # Top-20 by cosine, then re-rank by Jaccard tag overlap with target community
    candidates.sort(key=lambda t: t[2], reverse=True)
    top_n = candidates[:20]

    rescored: list[tuple[Rule, Community, float, list[str]]] = []
    for rule, ref_comm, cos_score in top_n:
        peer_tags = _community_tag_set(ref_comm.community_context)
        overlap = target_tags & peer_tags
        # A peer rule must share AT LEAST ONE context tag to surface as a suggestion —
        # the whole point of this list is "rules from communities that share your context".
        # When the target itself has no context tags yet, fall back to cosine-only ranking
        # so suggestions still appear during onboarding.
        if target_tags and not overlap:
            continue
        union = target_tags | peer_tags
        jaccard = (len(overlap) / len(union)) if union else 0.0
        # Combined score: 0.6·cosine + 0.4·jaccard
        combined = 0.6 * cos_score + 0.4 * jaccard
        shared = sorted({t for (_, t) in overlap})
        rescored.append((rule, ref_comm, combined, shared))

    rescored.sort(key=lambda t: t[2], reverse=True)
    # Deduplicate to one rule per community (highest-scored), then cap.
    seen_communities: set[str] = set()
    distinct_peers: list[tuple] = []
    for entry in rescored:
        rule, ref_comm, _score, _shared = entry
        if ref_comm.id in seen_communities:
            continue
        seen_communities.add(ref_comm.id)
        distinct_peers.append(entry)
        if len(distinct_peers) >= 5:
            break
    top_peers = distinct_peers

    # Build peer-rules payload for the compiler
    peer_rules_payload: list[dict] = []
    for rule, ref_comm, _score, shared in top_peers:
        peer_rules_payload.append({
            "community_name": ref_comm.name,
            "rule_title": rule.title,
            "rule_text": rule.text,
            "shared_tags": shared,
        })

    # Build the user-facing peer_options list (used by the multi-option suggestion UI).
    # Each option carries the source community's full context tags + the shared subset.
    peer_options: list[PeerRuleOption] = []
    for rule, ref_comm, _score, shared in top_peers:
        peer_pairs = _community_tag_set(ref_comm.community_context)
        shared_pairs = sorted(target_tags & peer_pairs)
        peer_options.append(PeerRuleOption(
            community_id=ref_comm.id,
            community_name=ref_comm.name,
            rule_title=rule.title,
            rule_text=rule.text,
            peer_context_tags=[
                SuggestedContextBundle(dimension=d, tag=t)
                for (d, t) in sorted(peer_pairs)
            ],
            shared_tags=[
                SuggestedContextBundle(dimension=d, tag=t)
                for (d, t) in shared_pairs
            ],
        ))

    compiler = get_compiler()
    try:
        result = await compiler.draft_rule_from_context(
            title=title,
            target_community_name=community.name,
            target_context=target_context,
            peer_rules=peer_rules_payload,
        )
    except Exception as e:
        # Degrade gracefully — peer-rule options can still carry the response.
        logger.warning(f"draft_rule_from_context failed: {e} — returning peer options only")
        result = {"draft_text": "", "clauses": [], "suggested_relevant_context": []}

    # Validate + hydrate citations. Reject ungrounded clauses; drop fabricated citations.
    target_note_texts = _community_tag_text_map(target_context)
    peer_lookup: dict[tuple[str, str], dict] = {
        (p["community_name"], p["rule_title"]): p for p in peer_rules_payload
    }

    raw_clauses = result.get("clauses") or []
    validated_clauses: list[RuleTextClause] = []
    for raw in raw_clauses:
        if not isinstance(raw, dict):
            continue
        c_text = (raw.get("text") or "").strip()
        if not c_text:
            continue
        valid_citations: list[RuleTextCitation] = []
        for c in raw.get("citations") or []:
            if not isinstance(c, dict):
                continue
            kind = c.get("kind")
            if kind == "context":
                dim, tag = c.get("dimension"), c.get("tag")
                if not dim or not tag:
                    continue
                note_text = target_note_texts.get((dim, tag))
                if note_text is None:
                    # Citation references a tag not present in target context — drop it.
                    continue
                valid_citations.append(RuleTextCitation(
                    kind="context", dimension=dim, tag=tag, note_text=note_text,
                ))
            elif kind == "peer_rule":
                cname, rtitle = c.get("community_name"), c.get("rule_title")
                peer = peer_lookup.get((cname, rtitle))
                if peer is None:
                    continue
                shared_tag = c.get("shared_tag")
                # Only allow shared_tags actually shared with the target.
                if shared_tag and shared_tag not in (peer.get("shared_tags") or []):
                    shared_tag = None
                valid_citations.append(RuleTextCitation(
                    kind="peer_rule",
                    community_name=cname,
                    rule_title=rtitle,
                    rule_text=peer.get("rule_text"),
                    shared_tag=shared_tag,
                ))
        if not valid_citations:
            # Reject ungrounded clauses outright.
            continue
        validated_clauses.append(RuleTextClause(text=c_text, citations=valid_citations))

    # If drafting produced nothing usable, leave the LLM-draft slot empty rather than
    # 502'ing — peer options below still give the user something to work with.
    draft_text_out = result.get("draft_text", "") if validated_clauses else ""

    # Validate suggested_relevant_context against target tags.
    suggested_bundles: list[SuggestedContextBundle] = []
    for entry in result.get("suggested_relevant_context") or []:
        if not isinstance(entry, dict):
            continue
        dim, tag = entry.get("dimension"), entry.get("tag")
        if dim and tag and (dim, tag) in target_tags:
            suggested_bundles.append(SuggestedContextBundle(dimension=dim, tag=tag))

    return SuggestRuleTextResponse(
        draft_text=draft_text_out,
        clauses=validated_clauses,
        suggested_relevant_context=suggested_bundles,
        peer_rules_considered=len(peer_rules_payload),
        target_has_context=bool(target_tags),
        peer_options=peer_options,
    )


@router.post("/communities/{community_id}/rules", response_model=RuleRead, status_code=201)
async def create_rule(
    community_id: str,
    body: RuleCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    # Verify community exists
    comm_result = await db.execute(select(Community).where(Community.id == community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    # Create the rule first
    rule = Rule(
        community_id=community_id,
        title=body.title,
        text=body.text,
        priority=body.priority,
        relevant_context=(
            [e.model_dump() for e in body.relevant_context]
            if body.relevant_context is not None else None
        ),
        custom_context_notes=[n.model_dump() for n in body.custom_context_notes],
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    # Triage the rule (blocking — user needs to see the type)
    try:
        compiler = get_compiler()
        triage = await compiler.triage_rule(rule.text, community.name, community.platform)
        rule.rule_type = triage["rule_type"]
        rule.rule_type_reasoning = triage["reasoning"]
        rule.applies_to = triage.get("applies_to", "both")
        await db.commit()
        await db.refresh(rule)
    except Exception as e:
        logger.error(f"Triage failed for rule {rule.id}: {e}")

    # If actionable, compile in background
    if rule.rule_type == "actionable":
        background_tasks.add_task(_compile_rule_background, rule.id, community_id)

    return RuleRead.model_validate(rule)


@router.post("/communities/{community_id}/rules/batch", response_model=RuleBatchImportResponse, status_code=201)
async def batch_import_rules(
    community_id: str,
    body: RuleBatchImportRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RuleBatchImportResponse:
    """Import multiple rules at once. Triages all concurrently, then compiles actionable ones in the background."""
    comm_result = await db.execute(select(Community).where(Community.id == community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    # Create all Rule records first (assign auto-priority if not provided)
    rules: list[Rule] = []
    for i, item in enumerate(body.rules):
        priority = item.priority if item.priority is not None else i
        rule = Rule(
            community_id=community_id,
            title=item.title,
            text=item.text,
            priority=priority,
        )
        db.add(rule)
        rules.append(rule)
    await db.commit()
    for rule in rules:
        await db.refresh(rule)

    # Triage all rules concurrently
    compiler = get_compiler()

    async def _triage(rule: Rule) -> tuple[Rule, str | None]:
        try:
            result = await compiler.triage_rule(rule.text, community.name, community.platform)
            rule.rule_type = result["rule_type"]
            rule.rule_type_reasoning = result["reasoning"]
            rule.applies_to = result.get("applies_to", "both")
            return rule, None
        except Exception as e:
            logger.error(f"Triage failed for rule {rule.id}: {e}")
            return rule, str(e)

    triage_results = await asyncio.gather(*[_triage(r) for r in rules])
    await db.commit()
    for rule in rules:
        await db.refresh(rule)

    # Schedule background compilation for actionable rules (concurrently)
    results: list[RuleBatchImportResult] = []
    actionable_ids: list[int] = []
    for rule, triage_error in triage_results:
        if rule.rule_type == "actionable":
            actionable_ids.append(rule.id)
        results.append(RuleBatchImportResult(rule=RuleRead.model_validate(rule), triage_error=triage_error))

    async def _compile_batch() -> None:
        # Phase 1: Run all LLM compilations in parallel
        llm_results = await asyncio.gather(
            *[_compile_rule_read_and_llm(rid, community_id) for rid in actionable_ids],
            return_exceptions=True,
        )
        # Phase 2: Persist results sequentially (SQLite single-writer)
        for rid, result in zip(actionable_ids, llm_results):
            if isinstance(result, Exception):
                logger.error(f"Compilation LLM phase failed for rule {rid}: {result}")
                continue
            if result is None:
                continue
            await _compile_rule_persist(result)

    if actionable_ids:
        background_tasks.add_task(_compile_batch)
    actionable_count = len(actionable_ids)

    return RuleBatchImportResponse(
        imported=results,
        total=len(results),
        actionable_count=actionable_count,
        skipped_count=len(results) - actionable_count,
    )


class RedditRuleItem(BaseModel):
    title: str
    text: str


class RedditRulesResponse(BaseModel):
    rules: list[RedditRuleItem]
    subreddit: str


@router.get("/reddit-rules/{subreddit}", response_model=RedditRulesResponse)
async def fetch_reddit_rules(subreddit: str) -> RedditRulesResponse:
    """Fetch rules from a subreddit via the authenticated Reddit API."""
    sub = re.sub(r"^r/", "", subreddit.strip(), flags=re.IGNORECASE)
    if not re.match(r"^[A-Za-z0-9_]+$", sub):
        raise HTTPException(status_code=422, detail="Invalid subreddit name")

    if not settings.reddit_client_id or not settings.reddit_client_secret:
        raise HTTPException(
            status_code=500,
            detail="Reddit OAuth credentials not configured",
        )

    rules: list[RedditRuleItem] = []
    try:
        async with asyncpraw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
            username=settings.reddit_username or None,
            password=settings.reddit_password or None,
        ) as reddit:
            subreddit_obj = await reddit.subreddit(sub)
            async for r in subreddit_obj.rules:
                title = (r.short_name or "").strip()
                text = (r.description or "").strip()
                if title:
                    rules.append(RedditRuleItem(title=title, text=text or title))
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch rules for r/{sub}: {e}",
        )

    return RedditRulesResponse(rules=rules, subreddit=sub)


@router.get("/communities/{community_id}/rules", response_model=list[RuleRead])
async def list_rules(
    community_id: str,
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[RuleRead]:
    query = select(Rule).where(Rule.community_id == community_id)
    if not include_inactive:
        query = query.where(Rule.is_active == True)
    query = query.order_by(Rule.priority.asc())
    result = await db.execute(query)
    rules = result.scalars().all()
    return [RuleRead.model_validate(r) for r in rules]


class CommitContextDraft(BaseModel):
    relevant_context: Optional[list[RuleContextTag]] = None
    custom_context_notes: list[CommunityContextNote] = []


class CommitRecompileRequest(BaseModel):
    rule_text: str
    title: Optional[str] = None
    operations: list[dict] = []
    context: Optional[CommitContextDraft] = None


@router.post("/rules/{rule_id}/commit-recompile", response_model=RuleRead)
async def commit_recompile(
    rule_id: str,
    body: CommitRecompileRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    """Apply a pre-computed recompile diff and persist the rule text in one shot.

    Used by the fluid editor: the live preview already produced `operations`
    via recompile_with_diff. Saving should apply THOSE ops, not re-run the
    LLM — both for cost and so what the moderator saw is what gets persisted.
    """
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    comm_result = await db.execute(select(Community).where(Community.id == rule.community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    text_changed = body.rule_text != rule.text
    rule.text = body.rule_text
    if body.title is not None:
        rule.title = body.title

    # Apply draft context fields, if supplied.
    if body.context is not None:
        rule.relevant_context = (
            [t.model_dump() for t in body.context.relevant_context]
            if body.context.relevant_context is not None else None
        )
        rule.custom_context_notes = [n.model_dump() for n in body.context.custom_context_notes]

    # Clear any stashed context preview — text or context changed.
    rule.pending_checklist_json = None
    rule.pending_context_adjustment_summary = None
    rule.pending_relevant_context = None
    rule.pending_custom_context_notes = None
    rule.pending_generated_at = None

    # Re-triage only if text actually changed.
    if text_changed:
        try:
            compiler = get_compiler()
            triage = await compiler.triage_rule(rule.text, community.name, community.platform)
            rule.rule_type = triage["rule_type"]
            rule.rule_type_reasoning = triage["reasoning"]
            rule.applies_to = triage.get("applies_to", "both")
        except Exception as e:
            logger.error(f"Re-triage failed during commit-recompile: {e}")

    # Apply the supplied operations synchronously, mirroring _compile_rule_persist's recompile path.
    if rule.rule_type == "actionable" and body.operations:
        existing_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.rule_id == rule_id)
        )
        existing_by_id = {item.id: item for item in existing_result.scalars()}
        try:
            await _apply_diff_operations(db, body.operations, existing_by_id, rule_id)
            await db.flush()
            await _re_resolve_checklist_links(db, rule_id)
        except Exception as e:
            logger.error(f"Apply ops failed during commit-recompile for {rule_id}: {e}")
            await db.rollback()
            raise HTTPException(status_code=500, detail="Failed to apply operations")

    await db.commit()
    await db.refresh(rule)

    # Fill any missing examples + re-eval pending queue in the background.
    if rule.rule_type == "actionable":
        background_tasks.add_task(_fill_examples_and_reeval, rule_id, rule.community_id)

    return RuleRead.model_validate(rule)


async def _fill_examples_and_reeval(rule_id: str, community_id: str) -> None:
    """Background follow-up after a synchronous commit-recompile.

    Generates violating examples for any newly-added items that lack one, then
    re-evaluates the moderation queue against the new logic.
    """
    from ..db.database import AsyncSessionLocal
    try:
        compiler = get_compiler()
        async with AsyncSessionLocal() as db:
            rule = (await db.execute(select(Rule).where(Rule.id == rule_id))).scalar_one_or_none()
            community = (await db.execute(
                select(Community).where(Community.id == community_id)
            )).scalar_one_or_none()
            if rule and community:
                await _fill_missing_examples(db, rule_id, compiler, rule, community)
                await db.commit()
    except Exception as e:
        logger.error(f"Background follow-up failed for rule {rule_id}: {e}")
    try:
        from .checklist import spawn_pending_queue_reeval
        spawn_pending_queue_reeval(rule_id)
    except Exception as e:
        logger.error(f"Pending-queue re-eval spawn failed for rule {rule_id}: {e}")


@router.put("/rules/{rule_id}", response_model=RuleRead)
async def update_rule(
    rule_id: str,
    body: RuleUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if body.title is not None:
        rule.title = body.title
    if body.text is not None:
        rule.text = body.text
    if body.priority is not None:
        rule.priority = body.priority
    if body.is_active is not None:
        rule.is_active = body.is_active
    if body.applies_to is not None:
        rule.applies_to = body.applies_to

    context_changed = False
    fields_set = body.model_fields_set
    if "relevant_context" in fields_set:
        rule.relevant_context = (
            [e.model_dump() for e in body.relevant_context]
            if body.relevant_context is not None else None
        )
        context_changed = True
    if "custom_context_notes" in fields_set:
        rule.custom_context_notes = (
            [n.model_dump() for n in body.custom_context_notes]
            if body.custom_context_notes is not None else []
        )
        context_changed = True

    # Any text or context change invalidates a pending preview — clear it so the
    # moderator has to regenerate before committing.
    if body.text is not None or context_changed:
        rule.pending_checklist_json = None
        rule.pending_context_adjustment_summary = None
        rule.pending_relevant_context = None
        rule.pending_custom_context_notes = None
        rule.pending_generated_at = None

    # If text changed, re-triage and queue recompile
    if body.text is not None:
        comm_result = await db.execute(
            select(Community).where(Community.id == rule.community_id)
        )
        community = comm_result.scalar_one_or_none()
        if community:
            try:
                compiler = get_compiler()
                triage = await compiler.triage_rule(rule.text, community.name, community.platform)
                rule.rule_type = triage["rule_type"]
                rule.rule_type_reasoning = triage["reasoning"]
                rule.applies_to = triage.get("applies_to", "both")
            except Exception as e:
                logger.error(f"Re-triage failed: {e}")

        if rule.rule_type == "actionable":
            background_tasks.add_task(_compile_rule_background, rule.id, rule.community_id)

    await db.commit()
    await db.refresh(rule)
    return RuleRead.model_validate(rule)


@router.put("/rules/{rule_id}/priority", response_model=RuleRead)
async def update_rule_priority(
    rule_id: str,
    body: RulePriorityUpdate,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    rule.priority = body.priority
    await db.commit()
    await db.refresh(rule)
    return RuleRead.model_validate(rule)


@router.put("/rules/{rule_id}/rule-type", response_model=RuleRead)
async def override_rule_type(
    rule_id: str,
    body: RuleTypeOverride,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RuleRead:
    valid_types = {"actionable", "procedural", "meta", "informational"}
    if body.rule_type not in valid_types:
        raise HTTPException(status_code=422, detail=f"rule_type must be one of {valid_types}")

    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    rule.rule_type = body.rule_type
    if body.reasoning:
        rule.rule_type_reasoning = body.reasoning

    await db.commit()
    await db.refresh(rule)

    # If overridden to actionable and no checklist exists, compile it
    if body.rule_type == "actionable":
        items_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.rule_id == rule_id).limit(1)
        )
        if not items_result.scalar_one_or_none():
            background_tasks.add_task(_compile_rule_background, rule.id, rule.community_id)

    return RuleRead.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def deactivate_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.is_active = False
    await db.commit()


class MatchContextResponse(BaseModel):
    relevant_context: list[RuleContextTag]


@router.post("/rules/{rule_id}/match-context", response_model=MatchContextResponse)
async def match_rule_context(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> MatchContextResponse:
    """Run the LLM auto-match for relevant context tags + weights.

    Does NOT persist — caller decides whether to commit (e.g. via PUT /rules/{id}).
    Use this when the mod wants to (re-)populate the rule editor's slider state from the
    LLM, e.g. after a context regeneration or for a hand-authored rule that was created
    with relevant_context = [].
    """
    rule_result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    comm_result = await db.execute(select(Community).where(Community.id == rule.community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    community_context = community.community_context or {}
    valid_pairs: set[tuple[str, str]] = set()
    for dim in ("purpose", "participants", "stakes", "tone"):
        d = community_context.get(dim) or {}
        for note in d.get("notes") or []:
            tag = note.get("tag", "") if isinstance(note, dict) else ""
            if tag:
                valid_pairs.add((dim, tag))
    if not valid_pairs:
        return MatchContextResponse(relevant_context=[])

    compiler = get_compiler()
    try:
        matched = await compiler.match_relevant_context(
            rule_title=rule.title,
            rule_text=rule.text,
            community_name=community.name,
            community_context=community_context,
        )
    except Exception as e:
        logger.error(f"match_relevant_context failed for rule {rule_id}: {e}")
        raise HTTPException(status_code=502, detail="LLM match failed")

    out: list[RuleContextTag] = []
    for entry in matched:
        dim = entry.get("dimension")
        tag = entry.get("tag")
        if (dim, tag) not in valid_pairs:
            continue
        out.append(RuleContextTag(dimension=dim, tag=tag, weight=entry.get("weight", 1.0)))
    return MatchContextResponse(relevant_context=out)


class PeerRule(BaseModel):
    community_id: str
    community_name: str
    rule_title: str
    rule_text: str
    shared_tags: list[str]


class PeerRulesGroup(BaseModel):
    dimension: str
    tag: str
    rules: list[PeerRule]


class PeerSuggestionsResponse(BaseModel):
    groups: list[PeerRulesGroup]
    target_tags: list[RuleContextTag]  # this community's tags, for UI


@router.get("/communities/{community_id}/rules/peer-suggestions", response_model=PeerSuggestionsResponse)
async def peer_rule_suggestions(
    community_id: str,
    per_tag_limit: int = 3,
    min_jaccard: float = 0.2,
    db: AsyncSession = Depends(get_db),
) -> PeerSuggestionsResponse:
    """Surface rules from reference communities that share context tags with this community.

    Used as the empty-state of the NewRuleModal — when the moderator hasn't typed a title
    yet, show what rules peer communities (that share this community's context) actually
    have, grouped by which tag they share.
    """
    comm_result = await db.execute(select(Community).where(Community.id == community_id))
    community = comm_result.scalar_one_or_none()
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")

    target_pairs = _community_tag_set(community.community_context)
    if not target_pairs:
        return PeerSuggestionsResponse(groups=[], target_tags=[])

    # Pull all reference communities with non-empty context, plus their rules.
    ref_result = await db.execute(
        select(Rule, Community)
        .join(Community, Rule.community_id == Community.id)
        .where(Community.is_reference.is_(True))
        .where(Rule.is_active == True)
        .where(Community.id != community_id)
    )

    # Group rules by shared tag — a single peer rule may surface under multiple tags.
    grouped: dict[tuple[str, str], list[PeerRule]] = {}
    seen_per_tag: dict[tuple[str, str], set[tuple[str, str]]] = {}

    for rule, ref_comm in ref_result.all():
        peer_pairs = _community_tag_set(ref_comm.community_context)
        if not peer_pairs:
            continue
        overlap = target_pairs & peer_pairs
        if not overlap:
            continue
        union = target_pairs | peer_pairs
        jaccard = len(overlap) / len(union) if union else 0.0
        if jaccard < min_jaccard:
            continue
        peer_obj = PeerRule(
            community_id=ref_comm.id,
            community_name=ref_comm.name,
            rule_title=rule.title,
            rule_text=rule.text,
            shared_tags=sorted({t for (_, t) in overlap}),
        )
        for pair in overlap:
            key = pair
            if pair in target_pairs:
                # de-dupe by (peer_community, rule_title) within a tag bucket
                seen = seen_per_tag.setdefault(key, set())
                ident = (ref_comm.id, rule.title)
                if ident in seen:
                    continue
                seen.add(ident)
                grouped.setdefault(key, []).append(peer_obj)

    groups: list[PeerRulesGroup] = []
    for (dim, tag), rules in grouped.items():
        groups.append(PeerRulesGroup(
            dimension=dim,
            tag=tag,
            rules=rules[:per_tag_limit],
        ))
    # Sort: groups with more peer rules first, then dimension/tag alpha for stability.
    groups.sort(key=lambda g: (-len(g.rules), g.dimension, g.tag))

    target_tag_list = [
        RuleContextTag(dimension=dim, tag=tag, weight=1.0)
        for (dim, tag) in sorted(target_pairs)
    ]
    return PeerSuggestionsResponse(groups=groups, target_tags=target_tag_list)
