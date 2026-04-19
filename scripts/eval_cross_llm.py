"""
Q5: Cross-LLM comparison.

Tests whether Claude is the best LLM for the compilation + moderation pipeline
by swapping the LLM backend while keeping the pipeline constant.

Two evaluation modes:
  1. Compilation: Compile the same rules with each LLM, then measure functional accuracy
  2. Evaluation: Use each LLM as the SubjectiveEvaluator on the same compiled checklists

Supported providers: anthropic (Claude), openai (GPT), google (Gemini)

Usage:
    # Compare compilation quality across LLMs
    python scripts/eval_cross_llm.py compile \\
        --rules scripts/modbench_rules.json \\
        --subreddits AskReddit science

    # Compare subjective evaluation across LLMs (using Claude-compiled checklists)
    python scripts/eval_cross_llm.py evaluate \\
        --modbench scripts/modbench.json \\
        --compiled scripts/compiler_test_output.json

    # Run both
    python scripts/eval_cross_llm.py both --rules ... --modbench ... --compiled ...
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent
DEFAULT_MODBENCH = SCRIPTS_DIR / "modbench.json"
DEFAULT_OUTPUT = SCRIPTS_DIR / "eval_cross_llm_results.json"
MAX_CONCURRENT = 5

# ---------------------------------------------------------------------------
# Model configurations
# ---------------------------------------------------------------------------

MODEL_CONFIGS = {
    "claude-haiku": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "tier": "fast",
    },
    "claude-sonnet": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "tier": "mid",
    },
    "gpt-4o-mini": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "tier": "fast",
    },
    "gpt-4o": {
        "provider": "openai",
        "model": "gpt-4o",
        "tier": "mid",
    },
    "gpt-5.4-mini": {
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "tier": "fast",
    },
    "gpt-5.4-nano": {
        "provider": "openai",
        "model": "gpt-5.4-nano",
        "tier": "fast",
    },
    "gpt-5.4": {
        "provider": "openai",
        "model": "gpt-5.4",
        "tier": "pro",
    },
    "gemini-flash": {
        "provider": "google",
        "model": "gemini-3-flash-preview",
        "tier": "fast",
    },
    "gemini-pro": {
        "provider": "google",
        "model": "gemini-3.1-pro-preview",
        "tier": "mid",
    },
    "claude-sonnet-bedrock": {
        "provider": "bedrock",
        "model": "global.anthropic.claude-sonnet-4-6",
        "tier": "mid",
    },
}

DEFAULT_MODELS = ["claude-haiku", "gpt-4o-mini", "gemini-flash"]


# ---------------------------------------------------------------------------
# Unified LLM client: translates tool_use across providers
# ---------------------------------------------------------------------------

class UnifiedLLMClient:
    """Wraps Anthropic, OpenAI, and Google clients with a common tool_use interface."""

    def __init__(self, provider: str, model: str, api_key: str):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        if self.provider == "anthropic":
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
        elif self.provider == "bedrock":
            import os
            import anthropic
            self._client = anthropic.AsyncAnthropicBedrock(
                aws_access_key=os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY"),
                aws_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_KEY"),
                aws_region=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")),
            )
        elif self.provider == "openai":
            import openai
            self._client = openai.AsyncOpenAI(api_key=self.api_key)
        elif self.provider == "google":
            import google.genai as genai
            self._client = genai.Client(api_key=self.api_key)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

        return self._client

    async def call_with_tool(
        self,
        system: str,
        user: str,
        tool: dict,
        max_tokens: int = 8192,
        temperature: float | None = None,
    ) -> dict:
        """Make an LLM call with forced tool use. Returns the tool input dict."""
        client = self._get_client()

        if self.provider in ("anthropic", "bedrock"):
            return await self._call_anthropic(client, system, user, tool, max_tokens, temperature)
        elif self.provider == "openai":
            return await self._call_openai(client, system, user, tool, max_tokens, temperature)
        elif self.provider == "google":
            return await self._call_google(client, system, user, tool, max_tokens, temperature)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    async def _call_anthropic(self, client, system, user, tool, max_tokens, temperature=None):
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
        )
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = await client.messages.create(**kwargs)
        return response.content[0].input

    async def _call_openai(self, client, system, user, tool, max_tokens, temperature=None):
        """Translate Anthropic tool schema to OpenAI function calling."""
        openai_tool = {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["input_schema"],
            },
        }
        kwargs = dict(
            model=self.model,
            max_completion_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[openai_tool],
            tool_choice={"type": "function", "function": {"name": tool["name"]}},
        )
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = await client.chat.completions.create(**kwargs)
        tool_call = response.choices[0].message.tool_calls[0]
        return json.loads(tool_call.function.arguments)

    async def _call_google(self, client, system, user, tool, max_tokens, temperature=None):
        """Translate Anthropic tool schema to Gemini tool declarations."""
        from google.genai import types

        # Convert JSON schema to Gemini format
        func_decl = types.FunctionDeclaration(
            name=tool["name"],
            description=tool.get("description", ""),
            parameters=_convert_schema_for_gemini(tool["input_schema"]),
        )
        gemini_tool = types.Tool(function_declarations=[func_decl])

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[gemini_tool],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY",
                    allowed_function_names=[tool["name"]],
                )
            ),
            max_output_tokens=max_tokens,
        )
        if temperature is not None:
            config.temperature = temperature

        response = await client.aio.models.generate_content(
            model=self.model,
            contents=user,
            config=config,
        )

        # Extract function call result
        for part in response.candidates[0].content.parts:
            if part.function_call:
                return dict(part.function_call.args)

        raise RuntimeError("No function call in Gemini response")


def _convert_schema_for_gemini(schema: dict) -> dict:
    """Convert JSON Schema to Gemini-compatible format.

    Gemini's FunctionDeclaration doesn't support:
    - Union types like {"type": ["string", "null"]}
    - Some nested constructs
    We recursively clean the schema.
    """
    if not isinstance(schema, dict):
        return schema

    result = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, list):
            # Union type — pick the first non-null type
            non_null = [t for t in v if t != "null"]
            result[k] = non_null[0] if non_null else "string"
            result["nullable"] = "null" in v
        elif k == "properties" and isinstance(v, dict):
            result[k] = {pk: _convert_schema_for_gemini(pv) for pk, pv in v.items()}
        elif k == "items" and isinstance(v, dict):
            result[k] = _convert_schema_for_gemini(v)
        else:
            result[k] = v

    return result


# ---------------------------------------------------------------------------
# Compilation comparison (Q5a)
# ---------------------------------------------------------------------------

async def _compile_single_rule(
    llm_client: UnifiedLLMClient,
    sub: str,
    rule_dict: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Compile a single rule. Designed to be gathered concurrently."""
    from src.automod.compiler.prompts import COMPILE_SYSTEM, build_compile_prompt
    from src.automod.compiler.compiler import _COMPILE_TOOL

    title = rule_dict.get("title", "")
    description = rule_dict.get("description", "")
    rule_text = f"{title}\n\n{description}" if description else title

    user_prompt = build_compile_prompt(
        rule_title=title,
        rule_text=rule_text,
        community_name=sub,
        platform="reddit",
        other_rules_summary="No other rules.",
    )

    async with semaphore:
        try:
            result = await llm_client.call_with_tool(
                system=COMPILE_SYSTEM,
                user=user_prompt,
                tool=_COMPILE_TOOL,
            )
            return {
                "rule_text": rule_text,
                "checklist": result.get("checklist_tree", []),
                "examples": result.get("examples", []),
                "subreddit": sub,
            }
        except Exception as e:
            logger.error(f"Compilation failed for {sub}/{title}: {e}")
            return {
                "rule_text": rule_text,
                "checklist": [],
                "examples": [],
                "subreddit": sub,
                "error": str(e),
            }


