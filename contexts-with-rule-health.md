# Community Contexts on the Rule Health Page — Design

## Context

Two surfaces in automod_agent_v2 both calibrate rule logic but from different angles:

- **Rule health / alignment** (rule-local): per-rule dashboard of decisions, FP/FN rates, error cases, and per-item fixes (`tighten_rubric`, `adjust_threshold`, `split_item`, `promote_to_deterministic`, `add_item`). Driven by disagreement between the rule's predictions and moderator overrides.
- **Community contexts** (community-global): four dimensions (purpose, participants, stakes, tone) encoded as tagged notes, consumed by Pass 2 of compilation to adjust thresholds and rubric language across *all* rules whose `relevant_context` includes the tag. Each context-adjusted item carries a `context_note` tracing "[situational fact] → [calibration decision]".

Today these surfaces are disconnected. The rule health page only proposes rule-local fixes. Contexts can be edited from `CommunitySettings` with an outbound "Preview Impact" onto rules, but there's no inbound path from rule health *back* into context. When a moderator is looking at decisions and calibrating, the system gives them one shaped hammer.

This plan designs the bridge under a **strict separation** model: every fix belongs to exactly one surface — the rule health page or the context editor — and the system's job is to nudge moderators to the right one.

---

## Scope model: strict separation

| | Rule calibration (rule health) | Community context |
|---|---|---|
| **Answers** | "Is this rule's *logic* right?" | "Does this rule know *where it is*?" |
| **Unit of fix** | One checklist item | One situational fact shared across rules |
| **Evidence trigger** | Errors cluster on one item | Errors cluster on a *theme* across items or rules |
| **Blast radius** | This rule only | Every rule whose `relevant_context` includes the tag |
| **Ownership** | Rule author | Community-level fact |

**Rule of thumb.** If the fix removes a bad trigger, it's a rule fix. If the fix changes *why the rule would trigger differently here*, it's a context fix. A regex catching URLs it shouldn't → rule. A rule correctly catching "low-effort posts" but mods keep approving beginners → context.

**Consequence for UI.** Do not add a "context note" action alongside rule-local actions on the health panel. Instead, when the system detects a context-shaped cluster, it visibly *redirects* the moderator — a banner saying "this looks systemic, fix it upstream" with a deep-link into the context editor. Keeping the actions on separate surfaces preserves the blast-radius signal.

---

## Scenarios we're designing for

The three patterns confirmed as active pain today:

### S1 — Whack-a-mole across rules
Multiple rules over-flag the same kind of post (e.g., new accounts, beginner posts). Individual rule fixes are brittle; the real fix is a shared `participants` or `purpose` note.

### S3 — Tone/rubric mismatch
A rule's logic is correct but the LLM rubric language doesn't match community register (e.g., roasting is affectionate). Fix lives in `tone`, not in the rule.

### S5/S6 — Drift or imported defaults
Override rate creeps up across many rules over months, or rules imported from templates / other communities are miscalibrated at every threshold. Fix is a context regeneration (with `manually_edited: true` preserved) or populating `relevant_context` for templates that lack it.

Stakes/participant-ambiguity scenarios are plausible but are not driving the design.

---

## Interactions

Three interactions, prioritized. Each one is a **redirect** from a rule-local surface toward context, not a merging of controls.

### I1 — Systemic-cluster banner on `RuleHealthPanel` (serves S1, S3)

When a rule's error cases on a single item share a detectable theme — author age, expert/newcomer asymmetry, register/tone signals, crisis keywords, flair — render a banner *above* the rule-local action list:

> ⚠ This looks like a **community-context** issue, not a rule-logic issue.
> 5 of 7 false positives on this item involve posts from accounts under 30 days old.
> Proposed context note: `participants: newcomers welcomed, low bar for basic questions`
> Would affect: 3 rules. [Inspect impact] [Open in context editor]

Clicking the action deep-links into `ContextDimensionsView` with the candidate `{dimension, tag, note}` pre-filled as a pending edit. The moderator reviews, runs the existing impact preview, and commits — or dismisses.

Critically, the rule-local actions below the banner remain available. The system doesn't hide them; it says "consider this upstream fix first." If the moderator disagrees, they can still tighten the rubric locally.

