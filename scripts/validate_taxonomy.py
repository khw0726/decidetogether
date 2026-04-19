"""
Validate the community context taxonomy.

Checks:
1. Spot-check: show 30 random subs with their assigned categories for manual review
2. "Other" bucket: how much lands in "other" per dimension
3. Co-occurrence: find categories that always appear together (potential merges)
4. Saturation curve: plot categories discovered as function of subs processed
5. Category balance: flag categories that are too rare or too dominant

Usage:
    python scripts/validate_taxonomy.py
    python scripts/validate_taxonomy.py --spot-check 50
"""

import argparse
import collections
import json
import random
from pathlib import Path

CLUSTERED_PATH = Path("scripts/community_contexts_clustered.jsonl")
TAXONOMY_PATH = Path("scripts/context_taxonomy.json")
DIMENSIONS = ["purpose", "participants", "stakes", "tone"]


def load_data():
    data = [json.loads(line) for line in open(CLUSTERED_PATH)]
    taxonomy = json.load(open(TAXONOMY_PATH))
    return data, taxonomy


def check_other_bucket(data):
    """Check how much lands in 'other' per dimension."""
    print("\n" + "=" * 70)
    print("1. 'OTHER' BUCKET ANALYSIS")
    print("=" * 70)
    for dim in DIMENSIONS:
        total = 0
        other_count = 0
        other_subs = []
        for d in data:
            cats = d["clustered"][dim]
            total += len(cats)
            for c in cats:
                if c == "other":
                    other_count += 1
                    other_subs.append(d["name"])
        pct = 100 * other_count / total if total else 0
        status = "OK" if pct < 5 else "WARN" if pct < 15 else "BAD"
        print(f"\n  {dim.upper()}: {other_count}/{total} tags in 'other' ({pct:.1f}%) [{status}]")
        if other_subs:
            # Show what original tags mapped to other
            other_orig = []
            extracted = [json.loads(line) for line in open("scripts/community_contexts_extracted.jsonl")]
            mapping = json.load(open("scripts/context_tag_mapping.json"))
            for d in extracted:
                for tag in d["extracted"][dim]["tags"]:
                    if mapping[dim].get(tag) == "other":
                        other_orig.append(tag)
            other_counter = collections.Counter(other_orig)
            top_other = other_counter.most_common(10)
            if top_other:
                print(f"    Top 'other' tags: {top_other}")


def check_cooccurrence(data):
    """Find categories that frequently co-occur (potential merges)."""
    print("\n" + "=" * 70)
    print("2. CO-OCCURRENCE ANALYSIS (categories that always appear together)")
    print("=" * 70)
    for dim in DIMENSIONS:
        pair_counts = collections.Counter()
        cat_counts = collections.Counter()
        for d in data:
            cats = set(d["clustered"][dim])
            for c in cats:
                cat_counts[c] += 1
            for c1 in cats:
                for c2 in cats:
                    if c1 < c2:
                        pair_counts[(c1, c2)] += 1

        print(f"\n  {dim.upper()}:")
        found = False
        for (c1, c2), count in pair_counts.most_common(50):
            # Jaccard: co-occur / (either appears)
            union = cat_counts[c1] + cat_counts[c2] - count
            jaccard = count / union if union else 0
            # Conditional: P(c2|c1) and P(c1|c2)
            p_c2_given_c1 = count / cat_counts[c1] if cat_counts[c1] else 0
            p_c1_given_c2 = count / cat_counts[c2] if cat_counts[c2] else 0
            if jaccard > 0.3 or max(p_c2_given_c1, p_c1_given_c2) > 0.6:
                found = True
                print(f"    {c1} + {c2}: Jaccard={jaccard:.2f}, "
                      f"P({c2}|{c1})={p_c2_given_c1:.2f}, P({c1}|{c2})={p_c1_given_c2:.2f} "
                      f"(co-occur {count}x)")
        if not found:
            print("    No high co-occurrence pairs found (good — categories are distinct)")


