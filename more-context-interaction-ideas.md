# Context Interactions — Ideation

## Context

The system already has community contexts (4 dimensions × tagged notes), two-pass compilation that uses them, and a designed-but-not-fully-built bridge between rule-health errors and context fixes (`contexts-with-rule-health.md`: I1 systemic-cluster banner, I2 cross-rule pattern card, I3 drift / imported-default banners).

This document collects *additional* interaction ideas beyond what is already designed, then deepens a selected subset (Round 4) for implementation planning. **No code changes yet.**

## Existing interactions (for reference, not to reimplement)

- Context generation from sample posts (`POST /communities/{id}/context/generate`)
- Per-dimension tag + note editing in `ContextDimensionsView` with "Preview Impact"
- `RuleContextPicker` for `relevant_context` + per-rule `custom_context_notes`
- Two-pass compile: base checklist → context-adjusted, with `context_influenced` + `context_note` on each item
- Example generation conditioned on context (community register / style)
- Designed (per `contexts-with-rule-health.md`):
  - I1 systemic-cluster banner on `RuleHealthPanel`
  - I2 cross-rule pattern card on `AlignmentDashboard`
  - I3 drift indicator + `relevant_context is None` banner

## New ideas — by lifecycle stage

### A. Making rules

**A1. Context-first rule scaffolding.** Given a context note, propose candidate rules that note implies the community wants. Inverse of I1.

**A2. Auto-suggest `relevant_context` on rule create.** Classify pasted rule text against the taxonomy and pre-check the picker. Removes the "imported with `relevant_context=None`" failure mode (S6) at the source rather than after the fact.

**A3. Cross-community rule import with context translation.** Show a diff of how the imported rule compiles under the *target* community's context vs. its origin context, before commit.

**A4. Context-aware example seeding.** On rule create, generate one seed example per relevant context tag — exercises the rule against the situations the context says should bend it.

### B. Refining rules

**B1. Context-note what-if toggle.** In the rule editor, toggle individual notes off and live-preview the checklist diff. Answers "is this note doing work for *this* rule?"

**B2. Provenance hover on checklist items.** Click a `context_influenced` item → see the source note plus *all* other items (same rule and across rules) that depend on the same note. Surfaces blast radius at item granularity.

**B3. Reverse extract-to-context.** Detect when a mod manually applies a similar rubric tightening across 3+ rules in one session and suggest extracting a context note. Mirror of I1, observed from editor side.

**B4. Context conflict / dead-note lints.** Flag opposing-direction notes affecting one rule; flag notes that never produced a `context_note` in N days.

**B5. Custom-context-note diff.** When a rule sets `custom_context_notes`, render them as a diff vs. community context with a required "why" field. Keeps overrides accountable.

### C. Testing rules

**C1. A/B context replay.** Replay last N decisions through {current, candidate} context; show flip count and direction. Decision-level analog of "Preview Impact" (which today is rule-level).

**C2. Context-aware test panel.** In the hypothetical-post tester, surface which context tags fired and how they shifted the verdict, alongside the verdict itself.

**C3. Adversarial probes per context note.** Auto-generate borderline posts that target a single note's calibration; use as a regression suite when context changes.

**C4. Context coverage report.** Per-note: influence count, flip count (verdict-changing vs. cosmetic), and override rate on items it shaped. A note with high influence but high override rate is wrong, not the rule.

**C5. Cross-community context comparison.** Diff this community's context against a peer community's, with "borrow note" affordance.

### D. Cross-cutting

**D1. Context changelog & lineage.** Per-note audit trail (who, when, why) plus before/after decision metrics so mods can answer "did this note help?"

**D2. Mod-onboarding tour.** Guided pass through the context with linked real example decisions for each note — onboarding doc that stays current automatically.

## Round 2 — additional variants

### A. Making rules (more)

**A5. Context → coverage gap analysis.** Scan existing rules against the context; flag situational facts with no corresponding rule (e.g., `stakes: medication misinformation` but no misinformation rule). A TODO list for community rule coverage.

**A6. Tone-aware rule wording assistant.** As mod types rule text, suggest register changes to match `tone` (e.g., "users must" → "y'all should"). Targets rule-text register, not just checklist calibration.