async def compile_rules_with_model(
    llm_client: UnifiedLLMClient,
    rules_by_sub: dict[str, list[dict]],
    subreddits: list[str],
    semaphore: asyncio.Semaphore,
) -> dict[str, list[dict]]:
    """Compile all rules for given subreddits using the specified LLM.

    All rules across all subreddits are compiled concurrently (bounded by semaphore).
    Returns {subreddit: [compiled_rule_dicts]}.
    """
    # Build all tasks across all subreddits at once
    tasks = []
    task_subs = []
    for sub in subreddits:
        sub_rules = rules_by_sub.get(sub, [])
        if not sub_rules:
            logger.warning(f"No rules for {sub}, skipping")
            continue
        for rule_dict in sub_rules:
            tasks.append(_compile_single_rule(llm_client, sub, rule_dict, semaphore))
            task_subs.append(sub)

    if not tasks:
        return {}

    results = await asyncio.gather(*tasks)

    # Group results by subreddit (preserving order)
    compiled_by_sub: dict[str, list[dict]] = {}
    for sub, result in zip(task_subs, results):
        compiled_by_sub.setdefault(sub, []).append(result)

    for sub, rules in compiled_by_sub.items():
        logger.info(f"Compiled {len(rules)} rules for {sub}")

    return compiled_by_sub


