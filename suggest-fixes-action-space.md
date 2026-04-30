# Suggest Fixes from Errors — Expanded Action Space

## Context

`POST /rules/{rule_id}/analyze-health` (`src/automod/api/health.py:397`) currently turns moderator-override patterns into *checklist-only* suggestions via the `DIAGNOSE_HEALTH_SYSTEM` prompt (`src/automod/compiler/prompts.py:1159`). The five fix types — `tighten_rubric`, `adjust_threshold`, `promote_to_deterministic`, `split_item`, `add_item` — all live below the rule text and below the rule's context calibration.

But moderator notes routinely surface causes that sit *upstream* of the checklist:
- "this is satire — we allow it here" → community-context calibration drift, not a logic bug
- "the rule never said anything about X" → the rule text itself has a gap
- a single ambiguous phrase in rule text producing two confused items → rewriting the phrase fixes both

We need (a) a wider action space — rule text and rule-context updates as first-class outputs of error analysis — and (b) a clear rubric for *where the fix belongs*, since the same FP cluster could plausibly justify edits at any of three levels.

## Three-level action space

| Level | Target | Mechanism on accept |
|---|---|---|
| **L1 logic** *(exists)* | `ChecklistItem.logic` / `description` / `item_type` / structural splits | Existing recompile-diff apply |
| **L2 context** *(new)* | `Rule.custom_context_notes`, `Rule.relevant_context` | Background `adjust_for_context` (`compiler.py:706`); checklist is silently re-derived |
| **L3 rule text** *(new from this path)* | `Rule.text` | Background `recompile_with_diff` (`compiler.py:1034`); resulting checklist applied silently (per user choice) |

`Suggestion` already supports arbitrary `content` JSON and arbitrary `suggestion_type` strings (`db/models.py:216`); `accept_suggestion` already has a `rule_text` branch (`alignment.py:76`). We add a new `"context"` type and extend the `rule_text` branch to also recompile.

## Decision rubric — which level owns this error cluster

The default workflow is **L1↔L3 sync**: the rule text is the user-facing source of truth, and most logic fixes have a paired text clarification (the fluid editor makes text edits the primary iteration surface). **L2 is a narrow side-channel**, not a peer bucket — it fires only on two explicit signals.

### Step 1 — Check L2 triggers (rare path)

Emit an L2 (context) suggestion **only** if at least one of:

- **Against existing context.** The moderator note explicitly invokes or contradicts a community-context tag/note — the disagreement is *about* the calibration, not about what the rule says. Detection: note overlaps with a `Community.community_context` note's text, or names a context dimension/tag.
- **Cross-rule applicability.** The proposed calibration plausibly applies to ≥ 2 rules in the community (lexical scan over sibling rules — see "Cross-rule context detection" below). A calibration that benefits multiple rules belongs in shared context, not in any single rule's text.

L2 may be emitted **alongside** an L1 logic fix (calibrate the context *and* the threshold), but it does not replace L1↔L3 sync — it lives on its own track.

### Step 2 — L1↔L3 sync (default path)

Pick the emission shape from the L1 action type:

| L1 action | Default emission |
|---|---|
| `tighten_rubric` | **Paired L1+L3** — vague rubric usually traces to a vague phrase in the rule text. |
| `split_item` | **Paired L1+L3** — splitting items without distinguishing the conflated concepts in the rule text leaves the next compile vulnerable to re-merging them. |
| `add_item` | **L3 by default** — uncovered violations almost always indicate a text gap. Fall back to L1-only only if the new item makes implicit text explicit. |
| `adjust_threshold` | **L1 only** — pure calibration knob; rule text doesn't encode strictness. |
| `promote_to_deterministic` | **L1 only** — representation change; concept is unchanged. |

**Tie-breakers:**
- Prefer L1-only if `decision_count < 5` — insufficient evidence to mutate the rule text.
- For paired L1+L3, the moderator can accept either independently; UI warns when accepting L1-only that text/logic will drift.

## How "best level" is determined (answering the user's question)

The diagnoser emits per error cluster:

