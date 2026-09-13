"""Proves rank_airports only fully assesses the configured shortlist size,
not every candidate in a region — the fix for excessively expensive
regional rankings. Mocks find_airports and assess_airport_opportunity so
this runs fast with no network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.models import (
    Actionability,
    Airport,
    Bottleneck,
    ClassificationResult,
    Confidence,
    Evidence,
    FinalClassification,
    HubClass,
    InvestmentFit,
    InvestmentFocus,
    Level,
    ObjectiveType,
    OpportunityAssessment,
)


def _fake_airport(code: str) -> Airport:
    return Airport(code=code, name=code, city=code, state="XX", hub_class=HubClass.SMALL)


def _fake_assessment(code: str, demand: Level) -> dict:
    airport = _fake_airport(code)
    assessment = OpportunityAssessment(
        airport=airport,
        investment_focus=InvestmentFocus.GENERAL_MODERNIZATION,
        objective=ObjectiveType.BOTH,
        demand_level=ClassificationResult(level=demand, evidence=Evidence.DIRECT, reasoning="x"),
        passenger_side_pressure=ClassificationResult(level=Level.MEDIUM, evidence=Evidence.DIRECT, reasoning="x"),
        flight_side_pressure=ClassificationResult(level=Level.MEDIUM, evidence=Evidence.DIRECT, reasoning="x"),
        need_level=ClassificationResult(level=demand, evidence=Evidence.DIRECT, reasoning="x"),
        likely_bottleneck=Bottleneck.UNKNOWN,
        bottleneck_reasoning="x",
        investment_fit=InvestmentFit.LIKELY,
        actionability=Actionability.UNKNOWN,
        confidence=Confidence.MEDIUM,
        confidence_reason="x",
        final_classification=FinalClassification.PROMISING,
    )
    from core.models import AirportProfile
    profile = AirportProfile(airport=airport, period="2023-2025")
    return {"assessment": assessment, "profile": profile}


def test_rank_airports_default_shortlist_is_three():
    from agent.tools import rank_airports as rank_mod

    fake_airports = [_fake_airport(f"A{i}") for i in range(10)]
    discovery = {
        "airports": fake_airports,
        "candidate_set_description": "Test Region",
        "count": 10,
        "enplanements_by_code": {a.code: 10000 - i for i, a in enumerate(fake_airports)},
    }

    full_assess_calls = []

    def fake_assess(code, investment_focus="general_modernization", research=False, include_bts=True):
        if include_bts:
            full_assess_calls.append(code)
        return _fake_assessment(code, Level.HIGH)

    with patch.object(rank_mod, "find_airports", return_value=discovery), \
         patch.object(rank_mod, "assess_airport_opportunity", side_effect=fake_assess), \
         patch.object(rank_mod.bts_t100, "get_routes_bulk", return_value={}), \
         patch.object(rank_mod.bts_on_time, "get_on_time_records_bulk", return_value={}):
        result = rank_mod.rank_airports(region="Test Region", investment_focus="general_modernization")

    assert "error" not in result
    assert len(full_assess_calls) == 3, "default shortlist must fully assess exactly 3 airports, not all 10"
    assert result["fully_assessed_count"] == 3
    assert "tiered_methodology_note" in result
    assert "3" in result["tiered_methodology_note"]


def test_rank_airports_shortlist_size_is_configurable_and_capped():
    from agent.tools import rank_airports as rank_mod

    fake_airports = [_fake_airport(f"A{i}") for i in range(10)]
    discovery = {
        "airports": fake_airports,
        "candidate_set_description": "Test Region",
        "count": 10,
        "enplanements_by_code": {a.code: 10000 - i for i, a in enumerate(fake_airports)},
    }

    full_assess_calls = []

    def fake_assess(code, investment_focus="general_modernization", research=False, include_bts=True):
        if include_bts:
            full_assess_calls.append(code)
        return _fake_assessment(code, Level.HIGH)

    with patch.object(rank_mod, "find_airports", return_value=discovery), \
         patch.object(rank_mod, "assess_airport_opportunity", side_effect=fake_assess), \
         patch.object(rank_mod.bts_t100, "get_routes_bulk", return_value={}), \
         patch.object(rank_mod.bts_on_time, "get_on_time_records_bulk", return_value={}):
        # request 20 (above the hard ceiling) — must be clamped to _MAX_SHORTLIST_SIZE
        result = rank_mod.rank_airports(region="Test Region", shortlist_size=20)

    assert len(full_assess_calls) == rank_mod._MAX_SHORTLIST_SIZE


def test_rank_airports_screens_all_candidates_but_only_fully_assesses_shortlist():
    from agent.tools import rank_airports as rank_mod

    fake_airports = [_fake_airport(f"A{i}") for i in range(10)]
    discovery = {
        "airports": fake_airports,
        "candidate_set_description": "Test Region",
        "count": 10,
        "enplanements_by_code": {},
    }

    screen_calls = []
    full_calls = []

    def fake_assess(code, investment_focus="general_modernization", research=False, include_bts=True):
        (full_calls if include_bts else screen_calls).append(code)
        return _fake_assessment(code, Level.MEDIUM)

    with patch.object(rank_mod, "find_airports", return_value=discovery), \
         patch.object(rank_mod, "assess_airport_opportunity", side_effect=fake_assess), \
         patch.object(rank_mod.bts_t100, "get_routes_bulk", return_value={}), \
         patch.object(rank_mod.bts_on_time, "get_on_time_records_bulk", return_value={}):
        result = rank_mod.rank_airports(region="Test Region")

    assert len(screen_calls) == 10, "every candidate must be screened (cheap, FAA-only)"
    assert len(full_calls) == 3, "only the shortlist gets the expensive full assessment"
    assert result["candidate_count"] == 10
    assert result["screened_count"] == 10
