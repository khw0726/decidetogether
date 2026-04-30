"""
Compare three rule compilation approaches:
  1. No context:  compile with rule text only (no community context)
  2. Two-pass:    compile without context first, then adjust using community context
  3. Single-pass: compile with community context embedded in the prompt (current approach)

Usage:
    python scripts/compare_context_approaches.py
    python scripts/compare_context_approaches.py --subreddits AskReddit movies
    python scripts/compare_context_approaches.py --output scripts/context_comparison.json
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.config import Settings
from src.automod.compiler.compiler import RuleCompiler, _COMPILE_TOOL
from src.automod.compiler import prompts
from src.automod.core.reddit_crawler import sample_subreddit_for_context
from scripts.evaluate_output import _make_anthropic_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent

_REDDIT_HEADERS = {
    "User-Agent": "automod-agent-comparison/1.0 (research script)",
}

DEFAULT_SUBREDDITS = ["AskReddit", "movies", "todayilearned", "ClaudeAI", "OUTFITS"]


# ── Reddit data fetching ─────────────────────────────────────────────────────

async def fetch_subreddit_rules(subreddit: str) -> list[dict]:
    """Fetch rules from Reddit's rules.json endpoint."""
    url = f"https://www.reddit.com/r/{subreddit}/about/rules.json"
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        resp = await client.get(url, headers=_REDDIT_HEADERS)
        resp.raise_for_status()
    data = resp.json()
    rules = []
    for r in data.get("rules", []):
        title = r.get("short_name", "").strip()
        text = r.get("description", "").strip()
        if title:
            rules.append({"title": title, "text": text or title})
    return rules


async def fetch_subreddit_about(subreddit: str) -> dict:
    """Fetch subreddit metadata (description, subscribers, etc.)."""
    url = f"https://www.reddit.com/r/{subreddit}/about.json"
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        resp = await client.get(url, headers=_REDDIT_HEADERS)
        resp.raise_for_status()
    data = resp.json().get("data", {})
    return {
        "name": data.get("display_name", subreddit),
        "description": data.get("public_description", ""),
        "subscribers": data.get("subscribers"),
        "over18": data.get("over18", False),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_community(name, platform="reddit"):
    return SimpleNamespace(id=str(uuid.uuid4()), name=name, platform=platform)


def _make_rule(title, text):
    return SimpleNamespace(
        id=str(uuid.uuid4()), title=title, text=text,
        rule_type="actionable", community_id="comparison",
    )


def _items_to_nested_dicts(items):
    """Convert flat ChecklistItem list to nested tree dicts."""
    items_by_id = {}
    for item in items:
        d = {
            "id": item.id,
            "description": item.description,
            "rule_text_anchor": getattr(item, "rule_text_anchor", None),
            "item_type": item.item_type,
            "logic": item.logic if isinstance(item.logic, dict) else {},
            "action": item.action,
            "context_influenced": getattr(item, "context_influenced", False),
            "context_note": getattr(item, "context_note", None),
            "children": [],
        }
        items_by_id[item.id] = d

    roots = []
    for item in items:
        d = items_by_id[item.id]
        parent_id = getattr(item, "parent_id", None)
        if parent_id and parent_id in items_by_id:
            items_by_id[parent_id]["children"].append(d)
        else:
            roots.append(d)
    return roots


# ── No-context compilation (clean system prompt, no context fields) ───────────

# System prompt stripped of all community context calibration instructions
NO_CONTEXT_COMPILE_SYSTEM = """You are an expert community moderation system architect. Your job is to compile a moderator's \
natural-language rule into a precise, structured decision tree that an automated system can execute.

Each node in the tree is a YES/NO question where YES = a potential violation is detected.

Each checklist item must have:
- description: A short, concise yes/no question framed so that YES = violation signal (e.g. "Does the post contain spam keywords?")
- rule_text_anchor: The exact phrase from the rule text this derives from (null if inferred). Keep the exact punctuation and wording.
- item_type: "deterministic" (regex), "structural" (metadata), or "subjective" (LLM judgment)
- logic: Type-specific schema (see below)
- action: "remove", "flag", or "continue"
  - Leaf nodes (no children): use "remove" or "flag" to set the consequence. "continue" is not allowed for leaf nodes.
  - Non-leaf nodes (has children): MUST always be "continue". The verdict comes entirely from the children.
- children: Sub-items evaluated when this item says YES (empty list for leaf nodes)

IMPORTANT: Compile this rule based SOLELY on the rule text itself. Do not infer or assume anything \
about the community's culture, tone, demographics, or moderation style. Produce a generic, \
rule-text-faithful checklist that would work for any community with this rule.

Tree evaluation semantics:
- If an item says NO: no action, children are skipped.
- If an item says YES: apply its action, then evaluate children. Children can only escalate the verdict.
- Non-leaf action is always "continue" — children are the sole decision-makers.
- At every level, the worst action (REMOVE > FLAG > approve) wins across all siblings.
- Frame every question so YES = violation. Avoid "Does the post have X?" (where having X is good).
  Instead write "Does the post lack X?" or restructure using children.
- Use children to refine broad checks: e.g. a deterministic parent (fast gate) with a subjective child
  (confirms it's a real violation, not a false positive). Parent action = "continue" always.

Logic schemas:
- deterministic: {"type": "deterministic", "patterns": [{"regex": "...", "case_sensitive": false}], "match_mode": "any"|"all", "negate": false, "field": "all"|"title"|"body"}
  - field: which part of the post to match against. "title" = title only, "body" = body/selftext only, "all" = title + body (default).
  - negate=false: triggered when pattern IS found
  - negate=true: triggered when pattern is NOT found
- structural: {"type": "structural", "checks": [{"field": "account_age_days"|"post_type"|"flair"|"karma", "operator": "<"|">"|"<="|">="|"=="|"!="|"in", "value": ...}], "match_mode": "all"|"any"}
- subjective: {"type": "subjective", "prompt_template": "...", "rubric": "...", "threshold": 0.7, "examples_to_include": 5}

Keep trees shallow (3 levels max). Generate one violating example and one borderline example per top-level checklist item.

Return ONLY valid JSON with no markdown formatting or code blocks."""

# Tool schema without context_influenced / context_note fields
_NO_CONTEXT_COMPILE_TOOL = {
    "name": "submit_compiled_rule",
    "description": "Submit the compiled checklist tree and examples",
    "input_schema": {
        "type": "object",
        "properties": {
            "checklist_tree": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "rule_text_anchor": {"type": ["string", "null"]},
                        "item_type": {
                            "type": "string",
                            "enum": ["deterministic", "structural", "subjective"],
                        },
                        "logic": {"type": "object"},
                        "action": {
                            "type": "string",
                            "enum": ["remove", "flag", "continue"],
                        },
                        "children": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["description", "item_type", "logic", "action", "children"],
                },
            },
            "examples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "enum": ["compliant", "violating", "borderline"]},
                        "content": {"type": "object"},
                        "relevance_note": {"type": "string"},
                        "related_checklist_item_description": {"type": ["string"]},
                    },
                    "required": ["label", "content", "relevance_note"],
                },
            },
        },
        "required": ["checklist_tree", "examples"],
    },
}