**A7. Source-tag chips on candidate rules.** Each suggested rule (from A1 or A5) shows which tags it would consume. Lets mods sanity-check the lineage before accepting.

**A8. Borrow-and-blend across communities.** Compose a draft rule from clauses pulled from 2–3 peer communities, each clause annotated with its source community's context tag. Prevents silent inheritance of foreign calibration.

**A9. Context-as-prompt drafting.** Rule editor pre-fills "given [context], a rule about X should…" as a generated draft instead of blank-page authoring.

### B. Refining rules (more)

**B6. Per-note time-travel.** Roll back one context note (not the whole context) to a prior version; compile-diff; commit or discard. Avoids "regenerate everything" overkill.

**B7. Scope migration (rule-local ↔ community-global).** One-click promote a `custom_context_notes` entry to a community-level note, or demote a community note to a single rule.

**B8. Attribution on calibration.** Hovering a `context_note` shows the mod who added the upstream note, why, and what override sequence triggered it.

**B9. Soft vs. hard notes.** Annotate notes as suggestion or constraint. Hard notes block contradictory rule edits; soft ones inform and yield.

**B10. Inline context pair-edit.** In the rule editor, side panel lists tags this rule consumes with inline edit affordances on each note.

**B11. Auto-prune `relevant_context`.** After compile, suggest removing tags the LLM didn't actually invoke.

### C. Testing rules (more)

**C6. Context-intensity slider.** In the test panel, dial each note's "weight" and watch verdict shift.

**C7. Regression budget on context edit.** Each rule pins a canonical decision set; every context edit replays it and gates the edit on a budget.

**C8. Counterfactual feed.** For a decision sample, render "what would this verdict have been with no context?" next to the actual.

**C9. Context-conditioned reliability plot.** Calibration / reliability diagram broken down by which tag influenced the verdict.

**C10. Shadow-run ablation.** Disable one note for a 7-day shadow run on incoming posts; compare override rate vs. baseline.

**C11. Disagreement-driven note splitting.** When two mods disagree on a context-influenced item, surface the upstream note as candidate for splitting.

## Shared primitives — what unlocks what

- **"Replay decisions under a candidate context" engine** unlocks A3, B1, B6, C1, C7, C8.
- **"Per-note influence telemetry"** unlocks B4, B11, C4, C9, D1.
- **"Adversarial / probe post generator"** unlocks A4, C3, C6.

## Round 3 — new axes

### E. Multi-mod collaboration on contexts

**E1.** Note proposals & review (PR workflow). **E2.** Per-mod calibration drift dashboard. **E3.** Inline note debate threads. **E4.** Conflict-of-edits resolution UI.

### F. Temporal / event-driven contexts

**F1.** Time-bounded notes (TTL). **F2.** Event-mode toggles (AMA / raid / surge). **F3.** Decay-aware drift detection. **F4.** Seasonality memory.

### G. Inheritance / packs

**G1.** Context packs (curated bundles). **G2.** Diff-from-pack view. **G3.** Pack contributions back.

### H. User-facing transparency

**H1.** Public-safe context summary. **H2.** Removal explanation cites the upstream note. **H3.** Posting-time prompt scaffolds for new users.

### I. Cross-modal / structural conditioning

**I1.** Per-post-type context. **I2.** Flair-conditioned context. **I3.** Comment-tree depth conditioning.

### J. Diagnostic / discovery UIs

**J1.** Context heatmap. **J2.** Reverse "find rules where this note matters" search. **J3.** "Context flipped this" decision filter. **J4.** Context tag co-occurrence map across communities.

### K. Anti-patterns to design against

**K1.** Context creep. **K2.** Sample bias on generation. **K3.** Calibration laundering (changing rules silently via context). **K4.** Stakes amnesia. **K5.** Echo-chamber generation (training on already-moderated posts).

---

# Round 4 — selected ideas, developed further

The user picked **A1, A3, A8, C2, C5, C6** for deepening. Below: mechanism, UX surface, edge cases, and notes on what's reusable from existing code.

---

## A1 — Context-first rule scaffolding

**Idea.** When a moderator adds (or just-generated) a context note, the system proposes candidate rules that note implies the community would want. Inverse of I1 — instead of "errors → suggest a context note," it's "context → suggest missing rules."