def check_balance(data, taxonomy):
    """Flag categories that are too rare or too dominant."""
    print("\n" + "=" * 70)
    print("3. CATEGORY BALANCE")
    print("=" * 70)
    for dim in DIMENSIONS:
        cat_counts = collections.Counter()
        for d in data:
            cat_counts.update(d["clustered"][dim])
        total = sum(cat_counts.values())
        n_cats = len(taxonomy[dim])

        print(f"\n  {dim.upper()} ({n_cats} categories, {total} assignments):")

        # Rare: < 1% of assignments
        rare = [(c, n) for c, n in cat_counts.items() if n / total < 0.01 and c != "other"]
        if rare:
            print(f"    Rare (<1%): {[(c, n) for c, n in sorted(rare, key=lambda x: x[1])]}")

        # Dominant: > 15% of assignments
        dominant = [(c, n) for c, n in cat_counts.items() if n / total > 0.15]
        if dominant:
            print(f"    Dominant (>15%): {[(c, f'{100*n/total:.1f}%') for c, n in sorted(dominant, key=lambda x: -x[1])]}")

        # Unused categories (in taxonomy but never assigned)
        unused = [c for c in taxonomy[dim] if c not in cat_counts]
        if unused:
            print(f"    Unused categories: {unused}")


def saturation_curve(data):
    """Plot categories discovered as function of subs processed."""
    print("\n" + "=" * 70)
    print("4. SATURATION CURVE (new categories discovered vs subs processed)")
    print("=" * 70)
    for dim in DIMENSIONS:
        seen = set()
        curve = []
        for i, d in enumerate(data):
            for c in d["clustered"][dim]:
                seen.add(c)
            if (i + 1) % 100 == 0 or i == len(data) - 1:
                curve.append((i + 1, len(seen)))

        print(f"\n  {dim.upper()}:")
        for n_subs, n_cats in curve:
            bar = "█" * n_cats + "░" * (30 - n_cats)
            print(f"    {n_subs:>5} subs → {n_cats:>3} categories  {bar}")

        # Check saturation: did it plateau?
        if len(curve) >= 3:
            last_growth = curve[-1][1] - curve[-3][1]
            if last_growth == 0:
                print(f"    ✓ Saturated (no new categories in last 200 subs)")
            elif last_growth <= 2:
                print(f"    ~ Nearly saturated (+{last_growth} in last 200 subs)")
            else:
                print(f"    ✗ Still growing (+{last_growth} in last 200 subs)")


def spot_check(data, n=30):
    """Show random subs with their assigned categories for manual review."""
    print("\n" + "=" * 70)
    print(f"5. SPOT CHECK ({n} random subreddits)")
    print("=" * 70)

    # Load original extractions for prose
    extracted = {
        json.loads(line)["name"]: json.loads(line)
        for line in open("scripts/community_contexts_extracted.jsonl")
    }

    sample = random.sample(data, min(n, len(data)))
    for d in sample:
        name = d["name"]
        subs = d.get("subscribers", 0) or 0
        ext = extracted.get(name, {}).get("extracted", {})

        print(f"\n  r/{name} ({subs:,} subs)")
        for dim in DIMENSIONS:
            cats = d["clustered"][dim]
            prose = ext.get(dim, {}).get("prose", "")
            # Truncate prose
            if len(prose) > 120:
                prose = prose[:120] + "..."
            print(f"    {dim:>12}: {cats}")
            print(f"    {'':>12}  \"{prose}\"")


def main():
    parser = argparse.ArgumentParser(description="Validate community context taxonomy")
    parser.add_argument("--spot-check", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    data, taxonomy = load_data()

    print(f"Loaded {len(data)} clustered subreddits")
    print(f"Taxonomy: {', '.join(f'{dim}={len(cats)}' for dim, cats in taxonomy.items())}")

    check_other_bucket(data)
    check_cooccurrence(data)
    check_balance(data, taxonomy)
    saturation_curve(data)
    spot_check(data, n=args.spot_check)


if __name__ == "__main__":
    main()
