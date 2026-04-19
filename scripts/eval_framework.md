# Evaluation Framework: Research Questions and Measures

## RQ1: Compilation Accuracy
*Does the compiler produce logic that faithfully operationalizes the rule?*

### Measures

| Layer | Type | Metrics | Script |
|-------|------|---------|--------|
| A — Structural | Automated | anchor_in_rule, regex_compiles, non_leaf_action, tree_depth_ok, example_count_ok, rubric_nonempty (pass/fail each) | `evaluate_output.py --no-llm` |
| B — LLM-as-judge | Absolute scoring (1–5) | coverage, logical_correctness, minimality, clarity, anchor_accuracy, example_quality — with chain-of-thought reasoning + level-anchored rubrics | `evaluate_output.py` |
| B' — LLM-as-judge | Pairwise comparison | Same 6 dims, head-to-head with 3 independent judge calls per pair (alternating order) + majority vote for position bias mitigation | `eval_pairwise.py` |
| C — Functional | Automated against ModBench | accuracy, F1 (remove class), FPR, FNR, confusion matrix, bootstrap 95% CIs | `eval_functional.py` |

---

## RQ2: Suggestion/Alignment Accuracy
*Do the alignment functions produce useful, correct suggestions?*

### 2A: `suggest_from_examples()`
**Approach:** Functional accuracy before/after applying suggestions.
1. Evaluate compiled rules on ModBench Set 1 to find false negatives and false positives
2. Feed FN/FP posts as labeled examples to `suggest_from_examples()`
3. Auto-apply returned checklist suggestions to the compiled checklist
4. Evaluate on ModBench Set 2 with both original and updated checklists
5. Compare moderation accuracy before vs. after

**Measures:** Accuracy delta, F1 delta (remove class), FPR/FNR change, paired McNemar's test, count of improved/degraded/unchanged verdicts.

**Data:** Two disjoint ModBench splits from `build_modbench.py --split`.

### 2C: `recompile_with_diff()`
**Approach:** Test whether compiling old rule + applying diff produces functionally equivalent results to compiling the new rule from scratch.
1. Take existing checklist (compiled from original rule text)
2. Edit rule text → apply `recompile_with_diff()` → checklist_diff
3. Compile edited rule text from scratch → checklist_fresh
4. Evaluate both on ModBench entries for that subreddit
5. Compare: diff-based accuracy vs. fresh compilation accuracy

**Measures:** Accuracy of diff-compiled vs. fresh-compiled checklists, agreement rate (how often both give the same verdict), stratified by edit type (minor rewording, add clause, remove clause, major rewrite).

Script: `eval_alignment.py`

---

## RQ3: Checklist Pipeline vs. Direct Prompting
*Does compiling rules into structured logic improve moderation accuracy over just prompting the LLM with raw rule text?*

**Design:** Paired within-subjects. Treatment = checklist pipeline (reuses RQ1 Layer C). Control = single LLM call with raw rule text.

**Measures:** accuracy, F1-remove, FPR, FNR under both conditions. McNemar's test for significance. Per-rule accuracy delta. Stratified by deterministic vs. subjective item ratio.

Script: `eval_ablation_direct.py`

---

## RQ4: Effect of Examples on Moderation
*Does including labeled examples in subjective evaluation improve accuracy?*

**Design:** Paired within-subjects. Condition A = full pipeline. Condition B = `examples=[]`.

**Measures:** Same as RQ3 + confidence calibration curve.

Script: `eval_ablation_examples.py`

---

## RQ5: Cross-LLM Comparison
*Is Claude the best LLM for this pipeline?*

**Design:** Same rules compiled by Claude (Bedrock), Gemini Pro, GPT-5.4. Same pipeline, swap the LLM.

**Measures:**
- Pairwise: head-to-head per dimension with 3 independent judge calls per pair (alternating orders, majority vote). Position bias rate tracked per dimension.

Scripts: `eval_cross_llm.py`, `eval_pairwise.py cross-llm --n-runs 3`, `eval_cross_judge.py`

---

## RQ6: Effect of Community Atmosphere on Compilation
*Does generating community atmosphere context improve compilation quality?*

**Design:** Paired. With atmosphere vs. without atmosphere, then evaluate both against ModBench.

**Measures:** Moderation accuracy of the rules with atmosphere vs. without atmosphere.

Script: `eval_ablation_atmosphere.py`
