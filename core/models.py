"""Typed domain models for the deterministic core.

No LLM, no LangChain, no I/O. Every user-visible metric or classification
carries its provenance (direct measurement, proxy signal, or missing) so the
distinction required by the methodology can never be silently dropped as
data flows from data/ through core/ to the agent layer.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Provenance — every metric/classification is tagged with how it was derived
# ---------------------------------------------------------------------------

class Evidence(str, Enum):
    """How a value was obtained. Mirrors config/methodology.yaml's
    threshold_kinds distinction: direct measurement vs. proxy inference vs.
    absent. A "proxy" tag must never be dropped or upgraded to "direct" as
    data moves through the system."""

    DIRECT = "direct"
    PROXY = "proxy"
    MISSING = "missing"


class Level(str, Enum):
    """Standard four-value classification scale used throughout core/.
    Deliberately not a numeric score — see plan Section 2."""

    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INSUFFICIENT = "Insufficient"


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
    note: str | None = None  # e.g. a proxy caveat from methodology.yaml


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
# Investment focus / question framing
# ---------------------------------------------------------------------------

class InvestmentFocus(str, Enum):
    TERMINAL = "terminal"
    GATES = "gates"
    RUNWAY_AIRFIELD = "runway_airfield"
    OPERATIONS_TECHNOLOGY = "operations_technology"
    GENERAL_MODERNIZATION = "general_modernization"


class ObjectiveType(str, Enum):
    PASSENGER_CAPACITY = "passenger_capacity"
    FLIGHT_CAPACITY = "flight_capacity"
    BOTH = "both"


# ---------------------------------------------------------------------------
# Bottleneck / fit / actionability taxonomy
# ---------------------------------------------------------------------------

class Bottleneck(str, Enum):
    TERMINAL = "terminal"
    GATES = "gates"
    RUNWAY_AIRFIELD = "runway_airfield"
    AIRSPACE_ATC = "airspace_atc"
    OPERATIONS_TECHNOLOGY = "operations_technology"
    AIRLINE_CONTROLLED = "airline_controlled"
    WEATHER = "weather"
    UNKNOWN = "unknown"


class InvestmentFit(str, Enum):
    CONFIRMED = "Confirmed"
    LIKELY = "Likely"
    UNCLEAR = "Unclear"
    MISMATCH = "Mismatch"


class Actionability(str, Enum):
    ACTIONABLE = "Actionable"
    CONDITIONAL = "Conditional"
    BLOCKED = "Blocked"
    UNKNOWN = "Unknown"


class Confidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INSUFFICIENT_EVIDENCE = "Insufficient evidence"


class FinalClassification(str, Enum):
    STRONG_CANDIDATE = "Strong candidate"
    PROMISING = "Promising — further diligence required"
    MIXED = "Mixed / conditional opportunity"
    HIGH_NEED_LOW_ACTIONABILITY = "High need, low actionability"
    WEAK_CANDIDATE = "Weak candidate"
    INSUFFICIENT_EVIDENCE = "Insufficient evidence"


# ---------------------------------------------------------------------------
# Gates and flags
# ---------------------------------------------------------------------------

class HardGate(BaseModel):
    reason: str
    triggered: bool
    detail: str | None = None


class SoftFlag(BaseModel):
    reason: str
    detail: str | None = None


# ---------------------------------------------------------------------------
# Research evidence (typed, produced by research/, consumed by core/)
# ---------------------------------------------------------------------------

class EvidenceStatus(str, Enum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    NOT_FOUND = "not_found"


class ResearchEvidence(BaseModel):
    """One typed research finding. core/ consumes only `field` and `value` —
    it must never interpret `claim` free text to decide a classification."""

    airport_code: str
    field: str  # e.g. "terminal_bottleneck", "funding_status"
    value: bool | str | float | None
    claim: str
    source_title: str
    source_url: str
    source_type: Literal["airport_official", "faa_government", "news", "other"]
    published_at: date | None = None
    status: EvidenceStatus
    notes: str | None = None


# ---------------------------------------------------------------------------
# Airport profile — the aggregated input to classification
# ---------------------------------------------------------------------------

class AirportProfile(BaseModel):
    airport: Airport
    period: str

    passengers: MetricValue | None = None
    passenger_yoy_growth: MetricValue | None = None
    departures: MetricValue | None = None
    departure_cagr: MetricValue | None = None
    seats: MetricValue | None = None
    load_factor: MetricValue | None = None
    passengers_per_departure: MetricValue | None = None

    departure_delay_rate: MetricValue | None = None
    median_taxi_out_minutes: MetricValue | None = None
    cancellation_rate: MetricValue | None = None
    delay_cause_breakdown: dict[str, float] | None = None

    # Computed once here from the same T-100 route list already fetched for
    # departures/passengers/seats above -- calculate_long_haul_share_tool
    # was previously a SEPARATE tool re-deriving this from the same data.
    # Folded in rather than duplicated (see tools/get_airport_profile.py).
    long_haul_share_departures: MetricValue | None = None
    long_haul_share_passengers: MetricValue | None = None
    long_haul_threshold_miles: float | None = None

    faa_forecast_passenger_cagr: MetricValue | None = None

    terminal_utilization: MetricValue | None = None  # direct, rare
    runway_utilization: MetricValue | None = None  # direct, rare


# ---------------------------------------------------------------------------
# Classification results
# ---------------------------------------------------------------------------

class ClassificationResult(BaseModel):
    """A single classified dimension (Demand, Passenger-Side Pressure, etc.)
    with the evidence tier and human-readable reasoning that produced it."""

    level: Level
    evidence: Evidence
    signals_triggered: list[str] = Field(default_factory=list)
    reasoning: str
    caveat: str | None = None  # e.g. load-factor-is-not-terminal-capacity note


class OpportunityAssessment(BaseModel):
    """Full seven-dimension output for one airport + investment focus.
    Produced by assess_airport_opportunity; consumed by rank_airports."""

    airport: Airport
    investment_focus: InvestmentFocus
    objective: ObjectiveType

    demand_level: ClassificationResult
    passenger_side_pressure: ClassificationResult
    flight_side_pressure: ClassificationResult
    need_level: ClassificationResult

    likely_bottleneck: Bottleneck
    bottleneck_reasoning: str

    investment_fit: InvestmentFit
    actionability: Actionability

    hard_gates: list[HardGate] = Field(default_factory=list)
    soft_flags: list[SoftFlag] = Field(default_factory=list)

    confidence: Confidence
    confidence_reason: str

    final_classification: FinalClassification

    research_evidence: list[ResearchEvidence] = Field(default_factory=list)
    research_invoked: bool = False

    limitations: list[str] = Field(default_factory=list)
