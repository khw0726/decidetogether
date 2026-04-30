"""Stratified sampler over the AIRules CHI25 dataset for the reference-corpus build.

Source: https://github.com/sTechLab/AIRules — `rules_subreddit_set/rules_subreddit_set.csv`
Schema we rely on:
  name, public_description, subscribers, rules (json), cleaned_rules (text),
  is_topical_question_and_answer_ca_label,
  is_learning_and_perspective_broadening_ca_label,
  is_social_support_ca_label,
  is_content_generation_ca_label,
  is_affiliation_with_an_entity_ca_label

Stratified sample: ~per-label communities (overlap allowed) → ~80 unique total.
Filter: ≥3 rules with ≥1 sentence each.

Output: scripts/reference_corpus.jsonl
  {name, public_description, subscribers, ca_labels, rules: [{short_name, description}, ...]}

License: AIRules is "free for research use" with citation to the CHI25 paper. See repo README.

Usage:
  python scripts/airules_sample.py \\
      --csv path/to/rules_subreddit_set.csv \\
      --per-label 20 \\
      --seed 42 \\
      --output scripts/reference_corpus.jsonl
"""

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


CA_LABELS = [
    "is_topical_question_and_answer_ca_label",
    "is_learning_and_perspective_broadening_ca_label",
    "is_social_support_ca_label",
    "is_content_generation_ca_label",
    "is_affiliation_with_an_entity_ca_label",
]

# Mega-communities with stub rules — exclude unconditionally.
EXCLUDE = {"AskReddit", "funny", "pics", "videos", "memes", "gaming"}


def _truthy(v: str) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"true", "1", "yes", "t"}


def _parse_rules_field(raw: str) -> list[dict]:
    """The `rules` column is a JSON-encoded list of rule objects.

    Reddit's API shape per rule: {short_name, description, ...}. We keep just those
    two fields for downstream use.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out: list[dict] = []
    if isinstance(data, list):
        rules = data
    elif isinstance(data, dict) and "rules" in data:
        rules = data["rules"]
    else:
        return []
    for r in rules:
        if not isinstance(r, dict):
            continue
        sn = (r.get("short_name") or "").strip()
        desc = (r.get("description") or "").strip()
        if sn:
            out.append({"short_name": sn, "description": desc})
    return out


def _rule_is_substantive(r: dict) -> bool:
    """A rule whose description carries actual phrasing, not just a paraphrase of the title."""
    sn = (r.get("short_name") or "").strip()
    desc = (r.get("description") or "").strip()
    if len(desc) < 50:
        return False
    # Description that just restates the title doesn't give us anything to borrow.
    if sn and desc.lower().startswith(sn.lower()) and len(desc) < 1.5 * len(sn):
        return False
    return True


def _quality_score(rules: list[dict]) -> float:
    """Mean description length over substantive rules — higher = more to borrow from."""
    sub = [r for r in rules if _rule_is_substantive(r)]
    if not sub:
        return 0.0
    return sum(len((r.get("description") or "")) for r in sub) / len(sub)


def _passes_quality(rules: list[dict]) -> bool:
    """Keep communities likely to be useful as borrowable rule-text exemplars.

    - 5–15 rules (avoid sparse stub sets and kitchen-sink AutoMod dumps)
    - ≥80% of rules are "substantive" (description ≥50 chars and not a paraphrase of the title)
    - Mean description length over substantive rules ≥120 chars
    """
    if not (5 <= len(rules) <= 15):
        return False
    substantive = [r for r in rules if _rule_is_substantive(r)]
    if not substantive or len(substantive) / len(rules) < 0.8:
        return False
    mean_len = sum(len((r.get("description") or "")) for r in substantive) / len(substantive)
    return mean_len >= 120


def load_rows(csv_path: Path, min_subscribers: int) -> list[dict]:
    csv.field_size_limit(sys.maxsize)
    rows: list[dict] = []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name or name in EXCLUDE:
                continue
            try:
                subs = int(row.get("subscribers") or 0)
            except (ValueError, TypeError):
                subs = 0
            if subs < min_subscribers:
                continue
            rules = _parse_rules_field(row.get("rules", ""))
            if not _passes_quality(rules):
                continue
            ca_labels = [lbl for lbl in CA_LABELS if _truthy(row.get(lbl, ""))]
            if not ca_labels:
                # No archetype label → skip; useless for stratified sampling.
                continue
            rows.append({
                "name": name,
                "public_description": (row.get("public_description") or "").strip(),
                "subscribers": subs,
                "ca_labels": ca_labels,
                "rules": rules,
                "quality_score": _quality_score(rules),
            })
    logger.info(f"Loaded {len(rows)} candidate rows after quality filter")
    return rows


def stratified_sample(rows: list[dict], per_label: int, seed: int) -> list[dict]:
    """Pick `per_label` communities for each CA label, ranked by rule-quality score.

    Within each label's pool we sort by `quality_score` desc (mean substantive
    description length) and take the top fresh communities. Ties break on
    subscribers desc, then name asc for stability.
    """
    del seed  # quality-ranked: no randomness
    by_label: dict[str, list[dict]] = {lbl: [] for lbl in CA_LABELS}
    for r in rows:
        for lbl in r["ca_labels"]:
            by_label[lbl].append(r)

    chosen: dict[str, dict] = {}
    for lbl in CA_LABELS:
        pool = sorted(
            by_label[lbl],
            key=lambda r: (-r["quality_score"], -r["subscribers"], r["name"]),
        )
        added = 0
        for r in pool:
            if added >= per_label:
                break
            if r["name"] in chosen:
                continue
            chosen[r["name"]] = r
            added += 1
        if pool:
            logger.info(
                f"  {lbl}: pool={len(pool)}, top-{added} added "
                f"(best quality_score={pool[0]['quality_score']:.0f})"
            )
        else:
            logger.info(f"  {lbl}: pool=0")

    return list(chosen.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, required=True,
                    help="Path to rules_subreddit_set.csv from sTechLab/AIRules")
    ap.add_argument("--per-label", type=int, default=20,
                    help="Target sample size per CA label (overlap allowed)")
    ap.add_argument("--min-subscribers", type=int, default=10000,
                    help="Drop subreddits with fewer subscribers than this.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output", type=Path,
                    default=Path("scripts/reference_corpus.jsonl"))
    args = ap.parse_args()

    if not args.csv.exists():
        logger.error(f"CSV not found: {args.csv}")
        return 1

    rows = load_rows(args.csv, min_subscribers=args.min_subscribers)
    sample = stratified_sample(rows, per_label=args.per_label, seed=args.seed)
    logger.info(f"Final sample size: {len(sample)} unique communities")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for r in sample:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
