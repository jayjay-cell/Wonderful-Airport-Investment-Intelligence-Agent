"""Tests for rank_airports_by_metric — the generic national-ranking
capability that fixes the "largest airport" gap. All fixtures mocked; no
live network."""

from __future__ import annotations

from unittest.mock import patch

from core.models import HubClass


def _fake_records():
    return [
        {"code": "ATL", "name": "Atlanta", "state": "GA", "hub_class": HubClass.LARGE,
         "enplanements_current_year": 50_000_000, "enplanements_prior_year": 48_000_000, "pct_change": 0.0417},
        {"code": "LAX", "name": "Los Angeles", "state": "CA", "hub_class": HubClass.LARGE,
         "enplanements_current_year": 37_000_000, "enplanements_prior_year": 40_000_000, "pct_change": -0.075},
        {"code": "ANC", "name": "Anchorage", "state": "AK", "hub_class": HubClass.MEDIUM,
         "enplanements_current_year": 2_700_000, "enplanements_prior_year": 2_600_000, "pct_change": 0.038},
    ]


def test_rank_by_enplanements_national():
    from agent.tools.rank_by_metric import rank_airports_by_metric

    with patch("data.clients.faa_enplanements.get_all_enplanements", return_value=_fake_records()):
        result = rank_airports_by_metric(metric="enplanements", limit=10)

    assert "error" not in result
    assert result["results"][0]["airport_code"] == "ATL"
    assert result["results"][0]["value"] == 50_000_000
    assert result["scope"] == "United States"


def test_rank_by_metric_unsupported_metric_returns_ambiguous_error():
    from agent.tools.rank_by_metric import rank_airports_by_metric

    result = rank_airports_by_metric(metric="physical_area")
    assert result["error"] == "AMBIGUOUS_QUERY"
    assert "physical_area" in result["detail"]


def test_rank_by_passenger_growth():
    from agent.tools.rank_by_metric import rank_airports_by_metric

    with patch("data.clients.faa_enplanements.get_all_enplanements", return_value=_fake_records()):
        result = rank_airports_by_metric(metric="passenger_growth", limit=10)

    assert result["results"][0]["airport_code"] == "ATL"  # highest pct_change


def test_rank_by_metric_state_filter():
    from agent.tools.rank_by_metric import rank_airports_by_metric

    def fake_get_all(state=None):
        records = _fake_records()
        if state:
            return [r for r in records if r["state"] == state]
        return records

    with patch("data.clients.faa_enplanements.get_all_enplanements", side_effect=fake_get_all):
        result = rank_airports_by_metric(metric="enplanements", state="AK")

    assert len(result["results"]) == 1
    assert result["results"][0]["airport_code"] == "ANC"


def test_rank_by_metric_limit_is_bounded():
    from agent.tools.rank_by_metric import rank_airports_by_metric

    with patch("data.clients.faa_enplanements.get_all_enplanements", return_value=_fake_records()):
        result = rank_airports_by_metric(metric="enplanements", limit=1000)

    # limit clamped to 50 max internally, but only 3 fake records exist anyway
    assert len(result["results"]) == 3


def test_rank_by_expensive_metric_uses_bounded_shortlist():
    from agent.tools.rank_by_metric import rank_airports_by_metric

    ontime_calls = []

    def fake_get_on_time(code, year, month):
        ontime_calls.append(code)
        return {"delay_rate": 0.1 if code == "ATL" else 0.2}

    with patch("data.clients.faa_enplanements.get_all_enplanements", return_value=_fake_records()), \
         patch("data.clients.bts_on_time.get_on_time_records", side_effect=fake_get_on_time):
        result = rank_airports_by_metric(metric="delay_rate", limit=10)

    assert "error" not in result
    assert len(ontime_calls) == 3  # all 3 fake records fit within the shortlist limit
    assert "methodology_note" in result
