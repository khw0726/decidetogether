# Context Interactions — Ideation

## Context

The system already has community contexts (4 dimensions × tagged notes), two-pass compilation that uses them, and a designed-but-not-fully-built bridge between rule-health errors and context fixes (`contexts-with-rule-health.md`: I1 systemic-cluster banner, I2 cross-rule pattern card, I3 drift / imported-default banners).

This document collects *additional* interaction ideas beyond what is already designed, for the user to pick which to develop into an implementation plan. **No code changes yet.**

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

**B7. Scope migration (rule-local ↔ community-global).** One-click promote a `custom_context_notes` entry to a community-level note, or demote a community note to a single rule. Surfaces explicit "is this a community fact or a rule fact?" decisions.

**B8. Attribution on calibration.** Hovering a `context_note` shows the mod who added the upstream note, why, and what override sequence triggered it (when produced via I1/I2).

**B9. Soft vs. hard notes.** Annotate notes as suggestion or constraint. Hard notes block contradictory rule edits; soft ones inform and yield. Lets communities lock down stakes-critical calibration.

**B10. Inline context pair-edit.** In the rule editor, side panel lists tags this rule consumes with inline edit affordances on each note. Removes the page-switch into `CommunitySettings` for small calibration tweaks.

**B11. Auto-prune `relevant_context`.** After compile, suggest removing tags the LLM didn't actually invoke. Keeps the picker honest and shrinks blast radius.

### C. Testing rules (more)

**C6. Context-intensity slider.** In the test panel, dial each note's "weight" and watch verdict shift. Robustness probe — does the calibration depend on a knife-edge?

**C7. Regression budget on context edit.** Each rule pins a canonical decision set; every context edit replays it and gates the edit on a budget ("4 keepers flipped — review before commit").

**C8. Counterfactual feed.** For a decision sample, render "what would this verdict have been with no context?" next to the actual. Quantifies how much work context is doing corpus-wide.

**C9. Context-conditioned reliability plot.** Calibration / reliability diagram broken down by which tag influenced the verdict. Detects tags that systematically over- or under-confident the model.

**C10. Shadow-run ablation.** Disable one note for a 7-day shadow run on incoming posts; compare override rate vs. baseline. Real-world effect, not just simulation.

**C11. Disagreement-driven note splitting.** When two mods disagree on a context-influenced item's review, surface the upstream note as candidate for splitting (the note is doing two jobs and conflating them).

## Shared primitives — what unlocks what

A few of these ideas are different UIs on top of the same backend engine. Building one engine would unlock several interactions:

- **"Replay decisions under a candidate context" engine** unlocks: A3 (cross-community import diff), B1 (per-note what-if), B6 (per-note time-travel), C1 (A/B replay), C7 (regression budget), C8 (counterfactual feed). Likely the highest-leverage single backend investment.
- **"Per-note influence telemetry"** (which compiled item, which decision, which override) unlocks: B4 (dead-note lints), B11 (prune `relevant_context`), C4 (coverage report), C9 (reliability plot), D1 (changelog).
- **"Adversarial / probe post generator"** unlocks: A4 (per-tag seed examples), C3 (per-note adversarial suite), C6 (intensity slider — needs probes to slide against).

The `contexts-with-rule-health.md` clustering primitive (metadata grouping → Claude classification) is independent of these and stays useful for I1/I2/B3.

## Round 3 — new axes (not just more variants of A/B/C)

### E. Multi-mod collaboration on contexts

**E1. Context note proposals & review.** A note isn't committed directly — it's a proposal with a "why," other mods +1 / object, then it merges. Pull-request workflow for community calibration. Avoids the "one mod ships a stakes change at 2 AM" failure mode.

**E2. Per-mod calibration drift dashboard.** Show how each mod's overrides correlate with context notes. "Mod A overrides items influenced by `tone:strict` 3× more than the team." Reveals split visions of the community.

**E3. Inline note debate threads.** For contested notes, allow short comment threads attached to the note itself; lock when consensus declared. The note doc becomes the artifact of record for the community's calibration argument.

**E4. Conflict-of-edits resolution.** Two mods edit the same note simultaneously → render a 3-way merge UI (base / theirs / yours) with rule-impact preview for each branch.

### F. Temporal / event-driven contexts

**F1. Time-bounded notes.** Notes with TTL or end date — `tone: extra strict (election season, expires 2026-11-15)`. Auto-expire so calibration doesn't ossify.

