"""Deterministic classification logic: Demand, Passenger-Side Pressure,
Flight-Side Pressure, Congestion, Need Level.

Reads thresholds from config/methodology.yaml at call time (via the
`config` param) rather than hardcoding numbers here, so every threshold
stays visible, editable, and traceable to its documented kind
(metric_definition / descriptive_classification / proxy_signal).

No single proxy signal may independently produce a "High" classification
where the methodology specifies multiple signals are required — this file
is where that rule is actually enforced in code, not just documented.
"""

from __future__ import annotations

from core.models import ClassificationResult, Evidence, Level


def classify_demand(
    passenger_yoy_growth: float | None,
    faa_forecast_cagr: float | None,
    config: dict,
) -> ClassificationResult:
    cfg = config["demand_level"]

    if passenger_yoy_growth is None and faa_forecast_cagr is None:
        return ClassificationResult(
            level=Level.INSUFFICIENT,
            evidence=Evidence.MISSING,
            reasoning="Required trailing passenger history and FAA forecast "
                      "data are both unavailable.",
        )

    high = cfg["high"]
    is_high = False
    signals = []

    if passenger_yoy_growth is not None and passenger_yoy_growth >= high["passenger_yoy_growth_min"]:
        is_high = True
        signals.append(f"passenger YoY growth {passenger_yoy_growth:.1%} >= {high['passenger_yoy_growth_min']:.1%}")
    elif (
        passenger_yoy_growth is not None
        and faa_forecast_cagr is not None
        and passenger_yoy_growth >= high["or_combined"]["passenger_yoy_growth_min"]
        and faa_forecast_cagr >= high["or_combined"]["faa_forecast_cagr_min"]
    ):
        is_high = True
        signals.append(
            f"passenger YoY growth {passenger_yoy_growth:.1%} + FAA forecast CAGR "
            f"{faa_forecast_cagr:.1%} both meet combined High thresholds"
        )

    if is_high:
        return ClassificationResult(
            level=Level.HIGH,
            evidence=Evidence.DIRECT,
            signals_triggered=signals,
            reasoning="Trailing passenger growth and/or FAA forecast growth "
                      "meet the configured High-demand thresholds.",
        )

    positive = (passenger_yoy_growth is not None and passenger_yoy_growth > 0) or (
        faa_forecast_cagr is not None and faa_forecast_cagr > 0
    )
    if positive:
        return ClassificationResult(
            level=Level.MEDIUM,
            evidence=Evidence.DIRECT,
            reasoning="Historical or forecast demand is positive but does "
                      "not meet the configured High-demand thresholds.",
        )

    return ClassificationResult(
        level=Level.LOW,
        evidence=Evidence.DIRECT,
        reasoning="Neither historical nor forecast demand is positive, or "
                  "there is sustained decline.",
    )


def classify_passenger_side_pressure(
    config: dict,
    terminal_utilization: float | None = None,
    load_factor: float | None = None,
    passengers_per_departure_growth: float | None = None,
    passenger_departure_cagr_gap_pp: float | None = None,
) -> ClassificationResult:
    """Prefers direct terminal-utilization evidence. Falls back to a
    3-signal proxy set. A proxy result requires the CONFIGURED NUMBER OF
    SIGNALS, never a single signal, to reach High — enforced here, not left
    to the caller."""
    cfg = config["passenger_side_pressure"]

    if terminal_utilization is not None:
        direct = cfg["direct_evidence"]
        if terminal_utilization >= direct["high_min_utilization"]:
            return ClassificationResult(
                level=Level.HIGH,
                evidence=Evidence.DIRECT,
                reasoning=f"Direct terminal utilization {terminal_utilization:.0%} "
                          f"meets the High threshold from an authoritative source.",
            )
        lo, hi = direct["medium_utilization_range"]
        if lo <= terminal_utilization <= hi:
            return ClassificationResult(
                level=Level.MEDIUM,
                evidence=Evidence.DIRECT,
                reasoning=f"Direct terminal utilization {terminal_utilization:.0%} "
                          f"falls in the Medium range.",
            )
        return ClassificationResult(
            level=Level.LOW,
            evidence=Evidence.DIRECT,
            reasoning=f"Direct terminal utilization {terminal_utilization:.0%} "
                      f"is below the Medium/High thresholds.",
        )

    proxy = cfg["proxy_signals"]
    signals: list[str] = []

    if load_factor is not None and load_factor >= proxy["load_factor_min"]:
        signals.append(
            f"load factor {load_factor:.0%} >= {proxy['load_factor_min']:.0%} "
            f"(seat utilization proxy — not direct terminal-capacity evidence)"
        )
    if (
        passengers_per_departure_growth is not None
        and passengers_per_departure_growth >= proxy["passengers_per_departure_growth_min"]
    ):
        signals.append(
            f"passengers/departure growth {passengers_per_departure_growth:.1%} "
            f">= {proxy['passengers_per_departure_growth_min']:.1%}"
        )
    if (
        passenger_departure_cagr_gap_pp is not None
        and passenger_departure_cagr_gap_pp >= proxy["passenger_vs_departure_cagr_gap_min_pp"]
    ):
        signals.append(
            f"passenger CAGR exceeds departure CAGR by "
            f"{passenger_departure_cagr_gap_pp:.1f}pp"
        )

    n = len(signals)
    if n == 0 and load_factor is None and passengers_per_departure_growth is None \
            and passenger_departure_cagr_gap_pp is None:
        return ClassificationResult(
            level=Level.INSUFFICIENT,
            evidence=Evidence.MISSING,
            reasoning="No direct terminal-utilization evidence and no proxy "
                      "signal inputs were available.",
        )

    level = Level.HIGH if n == 3 else Level.MEDIUM if n >= 1 else Level.LOW
    caveat = (
        "This is a proxy classification built from demand-intensity signals "
        "(seat/passenger-density metrics), not a direct measurement of "
        "terminal or gate capacity. No single signal here proves a terminal "
        "bottleneck on its own."
    )
    return ClassificationResult(
        level=level,
        evidence=Evidence.PROXY,
        signals_triggered=signals,
        reasoning=f"{n} of 3 configured proxy signals triggered.",
        caveat=caveat,
    )


