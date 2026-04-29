from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, field_validator


# ── Community ──────────────────────────────────────────────────────────────────

class CommunityCreate(BaseModel):
    name: str
    platform: str  # reddit | chatroom | forum
    platform_config: Optional[dict[str, Any]] = None


# ── Community Context ─────────────────────────────────────────────────────────

class CommunityContextNote(BaseModel):
    """A single calibration note paired with its tag."""
    text: str
    tag: str = ""


class CommunityContextDimension(BaseModel):
    """A single context dimension (purpose, participants, stakes, or tone)."""
    notes: list[CommunityContextNote] = []
    manually_edited: bool = False

    @field_validator('notes', mode='before')
    @classmethod
    def _migrate_notes(cls, v):
        """Handle old format where notes were plain strings."""
        if not v:
            return []
        if v and isinstance(v[0], str):
            return [{"text": note, "tag": ""} for note in v]
        return v


class CommunityContextData(BaseModel):
    """Full community context with four dimensions."""
    purpose: CommunityContextDimension = CommunityContextDimension()
    participants: CommunityContextDimension = CommunityContextDimension()
    stakes: CommunityContextDimension = CommunityContextDimension()
    tone: CommunityContextDimension = CommunityContextDimension()


class CommunityContextUpdate(BaseModel):
    """Partial update to community context — any field can be omitted."""
    purpose: Optional[CommunityContextDimension] = None
    participants: Optional[CommunityContextDimension] = None
    stakes: Optional[CommunityContextDimension] = None
    tone: Optional[CommunityContextDimension] = None


