# Evaluation Scripts

Scripts for evaluating the AutoMod Agent v2 pipeline. See `eval_framework.md` for research questions and measures.

## Prerequisites

```bash
# From the project root
pip install -e .
# Set API keys in .env
echo "ANTHROPIC_API_KEY=sk-..." >> .env
# Optional for cross-LLM (RQ5):
echo "OPENAI_API_KEY=..." >> .env
echo "GOOGLE_API_KEY=..." >> .env
```

## Data Preparation

### 1. Prepare rules

You need a JSON file mapping subreddit names to their rules:

```json
{
  "AskReddit": [
    {"title": "Rule 1: Be Nice", "description": "No personal attacks..."},
    ...
  ]
}
```

Save as `scripts/modbench_rules.json`.

### 2. Build ModBench dataset

ModBench is the shared test dataset used by most evaluations. It combines removal log data (ground truth = remove) with random pushshift/sqlite3 comments (ground truth = approve).

```bash
# Standard single dataset (used by RQ1, RQ3, RQ4, RQ5, RQ6, RQ2C)
# Defaults: 15 subreddits, 100 removals + 200 approvals each ≈ 4500 entries
python scripts/build_modbench.py real

# Two disjoint splits (needed for RQ2A)
python scripts/build_modbench.py real --split
```

Defaults pull from `modbench_rules_2016_2017.json` (15 subreddits) and `~/Downloads/reddit/subreddits24/` for pushshift archives. Override with `--rules`, `--subreddits`, `--pushshift-dir`.

Outputs:
- `scripts/modbench.json` (single dataset)
- `scripts/modbench_set1.json` + `scripts/modbench_set2.json` (split mode)

Legacy mode (from compiler-generated examples, no external data needed):
```bash
python scripts/build_modbench.py compiler
```

### 3. Compile rules

Compile subreddit rules into checklist trees:

```bash
python scripts/compile_rules.py \
    --rules scripts/modbench_rules.json \
    --subreddits AskReddit science politics relationships
```

Output: `scripts/compiled_<names>.json`

---

## Running Evaluations

### RQ1: Compilation Accuracy

**Layer A+B — Structural checks + LLM-as-judge:**
```bash
# Structural only (no API calls)
python scripts/evaluate_output.py --no-llm

# Full evaluation (structural + LLM judge)
python scripts/evaluate_output.py

# Compare two compiled outputs side by side
python scripts/evaluate_output.py --compare file_a.json file_b.json
```

**Layer B' — Pairwise comparison:**
See RQ5 pairwise section below.

**Layer C — Functional accuracy against ModBench:**
```bash
python scripts/eval_functional.py \
    --modbench scripts/modbench.json \
    --compiled scripts/compiler_test_output.json

# Structural/deterministic only (no LLM)
python scripts/eval_functional.py --no-llm

# Quick test on subset
python scripts/eval_functional.py --limit 20
```

Output: `scripts/eval_functional_results.json`

---

### RQ2: Alignment/Suggestion Accuracy

**RQ2A — suggest_from_examples (accuracy before/after):**

Requires the split ModBench datasets.

```bash
python scripts/eval_alignment.py --mode suggest-from-examples \
    --modbench-set1 scripts/modbench_set1.json \
    --modbench-set2 scripts/modbench_set2.json \
    --compiled scripts/compiler_test_output.json
```

Flow: evaluates set 1 to find FN/FP, generates suggestions, auto-applies them, then compares accuracy on set 2 before vs. after.

**RQ2C — recompile_with_diff (diff vs. fresh compilation):**
```bash
python scripts/eval_alignment.py --mode recompile-diff \
    --modbench scripts/modbench.json \
    --compiled scripts/compiler_test_output.json \
    --n-scenarios 10
```

**Both modes:**
```bash
python scripts/eval_alignment.py --mode all \
    --modbench-set1 scripts/modbench_set1.json \
    --modbench-set2 scripts/modbench_set2.json \
    --modbench scripts/modbench.json \
    --compiled scripts/compiler_test_output.json
```

Output: `scripts/eval_alignment_results.json`

---

### RQ3: Checklist Pipeline vs. Direct Prompting

```bash
# Run direct prompting baseline
python scripts/eval_ablation_direct.py \
    --modbench scripts/modbench.json

# Compare against checklist pipeline (needs eval_functional_results.json)
python scripts/eval_ablation_direct.py \
    --modbench scripts/modbench.json \
    --compare scripts/eval_functional_results.json
```

Output: `scripts/eval_ablation_direct_results.json`

---

### RQ4: Effect of Examples on Moderation

Requires two runs of `eval_functional.py` — one with examples (default) and one without:

