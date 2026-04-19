"""
Evaluate a compiler_test_output.json file on two layers:
  1. Automated structural checks (pass/fail)
  2. LLM-as-judge on 5 semantic dimensions (1-5 scores)

When rule_descriptions.json is available, the LLM judge is grounded in the
moderator's actual description rather than doing open-ended quality scoring.

Usage (from repo root):
    python scripts/evaluate_output.py
    python scripts/evaluate_output.py --input path/to/output.json --output path/to/results.csv
    python scripts/evaluate_output.py --no-llm
    python scripts/evaluate_output.py --compare scripts/compiler_test_output.json scripts/compiler_test_output_augmented.json

Output: scripts/eval_results.csv
"""

import argparse
import asyncio
import csv
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_INPUT = Path(__file__).parent / "compiler_test_output.json"
DEFAULT_OUTPUT = Path(__file__).parent / "eval_results.csv"
DESCRIPTIONS_PATH = Path(__file__).parent / "rule_descriptions.json"

MAX_CONCURRENT = 20


def _make_anthropic_client() -> tuple[anthropic.AsyncAnthropic, str]:
    """Create an Anthropic client, preferring Bedrock if available.

    Returns (client, model_id) tuple. Falls back to direct API if Bedrock
    credentials are not set.
    """
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
    except ImportError:
        pass

    aws_key = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY")
    if aws_key:
        aws_secret = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_KEY")
        aws_region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        client = anthropic.AsyncAnthropicBedrock(
            aws_access_key=aws_key,
            aws_secret_key=aws_secret,
            aws_region=aws_region,
        )
        model = "global.anthropic.claude-sonnet-4-6"
        logger.info("Using Bedrock client")
        return client, model

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        try:
            api_key = Settings().anthropic_api_key
        except Exception:
            pass
    if not api_key:
        raise ValueError(
            "No Anthropic credentials found. Set ANTHROPIC_API_KEY or "
            "AWS_BEARER_TOKEN_BEDROCK in .env"
        )
    client = anthropic.AsyncAnthropic(api_key=api_key)
    model = "claude-sonnet-4-6"
    return client, model

_JUDGE_TOOL = {
    "name": "submit_rule_scores",
    "description": "Submit evaluation scores for a compiled rule",
    "input_schema": {
        "type": "object",
        "properties": {
            "coverage_reasoning": {
                "type": "string",
                "description": "List every distinct requirement in the rule text, then state which checklist item(s) address each. Note any requirements with no corresponding item.",
            },
            "coverage": {
                "type": "integer",
                "description": "1-5: Coverage score (see rubric in system prompt).",
            },
            "logical_correctness_reasoning": {
                "type": "string",
                "description": "For each checklist item, verify: (a) the condition correctly implements the rule's intent, (b) the parent-child hierarchy correctly reflects the rule's logical structure, (c) item type (deterministic/structural/subjective) fits the nature of the check. Note any contradictions or misinterpretations.",
            },
            "logical_correctness": {
                "type": "integer",
                "description": "1-5: Logical correctness score (see rubric in system prompt).",
            },
            "minimality_reasoning": {
                "type": "string",
                "description": "Identify any checklist items that are redundant (checking the same thing as another item), unnecessary (not traceable to any rule requirement), or could be merged without loss of fidelity.",
            },
            "minimality": {
                "type": "integer",
                "description": "1-5: Minimality score (see rubric in system prompt).",
            },
            "clarity_reasoning": {
                "type": "string",
                "description": "For each checklist item, assess whether the question/rubric is phrased so that a moderator could answer it without needing to guess the compiler's intent. Note any vague, ambiguous, or jargon-heavy items.",
            },
            "clarity": {
                "type": "integer",
                "description": "1-5: Clarity score (see rubric in system prompt).",
            },
            "anchor_accuracy_reasoning": {
                "type": "string",
                "description": "For each item's rule_text_anchor, check whether it is a verbatim substring of the rule text. Note any paraphrases, invented phrases, or missing anchors.",
            },
            "anchor_accuracy": {
                "type": "integer",
                "description": "1-5: Anchor accuracy score (see rubric in system prompt).",
            },
            "example_quality_reasoning": {
                "type": "string",
                "description": "Assess whether examples include positive, negative, and borderline cases. Check if they are realistic for the subreddit context and test boundary conditions rather than only obvious cases.",
            },
            "example_quality": {
                "type": "integer",
                "description": "1-5: Example quality score (see rubric in system prompt).",
            },
            "notes": {
                "type": "string",
                "description": "Any additional observations not captured by the dimension-specific reasoning fields.",
            },
        },
        "required": [
            "coverage_reasoning", "coverage",
            "logical_correctness_reasoning", "logical_correctness",
            "minimality_reasoning", "minimality",
            "clarity_reasoning", "clarity",
            "anchor_accuracy_reasoning", "anchor_accuracy",
            "example_quality_reasoning", "example_quality",
            "notes",
        ],
    },
}