### Mechanism

1. **Trigger points** (multiple):
   - After `POST /communities/{id}/context/generate` completes, batch-suggest rules across all generated notes.
   - When a mod adds/edits a note in `ContextDimensionsView`, suggest rules implied by that single note.
   - On-demand button in rules sidebar: "Suggest rules from context."
2. **Backend** (`api/rules.py` or new `api/suggestions.py`): single LLM call given:
   - The note(s) in question
   - Existing rule list (titles + types) — to filter out duplicates / overlaps
   - The full community context — for register / register-aware drafting
   - A small taxonomy of common rule shapes (no-spam, scope-restriction, evidence-required, etc.)
3. **Output schema:**
   ```
   suggestions: [
     { title, draft_rule_text, type, scope,
       relevant_context: [tag pre-selections],
       triggering_note: <which note implied this>,
       skip_reason?: "this note implies the absence of restriction" }
   ]
   ```
4. **Filter step.** Drop suggestions that semantically overlap an existing rule (cheap embedding similarity check). Always show why a suggestion was dropped, so mods can override.

### UX

- Card list rendered on `CommunitySettings` (after context generation) and as a modal/drawer from the rules sidebar.
- Each card: title, draft text, source-note chip ("triggered by `stakes: medication misinformation`"), [Draft this rule] [Dismiss] [Tweak].
- "Draft this rule" → opens new-rule editor pre-filled with the suggestion + `relevant_context` pre-checked. Mod still has to confirm and compile.

### Edge cases

- **Permissive notes.** `participants: newcomers welcomed` doesn't imply a rule — it implies softer calibration on existing rules. Prompt should explicitly skip and emit a `skip_reason` rather than fabricate a "newcomer rule."
- **Stale suggestions.** Once a suggestion is dismissed, persist that decision so re-running on the same note doesn't re-surface it (unless the note has materially changed).
- **Mod fatigue.** Cap to top N (3?) suggestions per note. Quality > quantity.

### Reuses

- The `compile_rule_two_pass` pipeline downstream once the mod accepts a draft — no new compilation infra.
- Prompt patterns from `build_compile_prompt` for rendering context.

---

## A3 — Cross-community rule import with context translation

**Idea.** A moderator imports a rule from another community. The system shows how that rule compiles *under the target's context* vs. *the source's context*, surfaces calibration deltas, and warns about orphan source-tags that have no analog in the target. Mod commits, edits, or rejects.

### How the import is sourced

Three import modes; design should support all three with a shared backend:

1. **From another community in the system.** Browse a community → pick a rule → import. Cleanest path because the source's full compiled state is available.
2. **From pasted rule text + (optional) source description.** Mod pastes rule text from a Reddit wiki / sidebar; optionally pastes "what kind of community is this?" prose. Source-context is heuristically inferred or marked as unknown.
3. **From a Reddit subreddit URL.** System fetches the public rules page, scrapes rule list, mod selects which to import. Source-context auto-generated from a sample of recent posts (re-using existing `context/generate` endpoint).

A unified flow: regardless of mode, the import reaches "we have (a) rule text, (b) source context [maybe partial], (c) target context." Then translation runs.

### Translation pipeline