def _build_no_context_compile_prompt(
    rule_title: str,
    rule_text: str,
    community_name: str,
    platform: str,
    other_rules_summary: str,
) -> str:
    return f"""Compile the following rule for the "{community_name}" community on {platform}.

Other rules in this community (for background only — do not duplicate their coverage):
{other_rules_summary or "No other rules yet."}

Rule to compile:
{rule_title}: {rule_text}

Generate a minimal checklist tree — only as many items as the rule genuinely requires. \
For each top-level item, provide one violating example and one borderline example.

Return JSON matching the tool schema."""


# ── Two-pass adjustment prompt ────────────────────────────────────────────────

TWO_PASS_ADJUST_SYSTEM = """You are an expert community moderation system architect. You are given a \
checklist tree that was compiled from a rule's text WITHOUT any community context. Your job is to \
ADJUST this checklist to better fit the specific community, using the community context provided.

You should:
1. Review each checklist item and consider whether the community context changes how it should be calibrated.
2. Adjust subjective thresholds based on the community's stakes and tone.
3. Refine rubric language to match the community's actual communication style.
4. Add or remove items if the community context reveals that certain checks are unnecessary or missing.
5. For every item you modify, set context_influenced=true and write a context_note explaining: \
   "[situational fact] → [calibration decision]".

Keep items you don't change as-is (with context_influenced=false).

Return the COMPLETE adjusted checklist tree (not a diff — return all items including unchanged ones).

Return ONLY valid JSON with no markdown formatting or code blocks."""

_TWO_PASS_ADJUST_TOOL = {
    "name": "submit_adjusted_checklist",
    "description": "Submit the community-context-adjusted checklist tree",
    "input_schema": {
        "type": "object",
        "properties": {
            "checklist_tree": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "rule_text_anchor": {"type": ["string", "null"]},
                        "item_type": {
                            "type": "string",
                            "enum": ["deterministic", "structural", "subjective"],
                        },
                        "logic": {"type": "object"},
                        "action": {
                            "type": "string",
                            "enum": ["remove", "flag", "continue"],
                        },
                        "children": {"type": "array", "items": {"type": "object"}},
                        "context_influenced": {
                            "type": "boolean",
                            "description": "True if community context shaped how this item was adjusted",
                        },
                        "context_note": {
                            "type": ["string", "null"],
                            "description": "Brief explanation: '[situational fact] → [calibration decision]'",
                        },
                    },
                    "required": [
                        "description", "item_type", "logic",
                        "action", "children", "context_influenced",
                    ],
                },
            },
            "adjustment_summary": {
                "type": "string",
                "description": "Brief summary of what was adjusted and why",
            },
        },
        "required": ["checklist_tree", "adjustment_summary"],
    },
}