**Detection.** A lightweight clustering pass on `error_cases` (cheap metadata grouping on author age, flair, post type, length) followed by a single Claude call to classify the cluster as *rule-local* or *context-systemic* and propose a `{dimension, tag, note}` candidate when systemic. Pre-cluster first, call the LLM only when the cluster is big enough to matter.

### I2 — Cross-rule pattern card on `AlignmentDashboard` (serves S1)

A new card aggregates error signals across rules and surfaces candidate *context* fixes:

> **Across 3 rules, posts from new accounts are being over-flagged.**
> Rules: low-effort posts (42% override), off-topic (31%), rule-breaking titles (28%).
> Suggested context: `participants: newcomers welcomed`
> [Inspect impact] [Draft context note]

This is the surface that rule-by-rule health pages can't show. Whereas I1 catches systemic issues that *also* show inside one rule, I2 catches issues that only become visible when you aggregate.

### I3 — Drift indicator on `CommunitySettings` (serves S5/S6)

Aggregate override-rate trend on the context page:

> Override rate has risen 8% across 12 rules in the last 60 days.
> The community context was last generated 14 months ago.
> [Regenerate from recent posts] (preserves manually-edited dimensions)

For imported-default cases (S6), add a one-time banner on rules whose `relevant_context` is `None` (distinct from `[]` which means explicitly disabled): "This rule doesn't yet consume community context. Populate `relevant_context` to let the community calibrate it."

### Deliberately NOT doing

- No "add context note" button on the rule-local action list. That would collapse the scope distinction.
- No auto-apply of context suggestions. Stays consistent with the "moderator trust over convenience" design principle.
- No classifier on every health-page render. Opt-in on large enough clusters, or moderator-triggered via a "diagnose" button, to keep LLM cost bounded.

---

## Implementation sketch

Priority order and rough scope. First slice is I1 + the backend primitive it shares with I2.

### Slice 1 — I1 (systemic-cluster banner on rule health)

**Backend**
- `src/automod/api/health.py`: new endpoint `POST /rules/{rule_id}/diagnose-context` — takes the current error cases on an item, runs (a) cheap metadata clustering on author age / flair / length / post type, (b) if any cluster ≥ 3 cases, a Claude classification call, (c) returns `{classification: "rule_local" | "context_systemic", candidate: {dimension, tag, note} | null, affected_rule_ids: [...]}`.
- `src/automod/compiler/prompts.py`: new `DIAGNOSE_CONTEXT_SYSTEM` prompt — shown the error cluster, the community's current context, and the taxonomy; asked to decide rule-local vs systemic and propose a candidate when systemic.
- Reuse the existing context impact-preview from `src/automod/api/communities.py` (the one `ContextDimensionsView` already calls for "Preview Impact") to compute `affected_rule_ids` without duplicating logic.

**Frontend**
- `admin/src/components/RuleHealthPanel.tsx`: render the banner when `diagnose-context` returns `context_systemic`. Trigger: auto-call on health load when any item has ≥ 3 error cases; otherwise show a "Diagnose" button.
- `admin/src/components/ContextDimensionsView.tsx`: accept a query-state `?draft_note={dimension, tag, text, source_rule_id}` so the banner can deep-link with a pre-filled candidate pending the moderator's approve/dismiss.
- Small "context-adjusted" badge on items where `context_influenced: true`, with the `context_note` on hover. Helps moderators recognize when they're looking at a calibration that already traces upstream.

### Slice 2 — I2 (cross-rule pattern card)

- `src/automod/api/alignment.py` (or a new `src/automod/api/patterns.py`): `GET /communities/{community_id}/cross-rule-patterns` — scans recent decisions across rules, re-uses the same clustering + diagnosis primitives from Slice 1, aggregates by candidate context tag, returns ranked pattern cards.
- `admin/src/pages/AlignmentDashboard.tsx`: render cards above the existing "most overridden" table.

### Slice 3 — I3 (drift indicator + imported-default banner)