async def evaluate_compiled_rules(
    compiled_by_sub: dict[str, list[dict]],
    modbench: list[dict],
    settings: Settings,
    use_examples: bool = True,
) -> list[dict]:
    """Evaluate compiled rules against ModBench using the standard pipeline."""
    import anthropic
    from src.automod.core.tree_evaluator import TreeEvaluator
    from src.automod.core.subjective import SubjectiveEvaluator
    from src.automod.core.actions import VERDICT_PRECEDENCE
    from scripts.eval_functional import (
        _flatten_checklist, _make_example_objects, _make_rule_object,
        evaluate_single_rule, compute_metrics,
    )

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    sub_eval = SubjectiveEvaluator(client, settings)
    tree_eval = TreeEvaluator(sub_eval)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    results = []
    for mb in modbench:
        subreddit = mb["subreddit"]
        compiled_rules = compiled_by_sub.get(subreddit, [])
        compiled_rules = [r for r in compiled_rules if r.get("checklist")]

        if not compiled_rules:
            results.append({
                "id": mb["id"],
                "subreddit": subreddit,
                "ground_truth": mb["ground_truth_verdict"],
                "predicted": "error",
                "correct": False,
                "confidence": 0.0,
                "reasoning": "No compiled rules available",
            })
            continue

        async def _eval_rule(cr):
            async with semaphore:
                try:
                    return await evaluate_single_rule(
                        tree_eval, cr, mb["post"], subreddit, use_examples
                    )
                except Exception as e:
                    logger.error(f"Eval failed {mb['id']}: {e}")
                    return None

        raw = await asyncio.gather(*[_eval_rule(cr) for cr in compiled_rules])
        rule_results = [r for r in raw if r is not None]

        if not rule_results:
            final_verdict = "error"
            final_confidence = 0.0
        else:
            final_verdict = "approve"
            final_confidence = 1.0
            for rr in rule_results:
                v = rr.get("verdict", "approve")
                c = rr.get("confidence", 0.5)
                if VERDICT_PRECEDENCE.get(v, 0) > VERDICT_PRECEDENCE.get(final_verdict, 0):
                    final_verdict = v
                    final_confidence = c

        ground_truth = mb["ground_truth_verdict"]
        results.append({
            "id": mb["id"],
            "subreddit": subreddit,
            "ground_truth": ground_truth,
            "predicted": final_verdict,
            "correct": final_verdict == ground_truth,
            "confidence": final_confidence,
            "difficulty": mb.get("difficulty", ""),
            "source": mb.get("source", ""),
        })

    return results


# ---------------------------------------------------------------------------
# Subjective evaluation comparison (Q5b)
# ---------------------------------------------------------------------------

