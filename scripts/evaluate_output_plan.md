# Plan: Compiler Prompt Evaluation Harness

## Context

The rule compiler (`src/automod/compiler/compiler.py` + `prompts.py`) converts human-readable subreddit rules into executable checklist trees. A baseline test run exists at `scripts/compiler_test_output.json` (20 subreddits, ~84 compiled rules, seed=43). We want to iterate on the prompts to improve output quality, but need an evaluation harness to measure progress objectively across prompt versions — rather than relying solely on manual inspection.

**Problem**: No ground-truth labels exist for "correct" checklist trees, so we need proxy metrics that correlate with quality.

## Approach

Build `scripts/evaluate_output.py` that scores a `compiler_test_output.json` file on two layers:

1. **Automated structural checks** — deterministic, cheap, objective
2. **LLM-as-judge** — semantic quality on 5 rubric dimensions

Output: `scripts/eval_results.csv` — one row per compiled rule with scores. A summary section at the bottom aggregates per-dimension means and flags the weakest criterion.

## Evaluation Dimensions

### Layer 1: Automated checks (pass/fail per rule)
| Check | How |
|---|---|
| `anchor_in_rule` | Every non-null `rule_text_anchor` is a literal substring of `rule_text` |
| `non_leaf_action` | All non-leaf nodes have `action="continue"` |
| `regex_compiles` | All deterministic patterns compile as valid Python `re` patterns |
| `tree_depth_ok` | Tree depth ≤ 2 levels |
| `example_count_ok` | ≥ 3 examples with at least one "positive" and one "negative" label |
| `rubric_nonempty` | All subjective items have non-empty `rubric`, `prompt_template` > 50 chars, `threshold` in [0.4, 0.95] |

### Layer 2: LLM-as-judge (1–5 per rule, scored by Claude Sonnet)
| Dimension | What it measures |
|---|---|
| `coverage` | Do checklist items collectively address all distinct requirements in the rule text? |
| `logic_specificity` | Are patterns/rubrics specific enough to distinguish violations from edge cases without over-triggering? |
| `item_type_fit` | Is the det/str/sub classification appropriate for each item's actual evaluation mechanism? |
| `example_quality` | Are examples realistic, diverse, and near the decision boundary (not all obvious cases)? |
| `anchor_accuracy` | Do `rule_text_anchor` values quote the rule precisely, not paraphrase? |

LLM judge is called once per compiled rule (not per item), with the rule text, checklist tree, and examples as context. Uses a forced-choice structured output tool to return integer 1–5 scores for all 5 dimensions in one call.

## Critical Files

- **Script to create**: `scripts/evaluate_output.py`
- **Input**: `scripts/compiler_test_output.json` (or any path passed as CLI arg)
- **Output**: `scripts/eval_results.csv`
- **Prompts to iterate on**: `src/automod/compiler/prompts.py` — specifically `COMPILE_SYSTEM`
- **Config**: `src/automod/config.py` — `Settings` (for API key)

## Implementation Details

### CLI interface
```bash
python scripts/evaluate_output.py                          # uses default paths
python scripts/evaluate_output.py --input path/to/file.json --output path/to/results.csv
python scripts/evaluate_output.py --no-llm                # skip LLM judge, structural only
```

### LLM judge tool schema
One tool `submit_rule_scores` that takes `{coverage, logic_specificity, item_type_fit, example_quality, anchor_accuracy}` all as integers 1–5, plus a `notes` string for the weakest dimension. Called once per rule.

### CSV output format
```
subreddit, rule_text_short, rule_type, anchor_in_rule, non_leaf_action, regex_compiles,
tree_depth_ok, example_count_ok, rubric_nonempty, coverage, logic_specificity,
item_type_fit, example_quality, anchor_accuracy, llm_notes
```
Plus a summary row at the bottom with means/pass-rates.

### Iteration workflow
1. Run `python scripts/test_compiler.py` → `compiler_test_output.json`
2. Run `python scripts/evaluate_output.py` → `eval_results.csv`
3. Review: which dimension has the lowest mean? What do the `llm_notes` say?
4. Edit the corresponding section of `COMPILE_SYSTEM` in `prompts.py`
5. Repeat from step 1

### Async execution
LLM judge calls are parallelized across rules (up to 10 concurrent) using `asyncio.gather` with a semaphore, since they're independent. Structural checks are synchronous.

## Verification

- Run with `--no-llm` first to check structural pass rates
- Verify CSV has one row per actionable rule (~84 rows)
- Spot-check: pick a rule with low `coverage` score, read `llm_notes`, verify the critique makes sense
- Confirm summary row appears and means are in range [1.0, 5.0]
