"""Distinctive-cluster lookup for the browse-for-inspiration UI.

Given a community's `community_context` dict (raw tags grouped by dimension),
returns the cluster — one per dimension — that is most *distinctive* across the
reference corpus, i.e. the cluster with the highest IDF score the community
participates in for that dimension.

Loads two static JSON tables on first use:
  * `scripts/context_tag_mapping.json`   — raw tag → cluster, per dimension
  * `scripts/context_cluster_idf.json`   — per-dimension IDF table

Regenerate the IDF table with `scripts/compute_context_cluster_idf.py` whenever
the reference corpus changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

DIMENSIONS = ("purpose", "participants", "stakes", "tone")

# `other` is the catch-all bucket — it's not a meaningful descriptor, so it must
# never be surfaced as a "distinctive" cluster, regardless of its IDF.
EXCLUDED_CLUSTERS = {"other"}

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
_MAPPING_PATH = _SCRIPTS_DIR / "context_tag_mapping.json"
_IDF_PATH = _SCRIPTS_DIR / "context_cluster_idf.json"

_tag_to_cluster: Optional[dict[str, dict[str, str]]] = None
_idf: Optional[dict[str, dict[str, float]]] = None


def _load() -> None:
    global _tag_to_cluster, _idf
    if _tag_to_cluster is None:
        _tag_to_cluster = json.loads(_MAPPING_PATH.read_text())
    if _idf is None:
        payload = json.loads(_IDF_PATH.read_text())
        _idf = payload["idf"]


def _community_clusters(community_context: dict) -> dict[str, set[str]]:
    """Map a community_context's raw tags to cluster sets, per dimension."""
    _load()
    assert _tag_to_cluster is not None
    out: dict[str, set[str]] = {dim: set() for dim in DIMENSIONS}
    for dim in DIMENSIONS:
        dim_data = (community_context or {}).get(dim) or {}
        notes = dim_data.get("notes") or []
        dim_mapping = _tag_to_cluster.get(dim, {})
        for note in notes:
            if not isinstance(note, dict):
                continue
            tag = note.get("tag")
            if not tag:
                continue
            cluster = dim_mapping.get(tag)
            if cluster:
                out[dim].add(cluster)
    return out


def _humanize(cluster: str) -> str:
    return cluster.replace("_", " ")


def distinctive_clusters(community_context: Optional[dict]) -> list[dict]:
    """Return one most-distinctive cluster per dimension for this community.

    Output: list of `{dimension, cluster, label}` dicts. Dimensions where no
    cluster is available (no tags / all clusters are `other`) are omitted.
    """
    if not community_context:
        return []
    _load()
    assert _idf is not None

    clusters = _community_clusters(community_context)
    out: list[dict] = []
    for dim in DIMENSIONS:
        candidates = [c for c in clusters[dim] if c not in EXCLUDED_CLUSTERS]
        if not candidates:
            continue
        dim_idf = _idf.get(dim, {})
        # Pick the cluster with highest IDF; tie-break alphabetically for
        # determinism across runs.
        best = max(candidates, key=lambda c: (dim_idf.get(c, 0.0), -ord(c[0]) if c else 0))
        out.append({
            "dimension": dim,
            "cluster": best,
            "label": _humanize(best),
        })
    return out


def shared_clusters(
    target_context: Optional[dict],
    peer_context: Optional[dict],
) -> set[tuple[str, str]]:
    """Return (dimension, cluster) pairs present in both communities."""
    if not target_context or not peer_context:
        return set()
    target = _community_clusters(target_context)
    peer = _community_clusters(peer_context)
    out: set[tuple[str, str]] = set()
    for dim in DIMENSIONS:
        for cluster in target[dim] & peer[dim]:
            out.add((dim, cluster))
    return out