class CrossLLMSubjectiveEvaluator:
    """SubjectiveEvaluator that uses a UnifiedLLMClient instead of Anthropic directly."""

    def __init__(self, llm_client: UnifiedLLMClient, settings: Settings):
        self.llm_client = llm_client
        self.settings = settings

    async def evaluate_batch(self, items, post, community_name, examples):
        """Evaluate subjective items using the cross-LLM client.

        Reuses the same prompt structure as SubjectiveEvaluator but routes
        through the unified client.
        """
        from src.automod.compiler.prompts import build_subjective_eval_prompt

        # Build the same prompt the real evaluator uses
        items_with_rubrics = []
        for item in items:
            logic = item.logic if isinstance(item.logic, dict) else {}
            items_with_rubrics.append({
                "id": item.id,
                "description": item.description,
                "prompt_template": logic.get("prompt_template", item.description),
                "rubric": logic.get("rubric", ""),
                "threshold": logic.get("threshold", 0.5),
            })

        example_dicts = []
        for ex in examples:
            example_dicts.append({
                "label": ex.label,
                "content": ex.content if isinstance(ex.content, dict) else {},
            })

        prompt = build_subjective_eval_prompt(
            post_content=post,
            items_with_rubrics=items_with_rubrics,
            community_name=community_name,
            examples=example_dicts,
        )

        # Use the same tool schema as the real evaluator
        eval_tool = {
            "name": "submit_evaluations",
            "description": "Submit evaluation results for subjective checklist items",
            "input_schema": {
                "type": "object",
                "properties": {
                    "evaluations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_id": {"type": "string"},
                                "triggered": {"type": "boolean"},
                                "confidence": {"type": "number"},
                                "reasoning": {"type": "string"},
                            },
                            "required": ["item_id", "triggered", "confidence", "reasoning"],
                        },
                    },
                },
                "required": ["evaluations"],
            },
        }

        system = (
            "You are a content moderator evaluating posts against community rules. "
            "Evaluate each checklist item and determine if it is triggered by the post."
        )

        try:
            result = await self.llm_client.call_with_tool(
                system=system,
                user=prompt,
                tool=eval_tool,
                max_tokens=4096,
            )

            evals = result.get("evaluations", [])
            # Map back to item IDs
            eval_by_id = {e["item_id"]: e for e in evals}

            results = []
            for item in items:
                if item.id in eval_by_id:
                    e = eval_by_id[item.id]
                    results.append({
                        "item_id": item.id,
                        "triggered": e.get("triggered", False),
                        "confidence": e.get("confidence", 0.5),
                        "reasoning": e.get("reasoning", ""),
                    })
                else:
                    results.append({
                        "item_id": item.id,
                        "triggered": False,
                        "confidence": 0.5,
                        "reasoning": "Item not in LLM response",
                    })

            return results

        except Exception as e:
            logger.error(f"Cross-LLM subjective eval failed: {e}")
            return [
                {
                    "item_id": item.id,
                    "triggered": False,
                    "confidence": 0.5,
                    "reasoning": f"Error: {e}",
                }
                for item in items
            ]


async def evaluate_with_llm_evaluator(
    llm_client: UnifiedLLMClient,
    modbench: list[dict],
    compiled_sources: list[Path],
    settings: Settings,
    use_examples: bool = True,
) -> list[dict]:
    """Run the standard checklist pipeline but with a different LLM for subjective eval."""
    from src.automod.core.tree_evaluator import TreeEvaluator
    from src.automod.core.actions import VERDICT_PRECEDENCE
    from scripts.eval_functional import (
        _flatten_checklist, _make_example_objects, _make_rule_object,
        build_subreddit_rule_index, evaluate_single_rule, _detect_modbench_format,
    )

    cross_eval = CrossLLMSubjectiveEvaluator(llm_client, settings)
    tree_eval = TreeEvaluator(cross_eval)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    fmt = _detect_modbench_format(modbench)
    sub_index = build_subreddit_rule_index(compiled_sources)

    results = []
    for mb in modbench:
        subreddit = mb["subreddit"]
        compiled_rules = sub_index.get(subreddit, [])

        if not compiled_rules:
            results.append({
                "id": mb["id"],
                "subreddit": subreddit,
                "ground_truth": mb["ground_truth_verdict"],
                "predicted": "error",
                "correct": False,
                "confidence": 0.0,
            })
            continue

        async def _eval_rule(cr):
            async with semaphore:
                try:
                    return await evaluate_single_rule(
                        tree_eval, cr, mb["post"], subreddit, use_examples
                    )
                except Exception as e:
                    logger.error(f"Eval failed {mb['id']}: {e}")
                    return None

        raw = await asyncio.gather(*[_eval_rule(cr) for cr in compiled_rules])
        rule_results = [r for r in raw if r is not None]

        if not rule_results:
            final_verdict = "error"
            final_confidence = 0.0
        else:
            final_verdict = "approve"
            final_confidence = 1.0
            for rr in rule_results:
                v = rr.get("verdict", "approve")
                c = rr.get("confidence", 0.5)
                if VERDICT_PRECEDENCE.get(v, 0) > VERDICT_PRECEDENCE.get(final_verdict, 0):
                    final_verdict = v
                    final_confidence = c

        ground_truth = mb["ground_truth_verdict"]
        results.append({
            "id": mb["id"],
            "subreddit": subreddit,
            "ground_truth": ground_truth,
            "predicted": final_verdict,
            "correct": final_verdict == ground_truth,
            "confidence": final_confidence,
            "difficulty": mb.get("difficulty", ""),
            "source": mb.get("source", ""),
        })

    return results


