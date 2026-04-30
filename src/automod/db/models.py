import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Community(Base):
    __tablename__ = "communities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)  # reddit | chatroom | forum
    platform_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    community_context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    context_samples: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # True when committed sample posts have changed since context was last generated.
    context_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Reference communities are read-only peer corpus used as grounding for rule-text
    # suggestions. They are excluded from user-facing community lists and decision flows.
    is_reference: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    public_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    rules: Mapped[list["Rule"]] = relationship("Rule", back_populates="community", cascade="all, delete-orphan")
    decisions: Mapped[list["Decision"]] = relationship("Decision", back_populates="community", cascade="all, delete-orphan")
    sample_posts: Mapped[list["CommunitySamplePost"]] = relationship("CommunitySamplePost", back_populates="community", cascade="all, delete-orphan")


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    community_id: Mapped[str] = mapped_column(String, ForeignKey("communities.id"), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rule_type: Mapped[str] = mapped_column(String, nullable=False, default="actionable")
    # actionable | procedural | meta | informational
    rule_type_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    applies_to: Mapped[str] = mapped_column(String, nullable=False, default="both")
    # posts | comments | both
    override_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    base_checklist_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    context_adjustment_summary: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)
    # Per-rule context bundle selection. None = all bundles apply (default).
    # Non-None = filtered list of {dimension, tag} entries to include in context calibration.
    relevant_context: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # Rule-specific calibration notes (e.g., inversions or extras not captured by community tags).
    custom_context_notes: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # Pending Pass 2 preview — stashed when moderator previews a context adjustment
    # but hasn't committed yet. Cleared on commit, discard, or when the inputs change.
    pending_checklist_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    pending_context_adjustment_summary: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)
    # Snapshot of the inputs used to generate the preview, for staleness detection.
    # Wrapped as {"value": <list|null>} so "None=use-all" is distinguishable from "column missing".
    pending_relevant_context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    pending_custom_context_notes: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    pending_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Embedding of the rule title (packed float32 bytes) — populated only for reference
    # rules in the peer-grounding corpus. Used for cosine retrieval at suggestion time.
    title_embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    community: Mapped["Community"] = relationship("Community", back_populates="rules")
    checklist_items: Mapped[list["ChecklistItem"]] = relationship(
        "ChecklistItem", back_populates="rule", cascade="all, delete-orphan",
        foreign_keys="ChecklistItem.rule_id"
    )
    example_links: Mapped[list["ExampleRuleLink"]] = relationship(
        "ExampleRuleLink", back_populates="rule", cascade="all, delete-orphan"
    )
    suggestions: Mapped[list["Suggestion"]] = relationship(
        "Suggestion", back_populates="rule", cascade="all, delete-orphan"
    )


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    rule_id: Mapped[str] = mapped_column(String, ForeignKey("rules.id"), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("checklist_items.id"), nullable=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    rule_text_anchor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    item_type: Mapped[str] = mapped_column(String, nullable=False)  # deterministic | structural | subjective
    logic: Mapped[dict] = mapped_column(JSON, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False, default="warn")
    # remove | warn | continue
    context_influenced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    context_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context_change_types: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # e.g. ["threshold", "rubric", "description", "action", "new_item", "pattern", "check"]
    base_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # If this item was produced by context adjustment, the exact description of the base-checklist
    # item it was derived from — used to look up base threshold / rubric for diffing.
    context_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    context_override_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # When pinned: which (dimension, tag) bundles justified preserving this calibration.
    # Used for orphan detection on context regeneration.
    pinned_tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    rule: Mapped["Rule"] = relationship("Rule", back_populates="checklist_items", foreign_keys=[rule_id])
    children: Mapped[list["ChecklistItem"]] = relationship(
        "ChecklistItem",
        back_populates="parent",
        cascade="all, delete-orphan",
        foreign_keys="ChecklistItem.parent_id",
    )
    parent: Mapped[Optional["ChecklistItem"]] = relationship(
        "ChecklistItem",
        back_populates="children",
        remote_side="ChecklistItem.id",
        foreign_keys="ChecklistItem.parent_id",
    )
    suggestions: Mapped[list["Suggestion"]] = relationship(
        "Suggestion", back_populates="checklist_item", cascade="all, delete-orphan"
    )
    example_links: Mapped[list["ExampleChecklistItemLink"]] = relationship(
        "ExampleChecklistItemLink", back_populates="checklist_item"
        # No cascade: links are preserved (checklist_item_id nulled) when item is deleted
    )


class Example(Base):
    __tablename__ = "examples"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    community_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("communities.id"), nullable=True)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)  # compliant | violating | borderline
    source: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    # manual | moderator_decision | generated
    moderator_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    rule_links: Mapped[list["ExampleRuleLink"]] = relationship(
        "ExampleRuleLink", back_populates="example", cascade="all, delete-orphan"
    )
    checklist_item_links: Mapped[list["ExampleChecklistItemLink"]] = relationship(
        "ExampleChecklistItemLink", back_populates="example", cascade="all, delete-orphan"
    )


