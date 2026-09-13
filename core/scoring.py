"""Deterministic scoring. Two functions, two scores -- nothing else.

    congestion_score()   -- from operational metrics (delay/taxi/cancel).
                             Used by compare_airports_tool directly, and
                             reused inside opportunity_score() below.
    opportunity_score()  -- from demand/pressure/long-haul metrics,
                             including congestion_score(). Used by
                             rank_airports_tool only.

Both scores are 0-100. Neither is influenced by research or by the LLM --
research/LLM output may explain a score after the fact (see
tools/research_facts.py), but never feeds into computing it. A missing
input metric is excluded from the weighted average and the remaining
weights are renormalized to sum to 1.0 -- recorded in Score.missing, never
coerced to 0.

Renormalizing keeps `value` comparable across candidates, but it also means
a score computed from partial data can look similar to one computed from
complete data. Every Score therefore also reports inputs_available,
inputs_expected, and coverage_ratio (core/models.py), so a caller can tell
"high score, thin data" apart from "high score, full data" instead of
trusting value alone.
"""

from __future__ import annotations

from core.models import AirportProfile, InvestmentFocus, Score

# ---------------------------------------------------------------------------
# Reference values a metric is normalized against, 0.0 (best) to 1.0+ (worst).
# Carried over from the thresholds the old proxy-signal classifiers used
# (config/methodology.yaml) -- same numbers, now used as normalization
# scale instead of High/Medium/Low cutoffs.
# ---------------------------------------------------------------------------

_DELAY_RATE_REFERENCE = 0.25          # departure_delay_rate at/above this -> fully "bad"
_TAXI_OUT_REFERENCE_MINUTES = 20.0    # median_taxi_out_minutes at/above this -> fully "bad"
_CANCELLATION_RATE_REFERENCE = 0.03   # cancellation_rate at/above this -> fully "bad"
_LOAD_FACTOR_REFERENCE = 0.85         # load_factor at/above this -> fully "high pressure"
_GROWTH_REFERENCE = 0.05              # passenger_yoy_growth at/above this -> fully "high demand"


def _normalize(value: float, reference: float) -> float:
    """0.0 (well below reference) to 1.0 (at/above reference), clamped."""
    if reference <= 0:
        return 0.0
    return max(0.0, min(1.0, value / reference))


_CONGESTION_INPUTS_EXPECTED = 3


def congestion_score(profile: AirportProfile) -> Score:
    """0-100: how congested this airport's operations currently are, from
    delay rate, taxi-out time, and cancellation rate. Higher = more
    congested. Averaged over whichever of the three inputs are actually
    present; all three missing -> value=None, not 0."""
    parts: list[tuple[str, float]] = []
    missing: list[str] = []

    if profile.departure_delay_rate and profile.departure_delay_rate.value is not None:
        parts.append(("departure_delay_rate", _normalize(profile.departure_delay_rate.value, _DELAY_RATE_REFERENCE)))
    else:
        missing.append("departure_delay_rate")

    if profile.median_taxi_out_minutes and profile.median_taxi_out_minutes.value is not None:
        parts.append(("median_taxi_out_minutes", _normalize(profile.median_taxi_out_minutes.value, _TAXI_OUT_REFERENCE_MINUTES)))
    else:
        missing.append("median_taxi_out_minutes")

    if profile.cancellation_rate and profile.cancellation_rate.value is not None:
        parts.append(("cancellation_rate", _normalize(profile.cancellation_rate.value, _CANCELLATION_RATE_REFERENCE)))
    else:
        missing.append("cancellation_rate")

    if not parts:
        return Score(
            value=None, missing=missing,
            limitations=["No delay-rate, taxi-out, or cancellation-rate data is available; congestion cannot be scored."],
            inputs_available=0, inputs_expected=_CONGESTION_INPUTS_EXPECTED, coverage_ratio=0.0,
        )

    avg = sum(v for _, v in parts) / len(parts)
    limitations = []
    if missing:
        limitations.append(f"Computed from {len(parts)} of 3 congestion signals; {', '.join(missing)} unavailable.")

    return Score(
        value=round(avg * 100, 1), basis=[name for name, _ in parts], missing=missing, limitations=limitations,
        inputs_available=len(parts), inputs_expected=_CONGESTION_INPUTS_EXPECTED,
        coverage_ratio=round(len(parts) / _CONGESTION_INPUTS_EXPECTED, 2),
    )