class CommunityRead(BaseModel):
    id: str
    name: str
    platform: str
    platform_config: Optional[dict[str, Any]] = None
    community_context: Optional[dict[str, Any]] = None
    context_samples: Optional[dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── CommunitySamplePost ────────────────────────────────────────────────────────

class CommunitySamplePostCreate(BaseModel):
    content: dict[str, Any]
    label: str  # acceptable | unacceptable
    note: Optional[str] = None


class CommunitySamplePostRead(BaseModel):
    id: str
    community_id: str
    content: dict[str, Any]
    label: str
    note: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Rule ───────────────────────────────────────────────────────────────────────

class RuleContextTag(BaseModel):
    """A reference to one (dimension, tag) bundle from community context."""
    dimension: str  # purpose | participants | stakes | tone
    tag: str


class RuleCreate(BaseModel):
    title: str
    text: str
    priority: int = 0
    applies_to: str = "both"
    relevant_context: Optional[list[RuleContextTag]] = None
    custom_context_notes: list[CommunityContextNote] = []


class RuleUpdate(BaseModel):
    title: Optional[str] = None
    text: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    applies_to: Optional[str] = None
    relevant_context: Optional[list[RuleContextTag]] = None
    custom_context_notes: Optional[list[CommunityContextNote]] = None


class RuleRead(BaseModel):
    id: str
    community_id: str
    title: str
    text: str
    priority: int
    is_active: bool
    rule_type: str
    rule_type_reasoning: Optional[str] = None
    applies_to: str = "both"
    override_count: int = 0
    base_checklist_json: Optional[list[dict[str, Any]]] = None
    context_adjustment_summary: Optional[str] = None
    relevant_context: Optional[list[RuleContextTag]] = None
    custom_context_notes: list[CommunityContextNote] = []
    pending_checklist_json: Optional[list[dict[str, Any]]] = None
    pending_context_adjustment_summary: Optional[str] = None
    pending_relevant_context: Optional[dict[str, Any]] = None
    pending_custom_context_notes: Optional[list[CommunityContextNote]] = None
    pending_generated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @field_validator('custom_context_notes', mode='before')
    @classmethod
    def _default_custom_notes(cls, v):
        return v or []

    @field_validator('context_adjustment_summary', 'pending_context_adjustment_summary', mode='before')
    @classmethod
    def _coerce_summary(cls, v):
        # Legacy rows stored bullet lists; join into a single string for the new schema.
        if isinstance(v, list):
            return " ".join(s.strip() for s in v if isinstance(s, str) and s.strip()) or None
        return v

    model_config = {"from_attributes": True}


class RulePriorityUpdate(BaseModel):
    priority: int


class RuleTypeOverride(BaseModel):
    rule_type: str
    reasoning: Optional[str] = None


# ── ChecklistItem ──────────────────────────────────────────────────────────────

class ChecklistItemRead(BaseModel):
    id: str
    rule_id: str
    order: int
    parent_id: Optional[str] = None
    description: str
    rule_text_anchor: Optional[str] = None
    item_type: str
    logic: dict[str, Any]
    action: str
    context_influenced: bool = False
    context_note: Optional[str] = None
    context_change_types: Optional[list[str]] = None
    base_description: Optional[str] = None
    context_pinned: bool = False
    context_override_note: Optional[str] = None
    pinned_tags: Optional[list[RuleContextTag]] = None
    updated_at: datetime
    children: list["ChecklistItemRead"] = []

    model_config = {"from_attributes": True}


class ChecklistItemCreate(BaseModel):
    description: str
    item_type: str = "subjective"  # deterministic | structural | subjective
    action: str = "warn"            # remove | warn | continue
    parent_id: Optional[str] = None
    rule_text_anchor: Optional[str] = None
    logic: dict[str, Any] = {}


class ChecklistItemUpdate(BaseModel):
    description: Optional[str] = None
    rule_text_anchor: Optional[str] = None
    item_type: Optional[str] = None
    logic: Optional[dict[str, Any]] = None
    action: Optional[str] = None
    order: Optional[int] = None


# ── Example ────────────────────────────────────────────────────────────────────

class ExampleCreate(BaseModel):
    content: dict[str, Any]
    label: str  # compliant | violating | borderline
    source: str = "manual"
    moderator_reasoning: Optional[str] = None
    relevance_note: Optional[str] = None
    checklist_item_id: Optional[str] = None


class ExampleRead(BaseModel):
    id: str
    content: dict[str, Any]
    label: str
    source: str
    moderator_reasoning: Optional[str] = None
    checklist_item_id: Optional[str] = None
    checklist_item_description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExampleUpdate(BaseModel):
    content: Optional[dict[str, Any]] = None
    label: Optional[str] = None
    moderator_reasoning: Optional[str] = None


class CommunityExampleRead(BaseModel):
    id: str
    content: dict[str, Any]
    label: str
    source: str
    moderator_reasoning: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    rule_ids: list[str]    # empty = unlinked
    rule_titles: list[str]  # parallel to rule_ids

    model_config = {"from_attributes": True}


# ── Decision ───────────────────────────────────────────────────────────────────

class DecisionRead(BaseModel):
    id: str
    community_id: str
    post_content: dict[str, Any]
    post_platform_id: str
    agent_verdict: str
    agent_confidence: float
    agent_reasoning: dict[str, Any]
    triggered_rules: list[Any]
    moderator_verdict: str
    moderator_reasoning_category: Optional[str] = None
    moderator_notes: Optional[str] = None
    moderator_tag: Optional[str] = None
    was_override: bool
    created_at: datetime
    resolved_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DecisionResolve(BaseModel):
    verdict: str  # approve | warn | remove
    reasoning_category: Optional[str] = None
    # rule_doesnt_apply | edge_case_allow | rule_needs_update | agent_wrong_interpretation | agree
    notes: Optional[str] = None
    tag: Optional[str] = None
    # For unlinked removes (no rule applies): spam | off-topic | hostile_tone | low_quality | other
    rule_ids: Optional[list[str]] = None
    # Required when moderator overrides to remove/review on a post the agent approved
    # (agent had no triggered_rules to link the example to)


class BulkDecisionResolve(BaseModel):
    decision_ids: list[str]
    verdict: str  # approve | remove
    notes: Optional[str] = None
    tag: Optional[str] = None


class BulkResolveResponse(BaseModel):
    resolved_count: int
    failed_ids: list[str]


# ── PostContent ────────────────────────────────────────────────────────────────

class PostAuthor(BaseModel):
    username: str = ""
    account_age_days: Optional[int] = None
    platform_metadata: dict[str, Any] = {}


class PostContentBody(BaseModel):
    title: str = ""
    body: str = ""
    media: list[Any] = []
    links: list[str] = []


class PostContext(BaseModel):
    channel: str = ""
    thread_id: Optional[str] = None
    parent_post_id: Optional[str] = None
    post_type: Optional[str] = None
    flair: Optional[str] = None
    platform_metadata: dict[str, Any] = {}


class ThreadContextItem(BaseModel):
    """A single item in the discussion thread (OP or parent comment)."""
    role: str  # "op" | "parent_comment" | "ancestor_comment"
    author: str = ""
    content: PostContentBody = PostContentBody()
    depth: int = 0  # 0 = OP, 1 = top-level comment, 2+ = nested reply
    platform_id: Optional[str] = None


class PostContent(BaseModel):
    id: str = ""
    platform: str = ""
    author: PostAuthor = PostAuthor()
    content: PostContentBody = PostContentBody()
    context: PostContext = PostContext()
    thread_context: list[ThreadContextItem] = []
    timestamp: Optional[str] = None


# ── Evaluation ─────────────────────────────────────────────────────────────────

class EvaluateRequest(BaseModel):
    post_content: PostContent


class EvaluateResponse(BaseModel):
    decision: DecisionRead


class BatchEvaluateRequest(BaseModel):
    posts: list[PostContent]


class BatchEvaluateResponse(BaseModel):
    decisions: list[DecisionRead]


# ── Suggestion ─────────────────────────────────────────────────────────────────

class SuggestionRead(BaseModel):
    id: str
    rule_id: Optional[str] = None
    checklist_item_id: Optional[str] = None
    suggestion_type: str
    content: dict[str, Any]
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Batch Import ───────────────────────────────────────────────────────────────

class RuleBatchImportItem(BaseModel):
    title: str
    text: str
    priority: Optional[int] = None  # auto-assigned (0, 1, 2, …) if omitted


class RuleBatchImportRequest(BaseModel):
    rules: list[RuleBatchImportItem]


class RuleBatchImportResult(BaseModel):
    rule: RuleRead
    triage_error: Optional[str] = None  # set if triage API call failed


class RuleBatchImportResponse(BaseModel):
    imported: list[RuleBatchImportResult]
    total: int
    actionable_count: int
    skipped_count: int  # non-actionable (procedural/meta/informational)


class SuggestRuleFromOverridesRequest(BaseModel):
    example_ids: list[str]


class SuggestRuleFromDecisionsRequest(BaseModel):
    decision_ids: list[str]


# ── Alignment ──────────────────────────────────────────────────────────────────

class DecisionStats(BaseModel):
    total_decisions: int
    pending_decisions: int
    resolved_decisions: int
    override_rate: float
    verdicts_breakdown: dict[str, int]
    override_categories: dict[str, int]


# Allow forward references in ChecklistItemRead
ChecklistItemRead.model_rebuild()


# ── Reddit Import ───────────────────────────────────────────────────────────────

class RedditImportRequest(BaseModel):
    subreddit: str
    limit: int = 25
    sort: str = "new"
    time_filter: str = "month"
    include_comments: bool = True
    comments_limit: int = 25

    @field_validator("sort")
    @classmethod
    def validate_sort(cls, v: str) -> str:
        valid = {"new", "top"}
        if v not in valid:
            raise ValueError(f"sort must be one of {valid}")
        return v

    @field_validator("time_filter")
    @classmethod
    def validate_time_filter(cls, v: str) -> str:
        valid = {"hour", "day", "week", "month", "year", "all"}
        if v not in valid:
            raise ValueError(f"time_filter must be one of {valid}")
        return v

    @field_validator("limit", "comments_limit")
    @classmethod
    def validate_limit(cls, v: int) -> int:
        if not (0 <= v <= 100):
            raise ValueError("limit must be between 0 and 100")
        return v


class RedditImportResponse(BaseModel):
    decisions: list[DecisionRead]
    crawled_count: int
    evaluated_count: int
    skipped_count: int


# ── Rule-text suggestion (grounded in context + peers) ─────────────────────────

class SuggestRuleTextRequest(BaseModel):
    title: str
    scope: Optional[str] = "both"  # post | comment | both


class RuleTextCitation(BaseModel):
    """Grounding citation for a single clause.

    kind=context: cites a {dimension, tag} note from the target community.
    kind=peer_rule: cites a peer-community rule with a shared context tag.
    """
    kind: str  # "context" | "peer_rule"
    # context-kind fields
    dimension: Optional[str] = None
    tag: Optional[str] = None
    note_text: Optional[str] = None
    # peer_rule-kind fields
    community_name: Optional[str] = None
    rule_title: Optional[str] = None
    rule_text: Optional[str] = None
    shared_tag: Optional[str] = None


class RuleTextClause(BaseModel):
    text: str
    citations: list[RuleTextCitation]


class SuggestedContextBundle(BaseModel):
    dimension: str
    tag: str


class SuggestRuleTextResponse(BaseModel):
    draft_text: str
    clauses: list[RuleTextClause]
    suggested_relevant_context: list[SuggestedContextBundle]
    peer_rules_considered: int
    target_has_context: bool
