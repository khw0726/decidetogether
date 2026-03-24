"""Action resolution and verdict aggregation."""

from typing import Any


# Verdict precedence: REMOVE > FLAG > APPROVE
VERDICT_PRECEDENCE = {"remove": 3, "flag": 2, "approve": 1}


def resolve_verdict(rule_results: list[dict[str, Any]]) -> tuple[str, float]:
    """Aggregate rule results into final verdict + confidence.

    Each rule_result has:
    - verdict: "approve" | "remove" | "flag"
    - confidence: float 0.0-1.0
    - rule_id: str

    Returns (verdict, confidence).
    Priority: REMOVE > FLAG > APPROVE.
    Confidence is the average of all rule confidences, weighted toward the worst case.
    """
    if not rule_results:
        return "approve", 1.0

    # Find highest-precedence verdict
    best_verdict = "approve"
    for result in rule_results:
        verdict = result.get("verdict", "approve")
        if VERDICT_PRECEDENCE.get(verdict, 0) > VERDICT_PRECEDENCE.get(best_verdict, 0):
            best_verdict = verdict

    # Compute confidence
    # For the final verdict, confidence = average confidence of rules that produced that verdict
    # Combined with a penalty based on how many rules disagreed
    matching_confidences = [
        r.get("confidence", 0.5)
        for r in rule_results
        if r.get("verdict") == best_verdict
    ]
    if matching_confidences:
        avg_confidence = sum(matching_confidences) / len(matching_confidences)
    else:
        avg_confidence = 0.5

    return best_verdict, round(avg_confidence, 3)


def merge_item_results(item_results: list[dict[str, Any]], combine_mode: str) -> tuple[bool, float]:
    """Merge multiple item evaluation results according to combine_mode.

    Returns (passes, confidence).
    """
    if not item_results:
        return True, 1.0

    passes_list = [r.get("passes", True) for r in item_results]
    confidences = [r.get("confidence", 0.5) for r in item_results]

    if combine_mode == "all_must_pass":
        passes = all(passes_list)
        # Confidence: min confidence of failing items (or avg if all pass)
        if not passes:
            failing_confs = [c for p, c in zip(passes_list, confidences) if not p]
            confidence = min(failing_confs) if failing_confs else 0.5
        else:
            confidence = sum(confidences) / len(confidences)

    elif combine_mode == "any_must_pass":
        passes = any(passes_list)
        if passes:
            passing_confs = [c for p, c in zip(passes_list, confidences) if p]
            confidence = max(passing_confs) if passing_confs else 0.5
        else:
            confidence = sum(confidences) / len(confidences)

    else:
        # Default: all must pass
        passes = all(passes_list)
        confidence = sum(confidences) / len(confidences)

    return passes, round(confidence, 3)


def determine_was_override(agent_verdict: str, moderator_verdict: str) -> bool:
    """Determine if moderator overrode the agent."""
    if moderator_verdict == "pending":
        return False
    return agent_verdict != moderator_verdict
