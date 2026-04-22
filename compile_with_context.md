# Plan: Two-Pass Compilation Approach

## Context

Currently, community context (purpose/participants/stakes/tone) is embedded directly into the compilation prompt in a single LLM call. Our comparison experiment (`scripts/context_comparison.json`) showed that a two-pass approach — compile without context first, then adjust using context — produces:
- Explicit, traceable adjustments with summaries explaining what changed and why
- Larger, more targeted threshold shifts (avg +0.05 to +0.14 for permissive communities)
- New items for community-specific threats the rule text doesn't mention (14 new items across 10 test rules)
- Preserved base structure with clean layering of context influence

The goal is to integrate this two-pass approach into all compilation paths: initial compile, rule edits, and decision-driven updates.

---

## Design Decisions

1. **Store the base (pre-context) checklist** as `base_checklist_json` (JSON column) on the Rule model. This enables re-running Pass 2 when context changes without re-running Pass 1.

2. **Rule text edits**: Diff against the stored base checklist to produce a new base, then re-apply context adjustment. This keeps rule-text changes and context adjustments cleanly separated.

3. **Suggestion paths** (diagnose_health): Single-pass with context passed into the existing prompts. Two-pass adds latency for minimal benefit on incremental suggestions.

4. **Adjustment summary**: Stored as `context_adjustment_summary` (TEXT) on Rule, exposed via `RuleRead` schema for UI display.

5. **Legacy rules** (no stored base): Fall back to current single-pass behavior. They'll gain a base on next recompile.

6. **Community context format change**: `CommunityContextDimension.prose` (freeform paragraph) is replaced with `notes: list[str]` (short calibration bullets). Each note captures one moderation-relevant insight, making context editable by moderators and more structured for the compiler. See `interact_with_context.md` for full rationale and migration plan.

---

## Implementation

### 1. Schema Changes

**`src/automod/db/models.py`** — Add to `Rule`:
- `base_checklist_json: Mapped[Optional[dict]]` — context-free checklist tree (nested dicts)
- `context_adjustment_summary: Mapped[Optional[str]]` — human-readable summary from Pass 2

**`src/automod/db/database.py`** — Migration to add both columns to `rules` table.

**`src/automod/models/schemas.py`**:
- Add both fields to `RuleRead`
- Change `CommunityContextDimension.prose: str = ""` → `notes: list[str] = []`
- Add `manually_edited: bool = False` to `CommunityContextDimension` (tracks whether a moderator has hand-edited this dimension; regeneration should warn before overwriting)

**Migration for existing context data**: Communities with existing `prose` values need a one-time migration to convert prose → notes. Strategy: split on sentence boundaries, store each sentence as a note. Can be a startup migration in `database.py` or a standalone script.

### 2. New Prompts & Tool Schema

**`src/automod/compiler/prompts.py`** — Add:
- `NO_CONTEXT_COMPILE_SYSTEM` — stripped of all context calibration instructions, explicitly says "compile based solely on rule text"
- `build_no_context_compile_prompt()` — same as `build_compile_prompt()` minus context/atmosphere/posts params
- `CONTEXT_ADJUST_SYSTEM` — based on experiment's `TWO_PASS_ADJUST_SYSTEM`, instructs to review items and adjust for context
- `build_context_adjust_prompt()` — takes base_checklist + community_context + optional atmosphere/posts

Also add `_NO_CONTEXT_COMPILE_TOOL` (without context_influenced/context_note fields) and `_CONTEXT_ADJUST_TOOL` (returns checklist_tree + adjustment_summary). These are defined in the experiment script (`scripts/compare_context_approaches.py`) and can be moved to the compiler module.

**Note:** Community context now uses `notes: list[str]` instead of `prose: str`. All prompt builders that render context sections (`build_compile_prompt`, `build_context_adjust_prompt`, `build_community_norms_prompt`) should render notes as bullet points:
```
PURPOSE:
  - Users seek legal guidance, but specific legal advice creates liability
  - Mix of laypeople and legal professionals
  [Tags: advice_seeking, legal_guidance]
```
The generation prompts (`GENERATE_CONTEXT_SYSTEM`, `build_generate_context_prompt`) should instruct the LLM to output `"notes": ["...", "..."]` (2-4 short bullets per dimension) instead of `"prose": "..."`. The `_GENERATE_CONTEXT_TOOL` schema must also use `"notes": {"type": "array", "items": {"type": "string"}}` instead of `"prose": {"type": "string"}`.

### 3. Compiler Methods

**`src/automod/compiler/compiler.py`** — Add to `RuleCompiler`:

```
compile_rule_base(rule, community, other_rules, existing_items?, existing_examples?)
    → (checklist_items, example_dicts)
    Pass 1: context-free compilation using NO_CONTEXT_COMPILE_SYSTEM

adjust_for_context(rule, community, base_checklist_dicts, community_context, atmosphere?, posts_sample?)
    → (adjusted_items, adjustment_summary)
    Pass 2: adjust base checklist using community context
    If no community_context available, returns base items unchanged

compile_rule_two_pass(rule, community, other_rules, ..., community_context?, atmosphere?, posts_sample?)
    → (adjusted_items, example_dicts, base_checklist_dicts, adjustment_summary)
    Convenience: calls compile_rule_base() then adjust_for_context()
```