_JUDGE_SYSTEM = """\
You are an expert evaluator of content moderation rule compilation quality.
You will be given a rule text (as seen by the compiler), its compiled checklist tree,
and generated examples. Optionally you will also see the moderator's full description
of the rule — when present, use it as the authoritative ground truth for coverage.

For each dimension, FIRST write your reasoning in the corresponding reasoning field \
(following the verification procedure described), THEN assign your integer score.

## Scoring scale

  5 = Excellent — no issues found; the tree is production-ready on this dimension.
  4 = Good — one minor issue that would not affect moderation outcomes.
  3 = Adequate — one or two notable gaps that could lead to occasional wrong verdicts.
  2 = Poor — multiple issues or one major flaw that would regularly cause wrong verdicts.
  1 = Failing — the dimension is fundamentally broken; the tree is unusable in this regard.

## Dimensions and level-anchored rubrics

### Coverage
Does the checklist collectively address every distinct requirement in the rule?

Verification procedure: Enumerate each distinct requirement or clause in the rule text. \
For each, identify whether at least one checklist item addresses it.

  5 = Every requirement in the rule maps to at least one checklist item; no gaps.
  4 = All major requirements covered; one minor sub-clause is implicit rather than explicit.
  3 = One distinct requirement has no corresponding item, or a requirement is only \
partially covered (e.g., the rule says "links or images" but only links are checked).
  2 = Two or more requirements are missing from the checklist.
  1 = The checklist addresses fewer than half of the rule's requirements.

### Logical correctness
Does the tree faithfully represent the original rule without contradictions or \
misinterpretations? This includes whether the parent-child hierarchy correctly \
reflects the rule's logical structure, and whether item type classification \
(deterministic / structural / subjective) matches the nature of each condition.

Verification procedure: (a) For each checklist item, verify the condition implements \
the rule's intent — not a looser or stricter version. (b) Check that the parent-child \
hierarchy reflects the rule's logical grouping: related conditions should share a \
parent, independent conditions should be siblings at the top level. (c) Verify item \
types: deterministic items should be decidable by pattern/keyword matching alone; \
structural items by post metadata; subjective items require human or LLM judgment.

  5 = All conditions, hierarchy, and item types are correct.
  4 = One item type is debatable (e.g., a borderline det/sub classification) but \
the condition itself is correct.
  3 = One condition is stricter or looser than the rule states, OR the hierarchy \
misgroups logically related items.
  2 = Multiple conditions diverge from the rule's intent, or hierarchy errors \
would cause systematic wrong verdicts.
  1 = The tree's logic contradicts the rule (e.g., approving what the rule removes).

### Minimality
Is the tree free of unnecessary or duplicate conditions?

Verification procedure: For each checklist item, check whether another item already \
covers the same condition. Check whether any item is not traceable to a specific \
requirement in the rule text. Consider whether items could be merged without loss.

  5 = Every item is necessary and distinct; no redundancy.
  4 = One item is marginally redundant but does not add confusion or evaluation cost.
  3 = Two items check effectively the same condition, or one item checks something \
not in the rule at all.
  2 = Multiple redundant items or items unrelated to the rule inflate the tree.
  1 = The tree is heavily bloated — more than half the items are redundant or irrelevant.

### Clarity
Are the yes/no questions and rubrics phrased so that a human moderator (or LLM) \
could answer them without ambiguity?

Verification procedure: Read each item's question or rubric as if you were a moderator \
seeing it for the first time. Flag any that use undefined jargon, double negatives, \
vague quantifiers ("excessive", "inappropriate" without criteria), or compound questions \
that ask two things at once.

  5 = All items are clear, specific, and unambiguous; a moderator could answer each \
without needing to interpret the compiler's intent.
  4 = One item uses a slightly vague term but the intent is recoverable from context.
  3 = One or two items are ambiguous enough that two reasonable moderators might \
interpret them differently.
  2 = Multiple items are vague or use undefined terms; consistent application would \
be difficult.
  1 = Most items are incomprehensible or so vague as to be unanswerable.

### Anchor accuracy
Do rule_text_anchor values quote exact phrases from the rule text?

Verification procedure: For each item's rule_text_anchor, search for it as a verbatim \
substring in the rule text. Flag any that are paraphrases, invented phrases, or missing.

  5 = Every anchor is a verbatim substring of the rule text.
  4 = One anchor has trivial differences (e.g., whitespace or capitalization) but is \
clearly the right phrase.
  3 = One anchor is a paraphrase rather than a quote, or one item is missing an anchor.
  2 = Multiple anchors are paraphrased or missing.
  1 = Most anchors do not correspond to any phrase in the rule text.

### Example quality
Are the generated examples realistic, diverse, and near the decision boundary?

Verification procedure: Check that examples include at least one positive (violating), \
one negative (non-violating), and ideally one borderline case. Assess whether they are \
realistic for the subreddit context. Check whether borderline examples test specific \
edge cases of the rule rather than being trivially obvious.

  5 = Examples include positive, negative, and borderline cases; they are realistic, \
diverse, and test boundary conditions.
  4 = Good variety but one category (e.g., borderline) is slightly weak or obvious.
  3 = Examples exist for all labels but are all obvious/clear-cut, or borderline cases \
are missing entirely.
  2 = Only one or two labels represented, or examples are unrealistic for the subreddit.
  1 = Fewer than 3 examples total, or examples are unrelated to the rule.
"""