def _build_two_pass_adjust_prompt(
    rule_title: str,
    rule_text: str,
    community_name: str,
    platform: str,
    base_checklist: list[dict],
    community_context: dict,
) -> str:
    import json as _json

    ctx_lines = []
    for dim, label in [("purpose", "PURPOSE"), ("participants", "PARTICIPANTS"),
                       ("stakes", "STAKES"), ("tone", "TONE")]:
        d = community_context.get(dim, {})
        prose = d.get("prose", "")
        tags = d.get("tags", [])
        if prose:
            ctx_lines.append(f"  {label}:")
            ctx_lines.append(f"    {prose}")
            if tags:
                ctx_lines.append(f"    [Tags: {', '.join(tags)}]")
    context_section = "\n".join(ctx_lines)

    return f"""Adjust this checklist tree for the "{community_name}" community on {platform}.

Rule: {rule_title}
Rule text: {rule_text}

Community context for calibration:
{context_section}

Base checklist (compiled WITHOUT community context):
{_json.dumps(base_checklist, indent=2)}

Review each item. Adjust thresholds, rubric language, patterns, or add/remove items based on the \
community context. For every item you change, set context_influenced=true and explain your reasoning \
in context_note.

Return the COMPLETE adjusted checklist tree as JSON."""


# ── Main compilation logic ────────────────────────────────────────────────────

async def compile_one_rule_three_ways(
    compiler: RuleCompiler,
    rule_dict: dict,
    community: SimpleNamespace,
    other_rules: list[SimpleNamespace],
    community_context: dict,
) -> dict:
    """Compile a single rule using all three approaches."""
    rule = _make_rule(rule_dict["title"], rule_dict["text"])
    result = {
        "rule_title": rule_dict["title"],
        "rule_text": rule_dict["text"],
    }

    # --- Approach 1: No context (clean prompt, no context fields) ---
    logger.info(f"    [1/3] No context...")
    try:
        other_rules_summary = compiler._make_other_rules_summary(
            [r for r in other_rules if r.id != rule.id]
        )
        nc_prompt = _build_no_context_compile_prompt(
            rule_title=rule_dict["title"],
            rule_text=rule_dict["text"],
            community_name=community.name,
            platform=community.platform,
            other_rules_summary=other_rules_summary,
        )
        nc_compiled = await compiler._call_claude(
            NO_CONTEXT_COMPILE_SYSTEM, nc_prompt, tool=_NO_CONTEXT_COMPILE_TOOL,
        )
        items_nc = compiler._parse_flat_items(
            nc_compiled.get("checklist_tree", []), rule.id
        )
        result["no_context"] = {
            "checklist": _items_to_nested_dicts(items_nc),
            "examples": nc_compiled.get("examples", []),
        }
    except Exception as e:
        logger.error(f"    No-context compilation failed: {e}")
        result["no_context"] = {"error": str(e), "checklist": [], "examples": []}

    # --- Approach 2: Two-pass (compile without context, then adjust) ---
    logger.info(f"    [2/3] Two-pass...")
    try:
        base_checklist = result["no_context"].get("checklist", [])
        if base_checklist and not result["no_context"].get("error"):
            adjust_prompt = _build_two_pass_adjust_prompt(
                rule_title=rule_dict["title"],
                rule_text=rule_dict["text"],
                community_name=community.name,
                platform=community.platform,
                base_checklist=base_checklist,
                community_context=community_context,
            )
            adjusted = await compiler._call_claude(
                TWO_PASS_ADJUST_SYSTEM,
                adjust_prompt,
                tool=_TWO_PASS_ADJUST_TOOL,
            )
            result["two_pass"] = {
                "base_checklist": base_checklist,
                "adjusted_checklist": adjusted.get("checklist_tree", []),
                "adjustment_summary": adjusted.get("adjustment_summary", ""),
            }
        else:
            result["two_pass"] = {"error": "Base compilation failed", "adjusted_checklist": []}
    except Exception as e:
        logger.error(f"    Two-pass adjustment failed: {e}")
        result["two_pass"] = {"error": str(e), "adjusted_checklist": []}

    # --- Approach 3: Single-pass with context (current approach) ---
    logger.info(f"    [3/3] Single-pass with context...")
    try:
        items_sp, examples_sp = await compiler.compile_rule(
            rule=rule, community=community, other_rules=other_rules,
            community_context=community_context,
        )
        result["single_pass_with_context"] = {
            "checklist": _items_to_nested_dicts(items_sp),
            "examples": examples_sp,
        }
    except Exception as e:
        logger.error(f"    Single-pass compilation failed: {e}")
        result["single_pass_with_context"] = {"error": str(e), "checklist": [], "examples": []}

    return result