# ---------------------------------------------------------------------------
# Comparison and metrics
# ---------------------------------------------------------------------------

def compute_metrics(results: list[dict]) -> dict:
    """Same metric computation as eval_functional for comparability."""
    valid = [r for r in results if r["predicted"] != "error"]
    n = len(valid)
    if not n:
        return {"n_total": 0, "n_errors": len(results)}

    correct = sum(1 for r in valid if r["correct"])
    metrics: dict[str, Any] = {
        "n_total": n,
        "n_errors": len(results) - n,
        "accuracy": round(correct / n, 4),
    }

    for v in ["approve", "remove", "review"]:
        tp = sum(1 for r in valid if r["ground_truth"] == v and r["predicted"] == v)
        fp = sum(1 for r in valid if r["ground_truth"] != v and r["predicted"] == v)
        fn = sum(1 for r in valid if r["ground_truth"] == v and r["predicted"] != v)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        metrics[f"{v}_precision"] = round(precision, 4)
        metrics[f"{v}_recall"] = round(recall, 4)
        metrics[f"{v}_f1"] = round(f1, 4)

    by_sub: dict[str, list[bool]] = defaultdict(list)
    for r in valid:
        by_sub[r["subreddit"]].append(r["correct"])
    sub_accs = [sum(v) / len(v) for v in by_sub.values()]
    metrics["mean_per_subreddit_accuracy"] = round(sum(sub_accs) / len(sub_accs), 4) if sub_accs else 0

    return metrics


def compare_models(all_results: dict[str, dict], output_path: Path):
    """Print and save cross-model comparison."""
    print(f"\n{'='*80}")
    print(f"Q5: Cross-LLM Comparison")
    print(f"{'='*80}")

    # Header
    model_names = list(all_results.keys())
    header = f"{'Metric':<28}" + "".join(f"{m:>14}" for m in model_names)
    print(header)
    print("-" * len(header))

    compare_keys = [
        ("accuracy", "Accuracy"),
        ("mean_per_subreddit_accuracy", "Per-sub Accuracy"),
        ("remove_f1", "Remove F1"),
        ("remove_precision", "Remove Precision"),
        ("remove_recall", "Remove Recall"),
        ("approve_f1", "Approve F1"),
    ]

    for key, label in compare_keys:
        row = f"  {label:<26}"
        for model_name in model_names:
            m = all_results[model_name].get("metrics", {})
            val = m.get(key, 0)
            row += f"{val:>13.1%}"
        print(row)

    # Quality scores (if available)
    has_quality = any(all_results[m].get("quality") for m in model_names)
    if has_quality:
        print(f"\nCompilation Quality (LLM-judge, 1-5 scale):")
        dims = ["coverage", "logical_correctness", "minimality", "clarity", "anchor_accuracy", "example_quality", "mean"]
        q_header = f"  {'Dimension':<22}" + "".join(f"{m:>14}" for m in model_names)
        print(q_header)
        print("  " + "-" * (22 + 14 * len(model_names)))
        for dim in dims:
            row = f"  {dim:<22}"
            for m in model_names:
                q = all_results[m].get("quality", {}).get("llm_scores", {})
                val = q.get(dim, 0)
                row += f"{val:>14.2f}"
            print(row)

    # Pairwise McNemar's tests
    print(f"\nPairwise McNemar's tests:")
    for i, m1 in enumerate(model_names):
        for m2 in model_names[i+1:]:
            r1 = {r["id"]: r["correct"] for r in all_results[m1].get("results", []) if r["predicted"] != "error"}
            r2 = {r["id"]: r["correct"] for r in all_results[m2].get("results", []) if r["predicted"] != "error"}
            common = set(r1.keys()) & set(r2.keys())
            if common:
                b = sum(1 for i in common if r1[i] and not r2[i])
                c = sum(1 for i in common if not r1[i] and r2[i])
                if (b + c) > 0:
                    chi2 = (abs(b - c) - 1) ** 2 / (b + c)
                    sig = "sig" if chi2 > 3.84 else "n.s."
                    print(f"  {m1} vs {m2}: b={b}, c={c}, chi2={chi2:.2f} ({sig} at p<.05)")
                else:
                    print(f"  {m1} vs {m2}: identical results")

    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults written to {output_path}")


