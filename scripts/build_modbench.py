"""
Build the ModBench evaluation dataset from real moderation data.

Combines three data sources:
  1. Removal log (reddit-removal-log.csv) — removed comments as ground_truth=remove
  2. Pushshift archives (*_comments.zst) — random comments as ground_truth=approve
  3. Rules file (rules.json) — subreddit rules from the same time period

The rules file should map subreddit names to their rule sets:
  {
    "AskReddit": [
      {"title": "Rule 1: ...", "description": "..."},
      ...
    ],
    ...
  }

Usage:
    # Build from real data
    python scripts/build_modbench.py \\
        --removal-log ../moderated_comment_dataset/reddit-removal-log.csv \\
        --pushshift-dir .. \\
        --rules scripts/modbench_rules.json \\
        --subreddits AskReddit science politics relationships \\
        --output scripts/modbench.json

    # Build from compiler outputs only (legacy mode)
    python scripts/build_modbench.py --compiler-sources scripts/compiler_test_output.json
"""

import argparse
import csv
import json
import random
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
DEFAULT_OUTPUT = SCRIPTS_DIR / "modbench.json"
DEFAULT_REMOVAL_LOG = Path(__file__).parent.parent.parent / "moderated_comment_dataset" / "reddit-removal-log.csv"
DEFAULT_PUSHSHIFT_DIR = Path.home() / "Downloads" / "reddit" / "subreddits24"
DEFAULT_RULES = Path(__file__).parent / "modbench_rules_2016_2017.json"
DEFAULT_SUBREDDITS = [
    "AskReddit", "science", "AskHistorians", "explainlikeimfive",
    "relationships", "legaladvice", "personalfinance", "changemyview",
    "news", "depression", "books", "hiphopheads", "asoiaf", "anime", "space",
]

# Removal log time range: May 2016 - March 2017
# Filter pushshift comments to same window
TIME_RANGE_START = 1462060800  # 2016-05-01 UTC
TIME_RANGE_END = 1490918400    # 2017-03-31 UTC


def _comment_to_post(comment: dict, subreddit: str) -> dict:
    """Convert a pushshift/removal-log comment to PostContent format."""
    body = comment.get("body", "")
    author = comment.get("author", "unknown")
    created = comment.get("created_utc", 0)
    account_age = comment.get("account_age_days")  # may not be available

    return {
        "id": comment.get("id", ""),
        "platform": "reddit",
        "author": {
            "username": author,
            "account_age_days": account_age or 365,  # default if unknown
            "platform_metadata": {
                "karma": comment.get("score", 0),
            },
        },
        "content": {
            "title": comment.get("title", ""),
            "body": body,
            "media": [],
            "links": [],
        },
        "context": {
            "channel": f"r/{subreddit}",
            "thread_id": comment.get("link_id", ""),
            "parent_post_id": comment.get("parent_id", ""),
            "post_type": "comment",
            "flair": None,
            "platform_metadata": {},
        },
        "timestamp": datetime.utcfromtimestamp(int(created)).isoformat() if created else None,
    }


# ---------------------------------------------------------------------------
# Source 1: Removal log
# ---------------------------------------------------------------------------

def load_removals(
    removal_log_path: Path,
    subreddits: set[str],
    max_per_sub: int,
) -> dict[str, list[dict]]:
    """Load removed comments from the removal log, grouped by subreddit."""
    # Case-insensitive matching
    sub_lower_map = {s.lower(): s for s in subreddits}
    by_sub: dict[str, list[dict]] = defaultdict(list)

    print(f"Reading removal log: {removal_log_path}")
    with open(removal_log_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader)
        body_idx = header.index("body")
        sub_idx = header.index("subreddit")

        for row in reader:
            if len(row) < 2:
                continue
            sub = row[sub_idx]
            canonical = sub_lower_map.get(sub.lower())
            if not canonical:
                continue
            body = row[body_idx]
            # Skip very short or deleted comments
            if len(body) < 20 or body in ("[deleted]", "[removed]"):
                continue
            by_sub[canonical].append({
                "body": body,
                "author": "removed_user",
                "id": f"removal-{len(by_sub[canonical])}",
                "created_utc": 0,
            })

    # Sample down to max_per_sub
    for sub in by_sub:
        if len(by_sub[sub]) > max_per_sub:
            by_sub[sub] = random.sample(by_sub[sub], max_per_sub)

    return dict(by_sub)


# ---------------------------------------------------------------------------
# Source 2: Pushshift archives
# ---------------------------------------------------------------------------

