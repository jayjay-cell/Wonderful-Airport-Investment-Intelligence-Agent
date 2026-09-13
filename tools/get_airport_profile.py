# ORIENTATION: SHARED BUILDING BLOCK. Fetches every data source for one airport. Other tools call this directly (not through the LLM).
"""get_airport_profile: fetches and assembles a full profile for one
airport from all 5 applicable data sources, with each field labeled
direct/proxy/missing.

Also exposed as a LangChain tool so the model can call it directly for a
plain "what's the delay rate at SFO"-type question, but most of its real
use is as a plain Python function other tools (compare_airports,
rank_airports, assess_airport_opportunity) call into.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

from langchain_core.tools import tool

from core.metrics import (
    as_metric_value,
    cagr,
    latest_complete_year,
    load_factor as calc_load_factor,
    long_haul_share,
    passengers_per_departure as calc_ppd,
    trailing_years,
)
from core.models import AirportProfile, Evidence, SourceRecord
from data.clients import bts_on_time, bts_t100, faa_airports, faa_enplanements, faa_taf
from data.degradation import SourceUnavailable
from data.timing import timed


def _source(source_name: str, coverage_period: str, origin: str = "live") -> SourceRecord:
    """Each MetricValue carries its OWN actual coverage period -- FAA
    enplanements (annual, ~CY2024) and BTS On-Time/T-100 (which may already
    cover a later year/month) must never be presented as sharing one period
    just because both live on the same profile."""
    return SourceRecord(
        source_name=source_name, retrieved_at=date.today(),
        coverage_period=coverage_period, origin=origin,
    )


_DEFAULT_LONG_HAUL_THRESHOLD_MILES = 1500.0


def get_airport_profile(airport_code: str, trend_years: int = 3, include_bts: bool = True, long_haul_threshold_miles: float = _DEFAULT_LONG_HAUL_THRESHOLD_MILES) -> dict:
    """Returns {"profile": AirportProfile, "limitations": [...], "identity_source": SourceRecord}
    or {"error": ..., "detail": ...}.

    include_bts=False skips the two slow BTS sources for a fast FAA-only
    screening pass (used by rank_airports's pre-screen tier)."""
    code = airport_code.strip().upper()
    limitations: list[str] = []

    try:
        airport, identity_source = faa_airports.get_airport_identity(code)
    except SourceUnavailable as exc:
        return {"error": "INVALID_AIRPORT", "detail": f"Could not resolve airport identity for {code!r}: {exc.detail}"}

    years = trailing_years(trend_years)
    end_year, start_year = years[-1], years[0]

    def _fetch_enplanements():
        return faa_enplanements.get_enplanements(code)

    def _fetch_taf():
        try:
            return faa_taf.get_forecast_series(code)
        except SourceUnavailable as exc:
            return exc

    def _fetch_t100():
        try:
            return bts_t100.get_annual_routes(code, end_year)
        except SourceUnavailable as exc:
            return exc

    def _fetch_on_time():
        try:
            return bts_on_time.get_on_time_records(code, end_year)
        except SourceUnavailable as exc:
            return exc

    workers = 4 if include_bts else 2
    with timed("airport_profile.load_sources", airport=code, include_bts=include_bts):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            f_enp = pool.submit(_fetch_enplanements)
            f_taf = pool.submit(_fetch_taf)
            f_t100 = pool.submit(_fetch_t100) if include_bts else None
            f_ontime = pool.submit(_fetch_on_time) if include_bts else None

            enp_record = f_enp.result()
            taf_result = f_taf.result()
            t100_result = f_t100.result() if f_t100 else None
            ontime_result = f_ontime.result() if f_ontime else None

    if not include_bts:
        limitations.append(
            "BTS T-100 and On-Time Performance were not queried for this result "
            "(fast-screening pass) -- departures, seats, load factor, delay rate, "
            "cancellation rate, and bottleneck cause data are unavailable here."
        )

    if enp_record is not None:
        airport.hub_class = enp_record["hub_class"]
    else:
        limitations.append(
            f"No FAA commercial-service enplanements record found for {code} "
            f"-- airport may be non-commercial or below reporting threshold."
        )

    passengers_end = enp_record["enplanements_current_year"] if enp_record else None
    passenger_yoy_growth_value = None
    if enp_record is not None:
        pct_change = enp_record.get("pct_change")
        if pct_change is not None and passengers_end:
            passengers_start = passengers_end / (1 + pct_change)
            if passengers_start > 0:
                passenger_yoy_growth_value = pct_change  # genuine single-year (YoY) change, not a multi-year CAGR

    if isinstance(taf_result, SourceUnavailable):
        taf = {"found": False}
        limitations.append(f"FAA TAF unavailable: {taf_result.detail}")
    else:
        taf = taf_result

    forecast_cagr_value = None
    if taf.get("found"):
        forecast_rows = sorted([r for r in taf["enplanements"] if r["is_forecast"]], key=lambda r: r["year"])
        if len(forecast_rows) >= 2:
            first, last = forecast_rows[0], forecast_rows[min(4, len(forecast_rows) - 1)]
            first_total = (first.get("air_carrier") or 0) + (first.get("air_taxi") or 0) + (first.get("commuter") or 0)
            last_total = (last.get("air_carrier") or 0) + (last.get("air_taxi") or 0) + (last.get("commuter") or 0)
            years_span = last["year"] - first["year"]
            if years_span > 0 and first_total > 0:
                forecast_cagr_value = cagr(first_total, last_total, years_span)

    if t100_result is None:
        adapted_routes, total_departures, total_passengers_t100, total_seats = [], None, None, None
    elif isinstance(t100_result, SourceUnavailable):
        adapted_routes, total_departures, total_passengers_t100, total_seats = [], None, None, None
        limitations.append(f"BTS T-100 Segment unavailable: {t100_result.detail}")
    else:
        adapted_routes = bts_t100.adapt_for_long_haul_share(t100_result["routes"])
        total_departures = sum(r["departures"] for r in adapted_routes)
        total_passengers_t100 = sum(r["passengers"] for r in adapted_routes)
        total_seats = sum(r["seats"] for r in t100_result["routes"] if r["passengers"] > 0)

    lf_value = calc_load_factor(total_passengers_t100, total_seats) if total_seats and total_passengers_t100 else None
    ppd_value = calc_ppd(total_passengers_t100, total_departures) if total_departures and total_passengers_t100 else None

    # Computed here rather than in a separate tool: adapted_routes already
    # has the exact per-route {distance_miles, departures, passengers} shape
    # long_haul_share() needs, and it would otherwise be discarded right
    # after this block. A second tool re-fetching the same T-100 data just
    # to re-derive this was pure duplication.
    lh_departures_share = lh_passengers_share = None
    if adapted_routes:
        lh_departures_share = long_haul_share(adapted_routes, threshold_miles=long_haul_threshold_miles, basis="departures")["percentage"]
        lh_passengers_share = long_haul_share(adapted_routes, threshold_miles=long_haul_threshold_miles, basis="passengers")["percentage"]

    if ontime_result is None:
        delay_rate_value = cancellation_rate_value = median_taxi = delay_causes = None
    elif isinstance(ontime_result, SourceUnavailable):
        delay_rate_value = cancellation_rate_value = median_taxi = delay_causes = None
        limitations.append(f"BTS On-Time Performance unavailable: {ontime_result.detail}")
    else:
        delay_rate_value = ontime_result.get("delay_rate")
        cancellation_rate_value = ontime_result.get("cancellation_rate")
        median_taxi = ontime_result.get("median_taxi_out_minutes")
        delay_causes = ontime_result.get("delay_causes")

    enp_meta = faa_enplanements.source_metadata()
    enp_source = _source("FAA Commercial Service Enplanements", enp_meta["coverage_period"])
    taf_source = _source("FAA Terminal Area Forecast (TAF)", f"forecast as of {enp_meta['coverage_period']}")
    t100_source = _source("BTS T-100 Segment", str(end_year)) if t100_result is not None and not isinstance(t100_result, SourceUnavailable) else None
    ontime_source = _source("BTS Reporting Carrier On-Time Performance", f"{end_year}-01") if ontime_result is not None and not isinstance(ontime_result, SourceUnavailable) else None

    if t100_source or ontime_source:
        limitations.append(
            f"FAA enplanements cover {enp_meta['coverage_period']}; BTS T-100/On-Time "
            f"data cover {end_year} -- independently published sources, not guaranteed "
            f"to be the same period."
        )

    profile = AirportProfile(
        airport=airport,
        period=f"{start_year}-{end_year}",
        passengers=as_metric_value(passengers_end, Evidence.DIRECT if passengers_end else Evidence.MISSING, unit="enplanements", source=enp_source if passengers_end else None),
        passenger_yoy_growth=as_metric_value(passenger_yoy_growth_value, Evidence.DIRECT if passenger_yoy_growth_value is not None else Evidence.MISSING, unit="ratio", source=enp_source if passenger_yoy_growth_value is not None else None),
        departures=as_metric_value(total_departures, Evidence.DIRECT if total_departures else Evidence.MISSING, unit="count", source=t100_source if total_departures else None),
        seats=as_metric_value(total_seats, Evidence.DIRECT if total_seats else Evidence.MISSING, unit="count", source=t100_source if total_seats else None),
        load_factor=as_metric_value(lf_value, Evidence.PROXY if lf_value is not None else Evidence.MISSING, unit="ratio", note="Seat utilization proxy, not direct terminal-capacity evidence.", source=t100_source if lf_value is not None else None),
        passengers_per_departure=as_metric_value(ppd_value, Evidence.PROXY if ppd_value is not None else Evidence.MISSING, unit="passengers/departure", source=t100_source if ppd_value is not None else None),
        departure_delay_rate=as_metric_value(delay_rate_value, Evidence.DIRECT if delay_rate_value is not None else Evidence.MISSING, unit="ratio", source=ontime_source if delay_rate_value is not None else None),
        median_taxi_out_minutes=as_metric_value(median_taxi, Evidence.DIRECT if median_taxi is not None else Evidence.MISSING, unit="minutes", source=ontime_source if median_taxi is not None else None),
        cancellation_rate=as_metric_value(cancellation_rate_value, Evidence.DIRECT if cancellation_rate_value is not None else Evidence.MISSING, unit="ratio", source=ontime_source if cancellation_rate_value is not None else None),
        delay_cause_breakdown=delay_causes,
        faa_forecast_passenger_cagr=as_metric_value(forecast_cagr_value, Evidence.DIRECT if forecast_cagr_value is not None else Evidence.MISSING, unit="ratio", definition="FAA TAF forecast CAGR (multi-year), not observed demand", source=taf_source if forecast_cagr_value is not None else None),
        long_haul_share_departures=as_metric_value(
            lh_departures_share, Evidence.DIRECT if lh_departures_share is not None else Evidence.MISSING, unit="ratio",
            definition=f"Share of scheduled passenger departures with great-circle distance >= {long_haul_threshold_miles:.0f} statute miles (cargo-only excluded).",
            source=t100_source if lh_departures_share is not None else None,
        ),
        long_haul_share_passengers=as_metric_value(
            lh_passengers_share, Evidence.DIRECT if lh_passengers_share is not None else Evidence.MISSING, unit="ratio",
            definition=f"Share of scheduled passengers on routes with great-circle distance >= {long_haul_threshold_miles:.0f} statute miles (cargo-only excluded).",
            source=t100_source if lh_passengers_share is not None else None,
        ),
        long_haul_threshold_miles=long_haul_threshold_miles,
    )

    return {"profile": profile, "limitations": limitations, "identity_source": identity_source}


def _serialize(result: dict) -> dict:
    if "error" in result:
        return result
    return {
        "profile": result["profile"].model_dump(),
        "limitations": result["limitations"],
    }


@tool
def get_airport_profile_tool(airport_code: str, long_haul_threshold_miles: float = 1500) -> dict:
    """Get a structured profile for one airport: passenger/flight volumes,
    growth, load factor, delays, cancellations, FAA forecast, AND long-haul
    share (both by departures and by passengers, at the given distance
    threshold -- default 1500 statute miles, this system's configured
    definition). Each field is labeled direct/proxy/missing. Use this for
    any single-airport factual lookup, including "what % of flights from X
    are long-haul" -- it is not a separate tool. Not for a full opportunity
    assessment (use assess_airport_opportunity_tool for that)."""
    return _serialize(get_airport_profile(airport_code, long_haul_threshold_miles=long_haul_threshold_miles))
