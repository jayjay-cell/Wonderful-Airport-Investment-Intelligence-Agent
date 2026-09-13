"""get_airport_profile tool: fetches and assembles a full AirportProfile for
one airport, from all applicable data sources, with each field labeled
direct/proxy/missing. This is the shared building block other tools
(compare_airports, rank_airports, assess_opportunity) call into — it
contains no scoring/classification logic itself, only fetching + shaping.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from core.metrics import as_metric_value, cagr, load_factor as calc_load_factor, passengers_per_departure as calc_ppd
from core.models import AirportProfile, Evidence, HubClass
from core.service_period import latest_complete_year, trailing_years
from data.clients import bts_on_time, bts_t100, faa_airports, faa_enplanements, faa_taf
from data.degradation import SourceUnavailable


def get_airport_profile(airport_code: str, trend_years: int = 3, include_bts: bool = True) -> dict:
    """Returns a structured airport profile plus a list of any data gaps
    encountered (source name + reason), so the caller can decide how that
    affects Confidence rather than the fetch silently degrading it.

    Fetches FAA identity/enplanements/TAF and (if include_bts) BTS T-100/
    On-Time CONCURRENTLY (via a thread pool) rather than sequentially.

    include_bts=False skips the two BTS sources (T-100 form scrape,
    On-Time multi-MB download) entirely — these are the slow part (often
    20-90s combined) versus the three FAA sources, which are consistently
    sub-second. Used by rank_airports for its fast pre-screen pass across
    many candidates; the small shortlist that survives screening then gets
    a full (include_bts=True) profile. All BTS-derived fields are returned
    as Evidence.MISSING when skipped, never silently defaulted to a value.
    """
    code = airport_code.strip().upper()
    limitations: list[str] = []

    try:
        airport, identity_source = faa_airports.get_airport_identity(code)
    except SourceUnavailable as exc:
        return {
            "error": "INVALID_AIRPORT",
            "detail": f"Could not resolve airport identity for {code!r}: {exc.detail}",
        }

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
            return bts_on_time.get_on_time_records(code, end_year, 1)
        except SourceUnavailable as exc:
            return exc

    workers = 4 if include_bts else 2
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
            "BTS T-100 and On-Time Performance were not queried for this "
            "result (fast-screening pass) — departures, seats, load factor, "
            "delay rate, cancellation rate, and bottleneck cause data are "
            "unavailable here. Ask about this airport specifically for a "
            "full assessment including live operational data."
        )

    if enp_record is not None:
        airport.hub_class = enp_record["hub_class"]
    else:
        limitations.append(
            f"No FAA commercial-service enplanements record found for {code} "
            f"— airport may be non-commercial or below reporting threshold; "
            f"hub class is Unknown."
        )

    passengers_end = enp_record["enplanements_current_year"] if enp_record else None
    passenger_cagr_value = None
    if enp_record is not None:
        pct_change = enp_record.get("pct_change")
        if pct_change is not None and passengers_end:
            passengers_start = passengers_end / (1 + pct_change)
            if passengers_start > 0:
                passenger_cagr_value = pct_change  # single-year change; already a CAGR-equivalent for 1 year

    if isinstance(taf_result, SourceUnavailable):
        taf = {"found": False}
        limitations.append(f"FAA TAF unavailable: {taf_result.detail}")
    else:
        taf = taf_result

    forecast_cagr_value = None
    if taf.get("found"):
        forecast_rows = sorted(
            [r for r in taf["enplanements"] if r["is_forecast"]],
            key=lambda r: r["year"],
        )
        if len(forecast_rows) >= 2:
            first, last = forecast_rows[0], forecast_rows[min(4, len(forecast_rows) - 1)]
            first_total = (first.get("air_carrier") or 0) + (first.get("air_taxi") or 0) + (first.get("commuter") or 0)
            last_total = (last.get("air_carrier") or 0) + (last.get("air_taxi") or 0) + (last.get("commuter") or 0)
            years_span = last["year"] - first["year"]
            if years_span > 0 and first_total > 0:
                forecast_cagr_value = cagr(first_total, last_total, years_span)

    if t100_result is None:
        adapted_routes = []
        total_departures = None
        total_passengers_t100 = None
        total_seats = None
    elif isinstance(t100_result, SourceUnavailable):
        adapted_routes = []
        total_departures = None
        total_passengers_t100 = None
        total_seats = None
        limitations.append(f"BTS T-100 Segment unavailable: {t100_result.detail}")
    else:
        t100 = t100_result
        adapted_routes = bts_t100.adapt_for_long_haul_share(t100["routes"])
        total_departures = sum(r["departures"] for r in adapted_routes)
        total_passengers_t100 = sum(r["passengers"] for r in adapted_routes)
        total_seats = sum(r["seats"] for r in t100["routes"] if r["passengers"] > 0)

    lf_value = None
    if total_seats and total_passengers_t100:
        lf_value = calc_load_factor(total_passengers_t100, total_seats)

    ppd_value = None
    if total_departures and total_passengers_t100:
        ppd_value = calc_ppd(total_passengers_t100, total_departures)

    if ontime_result is None:
        delay_rate_value = None
        cancellation_rate_value = None
        median_taxi = None
        delay_causes = None
    elif isinstance(ontime_result, SourceUnavailable):
        delay_rate_value = None
        cancellation_rate_value = None
        median_taxi = None
        delay_causes = None
        limitations.append(f"BTS On-Time Performance unavailable: {ontime_result.detail}")
    else:
        ontime = ontime_result
        flights = ontime["flights"]
        eligible = len(flights)
        delayed = sum(1 for f in flights if (f["dep_del15"] or 0) >= 1)
        cancelled = sum(1 for f in flights if f["cancelled"])
        taxi_outs = [f["taxi_out_minutes"] for f in flights if f["taxi_out_minutes"] is not None]
        median_taxi = sorted(taxi_outs)[len(taxi_outs) // 2] if taxi_outs else None

        delay_causes = {"carrier": 0.0, "weather": 0.0, "nas": 0.0, "security": 0.0, "late_aircraft": 0.0}
        for f in flights:
            for k, v in f["delay_causes"].items():
                if v:
                    delay_causes[k] += v

        delay_rate_value = (delayed / eligible) if eligible else None
        cancellation_rate_value = (cancelled / eligible) if eligible else None

    profile = AirportProfile(
        airport=airport,
        period=f"{start_year}-{end_year}",
        passengers=as_metric_value(
            passengers_end, Evidence.DIRECT if passengers_end else Evidence.MISSING,
            unit="enplanements",
        ),
        passenger_cagr=as_metric_value(
            passenger_cagr_value, Evidence.DIRECT if passenger_cagr_value is not None else Evidence.MISSING,
            unit="ratio",
        ),
        departures=as_metric_value(
            total_departures, Evidence.DIRECT if total_departures else Evidence.MISSING,
            unit="count",
        ),
        seats=as_metric_value(total_seats, Evidence.DIRECT if total_seats else Evidence.MISSING, unit="count"),
        load_factor=as_metric_value(
            lf_value, Evidence.PROXY if lf_value is not None else Evidence.MISSING,
            unit="ratio",
            note="Seat utilization proxy, not direct terminal-capacity evidence.",
        ),
        passengers_per_departure=as_metric_value(
            ppd_value, Evidence.PROXY if ppd_value is not None else Evidence.MISSING, unit="passengers/departure",
        ),
        departure_delay_rate=as_metric_value(
            delay_rate_value, Evidence.DIRECT if delay_rate_value is not None else Evidence.MISSING, unit="ratio",
        ),
        median_taxi_out_minutes=as_metric_value(
            median_taxi, Evidence.DIRECT if median_taxi is not None else Evidence.MISSING, unit="minutes",
        ),
        cancellation_rate=as_metric_value(
            cancellation_rate_value, Evidence.DIRECT if cancellation_rate_value is not None else Evidence.MISSING,
            unit="ratio",
        ),
        delay_cause_breakdown=delay_causes,
        faa_forecast_passenger_cagr=as_metric_value(
            forecast_cagr_value, Evidence.DIRECT if forecast_cagr_value is not None else Evidence.MISSING,
            unit="ratio", definition="FAA TAF forecast, not observed demand",
        ),
    )

    return {
        "profile": profile,
        "limitations": limitations,
        "identity_source": identity_source,
    }
