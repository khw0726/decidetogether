# Moderator-Facing Community Context Features

## Context

Community contexts (purpose/participants/stakes/tone) are currently auto-generated and displayed read-only in the admin UI. Moderators can regenerate them but can't directly shape or understand their downstream impact. This creates a gap: the system's understanding of the community may diverge from what moderators know to be true, and moderators have no visibility into *how* context calibrates their rules.

The two-pass compilation approach (from `compile_with_context.md`) creates a clean separation between base checklist and context-adjusted checklist, which opens up new interaction surfaces.

---

## Prerequisite: Replace `prose` with `notes` in CommunityContextDimension

The current `prose: str` field stores a 2-3 sentence paragraph in system voice — hard for moderators to read, edit, or correct. Replace it with `notes: list[str]`, where each note is a short, moderator-readable bullet capturing one moderation-relevant insight.

**Examples of notes (vs. prose):**

Current prose (r/legaladvice):
> *"This community serves as a peer support space where individuals seek legal guidance. Participants range from laypeople to legal professionals, creating an asymmetric expertise dynamic that moderators must account for."*

Replacement notes:
- "Users seek legal guidance, but giving specific legal advice creates liability"
- "Mix of laypeople and legal professionals — expertise claims need scrutiny"
- "Moderators enforce against unauthorized practice of law, not just tone"

Each note is a discrete claim a moderator can agree with, tweak, or delete.

**Schema change** (`src/automod/models/schemas.py`):
```python
class CommunityContextDimension(BaseModel):
    notes: list[str] = []   # was: prose: str = ""
    tags: list[str] = []
```

**Prompt changes** (`src/automod/compiler/prompts.py`):
- `GENERATE_CONTEXT_SYSTEM` (line 698): Change "prose description (2-3 sentences)" → "calibration notes (2-4 short bullet points, each capturing one moderation-relevant insight)"
- `build_generate_context_prompt()` (line 774-815): Update output format from `"prose": "..."` to `"notes": ["...", "..."]`
- `build_compile_prompt()` (line 318-331): Render notes as bullets instead of paragraph:
  ```
  PURPOSE:
    - Users seek legal guidance, but giving specific legal advice creates liability
    - Mix of laypeople and legal professionals
    [Tags: advice_seeking, legal_guidance, ...]
  ```
- `build_community_norms_prompt()` (line 589-598): Same bullet rendering

**Tool schema change** (`src/automod/compiler/compiler.py`):
- `_GENERATE_CONTEXT_TOOL` (line 276-317): Replace `"prose": {"type": "string"}` with `"notes": {"type": "array", "items": {"type": "string"}}` in all 4 dimensions
- `generate_community_context()` (line 559-566): Normalize `notes` instead of `prose`

**API change** (`src/automod/api/communities.py`):
- `generate_community_context` endpoint (line 738-750): Merge logic uses `notes` field
- `PUT /communities/{id}/context`: Already works with whatever schema defines

**Frontend** (`admin/src/api/client.ts`, `ContextDimensionsView.tsx`):
- Update `CommunityContextDimension` interface: `notes: string[]` instead of `prose: string`
- Render notes as bullet list instead of paragraph text

**Migration**: Existing communities with `prose` data need a one-time migration — split prose into sentences and store as notes array. Can be a simple script or handled on first read.

---

## Feature 1: Inline Context Editing

**What:** Let moderators directly edit tags and calibration notes for each context dimension.

**UX Flow:**
1. On the Community Settings page, each dimension card gets an "Edit" button alongside the existing expand/collapse
2. Clicking "Edit" switches that dimension to an editable form:
   - **Tags**: tag input with autocomplete from the taxonomy (`scripts/context_taxonomy.json`), plus ability to type free-form tags
   - **Calibration notes**: each note is an editable text input in a list. Moderators can edit, delete, or add notes. Each note should be short (one line, one insight).
3. "Save" calls `PUT /communities/{id}/context` with the edited dimension
4. After save, a banner appears: *"Context updated. X rules may need recompilation to reflect this change."* with a "Reapply to all rules" button (calls the planned `POST /communities/{id}/reapply-context` endpoint from the two-pass plan)

**Scenario:** A moderator of r/AskHistorians sees the auto-generated notes for "tone":
- "Academic discussion with formal register"
- "Casual questions tolerated in top-level posts"