1. **Compile under source context** (Pass 1 + Pass 2 with source's context). Yields source-flavored checklist. If source mode = (1), reuse the stored compiled checklist.
2. **Compile under target context** (Pass 1 + Pass 2 with target's context). Yields target-flavored checklist.
3. **Diff**: per checklist item, compare:
   - Threshold values (deterministic / structural)
   - Rubric language and example phrasings (subjective)
   - Whether the item exists at all (Pass 2 may add or drop items based on context)
4. **Tag mapping audit.** For each tag the source rule consumed (`relevant_context`):
   - Does the target community have an analogous tag in the same dimension?
   - If yes → preserve the mapping.
   - If no → flag as **orphan calibration**: "Source tightened thresholds because of `stakes: financial-advice-permanence`. Target has no comparable stakes note. Imported rule will be effectively looser unless you add one."
5. **Cross-reference cleanup.** Detect references to community-specific things (rule numbers from the source, named people, links to the source's wiki) and flag for manual rewrite.

### UX

- Import modal as 3-step wizard: **Source** (pick mode 1/2/3) → **Translate** (review the diff and orphan warnings) → **Commit** (open in rule editor pre-filled).
- The translate screen has three columns:

  ```
  | Source community  |  Translation diff      |  Target (you)         |
  |  rule text        |  → tag map preserved   |  rule text (editable) |
  |  source context   |  ⚠ orphan: stakes      |  target context recap |
  |  source checklist |  ↻ thresholds shifted  |  target checklist     |
  ```

- Per-item diff supports inline accept/reject so mods can keep the source's calibration on some items while taking the target's on others.

### Edge cases

- **No source context** (mode 2 with no description). Fall back: render only the target compile, with a banner "we don't know the source's context, so we can't show you what calibration you're losing." Mod proceeds at their own risk.
- **Tag-name collisions** (source and target both have `tone:irreverent` but the underlying notes differ). Show note text side-by-side, not just tag names.
- **Multi-rule imports** (mod imports 5 rules at once). Run translations independently but aggregate orphan-tag warnings; if 4 out of 5 rules consume `stakes:medical` and target has none, surface as a single suggestion: "Add a stakes:medical note before importing."
- **Drift after commit.** After the imported rule is committed, mark `relevant_context` source so future audits can spot "this rule's calibration was inherited, not authored."

### Reuses

- Existing `compile_rule_two_pass` and `_filter_context_by_relevant`.
- Existing `context/generate` for mode 3 source-context inference.
- The "Preview Impact" mechanism (`previewContextImpact`) for translation diffs.

---

## A8 — Borrow-and-blend across communities

**Idea.** Different from A3. A3 is "I have one specific rule I want to port." A8 is "I want to design a rule about *X* by surveying how peer communities articulate it, and stitch a draft together."

### How the system finds peer rules

1. **Mod expresses intent** as either:
   - Free text: "I want a rule about low-effort posts."
   - Selected scope: post type / target behavior chips.
2. **Retrieval** across a peer corpus:
   - Tier 1: other communities currently using the system (rules + their compiled checklists are first-class).
   - Tier 2: a curated catalog of canonical Reddit rules harvested from public rules pages (text only, no compiled state).
3. **Semantic search** over rule text using embeddings; return top-K candidate rules with their source community.

### Decomposition + blending

1. **Clause extraction.** For each candidate rule, an LLM call decomposes the rule text into atomic *clauses* (one ask per clause): e.g., "no questions in titles," "must include a flair," "must be on-topic to the subreddit."
2. **Clustering.** Group clauses that mean approximately the same thing across the candidate rules. Surfaces convergence ("all 5 communities require flair") vs. divergence ("only 2 require evidence sourcing").
3. **Mod composes.** A clause-picker UI:
   - Each clause shows: text, source community, the source's relevant context tag, frequency across the K candidates, and a fit-score against the target's context (LLM judge).
   - Mod checks the clauses they want.
   - System stitches the selected clauses into a coherent rule draft via a final LLM compose call (one rule text, not a Frankenstein concatenation).
4. **Compile.** Compile the draft under the *target's* context. Show the resulting checklist.
5. **Source-tag annotation persists.** The drafted rule's `relevant_context` is the union of tags relevant to the kept clauses, mapped to target tags where available, with orphan warnings (same primitive as A3).

### UX

- New entry point on the rules sidebar: "Compose from peers."
- 4-step: **Intent → Survey → Compose → Compile-preview.**
- Survey screen renders clause clusters; mod picks; system explains "you took clause 2 from r/X (where `tone:strict`) — your community has `tone:irreverent`, this clause may need rephrasing" inline.

### Edge cases

- **Sparse peer corpus.** If fewer than 3 peers match the intent, fall back to A1 (context-first scaffolding) — "we don't have enough peers; here's what your context implies."
- **Conflicting clauses.** If mod picks two contradicting clauses from different peers, the compose step flags rather than silently picking one.
- **Provenance bookkeeping.** Persist clause provenance on the rule (`source_clauses: [{clause, origin_community, origin_rule_id}]`) so future audits can trace inherited calibration.

### Reuses

- A3's translation/orphan-tag primitive.
- Existing rule storage + compile pipeline. Only new pieces: clause-extractor LLM, embedding search, compose LLM.

### Relationship to A3

- **A3 = faithful import of one rule.**
- **A8 = composition from many.**
- Both share: (rule text → checklist under target context) + (orphan-tag detection).
- Implementation order: ship A3 first; A8 builds on A3's translation primitive plus a clause-extractor.

---

## C2 — Context-aware decision view (test panel + modqueue)

**Idea, expanded per user.** The original was about the hypothetical-post tester. The user pointed out: this is even more useful on **real posts in the modqueue** — where mods see actual verdicts and can act on the trace.

### What it shows on each post

For any evaluated post (test panel OR modqueue), expose a **context trace**:

1. **Tags that fired** — which `relevant_context` tags were applied during this evaluation.
2. **Per checklist item:**
   - Which items are `context_influenced: true`.
   - The `context_note` ("[fact] → [calibration]") text.
   - The verdict the item produced AND a **counterfactual chip** showing what the verdict would have been *without* that note (computed by re-running just the subjective evaluation pass with that note suppressed — cheap, single-item scope).
3. **Aggregate sentence:** "This post was [APPROVED / REVIEW / REMOVE] partly because of `participants: newcomers welcomed` (softened threshold on item 3)."

### UX

**Test panel** (existing): existing layout + a "context trace" expandable section.

**Modqueue / `DecisionQueue.tsx`** (new): each `PostCard` gets a collapsed "context trace" affordance:

- Closed: a small chip count — "3 context tags shaped this verdict."
- Expanded: the per-item breakdown above + two action buttons:
  - **"Calibration was wrong here"** → deep-link into the upstream note in `ContextDimensionsView` with the post linked as evidence (pre-fills feedback for I1's diagnose flow).
  - **"This isn't context's fault"** → marks the override as rule-local, suppresses I1 banner triggering for this case.

### Why this matters for modqueue (vs. only test panel)

- The test panel is for *what-if* exploration. The modqueue is where mods actually do work. Surfacing context influence at the moment of override is the quickest path to fixing miscalibrations — the mod has the post, the verdict, and the upstream note all in one screen.
- Feeds I1/I2 cluster detection: every override-with-context-trace becomes structured evidence. Today, an override is a bare flip. With C2, it's an annotated flip ("flipped despite `participants:newcomers` softening this item").
- Closes the loop with H2 (user-facing removal explanations) — once mods see "this note is doing decisive work," they're more likely to surface it to users.

### Mechanism

- **Storage.** During evaluation, persist per-decision: `{ tags_fired: [], items_with_context: [{item_id, note, counterfactual_verdict}] }` on the decision record. Cheap if done at write time.
- **Counterfactual computation.** For subjective items, the engine already computes a verdict with the rubric. Re-running with the note suppressed is one extra LLM call per `context_influenced` item. To bound cost: compute counterfactuals only for items the *mod expanded*, not eagerly for every decision.
- **API surface:** `GET /decisions/{id}/context-trace` (lazy) returns the trace including on-demand counterfactuals.

### Edge cases

- **Counterfactual cost.** Don't compute eagerly. Lazy + cache.
- **Many items, many notes.** Cap visible trace to top 3 items by influence; "show all" expander.
- **Borderline already-escalated cases.** If Haiku→Sonnet escalation happened, show which evaluator produced the trace.

### Reuses

- Existing `subjective.py` evaluation engine (with a note-suppression flag).
- Existing `PostCard.tsx` component (extend with the trace section).

---

## C5 — Cross-community context comparison

**Idea.** Side-by-side diff of this community's context against a peer's. Highlight overlap, divergence, and gaps. Allow borrowing notes (with regression preview).

### UX

- Entry: button on `CommunitySettings` — "Compare to peer community."
- Picker: dropdown of communities in the system OR a "find similar" suggestion (system ranks peers by tag-overlap + community-type similarity).
- Side-by-side view, four dimension cards × two columns:

  ```
  | Purpose (mine)        | Purpose (theirs)                |
  | • topic-focused Q&A   | • topic-focused Q&A   ✓ shared   |
  | • beginner-friendly   | (absent)            ✓ only mine  |
  | (absent)              | • discourages meta  ⊕ borrow?    |
  ```

- Each only-in-theirs note has a [Borrow] button → adds it to your context as a *pending* note (uses Preview Impact / regression budget from C7 if available before commit).

### Variants

- **Multi-peer view.** Pick 2–3 peers; system shows a heatmap-style "tag presence" matrix. Useful for finding consensus across peers ("4 of 5 medical communities have `stakes: vulnerable readers`; you don't").
- **Suggested peers.** Auto-rank by community-type and tag-overlap rather than alphabetical.

### Edge cases

- **Privacy.** If communities have any expectation of context privacy, gate "compare" behind opt-in or restrict to communities under the same mod-team (single user with cross-community access).
- **Borrowed note attribution.** When mod borrows a note, persist its origin (`source_community_id`) so future audits and changelog can show "this note came from r/X."
- **Tag mismatch.** Peer may have a tag in the taxonomy that this community has never used. Borrowing creates the tag from scratch.

### Reuses

- Existing context data model.
- "Preview Impact" infrastructure for borrow-preview.

---

## C6 — Context-intensity dial

**Idea.** Per-note dial in the test panel that lets mods see how *robust* a calibration is on a specific post. Probes "is this verdict knife-edge?"

### Mechanism

A note isn't a numeric weight — it's a calibration instruction. So "intensity" is implemented as a **3-state dial**, not a continuous slider:

1. **Off** — note suppressed entirely (same as the C2 counterfactual chip).
2. **As written** — current note text, used during compile.
3. **Strong** — note rephrased with intensifiers ("strongly emphasize…", "this is a hard line…") via a tiny LLM pass.

Re-evaluation on dial change runs only the **subjective evaluation pass** with the modified context, scoped to the test post. No recompile of the rule's checklist — reusing the existing checklist with an adjusted runtime rubric. Bounds cost.

### UX

- In the test panel (and optionally modqueue trace expansion): each context note that influenced the verdict gets a 3-state segmented control.
- Dialing a note re-runs subjective evaluation for that post in ~1 LLM call; updates verdict + per-item changes inline.
- Visual cue: if dialing one note flips the verdict between "off" and "as written," highlight as **knife-edge calibration** — likely needs revisiting.

### Use cases

1. **Robustness probe.** Mod tests a borderline post; if any single note flips the verdict, the calibration is fragile.
2. **Stakes stress test.** Dial up a stakes note to "strong" and confirm the rule actually tightens enough — useful before committing a stakes change.
3. **Disagreement debugging.** Two mods disagree on a post; dialing isolates which note causes the disagreement, which feeds C11 (note splitting).

### Edge cases

- **Cost control.** Only dial-able for context-influenced items on the post. No combinatorial explosion (one note at a time).
- **Strong rephrase quality.** Keep the rephrase deterministic / cached per note; don't regenerate on every dial.
- **Limit to test/explore surfaces.** Dialing on production decisions doesn't make sense; this is an exploration tool.

### Reuses

- `subjective.py` evaluator with a runtime context override.
- Same suppression mechanism C2 uses for counterfactuals (off-state).

---

## Synthesis — recommended implementation order

If all six ship, the dependencies suggest this ordering:

1. **C2** (context trace on modqueue + test panel) — foundational. Requires per-decision context trace storage and a note-suppression eval mode. Both are reused by C6 and unlock I1/I2 follow-up.
2. **C6** — small UI addition on top of C2's note-suppression mechanism.
3. **A3** — translation primitive (compile under {source, target} context + orphan-tag detection). Substantial backend.
4. **A8** — clause extractor + compose, builds on A3.
5. **A1** — context-first rule scaffolding. Independent; can ship anytime.
6. **C5** — cross-community context comparison. Independent; can ship anytime.

A3 and C2 are the two highest-leverage backends; the rest are lighter UIs over what they (and existing infra) provide.

## Open questions

1. For A3 import: which source modes (1/2/3) are must-have vs. nice-to-have?
2. For A8: do we have, or want to build, a peer corpus of rules from communities outside the system (scraped Reddit pages)? Affects scope significantly.
3. For C2 counterfactuals: lazy on-expand (cheap, but slow for the user clicking) or eager-on-write (fast UI, costs LLM calls on every decision)? My instinct is lazy, but worth your call.
4. For C6: 3-state dial vs. continuous "intensity" slider — is 3 states enough, or do you want more granularity?
