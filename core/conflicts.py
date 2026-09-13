"""Missing-data and conflict-resolution policy.

Canonical ownership: BTS owns commercial traffic/routes/seats/on-time
performance; FAA owns airport identity/runways/hub class/forecasts;
official airport-specific documents own local capacity/projects/
constraints; reputable news is context only, never authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass

_AUTHORITY_RANK = {
    "airport_official": 3,
    "faa_government": 3,
    "bts_government": 3,
    "other": 1,
    "news": 0,
}


@dataclass
class ConflictCheckInput:
    field: str
    definition: str
    period: str
    unit: str | None
    scope: str | None
    value: float | str | bool
    source_type: str
    is_current: bool


@dataclass
class ConflictResolution:
    resolved_value: float | str | bool | None
    status: str  # "resolved" | "different_metrics" | "data_conflict"
    detail: str


def resolve_conflict(candidates: list[ConflictCheckInput]) -> ConflictResolution:
    """Compares definition/period/unit/scope first. If those differ, the
    values are different metrics, not a conflict. If equivalent, prefers
    the more authoritative/direct/current/complete source. Never averages
    contradictory values."""
    if len(candidates) <= 1:
        c = candidates[0] if candidates else None
        return ConflictResolution(
            resolved_value=c.value if c else None,
            status="resolved",
            detail="Single source, no conflict to resolve." if c else "No candidate values provided.",
        )

    first = candidates[0]
    same_definition = all(
        c.definition == first.definition
        and c.period == first.period
        and c.unit == first.unit
        and c.scope == first.scope
        for c in candidates
    )
    if not same_definition:
        return ConflictResolution(
            resolved_value=None,
            status="different_metrics",
            detail="Candidate values differ in definition, period, unit, or "
                   "scope — treated as different metrics, not a conflict.",
        )

    values = {c.value for c in candidates}
    if len(values) == 1:
        return ConflictResolution(
            resolved_value=first.value,
            status="resolved",
            detail="All sources agree on an equivalent value.",
        )

    ranked = sorted(
        candidates,
        key=lambda c: (_AUTHORITY_RANK.get(c.source_type, 0), c.is_current),
        reverse=True,
    )
    best, second = ranked[0], ranked[1]

    if _AUTHORITY_RANK.get(best.source_type, 0) > _AUTHORITY_RANK.get(second.source_type, 0):
        return ConflictResolution(
            resolved_value=best.value,
            status="resolved",
            detail=f"Sources disagree; preferred the more authoritative "
                   f"source ({best.source_type}) over ({second.source_type}).",
        )

    return ConflictResolution(
        resolved_value=None,
        status="data_conflict",
        detail=f"Sources of equivalent authority disagree on {first.field} "
               f"({[c.value for c in candidates]}) with no basis to prefer "
               f"one. Recorded as an unresolved conflict rather than "
               f"averaged.",
    )


def is_critical_field(field: str, critical_fields: set[str]) -> bool:
    """A critically conflicted field must not be used in a classification —
    return Insufficient evidence instead. Non-critical conflicts become a
    soft flag."""
    return field in critical_fields


# not_found must never be silently treated as zero.
NOT_FOUND = object()


def as_value_or_not_found(raw: float | int | None) -> float | int | object:
    return NOT_FOUND if raw is None else raw