They know the system missed a crucial norm, so they add a third note:
- "All claims must cite primary or secondary sources — unsourced answers get removed"

They also remove "Casual questions tolerated" because that's not accurate. On reapply, subjective checklist items for source-checking rules get tighter thresholds.

**API changes:**
- `PUT /communities/{id}/context` already exists — just needs frontend wiring
- Add `manually_edited: bool` per dimension (so regeneration can warn before overwriting manual edits)

**Key files:**
- `admin/src/components/ContextDimensionsView.tsx` — add edit mode per dimension
- `admin/src/pages/CommunitySettings.tsx` — wire save mutation + reapply banner
- `admin/src/api/client.ts` — `updateCommunityContext()` already exists, add `reapplyContext()`
- `src/automod/models/schemas.py` — add `manually_edited` to `CommunityContextDimension`
- `src/automod/api/communities.py` — update PUT handler to set `manually_edited`, add regeneration warning logic

---

## Feature 2: Context Impact View (per rule)

**What:** Show moderators exactly how community context changed each rule's checklist — which items were added, which thresholds shifted, and why.

**UX Flow:**
1. On the rule detail page, next to each checklist item that has `context_influenced: true`, show a small "context" badge
2. Hovering/clicking the badge shows the `context_note` (e.g., "Vulnerable population seeking crisis support → threshold lowered to 0.6")
3. A new "Context Impact" tab/section on the rule page shows:
   - The `context_adjustment_summary` (from the two-pass plan)
   - A side-by-side diff: base checklist vs. context-adjusted checklist (thresholds highlighted, new items marked)
   - Which context dimensions drove each change

**Scenario:** A moderator of r/SuicideWatch wonders why the "dismissive comments" rule has unusually low thresholds compared to what they'd expect from the rule text alone. They open the Context Impact view and see: *"Participants dimension (vulnerable_population, crisis_seeking) lowered thresholds by 0.1-0.15 across 3 items. Added new item: 'Check for toxic positivity that minimizes distress.'"* This builds trust in the system and helps them decide whether the calibration is right.

**API changes:**
- Depends on `base_checklist_json` and `context_adjustment_summary` from the two-pass plan
- Add `GET /rules/{id}/context-impact` that returns structured diff between base and adjusted checklists
- Or: just expose `base_checklist_json` + `context_adjustment_summary` on `RuleRead` (simpler, compute diff client-side)

**Key files:**
- `src/automod/models/schemas.py` — ensure `base_checklist_json`, `context_adjustment_summary` on `RuleRead`
- `admin/src/components/ChecklistTree.tsx` — add context badge to influenced items
- New: `admin/src/components/ContextImpactView.tsx` — diff visualization component
- Rule detail page (wherever checklist is displayed) — add Context Impact section

---

## Feature 3: Context Override per Rule

**What:** Let moderators pin specific context calibration decisions on a per-rule basis, so they survive recompilation and context regeneration.

**UX Flow:**
1. In the Context Impact view (Feature 2), each context-driven change has a toggle: "Keep this adjustment" / "Revert to base"
2. Moderators can also add free-text "context notes" to any checklist item, which act as sticky instructions for future recompilations
3. Pinned overrides are stored on the rule and passed to the compiler as constraints during Pass 2
4. When context is regenerated or reapplied, pinned items are preserved and flagged: *"3 items have moderator-pinned context overrides that were preserved"*

**Scenario:** r/legaladvice has a "no specific legal advice" rule. The auto-context sets high thresholds for "giving advice" because the participants dimension shows "advice_seeking." But the moderators know this is backwards — the whole point is that despite users seeking advice, specific legal advice is dangerous. They pin the low threshold and add a note: "Community seeks advice but specific legal guidance creates liability." Future recompilations respect this.

**API changes:**
- Add `context_pinned: bool` and `context_override_note: Optional[str]` to ChecklistItem model
- Modify `adjust_for_context()` in compiler to accept pinned items as constraints
- Add `PATCH /rules/{id}/checklist-items/{item_id}/context-override` endpoint

**Key files:**
- `src/automod/db/models.py` — add fields to ChecklistItem
- `src/automod/compiler/compiler.py` — `adjust_for_context()` respects pins
- `src/automod/compiler/prompts.py` — adjust Pass 2 prompt to include pinned constraints
- `admin/src/components/ContextImpactView.tsx` — pin/unpin UI

