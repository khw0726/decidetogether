# AutoMod Agent: Planning Document for Initial Prototype

## 1. Project Overview

Build an interactive auto-moderation agent that translates human-readable community rules into machine-executable moderation logic, with a bidirectional alignment workflow. The agent evaluates posts against compiled rule checklists and outputs decisions (APPROVE, REMOVE, FLAG) for human moderator verification.

### Core Thesis

Community rules are written for humans and are too coarse-grained for automated enforcement. This system "compiles" rules into structured checklists, then provides an interactive loop where moderators refine rules, logic, and examples — each linked bidirectionally — to align the agent with community norms.

### Design Principles

- All decisions require human verification (no autonomous moderation in v1).
- The system is a triage assistant, not an enforcer.
- Transparency: moderators should be able to inspect why the agent made any decision.
- Platform-agnostic core with thin platform adapters.

---

## 2. Architecture

### 2.1 High-Level Components

```
┌─────────────────────────────────────────────────────┐
│                    Web Frontend                      │
│  (Rule Editor, Checklist Editor, Decision Queue,     │
│   Example Manager, Alignment Dashboard)              │
└──────────────────────┬──────────────────────────────┘
                       │ REST API
┌──────────────────────▼──────────────────────────────┐
│                   Backend Server                     │
│  ┌──────────┐ ┌──────────────┐ ┌─────────────────┐  │
│  │ Rule     │ │ Evaluation   │ │ Alignment       │  │
│  │ Compiler │ │ Engine       │ │ Sync Service    │  │
│  └──────────┘ └──────────────┘ └─────────────────┘  │
│  ┌──────────┐ ┌──────────────┐ ┌─────────────────┐  │
│  │ Platform │ │ LLM Service  │ │ Rule Suggestion │  │
│  │ Adapters │ │ (Claude API) │ │ Service         │  │
│  └──────────┘ └──────────────┘ └─────────────────┘  │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                    Database                           │
│  (Rules, Checklists, Examples, Decisions, Audit Log) │
└─────────────────────────────────────────────────────┘
```

### 2.2 Tech Stack (Prototype)

- **Frontend**: React (Vite), TypeScript, Tailwind CSS
- **Backend**: Python (FastAPI)
- **Database**: SQLite (sufficient for prototype; migrate to PostgreSQL later)
- **LLM**: Claude API (claude-sonnet-4-20250514 for compilation and evaluation)
- **Platform adapters**: Reddit (via PRAW or Reddit API), Generic Chatroom (mock), Generic Forum (mock)

---

## 3. Data Model

### 3.1 Core Entities

```
Community
├── id: UUID
├── name: string
├── platform: enum (reddit | chatroom | forum)
└── platform_config: JSON (subreddit name, API keys, etc.)

Rule
├── id: UUID
├── community_id: FK → Community
├── title: string
├── text: string (the human-readable rule as written)
├── priority: integer (lower = higher priority, user-reorderable)
├── is_active: boolean
├── rule_type: enum (actionable | procedural | meta | informational)
│   Classified during compilation triage (see 4.1). Only 'actionable'
│   rules get compiled into checklists. Types:
│     - actionable: describes content standards the agent can evaluate
│       (e.g., "No self-promotion or spam")
│     - procedural: describes moderator procedures or escalation paths
│       (e.g., "Moderators may act with discretion")
│     - meta: describes rule governance or scope
│       (e.g., "Rules are subject to change without notice")
│     - informational: provides context but no enforceable standard
│       (e.g., "This community is for discussing Python programming")
├── rule_type_reasoning: string (nullable; why the system classified it
│   this way, so the user can understand and override if they disagree)
├── created_at: timestamp
└── updated_at: timestamp

ChecklistItem
├── id: UUID
├── rule_id: FK → Rule
├── order: integer (position in evaluation tree)
├── parent_id: FK → ChecklistItem (nullable, for tree structure)
├── description: string (yes/no question where YES = potential violation;
│   always framed so YES = problem detected, e.g., "Does the post advertise
│   a product or service?" not "Is the post non-promotional?")
├── rule_text_anchor: string (nullable; the exact phrase or sentence from
│   the rule text that this item derives from, e.g., "not advertise
│   products or services")
├── item_type: enum (deterministic | structural | subjective)
├── logic: JSON (see 3.2 for type-specific schemas)
├── action: enum (remove | flag | continue)
│   - What to do when this item says YES (triggered = True = violation detected)
│   - For leaf nodes: the final consequence
│   - For non-leaf nodes: the minimum consequence; children can only escalate
│   - `continue` with no triggered children resolves to `approve`
└── updated_at: timestamp

**Decision tree semantics:**
- `triggered = True` means the item's question is answered YES (violation signal detected)
- `triggered = False` means NO — item passes, children are skipped entirely
- OR logic is always used: if any item at any level is triggered, the worst action wins
- No `combine_mode` — the tree structure itself encodes specificity, not combining logic
- "No leads to further inspection" cases are rephrased as YES-violation questions with children
  (e.g., "Post discusses political topics?" YES → child: "Is political flair missing?" YES → FLAG)

Example
├── id: UUID
├── content: JSON (platform-agnostic post representation)
├── label: enum (positive | negative | borderline)
├── source: enum (manual | moderator_decision | generated)
├── moderator_reasoning: string (nullable)
├── created_at: timestamp
└── updated_at: timestamp

ExampleRuleLink (junction table for many-to-many)
├── example_id: FK → Example
├── rule_id: FK → Rule
└── relevance_note: string (nullable, why this example relates to this rule)

Decision
├── id: UUID
├── community_id: FK → Community
├── post_content: JSON (platform-agnostic post representation)
├── post_platform_id: string (original post ID on the platform)
├── agent_verdict: enum (approve | remove | flag)
├── agent_reasoning: JSON (per-rule, per-checklist-item breakdown)
├── triggered_rules: JSON (list of rule IDs that triggered)
├── moderator_verdict: enum (approve | remove | flag | pending)
├── moderator_reasoning_category: enum (
│     rule_doesnt_apply | edge_case_allow | rule_needs_update |
│     agent_wrong_interpretation | agree | null)
├── moderator_notes: string (nullable, free-text)
├── was_override: boolean (moderator disagreed with agent)
├── created_at: timestamp
└── resolved_at: timestamp (nullable)
```