# ---------------------------------------------------------------------------
# API key helpers
# ---------------------------------------------------------------------------

def _get_api_key(provider: str) -> str | None:
    """Try to get API key for a provider from environment or .env file."""
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
    except ImportError:
        pass
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY", Settings().anthropic_api_key or None)
    elif provider == "bedrock":
        return os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY") or None
    elif provider == "openai":
        return os.environ.get("OPENAI_API_KEY")
    elif provider == "google":
        return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Q5: Cross-LLM comparison")
    sub = parser.add_subparsers(dest="mode", help="Evaluation mode")

    # Compile mode
    comp = sub.add_parser("compile", help="Compare compilation quality across LLMs")
    comp.add_argument("--rules", type=Path, required=True,
                      help="modbench_rules.json (subreddit → rules list)")
    comp.add_argument("--subreddits", nargs="+", required=True)
    comp.add_argument("--modbench", type=Path, default=DEFAULT_MODBENCH,
                      help="ModBench dataset for evaluating compiled output")
    comp.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                      choices=list(MODEL_CONFIGS.keys()))
    comp.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    comp.add_argument("--limit", type=int, default=None)
    comp.add_argument("--judge", action="store_true",
                      help="Run LLM-as-a-judge on compiled rules for quality scores")

    # Evaluate mode
    ev = sub.add_parser("evaluate", help="Compare subjective evaluation across LLMs")
    ev.add_argument("--modbench", type=Path, default=DEFAULT_MODBENCH)
    ev.add_argument("--compiled", nargs="*", type=Path, default=None)
    ev.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                    choices=list(MODEL_CONFIGS.keys()))
    ev.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ev.add_argument("--limit", type=int, default=None)

    # Both modes
    both = sub.add_parser("both", help="Run both compilation and evaluation comparisons")
    both.add_argument("--rules", type=Path, required=True)
    both.add_argument("--subreddits", nargs="+", required=True)
    both.add_argument("--modbench", type=Path, default=DEFAULT_MODBENCH)
    both.add_argument("--compiled", nargs="*", type=Path, default=None)
    both.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                      choices=list(MODEL_CONFIGS.keys()))
    both.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    both.add_argument("--limit", type=int, default=None)
    both.add_argument("--judge", action="store_true",
                      help="Run LLM-as-a-judge on compiled rules for quality scores")

    args = parser.parse_args()
    if not args.mode:
        parser.print_help()
        return

    settings = Settings()

    # Validate API keys for requested models
    models_to_run = args.models
    available_models = []
    for model_name in models_to_run:
        cfg = MODEL_CONFIGS[model_name]
        key = _get_api_key(cfg["provider"])
        if key:
            available_models.append(model_name)
        else:
            logger.warning(f"No API key for {cfg['provider']} — skipping {model_name}")

    if not available_models:
        logger.error("No models available (missing API keys)")
        sys.exit(1)

    logger.info(f"Models to evaluate: {available_models}")

    all_results: dict[str, dict] = {}

    # --- Compilation comparison ---
    if args.mode in ("compile", "both"):
        with open(args.rules) as f:
            rules_by_sub = json.load(f)

        with open(args.modbench) as f:
            modbench = json.load(f)
        if args.limit:
            modbench = modbench[:args.limit]

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        # --- Phase 1: Compile all models in parallel ---
        async def _compile_one_model(model_name: str) -> tuple[str, dict[str, list[dict]]]:
            cfg = MODEL_CONFIGS[model_name]
            key = _get_api_key(cfg["provider"])
            llm_client = UnifiedLLMClient(cfg["provider"], cfg["model"], key)

            cache_path = SCRIPTS_DIR / f"compiled_{model_name}.json"
            if cache_path.exists():
                logger.info(f"Loading cached compiled rules from {cache_path}")
                with open(cache_path) as f:
                    compiled_by_sub = json.load(f)
            else:
                logger.info(f"Compiling rules with {model_name}...")
                compiled_by_sub = await compile_rules_with_model(
                    llm_client, rules_by_sub, args.subreddits, semaphore,
                )
                with open(cache_path, "w") as f:
                    json.dump(compiled_by_sub, f, indent=2)
                logger.info(f"Saved compiled rules to {cache_path}")
            return model_name, compiled_by_sub

        compile_results = await asyncio.gather(
            *[_compile_one_model(m) for m in available_models]
        )
        compiled_map = dict(compile_results)

        # --- Phase 2: Evaluate + judge all models in parallel ---
        async def _evaluate_one_model(model_name: str, compiled_by_sub: dict) -> tuple[str, dict]:
            cfg = MODEL_CONFIGS[model_name]

            logger.info(f"Evaluating compiled output from {model_name}...")
            results = await evaluate_compiled_rules(
                compiled_by_sub, modbench, settings,
            )

            metrics = compute_metrics(results)
            entry = {
                "config": {"model": cfg["model"], "provider": cfg["provider"], "mode": "compile"},
                "metrics": metrics,
                "results": results,
            }

            if getattr(args, "judge", False):
                from scripts.evaluate_output import judge_compiled_rules
                logger.info(f"Running LLM-judge on {model_name} compiled rules...")
                quality = await judge_compiled_rules(compiled_by_sub, settings)
                entry["quality"] = quality.get("aggregate", {})
                entry["quality_per_rule"] = quality.get("per_rule", [])

            return model_name, entry

        eval_results = await asyncio.gather(
            *[_evaluate_one_model(m, compiled_map[m]) for m in available_models]
        )
        for model_name, entry in eval_results:
            all_results[f"{model_name}_compile"] = entry
            logger.info(f"{model_name} compilation accuracy: {entry['metrics'].get('accuracy', 0):.1%}")

    # --- Evaluation comparison ---
    if args.mode in ("evaluate", "both"):
        from scripts.eval_functional import DEFAULT_COMPILED_SOURCES

        with open(args.modbench) as f:
            modbench = json.load(f)
        if args.limit:
            modbench = modbench[:args.limit]

        compiled_sources = args.compiled or list(DEFAULT_COMPILED_SOURCES)

        for model_name in available_models:
            cfg = MODEL_CONFIGS[model_name]
            key = _get_api_key(cfg["provider"])
            llm_client = UnifiedLLMClient(cfg["provider"], cfg["model"], key)

            logger.info(f"Evaluating with {model_name} as subjective evaluator...")
            results = await evaluate_with_llm_evaluator(
                llm_client, modbench, compiled_sources, settings,
            )

            metrics = compute_metrics(results)
            all_results[f"{model_name}_evaluate"] = {
                "config": {"model": cfg["model"], "provider": cfg["provider"], "mode": "evaluate"},
                "metrics": metrics,
                "results": results,
            }

            logger.info(f"{model_name} evaluation accuracy: {metrics.get('accuracy', 0):.1%}")

    compare_models(all_results, args.output)


if __name__ == "__main__":
    asyncio.run(main())