JUDGE_DIMS = ["coverage", "logical_correctness", "minimality", "clarity", "anchor_accuracy", "example_quality"]

# ---------------------------------------------------------------------------
# Layer 2b: Pairwise LLM-as-judge (for comparative evaluation)
# ---------------------------------------------------------------------------

_PAIRWISE_JUDGE_TOOL = {
    "name": "submit_pairwise_scores",
    "description": "Submit pairwise comparison results for two compiled trees",
    "input_schema": {
        "type": "object",
        "properties": {
            "coverage_reasoning": {
                "type": "string",
                "description": "Enumerate the rule's requirements. For each, state which tree (Tree 1, Tree 2, or both) addresses it. Then decide which tree has better overall coverage.",
            },
            "coverage_winner": {
                "type": "string",
                "enum": ["tree_1", "tree_2", "tie"],
                "description": "Which tree has better coverage?",
            },
            "logical_correctness_reasoning": {
                "type": "string",
                "description": "Compare how faithfully each tree represents the rule's logic. Check parent-child hierarchy and item type classifications in both. Note errors in either.",
            },
            "logical_correctness_winner": {
                "type": "string",
                "enum": ["tree_1", "tree_2", "tie"],
                "description": "Which tree has better logical correctness?",
            },
            "minimality_reasoning": {
                "type": "string",
                "description": "Compare redundancy in both trees. Which has more unnecessary or duplicate items?",
            },
            "minimality_winner": {
                "type": "string",
                "enum": ["tree_1", "tree_2", "tie"],
                "description": "Which tree is more minimal?",
            },
            "clarity_reasoning": {
                "type": "string",
                "description": "Compare the clarity of questions/rubrics in both trees. Which has more ambiguous or vague items?",
            },
            "clarity_winner": {
                "type": "string",
                "enum": ["tree_1", "tree_2", "tie"],
                "description": "Which tree has clearer items?",
            },
            "anchor_accuracy_reasoning": {
                "type": "string",
                "description": "Check rule_text_anchor values in both trees against the rule text. Which tree quotes more accurately?",
            },
            "anchor_accuracy_winner": {
                "type": "string",
                "enum": ["tree_1", "tree_2", "tie"],
                "description": "Which tree has better anchor accuracy?",
            },
            "example_quality_reasoning": {
                "type": "string",
                "description": "Compare examples from both trees on realism, diversity, and boundary coverage.",
            },
            "example_quality_winner": {
                "type": "string",
                "enum": ["tree_1", "tree_2", "tie"],
                "description": "Which tree has better examples?",
            },
            "overall_winner": {
                "type": "string",
                "enum": ["tree_1", "tree_2", "tie"],
                "description": "Overall, which tree better operationalizes the rule?",
            },
            "overall_reasoning": {
                "type": "string",
                "description": "Brief justification for the overall winner choice.",
            },
        },
        "required": [
            "coverage_reasoning", "coverage_winner",
            "logical_correctness_reasoning", "logical_correctness_winner",
            "minimality_reasoning", "minimality_winner",
            "clarity_reasoning", "clarity_winner",
            "anchor_accuracy_reasoning", "anchor_accuracy_winner",
            "example_quality_reasoning", "example_quality_winner",
            "overall_winner", "overall_reasoning",
        ],
    },
}