class ExampleRuleLink(Base):
    __tablename__ = "example_rule_links"

    example_id: Mapped[str] = mapped_column(String, ForeignKey("examples.id"), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String, ForeignKey("rules.id"), primary_key=True)
    relevance_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    example: Mapped["Example"] = relationship("Example", back_populates="rule_links")
    rule: Mapped["Rule"] = relationship("Rule", back_populates="example_links")


class ExampleChecklistItemLink(Base):
    __tablename__ = "example_checklist_item_links"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    example_id: Mapped[str] = mapped_column(String, ForeignKey("examples.id"), nullable=False)
    # Nullable: set to NULL when the item is deleted during recompile; re-resolved by description later
    checklist_item_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("checklist_items.id"), nullable=True
    )
    # Stable fallback: survives item deletion and enables re-resolution after recompile
    checklist_item_description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    example: Mapped["Example"] = relationship("Example", back_populates="checklist_item_links")
    checklist_item: Mapped[Optional["ChecklistItem"]] = relationship(
        "ChecklistItem", back_populates="example_links"
        # No cascade: when item is deleted we null checklist_item_id, not delete this row
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    community_id: Mapped[str] = mapped_column(String, ForeignKey("communities.id"), nullable=False)
    post_content: Mapped[dict] = mapped_column(JSON, nullable=False)
    post_platform_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_verdict: Mapped[str] = mapped_column(String, nullable=False)  # approve | warn | remove | review
    agent_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    agent_reasoning: Mapped[dict] = mapped_column(JSON, nullable=False)
    triggered_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    moderator_verdict: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    # approve | warn | remove | pending
    moderator_reasoning_category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # rule_doesnt_apply | edge_case_allow | rule_needs_update | agent_wrong_interpretation | agree
    moderator_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    moderator_tag: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # tag for unlinked removes: spam | off-topic | hostile_tone | low_quality | other
    was_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    community: Mapped["Community"] = relationship("Community", back_populates="decisions")


class Suggestion(Base):
    __tablename__ = "suggestions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    rule_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("rules.id"), nullable=True)
    checklist_item_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("checklist_items.id"), nullable=True
    )
    suggestion_type: Mapped[str] = mapped_column(String, nullable=False)
    # checklist | rule_text | example
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    # pending | accepted | dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    rule: Mapped[Optional["Rule"]] = relationship("Rule", back_populates="suggestions")
    checklist_item: Mapped[Optional["ChecklistItem"]] = relationship(
        "ChecklistItem", back_populates="suggestions"
    )


class CommunitySamplePost(Base):
    __tablename__ = "community_sample_posts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    community_id: Mapped[str] = mapped_column(String, ForeignKey("communities.id"), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)  # acceptable | unacceptable
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # pending = staged from modqueue, awaiting mod review; committed = active sample
    status: Mapped[str] = mapped_column(String, nullable=False, default="committed")
    # manual | url_import | modqueue
    source: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    # For modqueue-sourced items: {action_id, mod_username, action, action_at}
    source_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    community: Mapped["Community"] = relationship("Community", back_populates="sample_posts")
