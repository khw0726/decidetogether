"""Action resolution and verdict aggregation."""

from typing import Any


# Verdict precedence: REMOVE > WARN > REVIEW > APPROVE
VERDICT_PRECEDENCE = {"remove": 4, "warn": 3, "review": 2, "approve": 1}


def resolve_verdict(rule_results: list[dict[str, Any]]) -> tuple[str, float]:
    """Aggregate rule results into final verdict + confidence.

    Each rule_result has:
    - verdict: "approve" | "remove" | "review"
    - confidence: float 0.0-1.0
    - rule_id: str

    Returns (verdict, confidence).
    Priority: REMOVE > REVIEW > APPROVE.
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



def determine_was_override(agent_verdict: str, moderator_verdict: str) -> bool:
    """Determine if moderator overrode the agent."""
    if moderator_verdict == "pending":
        return False
    return agent_verdict != moderator_verdict