def classify_flight_side_pressure(
    config: dict,
    runway_utilization: float | None = None,
    official_bottleneck_finding: bool = False,
    departure_delay_rate: float | None = None,
    median_taxi_out_minutes: float | None = None,
    cancellation_rate: float | None = None,
) -> ClassificationResult:
    cfg = config["flight_side_pressure"]
    proxy = cfg["proxy_signals"]

    if runway_utilization is not None and runway_utilization >= cfg["direct_evidence"]["high_min_runway_utilization"]:
        return ClassificationResult(
            level=Level.HIGH,
            evidence=Evidence.DIRECT,
            reasoning=f"Direct runway utilization {runway_utilization:.0%} "
                      f"meets the High threshold from an authoritative source.",
        )
    if official_bottleneck_finding:
        return ClassificationResult(
            level=Level.HIGH,
            evidence=Evidence.DIRECT,
            reasoning="An official source reports a confirmed flight-side "
                      "(airfield/runway) bottleneck finding.",
        )

    signals: list[str] = []
    if departure_delay_rate is not None and departure_delay_rate >= proxy["departure_delay_rate_min"]:
        signals.append(f"departure-delay rate {departure_delay_rate:.0%} >= "
                        f"{proxy['departure_delay_rate_min']:.0%}")
    if median_taxi_out_minutes is not None and median_taxi_out_minutes >= proxy["median_taxi_out_minutes_min"]:
        signals.append(f"median taxi-out {median_taxi_out_minutes:.0f}min >= "
                        f"{proxy['median_taxi_out_minutes_min']}min")
    if cancellation_rate is not None and cancellation_rate >= proxy["cancellation_rate_min"]:
        signals.append(f"cancellation rate {cancellation_rate:.1%} >= "
                        f"{proxy['cancellation_rate_min']:.1%}")

    if departure_delay_rate is None and median_taxi_out_minutes is None and cancellation_rate is None:
        return ClassificationResult(
            level=Level.INSUFFICIENT,
            evidence=Evidence.MISSING,
            reasoning="Core operational coverage (delay/taxi/cancellation "
                      "data) is missing.",
        )

    n = len(signals)
    caveat = (
        "Delay can result from airline operations, weather, or airspace/ATC "
        "constraints — not only a physical airfield bottleneck. Cause "
        "attribution and, for top ranking candidates, Research Agent "
        "findings are required before this signal set is used to support a "
        "runway/airfield investment recommendation specifically."
    )
    level = Level.HIGH if n >= 2 else Level.MEDIUM if n == 1 else Level.LOW
    return ClassificationResult(
        level=level,
        evidence=Evidence.PROXY,
        signals_triggered=signals,
        reasoning=f"{n} of 3 configured proxy signals triggered.",
        caveat=caveat,
    )


def classify_congestion(
    config: dict,
    departure_delay_rate: float | None,
    median_taxi_out_minutes: float | None,
    cancellation_rate: float | None,
) -> ClassificationResult:
    """Reuses the flight-side proxy signal set, applied identically across
    compared airports, rather than being an independently defined metric."""
    if departure_delay_rate is None and median_taxi_out_minutes is None and cancellation_rate is None:
        return ClassificationResult(
            level=Level.INSUFFICIENT,
            evidence=Evidence.MISSING,
            reasoning="No delay-rate, taxi-out, or cancellation-rate data "
                      "is available — congestion cannot be classified from "
                      "missing data, and must not default to Low.",
        )

    proxy = config["flight_side_pressure"]["proxy_signals"]
    signals: list[str] = []

    if departure_delay_rate is not None and departure_delay_rate >= proxy["departure_delay_rate_min"]:
        signals.append("departure_delay_rate")
    if median_taxi_out_minutes is not None and median_taxi_out_minutes >= proxy["median_taxi_out_minutes_min"]:
        signals.append("median_taxi_out_minutes")
    if cancellation_rate is not None and cancellation_rate >= proxy["cancellation_rate_min"]:
        signals.append("cancellation_rate")

    n = len(signals)
    level = Level.HIGH if n >= 2 else Level.MEDIUM if n == 1 else Level.LOW
    return ClassificationResult(
        level=level,
        evidence=Evidence.PROXY,
        signals_triggered=signals,
        reasoning=f"{n} of 3 congestion signals triggered "
                  f"(same signal set as Flight-Side Pressure).",
    )


def classify_need(demand: Level, relevant_pressure: Level, config: dict) -> ClassificationResult:
    table = config["need_level"]["combination_table"]

    for row in table:
        if "default" in row:
            continue
        if row["demand"] == demand.value.lower() and row["relevant_pressure"] == relevant_pressure.value.lower():
            level = Level(row["need"].capitalize())
            return ClassificationResult(
                level=level,
                evidence=Evidence.DIRECT,
                reasoning=f"Demand={demand.value}, relevant pressure={relevant_pressure.value} "
                          f"-> Need={level.value} per the configured combination table.",
            )

    return ClassificationResult(
        level=Level.LOW,
        evidence=Evidence.DIRECT,
        reasoning=f"Demand={demand.value}, relevant pressure={relevant_pressure.value} "
                  f"did not match a High/Medium row; default Need=Low applies.",
    )
