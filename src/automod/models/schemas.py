from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel


# ── Community ──────────────────────────────────────────────────────────────────

class CommunityCreate(BaseModel):
    name: str
    platform: str  # reddit | chatroom | forum
    platform_config: Optional[dict[str, Any]] = None


class CommunityRead(BaseModel):
    id: str
    name: str
    platform: str
    platform_config: Optional[dict[str, Any]] = None
    atmosphere: Optional[dict[str, Any]] = None
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

class RuleCreate(BaseModel):
    title: str
    text: str
    priority: int = 0


class RuleUpdate(BaseModel):
    title: Optional[str] = None
    text: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


class RuleRead(BaseModel):
    id: str
    community_id: str
    title: str
    text: str
    priority: int
    is_active: bool
    rule_type: str
    rule_type_reasoning: Optional[str] = None
    override_count: int = 0
    created_at: datetime
    updated_at: datetime

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
    atmosphere_influenced: bool = False
    atmosphere_note: Optional[str] = None
    updated_at: datetime
    children: list["ChecklistItemRead"] = []

    model_config = {"from_attributes": True}


class ChecklistItemCreate(BaseModel):
    description: str
    item_type: str = "subjective"  # deterministic | structural | subjective
    action: str = "flag"           # remove | flag | continue
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
    verdict: str  # approve | remove | review
    reasoning_category: Optional[str] = None
    # rule_doesnt_apply | edge_case_allow | rule_needs_update | agent_wrong_interpretation | agree
    notes: Optional[str] = None
    tag: Optional[str] = None
    # For unlinked removes (no rule applies): spam | off-topic | hostile_tone | low_quality | other
    rule_ids: Optional[list[str]] = None
    # Required when moderator overrides to remove/review on a post the agent approved
    # (agent had no triggered_rules to link the example to)


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


class PostContent(BaseModel):
    id: str = ""
    platform: str = ""
    author: PostAuthor = PostAuthor()
    content: PostContentBody = PostContentBody()
    context: PostContext = PostContext()
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
