"""When does the bounded deep-dive agent run? (ADR-015)

A pure function, so the routing decision is testable and can be explained
in the audit log ("deep dive ran because ...").
"""

from trustagent.domain import RiskLevel

# Scores within this distance of a level boundary (30, 60, 80) are "near the line".
BOUNDARY_MARGIN = 5
_BOUNDARIES = (30, 60, 80)


def deep_dive_reason(
    score: int,
    level: RiskLevel,
    rule_flag_count: int,
    ai_flag_count: int,
    supplier_verified: bool,
    history_count: int,
) -> str | None:
    """Why a deep dive is warranted, or None if it isn't.

    ``rule_flag_count`` / ``ai_flag_count`` exclude CONFIRMED_MATCH.
    """
    if level == RiskLevel.MEDIUM:
        return "Risk is MEDIUM: the outcome could go either way."
    near = [b for b in _BOUNDARIES if abs(score - b) <= BOUNDARY_MARGIN]
    if near:
        return f"Score {score} is within {BOUNDARY_MARGIN} points of the {near[0]} boundary."
    if ai_flag_count and not rule_flag_count:
        return "The AI review flagged concerns although every rule check passed."
    if not supplier_verified and history_count == 0:
        return "Unverified supplier with no payment history."
    return None