# Per-focus weights: how much each input contributes to the Opportunity
# Score. Congestion is always included (a proxy for operational pressure);
# long_haul_share only matters for terminal/gates (a long-haul-heavy
# airport has different terminal/passenger-flow needs than a short-haul
# one). Weights sum to 1.0 per focus.
_FOCUS_WEIGHTS: dict[InvestmentFocus, dict[str, float]] = {
    InvestmentFocus.TERMINAL: {"growth": 0.30, "load_factor": 0.30, "congestion": 0.25, "long_haul_share": 0.15},
    InvestmentFocus.GATES: {"growth": 0.30, "load_factor": 0.35, "congestion": 0.20, "long_haul_share": 0.15},
    InvestmentFocus.RUNWAY_AIRFIELD: {"growth": 0.25, "load_factor": 0.15, "congestion": 0.60},
    InvestmentFocus.OPERATIONS_TECHNOLOGY: {"growth": 0.25, "load_factor": 0.20, "congestion": 0.55},
    InvestmentFocus.GENERAL_MODERNIZATION: {"growth": 0.30, "load_factor": 0.25, "congestion": 0.45},
}


def opportunity_score(profile: AirportProfile, investment_focus: InvestmentFocus) -> Score:
    """0-100: how strong a modernization-investment candidate this airport
    is for the given focus. Higher = stronger candidate. A weighted blend
    of growth, load factor, congestion, and (for terminal/gates)
    long-haul share -- weights are named per focus in _FOCUS_WEIGHTS, so
    "why did this score come out this way" is always traceable to a
    specific number, not an opaque judgment call.

    A missing input drops out of the weighted average (weights of the
    remaining inputs are renormalized to sum to 1.0) and is recorded in
    `missing` -- never coerced to 0. If every input is missing, value=None.
    """
    weights = _FOCUS_WEIGHTS[investment_focus]

    congestion = congestion_score(profile)

    raw: dict[str, float] = {}
    missing: list[str] = []

    if profile.passenger_yoy_growth and profile.passenger_yoy_growth.value is not None:
        raw["growth"] = _normalize(max(0.0, profile.passenger_yoy_growth.value), _GROWTH_REFERENCE)
    elif profile.faa_forecast_passenger_cagr and profile.faa_forecast_passenger_cagr.value is not None:
        raw["growth"] = _normalize(max(0.0, profile.faa_forecast_passenger_cagr.value), _GROWTH_REFERENCE)
    else:
        missing.append("passenger_yoy_growth")

    if profile.load_factor and profile.load_factor.value is not None:
        raw["load_factor"] = _normalize(profile.load_factor.value, _LOAD_FACTOR_REFERENCE)
    else:
        missing.append("load_factor")

    if congestion.value is not None:
        raw["congestion"] = congestion.value / 100.0
    else:
        missing.append("congestion (see congestion_score limitations)")

    if "long_haul_share" in weights:
        share = profile.long_haul_share_departures
        if share and share.value is not None:
            raw["long_haul_share"] = share.value
        else:
            missing.append("long_haul_share_departures")

    inputs_expected = len(weights)

    if not raw:
        return Score(
            value=None, missing=missing,
            limitations=["No usable inputs (growth, load factor, congestion, long-haul share all unavailable)."],
            inputs_available=0, inputs_expected=inputs_expected, coverage_ratio=0.0,
        )

    used_weight = sum(weights[k] for k in raw)
    weighted_sum = sum(raw[k] * weights[k] for k in raw)
    score_value = (weighted_sum / used_weight) * 100 if used_weight > 0 else None

    limitations = list(congestion.limitations)
    if missing:
        limitations.append(
            f"Computed from {len(raw)} of {inputs_expected} scoring inputs for {investment_focus.value}; "
            f"{', '.join(missing)} unavailable. The remaining inputs' weights were renormalized to fill the "
            f"gap, so this score is not directly comparable in reliability to one computed from full data -- "
            f"see coverage_ratio."
        )

    return Score(
        value=round(score_value, 1) if score_value is not None else None,
        basis=list(raw.keys()), missing=missing, limitations=limitations,
        inputs_available=len(raw), inputs_expected=inputs_expected,
        coverage_ratio=round(len(raw) / inputs_expected, 2) if inputs_expected else None,
    )