```
proposed_levels:   list, any combination of "logic" | "rule_text" | "context"
level_reasoning:   short text — which action type, and (if "context") which L2 trigger fired
```

Typical shapes:
- `["logic", "rule_text"]` — paired sync for `tighten_rubric` / `split_item`.
- `["rule_text"]` — `add_item` covering a text gap; or any case where ≥2 items trace to one ambiguous phrase.
- `["logic"]` — `adjust_threshold`, `promote_to_deterministic`, or paired-default actions with `decision_count < 5`.
- `["context"]` or `["context", "logic"]` — only when an L2 trigger fires.

We drop the explicit confidence score and `alternative` payload from earlier revisions — the rubric is now small enough that ambiguity is rare, and a paired emission already gives the moderator the choice of depth.

## Cross-rule context detection (per user question)

When emitting an L2 suggestion that proposes a new/edited context note, the system scans sibling rules in the same community for likely co-applicability:

1. Compute lexical signals from the proposed note: keyword set, plus tokens of any `rule_text_anchor`s the cluster cited.
2. For each sibling rule, score:
   - overlap with sibling's `Rule.text` keywords,
   - overlap with sibling's existing `relevant_context` tag set (does this calibration touch a tag the sibling already opted into?),
   - presence of similar moderator-note language in sibling's recent override clusters (cheap: keyword count over the last N decisions).
3. Attach `affects_rules: [{rule_id, score, signals: [...]}]` to the suggestion content.
4. UI: a "may also apply to" section with a checkbox per sibling. Accept applies the note only to checked rules; each ticked sibling triggers its own `adjust_for_context` run.

Phase 1 uses lexical overlap only (no embeddings) — embeddings are a Phase 2 upgrade if precision is poor.

## Cascade behavior on accept

- **L1 accept** — unchanged (`alignment.py` recompile-diff path).
- **L2 accept** — write `custom_context_notes` / `relevant_context` on the source rule; background task per affected rule runs `adjust_for_context`; new checklist applied silently. Health metrics on materially-changed items are reset.
- **L3 accept** — write `Rule.text`; background task runs `recompile_with_diff`; resulting checklist applied silently (user chose this over a confirmation modal). Health metrics reset for items whose `description` / `logic` / `rule_text_anchor` changed.

## Implementation plan

1. **Prompt** (`src/automod/compiler/prompts.py:1159`)
   - Extend `DIAGNOSE_HEALTH_SYSTEM` with the L2 trigger check (Step 1) and the action-type emission table (Step 2). Embed the rubric verbatim.
   - Output schema additions per diagnosis: `proposed_levels: list[str]`, `level_reasoning: str`, plus a `text_change: {proposed_text, rationale}` and optional `context_change: {proposed_note: {text, tag}, l2_trigger: "against_existing_context" | "cross_rule"}` payload alongside the existing `proposed_change`/`proposed_item`.

2. **Tool schema** (`src/automod/compiler/compiler.py` `_DIAGNOSE_TOOL`)
   - Extend the union to include the three payload variants and the level-meta fields.

3. **Endpoint** (`src/automod/api/health.py:397` `analyze_rule_health`)
   - Iterate `proposed_levels` and emit one `Suggestion` per level, sharing a `linked_suggestion_id` when the diagnosis is paired L1+L3:
     - `logic` → existing checklist `Suggestion` path.
     - `context` → new `suggestion_type="context"` with `affects_rules` populated by a new helper.
     - `rule_text` → `suggestion_type="rule_text"` (already accepted by `alignment.py:76`).
   - For paired L1+L3, the L3 suggestion's `content` includes `supersedes_logic_suggestion_id`. Accepting the L3 marks the linked L1 as `superseded` (since the silent recompile re-derives the logic fix). Accepting only the L1 leaves the L3 pending and surfaces a "text/logic drift" warning in the UI.

4. **Cross-rule helper** (new in `src/automod/api/health.py` or `compiler.py`)
   - `find_related_rules_for_context_note(community_id, proposed_note, source_rule_id) -> list[{rule_id, score, signals}]`
   - Phase 1 implementation: lexical overlap over `Rule.text`, `Rule.custom_context_notes`, recent override notes per sibling.