_PAIRWISE_JUDGE_SYSTEM = """\
You are an expert evaluator comparing two compiled checklist trees for the same \
content moderation rule. You will be given the original rule text and two compiled \
outputs (Tree 1 and Tree 2), each containing a checklist tree and generated examples.

For each dimension, reason about BOTH trees, then declare a winner: tree_1, tree_2, \
or tie. A "tie" means the trees are roughly equivalent on that dimension — not that \
you cannot decide.

IMPORTANT: The labels "Tree 1" and "Tree 2" are arbitrary. Do NOT let the ordering \
influence your judgment. Evaluate each tree on its own merits.

## Dimensions

### Coverage
Which tree's checklist items more completely cover the rule's requirements?
Procedure: List each distinct requirement in the rule. For each, check which tree \
addresses it. The tree that covers more requirements wins.

### Logical correctness
Which tree more faithfully represents the rule's logic?
Procedure: Check parent-child hierarchy and item type classifications \
(deterministic/structural/subjective) in both trees. The tree with fewer logical errors wins.

### Minimality
Which tree is leaner without sacrificing coverage?
Procedure: Count redundant or unnecessary items in each tree. The tree with less \
bloat wins. Note: fewer items is not automatically better — only if coverage is equal.

### Clarity
Which tree's questions and rubrics are easier to answer without ambiguity?
Procedure: Read each item as a first-time moderator. The tree with fewer vague, \
jargon-heavy, or compound questions wins.

### Anchor accuracy
Which tree's rule_text_anchor values more accurately quote the rule text?
Procedure: For each anchor, check if it appears verbatim in the rule text. The tree \
with more exact quotes wins.

### Example quality
Which tree's examples are more realistic, diverse, and boundary-testing?
Procedure: Compare example sets on label diversity (positive/negative/borderline), \
realism, and whether they test genuine edge cases.

### Overall
Considering all dimensions together, which tree better operationalizes the rule? \
This is a holistic judgment — not a simple majority vote across dimensions. Some \
dimensions may matter more for a given rule.
"""


# ---------------------------------------------------------------------------
# Layer 1: Automated structural checks
# ---------------------------------------------------------------------------

def _walk_items(items: list[dict], depth: int = 0):
    """Yield (item, depth) for all nodes in the tree."""
    for item in items:
        yield item, depth
        for child in item.get("children", []):
            yield from _walk_items([child], depth + 1)