```bash
# With examples (standard run, reuse if you already ran RQ1)
python scripts/eval_functional.py \
    --modbench scripts/modbench.json \
    --output scripts/eval_functional_results.json

# Without examples
python scripts/eval_functional.py \
    --modbench scripts/modbench.json \
    --no-examples \
    --output scripts/eval_functional_no_examples_results.json

# Compare
python scripts/eval_ablation_examples.py \
    --with-examples scripts/eval_functional_results.json \
    --without-examples scripts/eval_functional_no_examples_results.json
```

Or let the script generate the without-examples run automatically:
```bash
python scripts/eval_ablation_examples.py
```

Output: `scripts/eval_ablation_examples_results.json`

---

### RQ5: Cross-LLM Comparison

**Step 1 — Compile rules with each LLM:**
```bash
python scripts/eval_cross_llm.py compile \
    --rules scripts/modbench_rules.json \
    --subreddits AskReddit science politics relationships \
    --models claude-sonnet-bedrock gemini-pro gpt-5.4
```

Outputs: `scripts/compiled_claude-sonnet-bedrock.json`, `scripts/compiled_gemini-pro.json`, `scripts/compiled_gpt-5.4.json`

**Step 2 — Pairwise head-to-head comparison (3 runs per pair):**
```bash
# Single judge
python scripts/eval_pairwise.py cross-llm \
    --judge claude-sonnet-bedrock \
    --n-runs 3

# Cross-judge (all 3 judge models)
python scripts/eval_pairwise.py cross-llm \
    --cross-judge \
    --n-runs 3
```

Output: `scripts/eval_pairwise_cross_llm.json`

**Step 3 — Cross-judge scoring (each judge scores each compiler):**
```bash
python scripts/eval_cross_judge.py
```

Output: `scripts/eval_cross_judge_results.json`

**Optional — Functional accuracy per LLM:**
```bash
python scripts/eval_cross_llm.py evaluate \
    --modbench scripts/modbench.json \
    --models claude-sonnet-bedrock gemini-pro gpt-5.4
```

---

### RQ6: Effect of Community Atmosphere

**Option A — Full run (generates atmosphere, compiles both conditions, evaluates):**
```bash
python scripts/eval_ablation_atmosphere.py run \
    --rules scripts/modbench_rules.json \
    --subreddits AskReddit science politics relationships \
    --modbench scripts/modbench.json \
    --save-compiled
```

**Option B — Compare pre-compiled files:**
```bash
python scripts/eval_ablation_atmosphere.py compare \
    --with-atmosphere scripts/compiled_with_atmosphere.json \
    --without-atmosphere scripts/compiled_without_atmosphere.json \
    --modbench scripts/modbench.json
```

**Pairwise quality comparison:**
```bash
python scripts/eval_pairwise.py ablation \
    --judge claude-sonnet \
    --n-runs 3
```

Outputs:
- `scripts/eval_ablation_atmosphere_results.json`
- `scripts/eval_pairwise_ablation.json`

---

## Combined Report

After running evaluations, generate a combined report:

```bash
python scripts/eval_report.py
```

Reads all `*_results.json` files and produces `scripts/eval_report.txt`. Missing evaluations are noted as "[Not yet evaluated]".

---

## Execution Order

Some evaluations depend on outputs from others:

```
1. build_modbench.py          → modbench.json (+ _set1/_set2 with --split)
2. compile_rules.py           → compiled_*.json
3. evaluate_output.py         → eval_results.csv (RQ1 A+B, independent)
4. eval_functional.py         → eval_functional_results.json (RQ1 C)
5. eval_ablation_direct.py    → (RQ3, needs step 4 for comparison)
6. eval_functional.py --no-examples → (RQ4, then eval_ablation_examples.py)
7. eval_alignment.py          → (RQ2, needs steps 1+2)
8. eval_cross_llm.py compile  → (RQ5, needs step 1)
9. eval_pairwise.py           → (RQ5/RQ6, needs step 8 or atmosphere files)
10. eval_ablation_atmosphere.py → (RQ6, needs step 1)
11. eval_report.py             → final report (reads all result files)
```

Steps 3, 5, 6, 7, 8, 10 can run in parallel once their prerequisites are met.

---

## Common Options

| Flag | Available in | Effect |
|------|-------------|--------|
| `--limit N` | Most scripts | Evaluate first N entries only (for quick testing) |
| `--no-llm` | evaluate_output, eval_functional | Skip LLM calls, structural/deterministic only |
| `--judge` | eval_pairwise, eval_ablation_atmosphere | Specify judge model |
| `--cross-judge` | eval_pairwise | Use all 3 judge models |
| `--n-runs N` | eval_pairwise | Independent judge calls per pair (default: 3) |
| `--save-compiled` | eval_ablation_atmosphere | Save compiled rules to JSON |
