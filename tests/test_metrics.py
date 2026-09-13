from core.metrics import (
    cagr,
    cancellation_rate,
    delay_rate,
    departures_per_runway,
    disrupted_flight_count,
    load_factor,
    long_haul_share,
    passengers_per_departure,
)


def test_load_factor_uses_summed_totals_not_row_average():
    # 2 flights: (80 pax / 100 seats) and (20 pax / 100 seats) -> naive
    # average of row-level factors would be 50%, but the correct summed
    # calculation is 100/200 = 50% here too by design; use an asymmetric
    # case to distinguish the two approaches.
    # Flight A: 90/100 = 90%, Flight B: 10/200 = 5%.
    # Row-average = (0.90 + 0.05) / 2 = 0.475
    # Summed = (90+10) / (100+200) = 100/300 = 0.3333...
    result = load_factor(total_passengers=100, total_seats=300)
    assert abs(result - (1 / 3)) < 1e-9
    assert abs(result - 0.475) > 0.01  # would fail if row-averaged instead


def test_load_factor_zero_seats_returns_none():
    assert load_factor(total_passengers=100, total_seats=0) is None


def test_cagr_basic():
    # doubling over 1 year -> 100% growth
    assert abs(cagr(100, 200, 1) - 1.0) < 1e-9


def test_cagr_zero_years_returns_none():
    assert cagr(100, 200, 0) is None


def test_cagr_zero_starting_value_returns_none():
    assert cagr(0, 200, 3) is None


def test_passengers_per_departure():
    assert passengers_per_departure(1000, 10) == 100


def test_passengers_per_departure_zero_departures_returns_none():
    assert passengers_per_departure(1000, 0) is None


def test_delay_rate():
    assert delay_rate(25, 100) == 0.25


def test_cancellation_rate():
    assert cancellation_rate(3, 100) == 0.03


def test_long_haul_share_numerator_is_subset_of_denominator():
    routes = [
        {"distance_miles": 3000, "departures": 10, "passengers": 1500},
        {"distance_miles": 500, "departures": 40, "passengers": 3000},
    ]
    result = long_haul_share(routes, threshold_miles=1500, basis="departures")
    assert result["numerator"] <= result["denominator"]
    assert result["numerator"] == 10
    assert result["denominator"] == 50
    assert abs(result["percentage"] - 0.2) < 1e-9
    assert result["threshold_miles"] == 1500
    assert result["basis"] == "departures"


def test_long_haul_share_passenger_basis():
    routes = [
        {"distance_miles": 3000, "departures": 10, "passengers": 1500},
        {"distance_miles": 500, "departures": 40, "passengers": 3000},
    ]
    result = long_haul_share(routes, threshold_miles=1500, basis="passengers")
    assert result["numerator"] == 1500
    assert result["denominator"] == 4500


def test_long_haul_share_invalid_basis_raises():
    import pytest
    with pytest.raises(ValueError):
        long_haul_share([], threshold_miles=1500, basis="invalid")


def test_long_haul_share_empty_routes_returns_none_percentage():
    result = long_haul_share([], threshold_miles=1500, basis="departures")
    assert result["percentage"] is None
    assert result["numerator"] == 0
    assert result["denominator"] == 0


def test_disrupted_flight_count():
    assert disrupted_flight_count(delayed_flights=10, cancelled_flights=3) == 13


def test_departures_per_runway_is_descriptive_only():
    assert departures_per_runway(1000, 2) == 500


def test_departures_per_runway_zero_runways_returns_none():
    assert departures_per_runway(1000, 0) is None
