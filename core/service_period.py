"""Shared helpers for picking analysis periods, since multiple tools need
"the latest complete trailing N years/months" consistently, per
config/methodology.yaml's metric_definitions.
"""

from __future__ import annotations

from datetime import date


def latest_complete_year(today: date | None = None) -> int:
    """FAA/BTS annual data for year Y is generally not complete/published
    until well into Y+1. Conservatively treat last calendar year as the
    latest complete year once we're at least 3 months into the current one,
    otherwise the year before that."""
    today = today or date.today()
    if today.month >= 4:
        return today.year - 1
    return today.year - 2


def trailing_years(n: int, today: date | None = None) -> list[int]:
    end = latest_complete_year(today)
    return list(range(end - n + 1, end + 1))