5. **Acceptance** (`src/automod/api/alignment.py:56` `accept_suggestion`)
   - New `suggestion_type == "context"` branch: write notes/tags on source rule; for each opted-in `affects_rules` entry, write the same delta and schedule `adjust_for_context` as a background task.
   - Extend `rule_text` branch (already exists at `alignment.py:76`): after writing `rule.text`, schedule `recompile_with_diff` and apply silently.

6. **Frontend** (`admin/src/components/SuggestionDiff.tsx`)
   - Render `"context"` suggestion type: proposed note text/tag, per-affected-rule checkboxes from `affects_rules`.
   - Extend `"rule_text"` rendering with `motivating_clusters` (which error cases drove this).
   - When `linked_suggestion_id` is present on an L1 suggestion, show a "paired text update available" pointer; when accepting L1 alone, show a drift warning.

## Critical files

- `src/automod/compiler/prompts.py:1159` — diagnose system prompt
- `src/automod/compiler/compiler.py:1118` — `diagnose_rule_health` and `_DIAGNOSE_TOOL`
- `src/automod/compiler/compiler.py:706,1034` — `adjust_for_context`, `recompile_with_diff` (reuse, do not modify)
- `src/automod/api/health.py:397` — `analyze_rule_health`
- `src/automod/api/alignment.py:56` — `accept_suggestion`
- `src/automod/db/models.py:216` — `Suggestion` (no schema change; uses content JSON)
- `admin/src/components/SuggestionDiff.tsx` — render new types

## Verification

The diagnoser is LLM-driven, so verification is a mix of (a) deterministic backend tests for the suggestion-emission and acceptance plumbing, and (b) manual scenarios via the admin UI to spot-check the prompt's classification.

**Backend (deterministic):**

- `accept_suggestion` for `suggestion_type="context"`: writes `custom_context_notes` on the source rule, schedules `adjust_for_context` once per opted-in `affects_rules` entry. Verify via SQL after accept and via background-task log.
- `accept_suggestion` for `suggestion_type="rule_text"`: writes `Rule.text`, schedules `recompile_with_diff`, the resulting checklist replaces the previous one. Verify by reading `ChecklistItem`s after the background task completes.
- Linked-pair supersession: given two suggestions linked via `linked_suggestion_id` (L1 logic + L3 text), accepting the L3 marks the L1 as `status="superseded"`. Accepting only the L1 leaves L3 pending and the response includes the drift warning flag.
- Cross-rule helper: unit-test `find_related_rules_for_context_note` with a fixture community that has one sibling sharing keywords with the proposed note and one that doesn't — assert score ordering.

**Manual UI scenarios** (seed via existing scripts/REPL, then click "analyze health" and inspect suggestions):

1. **L1 regression** — FP cluster with mod notes "wrong threshold, mod-judgment call" and high `avg_confidence_errors`. Expect a single `adjust_threshold` checklist suggestion (preserves current behavior).
2. **Paired L1+L3 (`tighten_rubric`)** — one underperforming item with a vague rubric tracing to a vague rule-text phrase. Expect two linked suggestions; accepting the rule-text one silently re-derives the logic fix and marks the L1 superseded.
3. **L3-by-default (`add_item`)** — FN cluster on a violation type not anchored anywhere in the rule text. Expect a `rule_text` suggestion adding the missing clause, not a bare `add_item` checklist suggestion.
4. **L2 against existing context** — rule "no low-effort posts" in a community whose `community_context` already has a `tone:satire-friendly` note; FP cluster has mod notes contradicting that note. Expect a `context` suggestion editing the existing note, plus possibly an L1 alongside.
5. **L2 cross-rule** — proposed calibration "weekend-only relaxed enforcement" applies to ≥ 2 rules in the community. Expect a `context` suggestion with `affects_rules` listing both rules and per-rule checkboxes in the UI; opting in only the source rule applies it only there.
6. **Insufficient evidence** — `decision_count < 5` for an item that would otherwise pair L1+L3. Expect L1-only emission per the tie-breaker.