### 3.2 ChecklistItem Logic Schemas

Each checklist item type has a different `logic` JSON structure:

**Deterministic:**
```json
{
  "type": "deterministic",
  "patterns": [
    {"regex": "\\b(buy|sell|discount)\\b", "case_sensitive": false}
  ],
  "match_mode": "any",
  "negate": false
}
```

**Structural:**
```json
{
  "type": "structural",
  "checks": [
    {"field": "account_age_days", "operator": "<", "value": 30},
    {"field": "post_type", "operator": "==", "value": "link"}
  ],
  "match_mode": "all"
}
```

**Subjective:**
```json
{
  "type": "subjective",
  "prompt_template": "Evaluate whether this post is self-promotional in tone...",
  "rubric": "Consider: does it primarily benefit the poster financially? ...",
  "threshold": 0.7,
  "examples_to_include": 5
}
```

### 3.3 Platform-Agnostic Post Representation

All platform adapters normalize posts to this schema:

```json
{
  "id": "platform-specific-id",
  "platform": "reddit",
  "author": {
    "username": "user123",
    "account_age_days": 45,
    "platform_metadata": {}
  },
  "content": {
    "title": "Check out my new app!",
    "body": "I built this tool that...",
    "media": [],
    "links": ["https://myapp.com"]
  },
  "context": {
    "channel": "r/programming",
    "thread_id": null,
    "parent_post_id": null,
    "post_type": "link",
    "flair": "Show & Tell",
    "platform_metadata": {}
  },
  "timestamp": "2026-03-23T10:00:00Z"
}
```

---

## 4. Core Features

### 4.1 Rule Compiler

**Input:** Human-readable rule text
**Output:** Either a classified non-actionable rule, or a tree of ChecklistItems with typed logic and auto-generated examples

**Process:**

**Step 0: Compilability Triage**
Before attempting compilation, the system classifies whether the rule is actionable — i.e., whether it describes a content standard that can be evaluated against a specific post. This is a separate, lightweight Claude API call.

1. System sends the rule text to Claude with the prompt: "Classify this community rule into one of four types: actionable (describes content standards the agent can evaluate against posts), procedural (describes moderator procedures, escalation paths, or enforcement discretion), meta (describes rule governance, scope, or applicability), informational (provides community context but no enforceable standard). Return the type and a one-sentence reasoning."
2. Claude returns `{ "rule_type": "...", "reasoning": "..." }`.
3. System stores `rule_type` and `rule_type_reasoning` on the Rule record.
4. If `rule_type != "actionable"`, compilation stops here. The UI shows the classification and reasoning, and the user can:
   - Accept the classification (rule is stored but not compiled).
   - Override to "actionable" and force compilation (for cases where the system misjudges — e.g., a rule like "Be respectful" might initially look informational but is actually an actionable standard).
   - Reclassify to a different non-actionable type.