Also add `_items_to_nested_dicts()` as a method (already exists in the experiment script).

Preserve `compile_rule()` for backward compat but it will no longer be the primary path.

### 4. Initial Compilation Path

**`src/automod/api/rules.py`** — Modify `_compile_rule_read_and_llm()`:

**No existing items branch** (line 151): Replace `compiler.compile_rule()` with `compiler.compile_rule_two_pass()`. Return `base_checklist_json` and `adjustment_summary` in the result dict.

**Existing items branch** (line 169): 
- Load `rule.base_checklist_json`
- If base exists: diff against base → new base → `adjust_for_context()` → adjusted items
- If no base (legacy): fall back to current `recompile_with_diff()` against existing items

**`_compile_rule_persist()`** (line 187): After persisting checklist items, also save `base_checklist_json` and `context_adjustment_summary` on the Rule record.

### 5. Manual Recompile

**`src/automod/api/checklist.py`** — Modify `recompile_rule()` (line 454):

Same two-pass approach as step 4. Pass community context through both branches. Store base + summary on rule after persisting.

### 6. Preview & Evaluate Flows

**`src/automod/api/alignment.py`**:

`preview_recompile()` (line 319): If `rule.base_checklist_json` exists, diff against base, produce new base, run `adjust_for_context()`. Return `adjustment_summary` in response. Fall back for legacy rules.

`evaluate_examples_with_draft()` (line 513): Same approach — build hypothetical checklist from two-pass, then evaluate examples against it.

### 7. Suggestion Paths (Context-Aware, Single-Pass)

These get community context passed in but remain single-pass:

**`src/automod/compiler/prompts.py`** — Add `community_context` and `community_atmosphere` params to:
- `build_diagnose_health_prompt()`

Append the same context section format used in `build_compile_prompt()`.

**`src/automod/compiler/compiler.py`** — Add optional `community_context`, `community_atmosphere` params to:
- `diagnose_rule_health()`

**API callers** — Load community context and pass it through:
- `health.py:analyze_rule_health()`

### 8. Re-Apply Context on Context Change

**`src/automod/api/communities.py`** — Add endpoint:
```
POST /communities/{community_id}/reapply-context
```
For each active actionable rule with a stored `base_checklist_json`:
- Run `adjust_for_context()` with the new community context
- Replace existing checklist items with re-adjusted items
- Update `context_adjustment_summary`

This is manual (not auto-triggered on context update) since it involves LLM calls per rule.

---

## Edge Cases

- **No community context**: Pass 2 is skipped; base checklist = final checklist. `context_adjustment_summary` = empty.
- **Legacy rules (no base_checklist_json)**: Fall back to current single-pass behavior. Base is populated on next recompile.
- **Pass 2 LLM failure**: Fall back to base checklist, log warning. Base checklist is usable standalone.
- **Latency**: Two sequential LLM calls. Pass 1 ~25-35s, Pass 2 ~20-45s based on experiment data. Acceptable for background tasks; preview flows will be slower.

---

## Implementation Order

0. **Prose → notes migration** (schemas, prompts, tool schemas, compiler, frontend types) — prerequisite for everything; see `interact_with_context.md` for full change list
1. Schema changes (models, database, schemas) — prerequisite
2. Prompts + tool schemas — can parallel with 1
3. Compiler methods — depends on 2
4. Initial compilation API — depends on 1, 3
5. Manual recompile API — depends on 1, 3
6. Preview flows — depends on 3
7. Suggestion paths (context passthrough) — independent, can parallel with 4-6
8. Re-apply context endpoint — depends on 1, 3

---

## Verification

1. **Unit test**: Compile a rule two-pass via the compiler directly, verify base checklist has no `context_influenced` items, adjusted checklist has some
2. **Integration test**: Create a community with context, create a rule, verify `base_checklist_json` and `context_adjustment_summary` are populated on the Rule record
3. **Rule edit test**: Update rule text, verify diff runs against base (not adjusted) checklist, and Pass 2 re-adjusts
4. **Preview test**: Call `preview-recompile` with draft text, verify response includes `adjustment_summary`
5. **Suggestion test**: Trigger `analyze-health`, verify community context appears in the LLM prompt
6. **Legacy fallback test**: Compile a rule, manually clear `base_checklist_json`, recompile — should fall back gracefully
7. **Re-apply context test**: Update community context, call `reapply-context`, verify checklist items change and summary updates

## Key Files

- `src/automod/compiler/compiler.py` — new methods
- `src/automod/compiler/prompts.py` — new prompts and tool schemas  
- `src/automod/api/rules.py` — initial compilation path
- `src/automod/api/checklist.py` — manual recompile
- `src/automod/api/alignment.py` — preview endpoints
- `src/automod/api/health.py` — health diagnosis
- `src/automod/api/communities.py` — re-apply context endpoint
- `src/automod/db/models.py` — Rule schema
- `src/automod/db/database.py` — migration
- `src/automod/models/schemas.py` — RuleRead schema
- `scripts/compare_context_approaches.py` — reference implementation for prompts/tools
