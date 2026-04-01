# Plan: Rule Description Crawl + Description-Grounded Evaluation

## Context

The CSV dataset (`rules_APR-2018-JUN-2024.csv`) contains only rule titles — not the full descriptions that moderators write on Reddit. This makes the current compilation test coarse-grained: the compiler has to infer rule intent from brief titles, and we have no authoritative reference to evaluate against.

Reddit rule descriptions (available via the PRAW API) are the moderator's stated intent in their own words — making them the best available ground truth for evaluation. We want to:
1. **Crawl** descriptions for the 20 test subreddits
2. **Use descriptions as ground truth** to grade title-only compilations (measures the inference gap)
3. **Use descriptions as compiler input** for a gold-standard compilation run (measures the ceiling)
4. **Build a description-grounded LLM judge** that compares checklist items against actual moderator intent

This is more rigorous than open-ended quality scoring because the judge has a concrete reference rather than general criteria.

## Approach: Three Scripts

### 1. `scripts/crawl_descriptions.py`
Fetch rule descriptions for the 20 subreddits using PRAW. Output: `scripts/rule_descriptions.json`.

### 2. `scripts/test_compiler_augmented.py`
Re-run compilation on the same 20 subreddits but with `rule_text = title + "\n\n" + description` as input. Output: `scripts/compiler_test_output_augmented.json`. (Reuses `test_compiler.py` logic.)

### 3. `scripts/evaluate_output.py`
Score any `compiler_test_output.json` against `rule_descriptions.json`. Output: `scripts/eval_results.csv`.

## Critical Files

- **Scripts to create**: `scripts/crawl_descriptions.py`, `scripts/test_compiler_augmented.py`, `scripts/evaluate_output.py`
- **Existing baseline**: `scripts/compiler_test_output.json` (title-only, seed=43)
- **Existing test script**: `scripts/test_compiler.py` — reuse `load_latest_rulesets`, `make_rule`, `make_community`, `process_subreddit`
- **Prompts to iterate on**: `src/automod/compiler/prompts.py` — `COMPILE_SYSTEM`, `build_compile_prompt`
- **Config**: `src/automod/config.py` — `Settings`

## Implementation Details

### Script 1: `crawl_descriptions.py`

Uses PRAW (`praw` package). Requires `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` in `.env`.

```python
import praw, json
from src.automod.config import Settings

# For each subreddit in the 20-subreddit list (hardcoded or read from compiler_test_output.json):
sub = reddit.subreddit(name)
rules = [{"short_name": r.short_name, "description": r.description} for r in sub.rules]
```

Output format (`rule_descriptions.json`):
```json
{
  "Boxing": [
    {"short_name": "Spreading misinformation", "description": "Do not spread..."},
    ...
  ],
  ...
}
```

**Matching strategy**: Join by fuzzy title match (rule title from CSV ≈ `short_name` from Reddit). Use `difflib.get_close_matches` with cutoff=0.6. Record unmatched rules so they're skipped in evaluation.

**Filter**: Skip descriptions where `description` is empty or near-identical to `short_name` (len < 80 chars or similarity > 0.85) — these add no signal.

### Script 2: `test_compiler_augmented.py`

Thin wrapper over `test_compiler.py` logic:
- Loads `rule_descriptions.json`
- For each rule, if a matched description exists: `rule_text = f"{title}\n\n{description}"`
- Otherwise falls back to title-only
- Same seed, same subreddits
- Writes `compiler_test_output_augmented.json`

### Script 3: `evaluate_output.py`

**Layer 1 — Automated structural checks (pass/fail):**
| Check | Logic |
|---|---|
| `anchor_in_rule` | Every non-null `rule_text_anchor` is a literal substring of `rule_text` |
| `non_leaf_action` | All non-leaf nodes have `action="continue"` |
| `regex_compiles` | All deterministic patterns are valid Python `re` patterns |
| `tree_depth_ok` | Tree depth ≤ 2 levels |
| `example_count_ok` | ≥ 3 examples with ≥1 positive and ≥1 negative label |
| `rubric_nonempty` | Subjective items: non-empty rubric, `prompt_template` > 50 chars, threshold in [0.4, 0.95] |

**Layer 2 — Description-grounded LLM judge (1–5 per rule):**

When a description is available, the judge prompt includes it as reference:
> "The moderator's full description of this rule is: [description]. Given this, evaluate the checklist..."

| Dimension | What it measures |
|---|---|
| `coverage` | Do checklist items cover all distinct criteria mentioned in the description? |
| `logic_specificity` | Are patterns/rubrics specific enough to avoid false positives given the description's carve-outs? |
| `item_type_fit` | Is det/str/sub classification appropriate for each item? |
| `example_quality` | Are examples realistic, diverse, near the decision boundary? |
| `anchor_accuracy` | Do `rule_text_anchor` values quote the input rule precisely? |

Tool: `submit_rule_scores` — returns integers 1–5 for all dimensions + `notes` string (required for any dimension ≤ 3).

**CLI:**
```bash
python scripts/evaluate_output.py                                      # title-only baseline
python scripts/evaluate_output.py --input compiler_test_output_augmented.json  # augmented
python scripts/evaluate_output.py --no-llm                            # structural only
python scripts/evaluate_output.py --compare baseline.json augmented.json  # side-by-side diff
```

**CSV output:**
```
subreddit, rule_text_short, has_description, anchor_in_rule, non_leaf_action,
regex_compiles, tree_depth_ok, example_count_ok, rubric_nonempty,
coverage, logic_specificity, item_type_fit, example_quality, anchor_accuracy, llm_notes
```
Plus summary rows: mean per dimension for rules with description, and for rules without.

**Async:** LLM judge calls parallelized across rules (semaphore=10).

## Iteration Workflow

```
crawl_descriptions.py          → rule_descriptions.json          (once)
test_compiler.py                → compiler_test_output.json       (title-only baseline)
test_compiler_augmented.py      → compiler_test_output_augmented.json
evaluate_output.py --compare    → eval_results.csv                (baseline vs augmented)

→ identify weakest dimension from summary rows
→ edit COMPILE_SYSTEM in prompts.py
→ re-run test_compiler.py + evaluate_output.py
→ compare new eval_results.csv to previous
```

The `coverage` score gap between title-only and augmented runs quantifies **how much implicit rule intent the compiler currently fails to infer** — and is the primary target for prompt improvement.

## Verification

- `crawl_descriptions.py`: check match rate (expect >70% of rules matched)
- `evaluate_output.py --no-llm`: all structural checks should pass at >95%
- Compare runs: augmented should score higher on `coverage` than title-only
- Spot-check: pick a low-`coverage` title-only rule, read `llm_notes`, verify the critique cites something in the description that the checklist missed