def structural_checks(rule_text: str, checklist: list[dict], examples: list[dict]) -> dict:
    checks = {
        "anchor_in_rule": True,
        "non_leaf_action": True,
        "regex_compiles": True,
        "tree_depth_ok": True,
        "example_count_ok": True,
        "rubric_nonempty": True,
    }

    label_counts = {"positive": 0, "negative": 0, "borderline": 0}
    for ex in examples:
        label = ex.get("label", "")
        if label in label_counts:
            label_counts[label] += 1
    total_examples = sum(label_counts.values())
    if total_examples < 3 or label_counts["positive"] < 1 or label_counts["negative"] < 1:
        checks["example_count_ok"] = False

    for item, depth in _walk_items(checklist):
        # Anchor check
        anchor = item.get("rule_text_anchor")
        if anchor and anchor not in rule_text:
            checks["anchor_in_rule"] = False

        # Non-leaf action check
        children = item.get("children", [])
        if children and item.get("action") != "continue":
            checks["non_leaf_action"] = False

        # Tree depth
        if depth >= 2:
            checks["tree_depth_ok"] = False

        # Logic checks
        logic = item.get("logic", {})
        item_type = item.get("item_type", "")

        if item_type == "deterministic":
            patterns = logic.get("patterns", [])
            for pat in patterns:
                regex = pat.get("regex", "") if isinstance(pat, dict) else str(pat)
                try:
                    re.compile(regex)
                except re.error:
                    checks["regex_compiles"] = False

        elif item_type == "subjective":
            rubric = logic.get("rubric", "")
            prompt_template = logic.get("prompt_template", "")
            threshold = logic.get("threshold", 0)
            if not rubric or not prompt_template or len(prompt_template) <= 50:
                checks["rubric_nonempty"] = False
            if not (0.4 <= threshold <= 0.95):
                checks["rubric_nonempty"] = False

    return checks


# ---------------------------------------------------------------------------
# Layer 2: LLM-as-judge
# ---------------------------------------------------------------------------

async def llm_judge(
    client: anthropic.AsyncAnthropic,
    model: str,
    rule_text: str,
    checklist: list[dict],
    examples: list[dict],
    description: str | None,
    semaphore: asyncio.Semaphore,
) -> dict:
    user_parts = []

    if description:
        user_parts.append(f"## Moderator's full rule description (ground truth)\n{description}")

    user_parts.append(f"## Rule text (as seen by compiler)\n{rule_text}")
    user_parts.append(f"## Compiled checklist tree\n```json\n{json.dumps(checklist, indent=2)}\n```")
    user_parts.append(f"## Generated examples\n```json\n{json.dumps(examples, indent=2)}\n```")

    user_prompt = "\n\n".join(user_parts)

    async with semaphore:
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=_JUDGE_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[_JUDGE_TOOL],
                tool_choice={"type": "tool", "name": "submit_rule_scores"},
            )
            return response.content[0].input
        except Exception as e:
            logger.error(f"LLM judge failed: {e}")
            return {
                "coverage": 0, "logical_correctness": 0, "minimality": 0,
                "clarity": 0, "anchor_accuracy": 0, "example_quality": 0,
                "notes": f"ERROR: {e}",
            }


# ---------------------------------------------------------------------------
# Evaluation orchestration
# ---------------------------------------------------------------------------

def iter_rules(compiler_output: list[dict]):
    """Yield (subreddit, rule_text, description, triage, checklist, examples) for each rule.

    Handles two formats:
    - Flat (test_compiler_sampled.py): [{subreddit, title, description, triage, checklist, examples}]
    - Nested (test_compiler.py):       [{subreddit, rules: [{rule_text, triage, checklist, examples}]}]
    """
    for entry in compiler_output:
        if "title" in entry:
            # Flat format
            yield (
                entry["subreddit"],
                entry["title"],
                entry.get("description"),
                entry.get("triage") or {},
                entry.get("checklist") or [],
                entry.get("examples") or [],
            )
        else:
            # Nested format
            for rule in entry.get("rules", []):
                yield (
                    entry["subreddit"],
                    rule["rule_text"],
                    None,
                    rule.get("triage") or {},
                    rule.get("checklist") or [],
                    rule.get("examples") or [],
                )


