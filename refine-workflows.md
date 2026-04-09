# Admin Workflows (Clarified)

## 1. Community Setup

**Flow:** Create community → add sample posts (acceptable/unacceptable, with optional note) → generate atmosphere profile → add rules → compile checklists

**Key design points:**
- Community atmosphere is passed as context to the rule compiler — subjective rubrics and thresholds are calibrated to the community's tone and norms
- Checklist items shaped by community atmosphere are marked with `atmosphere_influenced = true` and an `atmosphere_note` explaining how (shown as a ✦ badge in the checklist UI)

---

## 2. Rule Editing Sandbox

When editing rule text, the moderator sees a **preview** before committing:

1. Click **Edit** on the rule text → enters draft mode
2. Edit the text in the textarea
3. Click **Preview Changes** → calls `POST /rules/{id}/preview-recompile`
   - Shows a checklist diff summary: N kept / M updated / X deleted / Y added
   - Shows which labeled examples may be affected
4. Click **Confirm & Save** → rule text is saved and background recompile is queued
5. **Discard** at any point reverts to the original text

> Non-actionable rules skip the preview step and save directly.

---

## 3. Automod Decision + Approval/Override

### Agent verdicts
- **Approve**: no rule violations detected
- **Flag / Remove**: one or more rules triggered (these both go into the same decision queue)

### When human approves (override: agent removed/flagged)
1. The post is added as a **compliant** labeled example on the incorrectly-triggered rule(s)
2. If the agent cited a **community norm violation** (not a specific rule): the post is automatically added as an **acceptable** sample post to Community Settings

### When human removes (override: agent approved)
1. The moderator links the removal to an **existing rule** (or leaves blank if none applies)
2. **If a rule is linked**: override_count on that rule increments (toward the N=3 suggestion threshold)
3. **If no rule applies**: show optional memo + category tag
   - Tags: `spam` | `off-topic` | `hostile tone` | `low quality` | `other`

---

## 4. Override → Suggestion Feedback

### Rule-level (N = 3 overrides)
After 3 remove-overrides linked to the same rule:
- Dashboard shows a warning banner listing affected rules
- On the rule's checklist panel, an inline banner appears: *"X overrides — checklist may need updating. Analyze?"*
- Clicking triggers the existing recompile-with-diff suggestion flow

### Unlinked removes (M = 3)
After 3 unlinked remove-overrides accumulate in the community:
- System automatically clusters them (grouped by tag + content similarity) using AI
- A new rule suggestion appears in the Alignment Dashboard (marked as auto-generated)
- No moderator trigger needed

---

## 5. Rule Suggestion → Adoption

- Suggestions appear in the Alignment Dashboard under "Pending Suggestions"
- Moderator reviews and can **Accept** (creates the rule and queues compilation) or **Dismiss**
- Accepting a new rule suggestion from unlinked overrides links those override examples to the new rule

---

## Implementation Status

| Feature | Status |
|---|---|
| Atmosphere passed to compiler | ✅ Implemented |
| `atmosphere_influenced` field on checklist items | ✅ Implemented |
| Atmosphere badge in ChecklistTree UI | ✅ Implemented |
| Rule editing sandbox (preview before commit) | ✅ Implemented |
| `POST /rules/{id}/preview-recompile` endpoint | ✅ Implemented |
| Auto-add compliant example on approve override | ✅ Already existed |
| Auto-add acceptable sample post on norm-violation approve | ✅ Implemented |
| Optional memo + tag on no-rule remove | ✅ Implemented |
| Override counter per rule (toward N=3) | ✅ Implemented |
| Dashboard badge at N=3 | ✅ Implemented |
| Inline rule banner at N=3 | ✅ Implemented |
| Auto-cluster unlinked removes at M=3 | ✅ Implemented |