---

## Feature 4: Context-Aware Rule Health Warnings

**What:** Surface proactive warnings when community context suggests gaps or misalignment in the current rule set.

**UX Flow:**
1. On the community dashboard, a "Context Insights" card shows actionable warnings:
   - **Missing coverage:** "Your community context indicates 'vulnerable_population' but no rules address harmful advice or dismissive responses"
   - **Stale context:** "Community context was generated 45 days ago from 50 posts. The community now has 3x more subscribers — consider regenerating"
   - **Context-rule tension:** "Rule 'Keep it civil' has base threshold 0.7 but context adjusts to 0.4. Large gap may indicate the rule text should be rewritten to match community expectations"
2. Each warning has a suggested action: "Create rule", "Regenerate context", "Review rule"

**Scenario:** A rapidly growing community (r/newTechTopic) was set up with context when it had 5K subscribers and a casual tone. After reaching 100K, the moderator visits the dashboard and sees: *"Context was generated when community had 5K subscribers. Participant profile may have shifted — regenerate to recalibrate."* They regenerate, the tone shifts from "casual_banter" to "mixed_expertise_levels", and several rules get threshold adjustments on reapply.

**API changes:**
- Add `GET /communities/{id}/context-insights` endpoint
- Logic: compare context age vs. community growth, scan rules for coverage gaps against context tags, flag large base-vs-adjusted deltas
- Could reuse/extend the existing `diagnose_rule_health()` pattern

**Key files:**
- New: `src/automod/api/insights.py` — context insights endpoint
- `src/automod/compiler/compiler.py` — extend or add analysis methods
- New: `admin/src/components/ContextInsights.tsx` — warnings card
- Community dashboard page — add insights section

---

## Feature 5: "What Would Change" Preview on Context Edit

**What:** Before committing a context edit (Feature 1), show moderators a preview of how the change would affect their rules.

**UX Flow:**
1. After editing a context dimension but before saving, moderator clicks "Preview impact"
2. System runs Pass 2 of two-pass compilation with the draft context against all rules that have stored `base_checklist_json`
3. Returns a summary: *"3 rules affected. Rule 'No hate speech': threshold on 2 items would decrease by 0.05. Rule 'Stay on topic': 1 new item would be added."*
4. Moderator can then save or adjust their edit

**Scenario:** r/Conservative moderator wants to update the "tone" dimension to explicitly include "political_debate, partisan_discourse" to stop the system from over-flagging heated but legitimate political arguments. Before saving, they preview and see that the "civil discourse" rule would loosen thresholds on 4 items. They decide this is correct and save.

**API changes:**
- Add `POST /communities/{id}/context/preview-impact` — accepts draft context, returns per-rule impact summary
- Internally: for each rule with base_checklist_json, run `adjust_for_context()` with draft context, diff against current adjusted checklist

**Key files:**
- `src/automod/api/communities.py` — preview endpoint
- `src/automod/compiler/compiler.py` — reuse `adjust_for_context()`
- `admin/src/components/ContextDimensionsView.tsx` — preview button + results display

---

## Dependency Graph

```
Feature 1 (Inline Editing) ← standalone, no two-pass dependency
    ↓
Feature 5 (Preview Impact) ← requires Feature 1 + two-pass compilation
    ↓
Feature 2 (Context Impact View) ← requires two-pass compilation (base_checklist_json)
    ↓
Feature 3 (Per-Rule Override) ← requires Feature 2 for UI surface
    
Feature 4 (Health Warnings) ← standalone, but richer with two-pass data
```

**Recommended build order:**
1. **Feature 1** (Inline Editing) — highest immediate value, no dependencies, already has backend support
2. **Feature 2** (Context Impact View) — builds on two-pass plan, makes context transparent
3. **Feature 5** (Preview Impact) — natural extension of editing + two-pass
4. **Feature 3** (Per-Rule Override) — power-user feature for fine-grained control
5. **Feature 4** (Health Warnings) — nice-to-have, requires analysis logic

---

## Verification Approach

For each feature:
1. Create a test community with known context (e.g., support community with vulnerable participants)
2. Add 2-3 rules covering different types (civility, topic relevance, harmful advice)
3. Exercise the feature flow end-to-end through the admin UI
4. Verify that context changes propagate correctly to checklist items
5. Check that the UI correctly reflects the current state after mutations