5. Non-actionable rules are still visible in the rule list and contribute to community context (they're passed to the compiler and evaluation engine as background context, just not compiled into checklists themselves).

**Examples of triage classifications:**
- "No self-promotion or spam" → **actionable** (defines what content to remove)
- "Moderators may act with discretion" → **procedural** (describes mod authority, not a content standard)
- "Rules are subject to change without notice" → **meta** (about the rules themselves)
- "This is a community for Python developers" → **informational** (context, not a standard; though it could inform other rules' evaluation)
- "Repeated offenses will result in a permanent ban" → **procedural** (enforcement policy, not a per-post content standard)
- "Be respectful to other members" → **actionable** (vague, but still a standard posts can be evaluated against)

**Step 1: Compilation (actionable rules only)**
1. User pastes or writes a rule (e.g., "No self-promotion or spam. Posts should contribute to the community, not advertise products or services.")
2. System sends the rule text + community context + platform type to Claude API. Non-actionable rules from the same community are included as background context (so the compiler understands the community's tone and scope) but are not themselves compiled.
3. Claude returns a structured checklist tree. Prompt should instruct Claude to:
   - Break the rule into atomic yes/no questions where YES = violation detected.
   - Frame each question so that YES = problem exists (e.g., "Does this post advertise a product?" not "Is this post non-promotional?").
   - For each item, extract a `rule_text_anchor` — the specific phrase from the original rule text this item derives from (null if inferred rather than explicit).
   - Classify each as deterministic, structural, or subjective.
   - Provide regex patterns for deterministic items.
   - Specify structural field checks.
   - Write evaluation prompts/rubrics for subjective items.
   - Suggest a tree structure with combining logic.
   - Generate 3 positive and 3 negative examples.
4. System parses the response, creates ChecklistItem records, and links generated examples.

**Claude API prompt structure (pseudo):**
```
System: You are a moderation rule compiler. Given a community rule, break it
down into a checklist tree that a moderation agent can execute.

Return JSON matching this schema: { checklist_tree: ..., examples: ... }

Community context: {community_name}, platform: {platform_type}
Existing rules for context: {other_rules_summary}

Rule to compile: {rule_text}
```

### 4.2 Evaluation Engine

**Input:** A normalized post + the community's compiled rules (ordered by priority)
**Output:** A Decision with per-rule, per-item reasoning

**Process:**
1. Receive a post (from platform adapter or manual input).
2. Iterate **actionable** rules in priority order (non-actionable rules are skipped for evaluation but included as community context for subjective LLM calls).
3. For each rule, evaluate all checklist items (pre-evaluated in a single batch for subjective items), then walk the decision tree:
   - **Deterministic items:** Execute regex/pattern matching locally. `triggered=True` when a pattern matches (violation found). No LLM call.
   - **Structural items:** Check post metadata fields. `triggered=True` when condition is met (e.g., account too new). No LLM call.
   - **Subjective items:** Pre-evaluated in a single batched Claude API call (Haiku first, escalate low-confidence items to Sonnet). LLM returns `triggered: bool` (True = violation detected).
4. Walk the tree with OR logic:
   - If an item is NOT triggered (answer = NO): return approve, skip children entirely.
   - If an item IS triggered (answer = YES): apply its `action` as the minimum verdict, then evaluate children — any child can escalate but not lower the verdict.
   - Only items actually visited during the walk appear in the reasoning output.
5. Aggregate across rules to produce a final verdict:
   - If any rule's tree results in REMOVE → agent verdict = REMOVE.
   - If any rule's tree results in FLAG → agent verdict = FLAG (unless overridden by REMOVE).
   - Otherwise → APPROVE.
6. Additionally, run a "community norms" check (the FLAG-without-rule-violation case):
   - Send the post to Claude with the full community context, past decisions, and instruction to assess whether the post "feels off" relative to established norms, even if no explicit rule is violated.
   - If this fires, mark verdict as FLAG with reasoning "no rule violated, but community norms concern."
7. Store the Decision record with full per-item reasoning.

**Batching optimization:** For posts with multiple subjective items, batch them into a single Claude API call where possible to reduce latency and cost.

### 4.3 Interactive Alignment

Three editing surfaces, each triggering propagation to the others:

#### 4.3.1 Rule Text → Checklist + Examples (Recompile)

- User edits rule text → clicks "Recompile."
- System re-runs the compiler (4.1) but also passes the existing checklist and examples as context, asking Claude to produce an updated checklist that preserves user customizations where the rule intent hasn't changed.
- UI shows a diff of what changed in the checklist and examples. User can accept/reject individual changes.

#### 4.3.2 Examples → Checklist + Rule Text (Learn from Examples)

- User adds or edits an example and labels it (positive/negative/borderline).
- System sends the updated example set + current checklist + current rule text to Claude.
- Claude returns suggested modifications to the checklist logic (e.g., adjust regex, broaden rubric, change threshold) and optionally suggests edits to the rule text.
- **These are surfaced as suggestions, not auto-applied.** User sees "Suggested changes based on new example" and can accept/reject each.

#### 4.3.3 Checklist → Examples + Rule Text (Logic Changed)

- User directly edits a checklist item (changes regex, modifies rubric, restructures tree).
- System generates new examples that test the updated logic (especially edge cases near the new boundary).
- System optionally suggests rule text updates if the checklist has diverged significantly from the original rule.
- Again, surfaced as suggestions.

#### 4.3.4 Moderator Decisions → Examples (Feedback Loop)

- When a moderator resolves a decision (confirms or overrides), the system:
  1. Stores the decision with reasoning category.
  2. If it was an override, prompts the moderator for reasoning (dropdown + optional notes).
  3. Automatically adds the post as an example linked to the relevant rules, labeled based on the moderator's verdict.
  4. If enough overrides accumulate on a particular rule (e.g., 3+ overrides in same direction), surfaces a notification: "Moderators frequently override this rule. Consider reviewing the checklist or rule text."
  5. For FLAG decisions that moderators consistently confirm, the rule suggestion service (4.5) considers promoting them to draft rules.

### 4.4 Decision Queue (Moderator Interface)

The primary moderator-facing UI:

- Shows pending decisions in a queue, sorted by agent confidence (lowest confidence first, so the hardest calls get human attention first).
- Each decision shows:
  - The post content (rendered appropriately for the platform).
  - The agent's verdict with confidence.
  - Expandable per-rule breakdown: which rules triggered, which checklist items fired, what the reasoning was for subjective items.
  - Quick-action buttons: APPROVE / REMOVE / FLAG.
  - Reasoning dropdown (appears on action): rule_doesnt_apply / edge_case_allow / rule_needs_update / agent_wrong_interpretation / agree.
  - Optional notes field.
- Bulk actions for obvious cases (e.g., "approve all APPROVE verdicts with confidence > 0.9").

### 4.5 Rule Suggestion Service

A background process that analyzes patterns in FLAG decisions and moderator overrides:

- Periodically (or on-demand) reviews:
  - Posts flagged without rule violations (the "community norms" flags).
  - Posts where moderators consistently override the agent.
  - Clusters of similar content that get similar moderator treatment.
- Uses Claude to synthesize patterns and draft new rule text.
- Presents drafts to moderators as suggestions, not auto-created rules.
- Moderators can accept (sends to compiler), edit, or dismiss.

---

## 5. Platform Adapters

### 5.1 Adapter Interface

Each platform adapter implements:

```python
class PlatformAdapter(ABC):
    @abstractmethod
    async def fetch_new_posts(self) -> list[NormalizedPost]:
        """Poll for new posts since last check."""
        pass

    @abstractmethod
    async def get_user_profile(self, username: str) -> UserProfile:
        """Fetch user metadata (account age, karma, etc.)."""
        pass

    @abstractmethod
    async def execute_action(self, post_id: str, action: Action) -> bool:
        """Execute a moderation action on the platform (v2, not needed yet)."""
        pass

    @abstractmethod
    def normalize_post(self, raw_post: dict) -> NormalizedPost:
        """Convert platform-specific post format to normalized schema."""
        pass
```

### 5.2 Platform: Reddit

- Uses Reddit API (or PRAW) to poll a subreddit for new posts/comments.
- Normalizes: title, body (selftext or link), author profile (account age, karma, subreddit karma), flair, post type, awards.
- Structural fields available: account_age_days, karma, subreddit_karma, post_type (link/self/crosspost), flair, is_oc.

### 5.3 Platform: Generic Chatroom

- Minimal adapter for testing: ingests messages as (username, content, timestamp).
- Structural fields available: account_age_days, message_count (if tracked).
- Can be fed from a JSON file, a websocket mock, or manual input in the UI.

### 5.4 Platform: Generic Forum

- Extends chatroom with: post title, post category/subforum, thread context (is this a reply?), user trust level.
- Can be fed from JSON or manual input.

---

## 6. API Endpoints

### Rules
- `POST /api/communities/{id}/rules` — Create a rule + trigger triage classification, then compilation if actionable.
- `GET /api/communities/{id}/rules` — List rules (ordered by priority; includes rule_type).
- `PUT /api/rules/{id}` — Update rule text (triggers re-triage and recompile suggestion).
- `PUT /api/rules/{id}/priority` — Update rule priority.
- `PUT /api/rules/{id}/rule-type` — Override the system's triage classification (e.g., force a rule to "actionable" to trigger compilation, or reclassify an incorrectly-triaged rule).
- `DELETE /api/rules/{id}` — Deactivate a rule.

### Checklist
- `GET /api/rules/{id}/checklist` — Get the checklist tree for a rule.
- `PUT /api/checklist-items/{id}` — Edit a checklist item directly.
- `POST /api/rules/{id}/recompile` — Recompile rule into checklist (returns diff).
- `POST /api/rules/{id}/recompile/accept` — Accept recompile diff.

### Examples
- `POST /api/rules/{id}/examples` — Add an example (triggers suggestion generation).
- `PUT /api/examples/{id}` — Edit an example.
- `DELETE /api/examples/{id}` — Remove an example.
- `GET /api/rules/{id}/examples` — List examples for a rule.

### Alignment
- `POST /api/rules/{id}/suggest-from-examples` — Generate checklist/rule suggestions from current examples.
- `POST /api/rules/{id}/suggest-from-checklist` — Generate example/rule suggestions from current checklist.
- `GET /api/rules/{id}/suggestions` — List pending suggestions.
- `POST /api/suggestions/{id}/accept` — Accept a suggestion.
- `POST /api/suggestions/{id}/dismiss` — Dismiss a suggestion.

### Evaluation
- `POST /api/communities/{id}/evaluate` — Evaluate a single post against all rules.
- `POST /api/communities/{id}/evaluate/batch` — Evaluate multiple posts.

### Decisions
- `GET /api/communities/{id}/decisions?status=pending` — Get decision queue.
- `PUT /api/decisions/{id}/resolve` — Moderator resolves a decision (verdict + reasoning).
- `GET /api/communities/{id}/decisions/stats` — Alignment stats (override rate, common overrides, etc.).

### Rule Suggestions
- `POST /api/communities/{id}/suggest-rules` — Trigger rule suggestion analysis.
- `GET /api/communities/{id}/rule-suggestions` — List suggested rules.

---

## 7. Frontend Pages

### 7.1 Community Dashboard
- Overview: active rules count, pending decisions, override rate, recent activity.
- Quick links to decision queue and rule editor.

### 7.2 Rule Editor
- Left panel: list of rules, drag-to-reorder priority.
- Center panel: selected rule's text editor (markdown-capable).
- Right panel: compiled checklist tree (collapsible, editable inline).
- Below: examples panel (positive/negative/borderline, add/edit/remove).
- Action buttons: Recompile, Suggest from Examples, Suggest from Checklist.
- Suggestions appear as a diff overlay that can be accepted or dismissed per-item.

### 7.3 Decision Queue
- Card-based queue of pending decisions.
- Expandable reasoning for each decision.
- Quick-action buttons with reasoning dropdown.
- Filters: by verdict, by rule, by confidence, by date.
- Bulk actions.

### 7.4 Alignment Dashboard
- Override rate over time (chart).
- Most-overridden rules (table).
- Agent accuracy by rule (table).
- Suggested rules from the suggestion service.
- Example coverage: which rules have few examples, which have many.

---

## 8. Implementation Plan (Prototype Phases)

### Phase 1: Core Data Model + Rule Compiler
**Goal:** User can create a community, add a rule, and see it compiled into a checklist.

- Set up FastAPI project, SQLite database, and migrations.
- Implement data models (Community, Rule, ChecklistItem, Example, ExampleRuleLink).
- Build the rule compiler service (Claude API integration).
- Build basic API endpoints for CRUD on rules and viewing checklists.
- Simple React frontend: community creation, rule creation, checklist viewer.
- **Test with:** 3-5 real subreddit rule texts compiled into checklists. Manually inspect quality.

### Phase 2: Evaluation Engine + Decision Queue
**Goal:** User can submit a post and get an agent decision with reasoning, and resolve it.

- Implement the evaluation engine (deterministic, structural, subjective item evaluation).
- Build the decision queue API and moderator resolution flow.
- Implement the moderator reasoning capture (dropdown + notes).
- Auto-convert moderator decisions to examples.
- Frontend: decision queue page, post evaluation form (manual post input for testing).
- **Test with:** Manually input 20 posts against compiled rules. Check decision quality.

### Phase 3: Interactive Alignment
**Goal:** Bidirectional sync between rules, checklists, and examples.

- Implement recompile-with-diff (rule text → checklist).
- Implement suggest-from-examples (examples → checklist + rule text suggestions).
- Implement suggest-from-checklist (checklist → examples + rule text suggestions).
- Build suggestion UI with accept/dismiss per-item.
- Frontend: full rule editor with three-panel layout and suggestion overlays.
- **Test with:** Modify rules and examples, verify suggestions are coherent and useful.

### Phase 4: Platform Integration + Rule Suggestions
**Goal:** Connect to Reddit and run the full loop.

- Build the Reddit platform adapter.
- Build the generic chatroom adapter (for demos/testing).
- Implement polling and automatic evaluation of new posts.
- Build the rule suggestion service (background analysis of flags and overrides).
- Frontend: alignment dashboard, rule suggestion review.
- **Test with:** Connect to a real (or test) subreddit. Run for a week. Measure override rate.

---

## 9. Key Design Decisions and Risks

### Decision: Suggestions, Not Auto-Updates
All bidirectional alignment propagation is surfaced as suggestions. This avoids the "afraid to touch anything" problem and keeps moderators in control. The tradeoff is more clicks, but trust is worth more than convenience in v1.

### Decision: Rule Text Anchor on Checklist Items
Every checklist item carries a `rule_text_anchor` (which phrase in the rule it derives from, or null if inferred). This serves two purposes:
1. **Transparency:** Moderators can understand why the agent checks what it checks.
2. **Recompile stability:** When rule text is edited and recompiled, the compiler uses existing anchors to match old checklist items to their new counterparts, preserving user customizations on items whose anchoring text hasn't changed. Items whose anchor text was deleted or substantially rewritten get flagged for review.

### Decision: Decision Tree with OR Logic and YES=Violation Framing
Checklist items form a decision tree where every item is a yes/no question framed so YES = violation signal. OR logic is always used — if any triggered item leads to REMOVE/FLAG, that wins. There is no `combine_mode`; the tree structure itself encodes specificity. Children are only evaluated when their parent is triggered, so a NO answer prunes the entire subtree. This keeps the model simple and consistent: the compiler always frames questions as "does this violation exist?" Default trees should be shallow (2 levels max) to keep the UI manageable.

### Decision: Community Norms Layer Separate from Rules
The FLAG-without-rule-violation case uses a separate evaluation path. This keeps the rule-based system clean and deterministic while still catching emerging issues.

### Risk: LLM Cost at Scale
Every subjective checklist item requires an LLM call. For high-traffic communities, this could become expensive. Mitigations: batch subjective items into single calls, cache similar evaluations, use cheaper models for obvious cases and reserve expensive models for borderline ones.

### Risk: Compiler Quality Variance
Rule compilation quality will vary with rule complexity and ambiguity. Intentionally vague rules ("be respectful") will produce noisy checklists. Mitigation: the alignment loop is designed exactly for this — the initial compilation is a starting point, not a final product.

### Risk: Moderator Fatigue
If the agent has low accuracy initially, moderators will be overwhelmed with overrides and lose trust. Mitigation: start with high-confidence-only decisions in the queue, gradually lower the threshold as alignment improves. Provide bulk actions for obvious cases.

---

## 10. Claude API Usage Notes

### Models
- **Compilation and suggestions:** claude-sonnet-4.6 (structured output, reliability matters).
- **Subjective evaluation:** claude-sonnet-4.6 (same; could downgrade to haiku for obvious cases in v2).

### Prompt Engineering
- All Claude calls should use structured output (JSON mode) with explicit schemas.
- Compilation prompts should include 2-3 few-shot examples of well-compiled rules.
- Evaluation prompts should include the post, the rubric, and the most relevant examples (selected by recency and similarity).
- All prompts should include the community name and a brief community description for context.

### Rate Limiting
- Prototype: expect low volume. No special handling needed.
- Production: implement a queue for evaluation requests, with priority for moderator-initiated evaluations over automated polling.