async def evaluate_file(
    input_path: Path,
    output_path: Path,
    use_llm: bool,
    settings: Settings,
) -> list[dict]:
    logger.info(f"Loading {input_path}")
    with open(input_path) as f:
        compiler_output = json.load(f)

    if use_llm:
        client, judge_model = _make_anthropic_client()
    else:
        client, judge_model = None, None
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    rows = []
    judge_tasks = []
    rule_meta = []

    # Layer 1: structural checks (synchronous)
    for subreddit, rule_text, description, triage, checklist, examples in iter_rules(compiler_output):
        rule_type = triage.get("rule_type", "unknown")

        if rule_type != "actionable" or not checklist or "error" in (checklist[0] if checklist else {}):
            continue

        checks = structural_checks(rule_text, checklist, examples)

        rule_meta.append({
            "subreddit": subreddit,
            "rule_text_short": rule_text[:80].replace("\n", " "),
            "rule_type": rule_type,
            "has_description": description is not None,
            **checks,
        })

        if use_llm:
            task = asyncio.create_task(
                llm_judge(client, judge_model, rule_text, checklist, examples, description, semaphore)
            )
            judge_tasks.append(task)
        else:
            judge_tasks.append(None)

    # Layer 2: LLM judge (parallel)
    if use_llm:
        logger.info(f"Running LLM judge on {len(judge_tasks)} rules (concurrent={MAX_CONCURRENT})")
        judge_results = await asyncio.gather(*judge_tasks)
    else:
        judge_results = [None] * len(judge_tasks)

    llm_cols = ["coverage", "logical_correctness", "minimality", "clarity", "anchor_accuracy", "example_quality", "notes"]

    for meta, judge in zip(rule_meta, judge_results):
        row = dict(meta)
        if judge:
            for col in llm_cols:
                row[col] = judge.get(col, "")
        else:
            for col in llm_cols:
                row[col] = ""
        rows.append(row)
        logger.info(
            f"  {meta['subreddit']} | {meta['rule_text_short'][:40]!r} | "
            + " ".join(f"{k}={'OK' if v else 'FAIL'}" for k, v in meta.items() if k in (
                "anchor_in_rule", "non_leaf_action", "regex_compiles"
            ))
            + (f" | cov={judge.get('coverage', '?')}" if judge else "")
        )

    # Write CSV
    if not rows:
        logger.warning("No actionable compiled rules found in input.")
        return rows

    fieldnames = list(rows[0].keys())
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

        # Summary rows
        f.write("\n")
        writer.writerow({k: "" for k in fieldnames} | {"subreddit": "--- SUMMARY ---"})

        struct_cols = ["anchor_in_rule", "non_leaf_action", "regex_compiles",
                       "tree_depth_ok", "example_count_ok", "rubric_nonempty"]

        for group_name, group_rows in [
            ("all", rows),
            ("with_description", [r for r in rows if r.get("has_description")]),
            ("without_description", [r for r in rows if not r.get("has_description")]),
        ]:
            if not group_rows:
                continue
            n = len(group_rows)
            summary = {"subreddit": f"MEAN ({group_name}, n={n})"}
            for col in struct_cols:
                rate = sum(1 for r in group_rows if r.get(col)) / n
                summary[col] = f"{rate:.2f}"
            if use_llm:
                for col in ["coverage", "logical_correctness", "minimality",
                             "clarity", "anchor_accuracy", "example_quality"]:
                    vals = [r[col] for r in group_rows if isinstance(r.get(col), int)]
                    summary[col] = f"{sum(vals)/len(vals):.2f}" if vals else ""
            writer.writerow(summary)

    logger.info(f"Wrote {len(rows)} rows to {output_path}")
    return rows


async def compare_files(
    path_a: Path,
    path_b: Path,
    settings: Settings,
):
    """Evaluate both files and write side-by-side comparison."""
    output_a = path_a.parent / (path_a.stem + "_eval.csv")
    output_b = path_b.parent / (path_b.stem + "_eval.csv")

    logger.info("Evaluating file A...")
    rows_a = await evaluate_file(path_a, output_a, True, settings)
    logger.info("Evaluating file B...")
    rows_b = await evaluate_file(path_b, output_b, True, settings)

    # Print summary diff
    llm_dims = ["coverage", "logical_correctness", "minimality", "clarity", "anchor_accuracy", "example_quality"]
    print(f"\n{'Dimension':<22} {'File A':>8} {'File B':>8} {'Delta':>8}")
    print("-" * 50)
    for dim in llm_dims:
        vals_a = [r[dim] for r in rows_a if isinstance(r.get(dim), int)]
        vals_b = [r[dim] for r in rows_b if isinstance(r.get(dim), int)]
        mean_a = sum(vals_a) / len(vals_a) if vals_a else 0
        mean_b = sum(vals_b) / len(vals_b) if vals_b else 0
        delta = mean_b - mean_a
        sign = "+" if delta >= 0 else ""
        print(f"{dim:<22} {mean_a:>8.2f} {mean_b:>8.2f} {sign}{delta:>7.2f}")

    print(f"\nDetailed results: {output_a}, {output_b}")


