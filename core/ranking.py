"""Ranking: score every candidate with opportunity_score(), sort. That's
the whole calculation -- no separate sort-key logic per investment focus
(the old version had 3 near-duplicate tuple-sort functions; the score
itself already encodes which factors matter most, via the per-focus
weights in core/scoring.py, so there is nothing left for ranking to decide
beyond "higher score wins").
"""

from __future__ import annotations

from core.models import Airport, AirportProfile, InvestmentFocus, Score
from core.scoring import opportunity_score


def rank(profiles: list[AirportProfile], investment_focus: InvestmentFocus) -> list[tuple[Airport, Score]]:
    """Returns (airport, score) pairs sorted best-first. A profile that
    scores None (unscoreable) sorts last, visibly -- never silently
    dropped and never treated as a 0."""
    scored = [(p.airport, opportunity_score(p, investment_focus)) for p in profiles]
    return sorted(scored, key=lambda pair: pair[1].value if pair[1].value is not None else -1, reverse=True)


def scope_label(candidate_set_description: str, is_national: bool) -> str:
    """Always phrase results as 'best among the airports evaluated,' never
    'best in the US,' unless a national candidate set was actually
    evaluated."""
    if is_national:
        return "best among all evaluated US commercial airports"
    return f"best among the airports evaluated ({candidate_set_description})"