**F2. Event-mode toggles.** Pre-baked context overlays: "AMA mode," "raid mode," "newcomer surge mode." Activate for N hours; auto-revert. Reduces the panic-edit pattern during viral events.

**F3. Decay-aware drift detection.** Existing I3 drift indicator could weight recent overrides higher; surfaces fast-moving shifts (a meme arrives) separately from slow drift (community matures).

**F4. Seasonality memory.** If a note was active last year for a window and override rates dropped, suggest re-activating this year (e.g., "tax season — `stakes: financial advice scrutinized`").

### G. Inheritance / packs

**G1. Context packs.** Curated bundles ("support community pack," "hobbyist pack," "Q&A pack") a community can inherit from. Inherited notes are visible-but-not-editable; override creates a fork. Sharply reduces cold-start cost for new communities.

**G2. Diff-from-pack view.** Show "you have inherited 11 notes; you've overridden 3." Helps mods stay close to a known-good baseline unless they have reason to drift.

**G3. Pack contributions back.** When a community's overridden note proves valuable across many forks, surface it as a candidate to upstream into the pack.

### H. User-facing transparency (controlled)

**H1. Public-safe context summary.** A redacted form of the context published to community wiki, rendered as "what makes this community different." Reduces "why was my post removed?" friction.

**H2. Removal explanation includes the note.** When a post is removed because of a context-influenced item, the modmail/comment can cite the note ("this community values `participants: newcomers welcomed` — your post was reviewed with that in mind"). Calibration becomes a teachable moment.

**H3. Posting prompt scaffolds.** New-user post composer pre-shows the relevant context lines (not the rules) — the situational facts the community cares about. Often more useful than a wall of rules.

### I. Cross-modal / structural conditioning

**I1. Per-post-type context.** Notes can be scoped to text / link / image / video posts. `participants: link posts often drive-by; text posts higher-engagement`.

**I2. Flair-conditioned context.** Notes scoped to flairs ("Discussion" vs. "Help"). The same rule compiles differently depending on flair selection — closer to how mods actually think.

**I3. Comment-tree depth conditioning.** Top-level vs. deep-nested comments often have different stakes (`stakes: top-level visible, deep replies low-stakes`). Currently the rule scope is post|comment|both — this would let context further refine.

### J. Diagnostic / discovery UIs

**J1. Context heatmap.** Matrix: rules on one axis, context tags on the other; cell shading shows how much each tag influenced each rule. One screen to see calibration topology.

**J2. "Find rules where this note matters" search.** Click a note → list of rules + items it shaped, ranked by influence. Inverse of `RuleContextPicker`.

**J3. Decision filter: "context flipped this."** Decision queue filter for cases where without the context the verdict would differ. Lets mods spot-check whether context is doing the *right* work, not just any work.

**J4. Context tag co-occurrence map.** "Communities with `tone:irreverent` also tend to have `participants:in-group dominant`." Helps mods find missing notes by analogy to peer communities.

### K. Anti-patterns to design against

These aren't features but failure modes the design should resist; each implies a guardrail.

**K1. Context creep.** Mods keep adding notes; system never prunes. → B4 dead-note lint, B11 auto-prune, hard cap with a warning above N notes per dimension.
**K2. Sample bias on generation.** Context generated from a non-representative window. → Show sample-period and post-mix to mods; recommend re-generation when post-type distribution shifts.
**K3. Calibration laundering.** A mod tightens a rule via a context note to dodge a contentious rule-text edit (avoids the social cost of "changing the rules"). → Surface "rule effectively tightened" stats in changelog regardless of whether change was textual or contextual.
**K4. Stakes amnesia.** Stakes notes get deprioritized over time as override pressure favors permissive calibration. → B9 hard notes; or stakes-dimension changes require a co-mod sign-off.
**K5. Echo-chamber generation.** Context regenerated from posts that *survived prior moderation* drifts toward the current community's biases. → C8 counterfactual feed flags this; or generation pulls from removed posts too.

## Open questions

1. Are any of these axes (E–K) more interesting to you than continuing to refine A/B/C?
2. Anti-pattern framing (K) — useful as a design lens, or beside the point for now?
3. Want round 4 in a specific direction (e.g., go deep on collaboration / temporal / packs), or pivot to picking a primitive and planning?
