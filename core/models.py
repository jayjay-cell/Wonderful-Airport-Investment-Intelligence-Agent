"""Typed domain models for the deterministic core.

No LLM, no LangChain, no I/O. Every user-visible metric carries its
provenance (direct measurement, proxy signal, or missing) so that
distinction can never be silently dropped as data flows from data/ through
core/ to the tool layer.

The only calculations in this system are Congestion Score and Opportunity
Score (core/scoring.py), each represented as a single Score value with a
basis/missing/limitations breakdown -- see Score below. Research/LLM
explanation happens outside core/ entirely and never feeds back into a
score.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Provenance — every metric is tagged with how it was derived
# ---------------------------------------------------------------------------

class Evidence(str, Enum):
    """How a value was obtained: direct measurement, proxy inference, or
    absent. A "proxy" tag must never be dropped or upgraded to "direct" as
    data moves through the system."""

    DIRECT = "direct"
    PROXY = "proxy"
    MISSING = "missing"


class SourceRecord(BaseModel):
    """Provenance for one fetched value or dataset, attached by data/ and
    carried through unchanged by core/."""

    source_name: str  # e.g. "BTS T-100 Segment", "FAA TAF"
    retrieved_at: date
    coverage_period: str  # human-readable, e.g. "2022-2024" or "FY2025"
    origin: Literal["live", "cache", "demo_snapshot"]


class MetricValue(BaseModel):
    """A single computed or fetched number, with the provenance required to
    label it direct/proxy/missing in any user-facing answer."""

    value: float | int | None
    evidence: Evidence
    unit: str | None = None
    definition: str | None = None  # e.g. the long-haul threshold used
    source: SourceRecord | None = None
    note: str | None = None  # e.g. a proxy caveat


# ---------------------------------------------------------------------------
# Airport identity
# ---------------------------------------------------------------------------

class HubClass(str, Enum):
    LARGE = "large_hub"
    MEDIUM = "medium_hub"
    SMALL = "small_hub"
    NONHUB = "nonhub"
    UNKNOWN = "unknown"


class Airport(BaseModel):
    code: str  # IATA/FAA identifier, e.g. "ANC"
    name: str
    city: str
    state: str
    region: str | None = None  # e.g. "New England" — resolved by find_airports
    hub_class: HubClass = HubClass.UNKNOWN
    is_commercial: bool = True
    latitude: float | None = None
    longitude: float | None = None


# ---------------------------------------------------------------------------
# Investment focus — still needed: it selects WHICH weights apply in
# opportunity_score(), not which classification pipeline to run.
# ---------------------------------------------------------------------------

class InvestmentFocus(str, Enum):
    TERMINAL = "terminal"
    GATES = "gates"
    RUNWAY_AIRFIELD = "runway_airfield"
    OPERATIONS_TECHNOLOGY = "operations_technology"
    GENERAL_MODERNIZATION = "general_modernization"


# ---------------------------------------------------------------------------
# Airport profile — the aggregated input to both scores
# ---------------------------------------------------------------------------

class AirportProfile(BaseModel):
    airport: Airport
    period: str

    passengers: MetricValue | None = None
    passenger_yoy_growth: MetricValue | None = None
    departures: MetricValue | None = None
    seats: MetricValue | None = None
    load_factor: MetricValue | None = None
    passengers_per_departure: MetricValue | None = None

    departure_delay_rate: MetricValue | None = None
    median_taxi_out_minutes: MetricValue | None = None
    cancellation_rate: MetricValue | None = None
    delay_cause_breakdown: dict[str, float] | None = None

    # Computed once here from the same T-100 route list already fetched for
    # departures/passengers/seats above (see tools/get_airport_profile.py).
    long_haul_share_departures: MetricValue | None = None
    long_haul_share_passengers: MetricValue | None = None
    long_haul_threshold_miles: float | None = None

    faa_forecast_passenger_cagr: MetricValue | None = None


# ---------------------------------------------------------------------------
# Score — the ONE result shape for both Congestion Score and Opportunity
# Score. `value=None` means genuinely unscoreable (too much missing), never
# a silent 0.
#
# A missing input is dropped from the weighted average and the remaining
# weights are renormalized to sum to 1.0 (see core/scoring.py) -- this keeps
# `value` comparable across candidates, but it also means two airports with
# very different amounts of underlying data can land on a similar-looking
# score. `coverage_ratio` makes that visible: a high score computed from
# inputs_available=2/inputs_expected=4 is a genuinely weaker basis for a
# decision than the same score computed from 4/4, even though `value` alone
# doesn't show that. Callers ranking or comparing scores should treat
# coverage_ratio as part of the result, not an optional detail.
# ---------------------------------------------------------------------------

class Score(BaseModel):
    value: float | None  # 0-100, or None if unscoreable
    basis: list[str] = Field(default_factory=list)       # which metrics went into it
    missing: list[str] = Field(default_factory=list)     # which metrics were unavailable
    limitations: list[str] = Field(default_factory=list)  # plain-language caveats
    inputs_available: int = 0   # how many of the expected inputs had usable data
    inputs_expected: int = 0    # how many inputs this score type normally considers
    coverage_ratio: float | None = None  # inputs_available / inputs_expected, or None if inputs_expected == 0
