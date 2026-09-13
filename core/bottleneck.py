"""Bottleneck taxonomy inference and Investment Fit derivation.

Delay-cause attribution is a CLUE, not definitive infrastructure diagnosis —
this module never claims certainty beyond what the underlying signals
support, and defers to research evidence when available and consistent.
"""

from __future__ import annotations

from core.models import (
    Bottleneck,
    InvestmentFit,
    InvestmentFocus,
    Level,
    ResearchEvidence,
)


def infer_bottleneck(
    passenger_side_pressure: Level,
    flight_side_pressure: Level,
    delay_cause_breakdown: dict[str, float] | None,
    research_evidence: list[ResearchEvidence],
) -> tuple[Bottleneck, str]:
    """Infers the most likely bottleneck from classification levels, delay
    cause codes, and any research evidence. Research evidence with a typed
    `field == "confirmed_bottleneck"` and status "supported" takes priority
    over inferred signals, since it represents authoritative confirmation.
    """
    for ev in research_evidence:
        if ev.field == "confirmed_bottleneck" and ev.status == "supported" and isinstance(ev.value, str):
            try:
                bottleneck = Bottleneck(ev.value)
                return bottleneck, (
                    f"Confirmed by research evidence: {ev.claim} "
                    f"(source: {ev.source_title})."
                )
            except ValueError:
                pass  # unrecognized value string, fall through to inference

    if passenger_side_pressure == Level.LOW and flight_side_pressure == Level.LOW:
        return Bottleneck.UNKNOWN, (
            "Neither passenger-side nor flight-side pressure signals are "
            "elevated; no bottleneck is evident from structured data."
        )

    if delay_cause_breakdown:
        total = sum(delay_cause_breakdown.values()) or 1.0
        shares = {k: v / total for k, v in delay_cause_breakdown.items()}
        dominant_cause = max(shares, key=shares.get) if shares else None
        dominant_share = shares.get(dominant_cause, 0.0)

        if dominant_cause == "weather" and dominant_share >= 0.4:
            return Bottleneck.WEATHER, (
                f"Weather accounts for {dominant_share:.0%} of attributed "
                f"delay — the dominant cause is external and not addressable "
                f"by physical airport investment."
            )
        if dominant_cause == "nas" and dominant_share >= 0.4:
            return Bottleneck.AIRSPACE_ATC, (
                f"NAS (airspace/ATC) delay accounts for {dominant_share:.0%} "
                f"of attributed delay — the dominant cause sits outside "
                f"airport-level infrastructure."
            )
        if dominant_cause in ("carrier", "late_aircraft") and dominant_share >= 0.4:
            return Bottleneck.AIRLINE_CONTROLLED, (
                f"{dominant_cause.replace('_', ' ').title()} delay accounts "
                f"for {dominant_share:.0%} of attributed delay — this "
                f"reflects airline operational choices more than airport "
                f"infrastructure capacity."
            )

    if passenger_side_pressure in (Level.HIGH, Level.MEDIUM) and flight_side_pressure in (Level.HIGH, Level.MEDIUM):
        if passenger_side_pressure == flight_side_pressure == Level.HIGH:
            return Bottleneck.UNKNOWN, (
                "Both passenger-side and flight-side pressure are High with "
                "no dominant delay-cause signal or research confirmation — "
                "structured data cannot distinguish which specific "
                "infrastructure (terminal/gates vs. runway/airfield) is the "
                "primary constraint."
            )
        if passenger_side_pressure.value == "High":
            return Bottleneck.TERMINAL, (
                "Passenger-side pressure is High and flight-side pressure is "
                "comparatively lower, suggesting a terminal/landside "
                "constraint — but this is inferred from proxy signals, not "
                "confirmed by direct capacity evidence."
            )
        return Bottleneck.RUNWAY_AIRFIELD, (
            "Flight-side pressure is High and passenger-side pressure is "
            "comparatively lower, suggesting an airfield constraint — but "
            "this is inferred from proxy signals, not confirmed by direct "
            "capacity evidence."
        )

    if passenger_side_pressure.value == "High":
        return Bottleneck.TERMINAL, (
            "Passenger-side pressure is High; no dominant flight-side or "
            "delay-cause signal was found. Inferred, not directly confirmed."
        )
    if flight_side_pressure.value == "High":
        return Bottleneck.RUNWAY_AIRFIELD, (
            "Flight-side pressure is High; no dominant passenger-side "
            "signal was found. Inferred, not directly confirmed."
        )

    return Bottleneck.UNKNOWN, (
        "Available structured signals do not clearly point to a single "
        "likely bottleneck."
    )


_FOCUS_TO_BOTTLENECK = {
    InvestmentFocus.TERMINAL: {Bottleneck.TERMINAL, Bottleneck.GATES},
    InvestmentFocus.GATES: {Bottleneck.GATES, Bottleneck.TERMINAL},
    InvestmentFocus.RUNWAY_AIRFIELD: {Bottleneck.RUNWAY_AIRFIELD},
    InvestmentFocus.OPERATIONS_TECHNOLOGY: {Bottleneck.OPERATIONS_TECHNOLOGY, Bottleneck.AIRLINE_CONTROLLED},
}

_NEVER_ADDRESSABLE_BY_PHYSICAL_INVESTMENT = {Bottleneck.AIRSPACE_ATC, Bottleneck.WEATHER}


def derive_investment_fit(
    investment_focus: InvestmentFocus,
    likely_bottleneck: Bottleneck,
    bottleneck_evidence_is_direct: bool,
) -> InvestmentFit:
    """Terminal pressure plus available airside headroom -> plausible fit.
    Flight pressure caused mainly by runway saturation -> plausible fit.
    Delays dominated by weather/airspace -> mismatch for physical investment.
    """
    if investment_focus == InvestmentFocus.GENERAL_MODERNIZATION:
        if likely_bottleneck in _NEVER_ADDRESSABLE_BY_PHYSICAL_INVESTMENT:
            return InvestmentFit.MISMATCH
        if likely_bottleneck == Bottleneck.UNKNOWN:
            return InvestmentFit.UNCLEAR
        return InvestmentFit.LIKELY if bottleneck_evidence_is_direct else InvestmentFit.LIKELY

    if likely_bottleneck in _NEVER_ADDRESSABLE_BY_PHYSICAL_INVESTMENT:
        return InvestmentFit.MISMATCH

    if likely_bottleneck == Bottleneck.UNKNOWN:
        return InvestmentFit.UNCLEAR

    addressable = _FOCUS_TO_BOTTLENECK.get(investment_focus, set())
    if likely_bottleneck in addressable:
        return InvestmentFit.CONFIRMED if bottleneck_evidence_is_direct else InvestmentFit.LIKELY

    return InvestmentFit.MISMATCH
