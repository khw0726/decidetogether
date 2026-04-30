"""Compute per-dimension IDF over context clusters.

Reads `scripts/community_contexts_clustered.jsonl`, where each community already
has its raw context tags mapped into clusters (per the taxonomy in
`scripts/context_taxonomy.json` + mapping in `scripts/context_tag_mapping.json`).

For each context dimension (purpose / participants / stakes / tone), counts the
document frequency of each cluster across the corpus and writes
`scripts/context_cluster_idf.json` with per-dimension IDF scores.

The runtime "browse for inspiration" UI uses these scores to surface each peer
community's most *distinctive* cluster per dimension — a community whose stakes
include `financial_harm_risk` (rare across the corpus) gets that highlighted,
while one whose only stakes-cluster is `content_quality_degradation` (common)
gets nothing distinctive in that dimension.
"""

import json
import math
from collections import defaultdict
from pathlib import Path

DIMENSIONS = ("purpose", "participants", "stakes", "tone")
HERE = Path(__file__).parent
INPUT_PATH = HERE / "community_contexts_clustered.jsonl"
OUTPUT_PATH = HERE / "context_cluster_idf.json"


def main() -> None:
    df: dict[str, dict[str, int]] = {dim: defaultdict(int) for dim in DIMENSIONS}
    n = 0

    with INPUT_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            clustered = entry.get("clustered") or {}
            n += 1
            for dim in DIMENSIONS:
                clusters = clustered.get(dim) or []
                for c in set(clusters):
                    df[dim][c] += 1

    idf: dict[str, dict[str, float]] = {}
    for dim in DIMENSIONS:
        idf[dim] = {
            cluster: math.log(n / count)
            for cluster, count in df[dim].items()
        }

    output = {
        "n_communities": n,
        "dimensions": DIMENSIONS,
        "idf": idf,
        "df": {dim: dict(df[dim]) for dim in DIMENSIONS},
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, sort_keys=True))
    print(f"Wrote {OUTPUT_PATH} (N={n})")
    for dim in DIMENSIONS:
        n_clusters = len(idf[dim])
        top = sorted(idf[dim].items(), key=lambda kv: -kv[1])[:3]
        bottom = sorted(idf[dim].items(), key=lambda kv: kv[1])[:3]
        print(f"  {dim}: {n_clusters} clusters")
        print(f"    most distinctive: {top}")
        print(f"    least distinctive: {bottom}")


if __name__ == "__main__":
    main()
