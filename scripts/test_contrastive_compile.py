"""
Contrastive compilation test: compile the same rule for two communities with
different context profiles. Check whether the compiler produces meaningfully
different checklists.

This tests the core hypothesis: situational context → different calibration.

Usage:
    python scripts/test_contrastive_compile.py
    python scripts/test_contrastive_compile.py --rule "No NSFW content"
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.automod.compiler.compiler import RuleCompiler
from src.automod.compiler import prompts
from src.automod.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Load extracted contexts for real communities
EXTRACTED_PATH = Path("scripts/community_contexts_extracted.jsonl")


def load_extracted() -> dict[str, dict]:
    result = {}
    for line in open(EXTRACTED_PATH):
        d = json.loads(line)
        result[d["name"]] = d
    return result


# Community pairs with contrasting contexts — using REAL rule text from each sub
CONTRAST_PAIRS = [
    # Pair 1: Civility / respect — both have the rule but very different cultures
    {
        "rule": "Civility / Respect",
        "communities": {
            "gaming": (
                "Bigotry / Incivility / Toxicity",
                "Posts and comments, whether in jest or with malice, that contain racist, sexist, "
                "homophobic, threats, or other toxic content will be removed, regardless of popularity "
                "or relevance. Especially egregious offenses may result in a permanent ban."
            ),
            "mentalhealth": (
                "Be respectful and supportive",
                "All posts and comments on r/mentalhealth must be respectful and supportive. "
                "Do not insult, provoke, harass, or act disrespectfully; racist, discriminatory, "
                "or otherwise unsavory language is also not tolerated."
            ),
        },
        "why": "Gaming tolerates edgy banter and competitive trash-talk; mentalhealth has people in crisis who need protection from dismissiveness",
    },
    # Pair 2: No BS / misinformation — WSB's unique take vs science's rigorous standard
    {
        "rule": "Quality / Misinformation",
        "communities": {
            "wallstreetbets": (
                "No Bullshitting",
                "Don't make shit up, and be responsible giving and taking advice. This includes "
                "talking about things you don't know about. You should listen, not talk. Nobody "
                "wants an ill-informed opinion. Paper trading is not acceptable experience."
            ),
            "science": (
                "Comments dismissing established findings must provide evidence",
                "Comments that dispute well-established scientific concepts (e.g. gravity, "
                "vaccination, anthropogenic climate change, etc.) must be supported with "
                "appropriate peer-reviewed evidence. Links to personal blogs or YouTube are "
                "not sufficient."
            ),
        },
        "why": "WSB's 'no BS' is about financial skin-in-the-game; science's is about peer-reviewed evidence standards",
    },
    # Pair 3: NSFW / content appropriateness — facepalm's broad rule vs mentalhealth's protective rule
    {
        "rule": "No NSFW / Inappropriate Content",
        "communities": {
            "facepalm": (
                "No bigotry, misinformation, offensive content or personal attacks",
                "Submissions and comments in /r/Facepalm must be civil. Hate-speech and bigotry "
                "will result in permanent bans, as will instances of misinformation disinformation, "
                "personal attacks or name calling."
            ),
            "mentalhealth": (
                "Do not post NSFW content",
                "Please ensure that posts and comments on r/mentalhealth are reasonably appropriate "
                "for anyone 13 or older. We specifically prohibit posts and comments that provide "
                "too much detail about things like violence, abuse, or self-harm."
            ),
        },
        "why": "Facepalm is entertainment where offensive content is the subject matter itself; mentalhealth protects minors and people in crisis from graphic content",
    },
]


def context_to_atmosphere(extracted: dict) -> dict:
    """Convert our extracted context into the atmosphere dict the compiler expects."""
    ext = extracted.get("extracted", {})
    purpose = ext.get("purpose", {})
    participants = ext.get("participants", {})
    stakes = ext.get("stakes", {})
    tone = ext.get("tone", {})

    return {
        "tone": tone.get("prose", ""),
        "typical_content": purpose.get("prose", ""),
        "what_belongs": f"Purpose: {purpose.get('prose', '')}",
        "what_doesnt_belong": f"Stakes: {stakes.get('prose', '')}",
        "moderation_style": (
            f"Participants: {participants.get('prose', '')} | "
            f"Tags: purpose={purpose.get('tags', [])}, "
            f"participants={participants.get('tags', [])}, "
            f"stakes={stakes.get('tags', [])}, "
            f"tone={tone.get('tags', [])}"
        ),
    }


async def compile_with_context(
    compiler: RuleCompiler,
    rule_title: str,
    rule_text: str,
    community_name: str,
    atmosphere: dict,
) -> dict:
    """Compile a rule using the prompt builder directly (no DB needed)."""
    user_prompt = prompts.build_compile_prompt(
        rule_title=rule_title,
        rule_text=rule_text,
        community_name=community_name,
        platform="reddit",
        other_rules_summary="",
        community_atmosphere=atmosphere,
    )

    from src.automod.compiler.compiler import _COMPILE_TOOL
    result = await compiler._call_claude(prompts.COMPILE_SYSTEM, user_prompt, tool=_COMPILE_TOOL)
    return result


def print_checklist(items, indent=0):
    """Pretty print checklist tree."""
    for item in items:
        prefix = "  " * indent
        action = item.get("action", "?")
        itype = item.get("item_type", "?")
        atm = " 🌍" if item.get("atmosphere_influenced") else ""
        print(f"{prefix}[{action}/{itype}]{atm} {item['description']}")
        if item.get("atmosphere_note"):
            print(f"{prefix}  ↳ context: {item['atmosphere_note']}")
        for child in item.get("children", []):
            print_checklist([child], indent + 1)


def diff_checklists(result_a, result_b, name_a, name_b):
    """Compare two compiled results and highlight differences."""
    items_a = result_a.get("checklist_tree", [])
    items_b = result_b.get("checklist_tree", [])

    print(f"\n{'─' * 70}")
    print(f"  {name_a}: {len(items_a)} checklist items")
    print(f"{'─' * 70}")
    print_checklist(items_a)

    print(f"\n{'─' * 70}")
    print(f"  {name_b}: {len(items_b)} checklist items")
    print(f"{'─' * 70}")
    print_checklist(items_b)

    # Count atmosphere-influenced items
    def count_atm(items):
        count = 0
        for item in items:
            if item.get("atmosphere_influenced"):
                count += 1
            count += count_atm(item.get("children", []))
        return count

    atm_a = count_atm(items_a)
    atm_b = count_atm(items_b)

    # Collect all descriptions for comparison
    def collect_descs(items):
        descs = []
        for item in items:
            descs.append(item["description"])
            descs.extend(collect_descs(item.get("children", [])))
        return descs

    descs_a = set(collect_descs(items_a))
    descs_b = set(collect_descs(items_b))

    only_a = descs_a - descs_b
    only_b = descs_b - descs_a
    shared = descs_a & descs_b

    print(f"\n{'─' * 70}")
    print(f"  COMPARISON")
    print(f"{'─' * 70}")
    print(f"  Items in {name_a}: {len(items_a)}, context-influenced: {atm_a}")
    print(f"  Items in {name_b}: {len(items_b)}, context-influenced: {atm_b}")
    print(f"  Shared descriptions: {len(shared)}")
    print(f"  Only in {name_a}: {len(only_a)}")
    if only_a:
        for d in list(only_a)[:5]:
            print(f"    + {d[:100]}")
    print(f"  Only in {name_b}: {len(only_b)}")
    if only_b:
        for d in list(only_b)[:5]:
            print(f"    + {d[:100]}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", type=int, default=None,
                        help="Which pair to test (0-2), default: all")
    parser.add_argument("--model", type=str, default=settings.compiler_model)
    args = parser.parse_args()

    extracted = load_extracted()
    from src.automod.config import get_anthropic_client
    client = get_anthropic_client()
    compiler = RuleCompiler(client, settings)

    pairs = CONTRAST_PAIRS if args.pair is None else [CONTRAST_PAIRS[args.pair]]
    all_results = {}

    for pair in pairs:
        print("\n" + "=" * 70)
        print(f"  RULE: \"{pair['rule']}\"")
        print(f"  WHY: {pair['why']}")
        print("=" * 70)

        pair_results = {}
        for community_name, (rule_title, rule_text) in pair["communities"].items():
            if community_name not in extracted:
                print(f"  WARNING: {community_name} not in extracted data, skipping")
                continue

            ctx = extracted[community_name]
            atmosphere = context_to_atmosphere(ctx)

            print(f"\n  r/{community_name} rule: \"{rule_title}\"")
            print(f"    {rule_text[:120]}...")

            logger.info(f"Compiling '{rule_title}' for r/{community_name}...")
            result = await compile_with_context(
                compiler,
                rule_title,
                rule_text,
                community_name,
                atmosphere,
            )
            pair_results[community_name] = result

        names = list(pair_results.keys())
        if len(names) == 2:
            diff_checklists(pair_results[names[0]], pair_results[names[1]], f"r/{names[0]}", f"r/{names[1]}")
        all_results[pair["rule"]] = pair_results

    # Save results
    output_path = Path("scripts/contrastive_compile_results.json")
    output_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    logger.info(f"Results saved to {output_path}")

    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