def load_pushshift_comments(
    pushshift_dir: Path,
    subreddit: str,
    max_comments: int,
    time_start: int = TIME_RANGE_START,
    time_end: int = TIME_RANGE_END,
) -> list[dict]:
    """Stream comments from a pushshift .zst archive for one subreddit.

    Looks for {pushshift_dir}/{subreddit}_comments.zst.
    Filters to the specified time range and samples randomly.
    """
    zst_path = pushshift_dir / f"{subreddit}_comments.zst"
    if not zst_path.exists():
        # Try case-insensitive
        for p in pushshift_dir.glob("*_comments.zst"):
            if p.stem.rsplit("_comments", 1)[0].lower() == subreddit.lower():
                zst_path = p
                break
        if not zst_path.exists():
            print(f"  No pushshift archive found for {subreddit}")
            return []

    print(f"  Reading {zst_path.name}...")

    # Stream through zstd and collect candidates
    candidates = []
    try:
        proc = subprocess.Popen(
            ["zstd", "-d", "-c", str(zst_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        for line_bytes in proc.stdout:
            try:
                comment = json.loads(line_bytes)
            except json.JSONDecodeError:
                continue

            created = int(comment.get("created_utc", 0))
            body = comment.get("body", "")
            author = comment.get("author", "")

            # Filter
            if time_start and created < time_start:
                continue
            if time_end and created > time_end:
                continue
            if len(body) < 20:
                continue
            if body in ("[deleted]", "[removed]"):
                continue
            if author in ("[deleted]", "AutoModerator"):
                continue

            candidates.append(comment)

            # Reservoir sampling: keep going but cap memory
            if len(candidates) > max_comments * 10:
                candidates = random.sample(candidates, max_comments * 5)

        proc.wait()
    except FileNotFoundError:
        print(f"  zstd not found — install zstd to read .zst archives")
        return []

    if len(candidates) > max_comments:
        candidates = random.sample(candidates, max_comments)

    return candidates


# ---------------------------------------------------------------------------
# Source 2b: SQLite3 submission archives
# ---------------------------------------------------------------------------

def load_sqlite3_submissions(
    data_dir: Path,
    subreddit: str,
    max_posts: int,
    time_start: int = TIME_RANGE_START,
    time_end: int = TIME_RANGE_END,
) -> list[dict]:
    """Load submissions from a {subreddit}_submissions.sqlite3 file.

    Returns dicts with the same keys as pushshift comments (body, author,
    id, created_utc, score) so they can be used interchangeably.
    """
    import sqlite3

    db_path = data_dir / f"{subreddit}_submissions.sqlite3"
    if not db_path.exists():
        # Try case-insensitive
        for p in data_dir.glob("*_submissions.sqlite3"):
            if p.stem.rsplit("_submissions", 1)[0].lower() == subreddit.lower():
                db_path = p
                break
        if not db_path.exists():
            return []

    print(f"  Reading {db_path.name}...")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        """SELECT id, author, title, selftext, score, created_utc
           FROM submissions
           WHERE created_utc BETWEEN ? AND ?
             AND length(selftext) > 20
             AND selftext != '[deleted]'
             AND selftext != '[removed]'
             AND author != '[deleted]'
             AND author != 'AutoModerator'
           ORDER BY RANDOM()
           LIMIT ?""",
        (time_start, time_end, max_posts),
    )

    candidates = []
    for row in cursor:
        # Combine title + selftext as "body" for compatibility with _comment_to_post
        title = row["title"] or ""
        selftext = row["selftext"] or ""
        candidates.append({
            "id": row["id"],
            "author": row["author"] or "unknown",
            "body": selftext,
            "title": title,
            "score": row["score"] or 0,
            "created_utc": row["created_utc"] or 0,
        })

    conn.close()
    print(f"  Loaded {len(candidates)} submissions from sqlite3")
    return candidates


# ---------------------------------------------------------------------------
# Source 3: Rules file
# ---------------------------------------------------------------------------

def load_rules(rules_path: Path) -> dict[str, list[dict]]:
    """Load subreddit rules.

    Expected format:
    {
        "SubredditName": [
            {"title": "Rule 1", "description": "Full description..."},
            ...
        ]
    }
    """
    with open(rules_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Build dataset
# ---------------------------------------------------------------------------

def _build_entries(
    removals: dict[str, list[dict]],
    approvals: dict[str, list[dict]],
    rules_by_sub: dict[str, list[dict]],
    valid_subs: list[str],
    id_offset: int = 0,
) -> list[dict]:
    """Build ModBench entries from pre-loaded removals and approvals."""
    entries = []
    counter = id_offset

    for sub in valid_subs:
        sub_rules = rules_by_sub[sub]

        for comment in removals.get(sub, []):
            counter += 1
            entries.append({
                "id": f"mb-{counter:05d}",
                "subreddit": sub,
                "rules": sub_rules,
                "post": _comment_to_post(comment, sub),
                "ground_truth_verdict": "remove",
                "ground_truth_notes": "From removal log — actual moderator removal",
                "difficulty": "unknown",
                "source": "removal_log",
            })

        for comment in approvals.get(sub, []):
            counter += 1
            is_submission = "title" in comment and comment["title"]
            entries.append({
                "id": f"mb-{counter:05d}",
                "subreddit": sub,
                "rules": sub_rules,
                "post": _comment_to_post(comment, sub),
                "ground_truth_verdict": "approve",
                "ground_truth_notes": "Random post — assumed compliant",
                "difficulty": "unknown",
                "source": "sqlite3_submission" if is_submission else "pushshift_random",
            })

    random.shuffle(entries)
    return entries


def _print_summary(entries: list[dict], label: str = "ModBench dataset"):
    by_verdict = defaultdict(int)
    by_source = defaultdict(int)
    by_sub = defaultdict(int)
    for e in entries:
        by_verdict[e["ground_truth_verdict"]] += 1
        by_source[e["source"]] += 1
        by_sub[e["subreddit"]] += 1

    print(f"\n{label}: {len(entries)} entries")
    print(f"  By verdict: {dict(by_verdict)}")
    print(f"  By source:  {dict(by_source)}")
    print(f"  By subreddit:")
    for sub, n in sorted(by_sub.items(), key=lambda x: -x[1]):
        print(f"    {sub}: {n}")


def _split_dict_lists(
    data: dict[str, list],
    ratio: float,
) -> tuple[dict[str, list], dict[str, list]]:
    """Split each list in a dict into two parts by ratio."""
    part_a = {}
    part_b = {}
    for key, items in data.items():
        split_idx = max(1, int(len(items) * ratio))
        random.shuffle(items)
        part_a[key] = items[:split_idx]
        part_b[key] = items[split_idx:]
    return part_a, part_b


def build_modbench_real(
    removal_log_path: Path,
    pushshift_dir: Path,
    rules_path: Path,
    subreddits: list[str],
    removals_per_sub: int,
    approvals_per_sub: int,
    output_path: Path,
    seed: int,
    split: bool = False,
    split_ratio: float = 0.5,
):
    random.seed(seed)

    rules_by_sub = load_rules(rules_path)

    # Validate requested subreddits have rules
    valid_subs = []
    for sub in subreddits:
        if sub not in rules_by_sub:
            print(f"  Warning: no rules for {sub}, skipping")
        else:
            valid_subs.append(sub)

    if not valid_subs:
        print("No valid subreddits with rules. Exiting.")
        sys.exit(1)

    # Load removals
    removals = load_removals(removal_log_path, set(valid_subs), removals_per_sub)

    # Load approvals: try pushshift .zst first, then sqlite3 submissions
    approvals: dict[str, list[dict]] = {}
    for sub in valid_subs:
        comments = load_pushshift_comments(pushshift_dir, sub, approvals_per_sub)
        if not comments:
            comments = load_sqlite3_submissions(pushshift_dir, sub, approvals_per_sub)
        if comments:
            approvals[sub] = comments

    if split:
        # Split into two disjoint sets for RQ2A evaluation
        removals_a, removals_b = _split_dict_lists(removals, split_ratio)
        approvals_a, approvals_b = _split_dict_lists(approvals, split_ratio)

        entries_a = _build_entries(removals_a, approvals_a, rules_by_sub, valid_subs, id_offset=0)
        entries_b = _build_entries(removals_b, approvals_b, rules_by_sub, valid_subs, id_offset=len(entries_a))

        stem = output_path.stem
        suffix = output_path.suffix
        path_a = output_path.with_name(f"{stem}_set1{suffix}")
        path_b = output_path.with_name(f"{stem}_set2{suffix}")

        with open(path_a, "w") as f:
            json.dump(entries_a, f, indent=2)
        with open(path_b, "w") as f:
            json.dump(entries_b, f, indent=2)

        _print_summary(entries_a, "ModBench Set 1 (suggestion discovery)")
        _print_summary(entries_b, "ModBench Set 2 (re-evaluation)")
        print(f"\nWrote {path_a}")
        print(f"Wrote {path_b}")
    else:
        entries = _build_entries(removals, approvals, rules_by_sub, valid_subs)

        _print_summary(entries)

        with open(output_path, "w") as f:
            json.dump(entries, f, indent=2)
        print(f"\nWrote {output_path}")


# ---------------------------------------------------------------------------
# Legacy mode: build from compiler outputs
# ---------------------------------------------------------------------------

def _label_to_verdict(label: str) -> str:
    return {
        "positive": "approve", "negative": "remove", "borderline": "review",
        "compliant": "approve", "violating": "remove",
    }.get(label, "review")


def build_modbench_compiler(sources: list[Path], output_path: Path):
    """Build from compiler-generated examples (legacy mode)."""
    seen_keys: set[str] = set()
    entries: list[dict] = []
    counter = 0

    for source_path in sources:
        if not source_path.exists():
            print(f"  Skipping {source_path} (not found)")
            continue

        print(f"Processing {source_path.name}...")
        with open(source_path) as f:
            data = json.load(f)

        for entry in data:
            if "rules" in entry:
                for rule in entry.get("rules", []):
                    triage = rule.get("triage") or {}
                    if triage.get("rule_type") != "actionable":
                        continue
                    for ex in rule.get("examples", []):
                        content = ex.get("content", {})
                        label = ex.get("label", "")
                        dedup = (entry["subreddit"], rule["rule_text"][:60], content.get("id", ""))
                        if dedup in seen_keys:
                            continue
                        seen_keys.add(dedup)
                        counter += 1
                        entries.append({
                            "id": f"mb-{counter:04d}",
                            "subreddit": entry["subreddit"],
                            "rule_text": rule["rule_text"],
                            "post": content,
                            "ground_truth_verdict": _label_to_verdict(label),
                            "ground_truth_notes": "",
                            "difficulty": "hard" if label == "borderline" else "easy",
                            "source": "compiler_generated",
                        })
            else:
                triage = entry.get("triage") or {}
                if triage.get("rule_type") != "actionable":
                    continue
                rule_text = entry.get("title", "")
                desc = entry.get("description")
                if desc:
                    rule_text = f"{rule_text}\n\n{desc}"
                for ex in entry.get("examples", []):
                    content = ex.get("content", {})
                    label = ex.get("label", "")
                    dedup = (entry["subreddit"], rule_text[:60], content.get("id", ""))
                    if dedup in seen_keys:
                        continue
                    seen_keys.add(dedup)
                    counter += 1
                    entries.append({
                        "id": f"mb-{counter:04d}",
                        "subreddit": entry["subreddit"],
                        "rule_text": rule_text,
                        "post": content,
                        "ground_truth_verdict": _label_to_verdict(label),
                        "ground_truth_notes": "",
                        "difficulty": "hard" if label == "borderline" else "easy",
                        "source": "compiler_generated",
                    })

    verdict_counts = defaultdict(int)
    for e in entries:
        verdict_counts[e["ground_truth_verdict"]] += 1
    print(f"\nExtracted {len(entries)} pairs: {dict(verdict_counts)}")

    with open(output_path, "w") as f:
        json.dump(entries, f, indent=2)
    print(f"Wrote {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build ModBench evaluation dataset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode", help="Data source mode")

    # Real data mode
    real = sub.add_parser("real", help="Build from removal log + pushshift + rules")
    real.add_argument("--removal-log", type=Path, default=DEFAULT_REMOVAL_LOG)
    real.add_argument("--pushshift-dir", type=Path, default=DEFAULT_PUSHSHIFT_DIR)
    real.add_argument("--rules", type=Path, default=DEFAULT_RULES,
                      help="JSON file mapping subreddit → rules list")
    real.add_argument("--subreddits", nargs="+", default=DEFAULT_SUBREDDITS,
                      help="Subreddits to include")
    real.add_argument("--removals-per-sub", type=int, default=100)
    real.add_argument("--approvals-per-sub", type=int, default=200)
    real.add_argument("--seed", type=int, default=42)
    real.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    real.add_argument("--split", action="store_true",
                      help="Split into two disjoint sets (for RQ2A suggest evaluation)")
    real.add_argument("--split-ratio", type=float, default=0.5,
                      help="Ratio of data in set 1 vs set 2 (default: 0.5)")

    # Compiler mode (legacy)
    comp = sub.add_parser("compiler", help="Build from compiler-generated examples")
    comp.add_argument("--sources", nargs="*", type=Path,
                      default=[SCRIPTS_DIR / "compiler_test_output.json",
                               SCRIPTS_DIR / "compiler_test_sampled.json"])
    comp.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)

    args = parser.parse_args()

    if args.mode == "real":
        build_modbench_real(
            removal_log_path=args.removal_log,
            pushshift_dir=args.pushshift_dir,
            rules_path=args.rules,
            subreddits=args.subreddits,
            removals_per_sub=args.removals_per_sub,
            approvals_per_sub=args.approvals_per_sub,
            output_path=args.output,
            seed=args.seed,
            split=args.split,
            split_ratio=args.split_ratio,
        )
    elif args.mode == "compiler":
        build_modbench_compiler(args.sources, args.output)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