async def process_subreddit(
    compiler: RuleCompiler,
    subreddit: str,
    settings: Settings,
    max_rules: int = 3,
) -> dict:
    """Fetch data and compile rules for one subreddit."""
    logger.info(f"Processing r/{subreddit}...")

    # Fetch subreddit metadata and rules from Reddit
    try:
        about, rules_raw = await asyncio.gather(
            fetch_subreddit_about(subreddit),
            fetch_subreddit_rules(subreddit),
        )
    except Exception as e:
        logger.error(f"  Failed to fetch Reddit data for r/{subreddit}: {e}")
        return {"subreddit": subreddit, "error": f"Failed to fetch from Reddit: {e}"}

    logger.info(f"  Found {len(rules_raw)} rules, {about.get('subscribers', '?')} subscribers")

    if not rules_raw:
        return {"subreddit": subreddit, "error": "No rules found"}

    # Sample posts for context generation
    logger.info(f"  Sampling posts for context generation...")
    try:
        sampled_posts = await sample_subreddit_for_context(
            subreddit=subreddit,
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )
    except Exception as e:
        logger.warning(f"  Post sampling failed: {e}")
        sampled_posts = None

    # Generate community context
    logger.info(f"  Generating community context...")
    rules_summary = "\n".join(f"- {r['title']}: {r['text'][:150]}" for r in rules_raw)
    try:
        community_context = await compiler.generate_community_context(
            community_name=subreddit,
            platform="reddit",
            description=about.get("description", ""),
            rules_summary=rules_summary,
            subscribers=about.get("subscribers"),
            sampled_posts=sampled_posts,
        )
    except Exception as e:
        logger.error(f"  Context generation failed: {e}")
        community_context = {}

    # Triage rules, pick actionable ones
    community = _make_community(subreddit)
    rule_objects = []
    actionable_rules = []
    for r in rules_raw:
        text = f"{r['title']}\n\n{r['text']}" if r['text'] != r['title'] else r['title']
        r["text"] = text
        try:
            triage = await compiler.triage_rule(text, subreddit, "reddit")
            r["triage"] = triage
            if triage["rule_type"] == "actionable":
                actionable_rules.append(r)
        except Exception as e:
            logger.warning(f"  Triage failed for '{r['title']}': {e}")
            r["triage"] = {"rule_type": "unknown", "error": str(e)}

    logger.info(f"  {len(actionable_rules)}/{len(rules_raw)} rules are actionable")

    # Compile up to max_rules actionable rules with all three approaches
    selected = actionable_rules[:max_rules]
    other_rule_objs = [_make_rule(r["title"], r["text"]) for r in rules_raw]

    compiled_rules = []
    for r in selected:
        logger.info(f"  Compiling: {r['title'][:60]}")
        result = await compile_one_rule_three_ways(
            compiler=compiler,
            rule_dict=r,
            community=community,
            other_rules=other_rule_objs,
            community_context=community_context,
        )
        compiled_rules.append(result)

    return {
        "subreddit": subreddit,
        "about": about,
        "community_context": community_context,
        "all_rules": [{"title": r["title"], "triage": r.get("triage", {})} for r in rules_raw],
        "compiled_rules": compiled_rules,
    }


async def main():
    parser = argparse.ArgumentParser(description="Compare context compilation approaches")
    parser.add_argument("--subreddits", nargs="+", default=DEFAULT_SUBREDDITS)
    parser.add_argument("--max-rules", type=int, default=3,
                        help="Max actionable rules to compile per subreddit")
    parser.add_argument("--output", type=Path, default=SCRIPTS_DIR / "context_comparison.json")
    args = parser.parse_args()

    settings = Settings()
    client, model = _make_anthropic_client()
    settings.compiler_model = model
    compiler = RuleCompiler(client, settings)

    all_results = []
    for sub in args.subreddits:
        result = await process_subreddit(compiler, sub, settings, max_rules=args.max_rules)
        all_results.append(result)
        # Save incrementally
        with open(args.output, "w") as f:
            json.dump(all_results, f, indent=2)
        logger.info(f"  Saved progress to {args.output}")

    # Final summary
    print(f"\n{'='*60}")
    print(f"Comparison complete: {len(all_results)} subreddits")
    for r in all_results:
        sub = r["subreddit"]
        if "error" in r:
            print(f"  r/{sub}: ERROR - {r['error']}")
        else:
            n = len(r.get("compiled_rules", []))
            print(f"  r/{sub}: {n} rules compiled x 3 approaches")
    print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