- `src/automod/api/communities.py`: add a simple override-rate-trend computation (60-day window vs. prior window). Expose on the context GET response.
- `admin/src/pages/CommunitySettings.tsx`: render the drift banner.
- `admin/src/pages/RuleEditor.tsx`: show the "populate relevant_context" banner when `rule.relevant_context is None`.

### Critical files to modify

- `src/automod/api/health.py` — new diagnose endpoint
- `src/automod/compiler/prompts.py` — new DIAGNOSE_CONTEXT prompt
- `src/automod/api/communities.py` — expose impact preview primitive + drift trend
- `admin/src/components/RuleHealthPanel.tsx` — systemic banner, context-adjusted badges
- `admin/src/components/ContextDimensionsView.tsx` — accept pre-filled draft note
- `admin/src/pages/AlignmentDashboard.tsx` — cross-rule pattern card (Slice 2)

### Verification

- **S1 regression test:** seed a community with three rules that over-flag posts from new accounts; load each rule's health panel; confirm the systemic banner appears on each and the cross-rule card appears on the alignment dashboard.
- **S3 regression test:** seed a community where a harassment rule flags banter the mods approve; confirm the diagnosis proposes a `tone` note.
- **Negative case:** a single rule with a genuinely rule-local bug (regex too broad, no shared theme across errors) — confirm the banner does *not* appear and rule-local actions remain primary.
- **Deep-link:** clicking "Open in context editor" from the banner lands on `ContextDimensionsView` with the draft note pre-filled and editable.
- **Drift (S5):** simulate 60 days of rising override rates; confirm the drift indicator appears on `CommunitySettings`.
- **Imported default (S6):** create a rule with `relevant_context=None`; confirm the editor banner appears; populating `relevant_context` and recompiling clears it.

### Scenarios (For reference)

S1 — Whack-a-mole across rules (prioritized)

Mod opens rule health for Rule A (low-effort posts) and sees 40% override rate. Alignment dashboard shows Rules B
(rule-breaking titles) and C (off-topic) spiking too. All three trace to overrides on beginner-introduction posts.
Fixing each rule's rubric individually is brittle — next month a new rule inherits the same blindness. Right lever:
add participants: newcomers welcomed / encouraged to post basics to community context. Pass 2 softens thresholds
across all three at once.

S2 — Missing stakes raising thresholds too high (not prioritized)

Rule on medical-advice misinformation has high false negatives. Error cases all involve subtle, plausible-sounding
drug-interaction claims. The rule's logic is fine; it's calibrated for a general community. Fix: add stakes:
medication interactions have high permanence / vulnerable readers → Pass 2 tightens confidence thresholds on this
and sibling rules.

S3 — Tone/rubric mismatch (prioritized)

Harassment rule flags a lot of roast-style banter. Mods consistently approve. The rule text is correct; the
subjective rubric language carries general-purpose assumptions. Fix: tone: roasting is affectionate, high in-group
humor tolerance → rubric examples regenerate with the community's register.

S4 — Borderline decisions without errors (not prioritized)

Rule X isn't being overridden, but throws a high fraction of BORDERLINE verdicts (Haiku escalates to Sonnet a lot).
Pattern: the ambiguity is always about who the author is, not what the post says. Fix: participants: experts and
newcomers coexist → sharper rubric that checks expertise signals before pattern-matching post content.

S5 — Community drift (prioritized)

Override rate creeps up across many rules over months. Nothing on any one rule explains it. The context was
generated a year ago; the community has shifted. Health surface should show this as an aggregate signal and prompt a
  context regeneration from recent posts, preserving manually_edited: true dimensions.

S6 — Correct rule, wrong community (prioritized)

A rule imported from another community (or a default template) looks logically sound but is miscalibrated at every
threshold. Individual rule fixes would be enormous churn. Fix: ensure relevant_context is populated, so Pass 2 can
do the adjustment in one move.

Shared shape. In all six, the error pattern isn't "this rule is wrong" — it's "this rule doesn't know something
about the community that many rules need to know." S2 and S4 are the same idea, just on the stakes/participants
dimensions rather than purpose/tone — we dropped them from the design-for list because you said they're not active
pain, but they'd fall out for free once the I1 detector can cluster on those dimensions too.