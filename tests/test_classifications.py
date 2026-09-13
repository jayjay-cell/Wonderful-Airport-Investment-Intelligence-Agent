import yaml
import pytest

from core.classifications import (
    classify_congestion,
    classify_demand,
    classify_flight_side_pressure,
    classify_need,
    classify_passenger_side_pressure,
)
from core.models import Evidence, Level


@pytest.fixture(scope="module")
def config():
    with open("config/methodology.yaml") as f:
        return yaml.safe_load(f)


def test_demand_high_from_passenger_cagr(config):
    result = classify_demand(passenger_cagr=0.06, faa_forecast_cagr=None, config=config)
    assert result.level == Level.HIGH


def test_demand_high_from_combined_thresholds(config):
    result = classify_demand(passenger_cagr=0.025, faa_forecast_cagr=0.035, config=config)
    assert result.level == Level.HIGH


def test_demand_medium_when_positive_but_below_high(config):
    result = classify_demand(passenger_cagr=0.01, faa_forecast_cagr=None, config=config)
    assert result.level == Level.MEDIUM


def test_demand_low_when_negative(config):
    result = classify_demand(passenger_cagr=-0.02, faa_forecast_cagr=None, config=config)
    assert result.level == Level.LOW


def test_demand_insufficient_when_no_data(config):
    result = classify_demand(passenger_cagr=None, faa_forecast_cagr=None, config=config)
    assert result.level == Level.INSUFFICIENT
    assert result.evidence == Evidence.MISSING


def test_passenger_pressure_direct_evidence_path(config):
    result = classify_passenger_side_pressure(config, terminal_utilization=0.95)
    assert result.level == Level.HIGH
    assert result.evidence == Evidence.DIRECT


def test_passenger_pressure_single_proxy_signal_cannot_reach_high(config):
    """Critical rule: no single proxy signal may independently produce a
    High classification. Only load_factor triggered -> must be Medium, not
    High, and must be labeled proxy with a caveat."""
    result = classify_passenger_side_pressure(
        config,
        load_factor=0.90,  # triggers (>= 0.85)
        passengers_per_departure_growth=0.01,  # does not trigger (< 0.05)
        passenger_departure_cagr_gap_pp=1.0,  # does not trigger (< 3)
    )
    assert result.level == Level.MEDIUM
    assert result.level != Level.HIGH
    assert result.evidence == Evidence.PROXY
    assert result.caveat is not None
    assert "not a direct measurement of terminal" in result.caveat


def test_passenger_pressure_all_three_proxy_signals_reach_high(config):
    result = classify_passenger_side_pressure(
        config,
        load_factor=0.90,
        passengers_per_departure_growth=0.06,
        passenger_departure_cagr_gap_pp=4.0,
    )
    assert result.level == Level.HIGH
    assert result.evidence == Evidence.PROXY  # still proxy, even at High
    assert len(result.signals_triggered) == 3


def test_load_factor_signal_never_labeled_direct_terminal_evidence(config):
    """Load factor measures seat utilization, not terminal capacity — the
    caveat must always be present whenever load_factor drives the result."""
    result = classify_passenger_side_pressure(config, load_factor=0.99)
    assert result.evidence == Evidence.PROXY
    assert "seat utilization" in result.caveat or "not a direct measurement" in result.caveat


def test_flight_pressure_single_proxy_signal_is_medium_not_high(config):
    result = classify_flight_side_pressure(
        config,
        departure_delay_rate=0.30,  # triggers alone
        median_taxi_out_minutes=5,  # does not trigger
        cancellation_rate=0.01,  # does not trigger
    )
    assert result.level == Level.MEDIUM
    assert result.evidence == Evidence.PROXY


def test_flight_pressure_direct_runway_evidence(config):
    result = classify_flight_side_pressure(config, runway_utilization=0.95)
    assert result.level == Level.HIGH
    assert result.evidence == Evidence.DIRECT


def test_flight_pressure_official_bottleneck_finding_is_direct(config):
    result = classify_flight_side_pressure(config, official_bottleneck_finding=True)
    assert result.level == Level.HIGH
    assert result.evidence == Evidence.DIRECT


def test_congestion_reuses_flight_side_signal_set(config):
    result = classify_congestion(
        config,
        departure_delay_rate=0.30,
        median_taxi_out_minutes=25,
        cancellation_rate=0.01,
    )
    assert result.level == Level.HIGH  # 2 signals triggered


def test_need_level_combination_table(config):
    result = classify_need(Level.HIGH, Level.HIGH, config)
    assert result.level == Level.HIGH

    result = classify_need(Level.MEDIUM, Level.MEDIUM, config)
    assert result.level == Level.MEDIUM

    result = classify_need(Level.LOW, Level.LOW, config)
    assert result.level == Level.LOW