async def judge_compiled_rules(
    compiled_by_sub: dict[str, list[dict]],
    settings: Settings,
    descriptions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run LLM-as-a-judge on compiled rules and return per-rule and aggregate scores.

    Args:
        compiled_by_sub: {subreddit: [compiled_rule_dicts]} where each rule has
            rule_text, checklist, examples, subreddit keys.
        settings: Settings with anthropic_api_key and compiler_model.
        descriptions: Optional {rule_text_prefix: moderator_description} for grounding.

    Returns:
        {
            "per_rule": [{subreddit, rule_text_short, structural: {}, llm_scores: {}}, ...],
            "aggregate": {
                "structural": {check_name: pass_rate, ...},
                "llm_scores": {dimension: mean_score, ...},
            },
        }
    """
    client, judge_model = _make_anthropic_client()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    per_rule = []
    judge_tasks = []

    for sub, rules in compiled_by_sub.items():
        for rule_dict in rules:
            checklist = rule_dict.get("checklist", [])
            examples = rule_dict.get("examples", [])
            rule_text = rule_dict.get("rule_text", "")

            if not checklist:
                continue

            checks = structural_checks(rule_text, checklist, examples)

            # Try to find a moderator description
            desc = None
            if descriptions:
                for key, val in descriptions.items():
                    if rule_text.startswith(key):
                        desc = val
                        break

            meta = {
                "subreddit": sub,
                "rule_text_short": rule_text[:80].replace("\n", " "),
                "structural": checks,
            }
            per_rule.append(meta)

            task = asyncio.create_task(
                llm_judge(client, judge_model, rule_text, checklist, examples, desc, semaphore)
            )
            judge_tasks.append((len(per_rule) - 1, task))

    if judge_tasks:
        results = await asyncio.gather(*[t for _, t in judge_tasks])
        for (idx, _), scores in zip(judge_tasks, results):
            per_rule[idx]["llm_scores"] = scores

    # Aggregate
    dims = ["coverage", "logical_correctness", "minimality", "clarity", "anchor_accuracy", "example_quality"]
    struct_keys = ["anchor_in_rule", "non_leaf_action", "regex_compiles", "tree_depth_ok", "example_count_ok", "rubric_nonempty"]

    agg_structural = {}
    for k in struct_keys:
        vals = [r["structural"].get(k, False) for r in per_rule]
        agg_structural[k] = round(sum(vals) / len(vals), 4) if vals else 0

    agg_llm = {}
    for d in dims:
        vals = [r["llm_scores"].get(d, 0) for r in per_rule if "llm_scores" in r and isinstance(r["llm_scores"].get(d), (int, float)) and r["llm_scores"].get(d) > 0]
        agg_llm[d] = round(sum(vals) / len(vals), 4) if vals else 0
    agg_llm["mean"] = round(sum(agg_llm[d] for d in dims) / len(dims), 4) if agg_llm else 0

    return {
        "per_rule": per_rule,
        "aggregate": {
            "structural": agg_structural,
            "llm_scores": agg_llm,
        },
    }


async def main():
    parser = argparse.ArgumentParser(description="Evaluate compiler output quality")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM judge, structural checks only")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("FILE_A", "FILE_B"),
                        help="Compare two output files side-by-side")
    args = parser.parse_args()

    settings = Settings()
    if not args.no_llm:
        try:
            _make_anthropic_client()
        except ValueError as e:
            logger.error(f"{e}. Use --no-llm for structural checks only.")
            sys.exit(1)

    if args.compare:
        await compare_files(args.compare[0], args.compare[1], settings)
    else:
        await evaluate_file(args.input, args.output, not args.no_llm, settings)


if __name__ == "__main__":
    asyncio.run(main())
