"""
Compile rules from a modbench_rules JSON file into checklist trees.

Usage:
    python scripts/compile_rules.py --rules scripts/modbench_rules_2016_2017.json --subreddits books
    python scripts/compile_rules.py --rules scripts/modbench_rules_2016_2017.json --subreddits books --output scripts/compiled_books.json
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.config import Settings
from src.automod.compiler.compiler import RuleCompiler
from scripts.evaluate_output import _make_anthropic_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent


def _make_community(name, platform="reddit"):
    return SimpleNamespace(id=str(uuid.uuid4()), name=name, platform=platform)


def _make_rule(title, description):
    text = f"{title}\n\n{description}" if description else title
    return SimpleNamespace(
        id=str(uuid.uuid4()), title=title, text=text, rule_type="actionable",
    )


def _items_to_nested_dicts(items):
    """Convert flat ChecklistItem list back to nested tree."""
    items_by_id = {}
    for item in items:
        d = {
            "id": item.id,
            "description": item.description,
            "rule_text_anchor": getattr(item, "rule_text_anchor", None),
            "item_type": item.item_type,
            "logic": item.logic if isinstance(item.logic, dict) else {},
            "action": item.action,
            "atmosphere_influenced": getattr(item, "atmosphere_influenced", False),
            "atmosphere_note": getattr(item, "atmosphere_note", None),
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


async def compile_subreddit(compiler, sub, rules_dicts):
    community = _make_community(sub)
    rule_objects = [_make_rule(r["title"], r.get("description", "")) for r in rules_dicts]

    compiled = []
    for rule_dict, rule_obj in zip(rules_dicts, rule_objects):
        logger.info(f"  Compiling: {rule_dict['title'][:60]}")

        # Triage first
        triage = await compiler.triage_rule(rule_obj.text, sub, "reddit")
        logger.info(f"    Triage: {triage['rule_type']}")

        if triage["rule_type"] != "actionable":
            compiled.append({
                "subreddit": sub,
                "title": rule_dict["title"],
                "description": rule_dict.get("description", ""),
                "rule_text": rule_obj.text,
                "triage": triage,
                "applies_to": triage.get("applies_to", "both"),
                "checklist": [],
                "examples": [],
            })
            continue

        try:
            items, examples = await compiler.compile_rule(
                rule=rule_obj,
                community=community,
                other_rules=[r for r in rule_objects if r.id != rule_obj.id],
            )
            checklist_tree = _items_to_nested_dicts(items)
            compiled.append({
                "subreddit": sub,
                "title": rule_dict["title"],
                "description": rule_dict.get("description", ""),
                "rule_text": rule_obj.text,
                "triage": triage,
                "applies_to": triage.get("applies_to", "both"),
                "checklist": checklist_tree,
                "examples": examples,
            })
            logger.info(f"    {len(items)} checklist items, {len(examples)} examples, applies_to={triage.get('applies_to', 'both')}")
        except Exception as e:
            logger.error(f"    Compilation failed: {e}")
            compiled.append({
                "subreddit": sub,
                "title": rule_dict["title"],
                "description": rule_dict.get("description", ""),
                "rule_text": rule_obj.text,
                "triage": triage,
                "checklist": [],
                "examples": [],
                "error": str(e),
            })

    return compiled


async def main():
    parser = argparse.ArgumentParser(description="Compile rules into checklist trees")
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--subreddits", nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    settings = Settings()

    with open(args.rules) as f:
        all_rules = json.load(f)

    client, model = _make_anthropic_client()
    settings.compiler_model = model
    compiler = RuleCompiler(client, settings)

    all_compiled = []
    for sub in args.subreddits:
        rules = all_rules.get(sub, [])
        if not rules:
            logger.warning(f"No rules for {sub}")
            continue
        logger.info(f"Compiling {len(rules)} rules for r/{sub}...")
        compiled = await compile_subreddit(compiler, sub, rules)
        all_compiled.extend(compiled)

    output_path = args.output or SCRIPTS_DIR / f"compiled_{'_'.join(args.subreddits)}.json"
    with open(output_path, "w") as f:
        json.dump(all_compiled, f, indent=2)

    # Summary
    actionable = [c for c in all_compiled if c["triage"]["rule_type"] == "actionable"]
    print(f"\nCompiled {len(all_compiled)} rules ({len(actionable)} actionable)")
    for c in all_compiled:
        status = f"{len(c['checklist'])} items" if c["checklist"] else c["triage"]["rule_type"]
        print(f"  [{status}] {c['title'][:70]}")
    print(f"\nWrote {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
