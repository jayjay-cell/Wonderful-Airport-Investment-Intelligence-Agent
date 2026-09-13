from core.models import (
    Actionability,
    Airport,
    Bottleneck,
    ClassificationResult,
    Confidence,
    Evidence,
    FinalClassification,
    InvestmentFit,
    InvestmentFocus,
    Level,
    ObjectiveType,
    OpportunityAssessment,
)
from core.ranking import rank_candidates


def _assessment(code, need_level, passenger_pressure, demand_level=Level.HIGH,
                 evidence=Evidence.DIRECT) -> OpportunityAssessment:
    airport = Airport(code=code, name=code, city=code, state="XX")
    return OpportunityAssessment(
        airport=airport,
        investment_focus=InvestmentFocus.TERMINAL,
        objective=ObjectiveType.PASSENGER_CAPACITY,
        demand_level=ClassificationResult(level=demand_level, evidence=Evidence.DIRECT, reasoning="x"),
        passenger_side_pressure=ClassificationResult(level=passenger_pressure, evidence=evidence, reasoning="x"),
        flight_side_pressure=ClassificationResult(level=Level.LOW, evidence=Evidence.DIRECT, reasoning="x"),
        need_level=ClassificationResult(level=need_level, evidence=Evidence.DIRECT, reasoning="x"),
        likely_bottleneck=Bottleneck.TERMINAL,
        bottleneck_reasoning="x",
        investment_fit=InvestmentFit.LIKELY,
        actionability=Actionability.ACTIONABLE,
        confidence=Confidence.HIGH,
        confidence_reason="x",
        final_classification=FinalClassification.STRONG_CANDIDATE,
    )


def test_higher_need_level_ranks_first():
    a = _assessment("AAA", Level.HIGH, Level.HIGH)
    b = _assessment("BBB", Level.MEDIUM, Level.HIGH)
    ranked = rank_candidates([b, a], InvestmentFocus.TERMINAL, {})
    assert [x.airport.code for x in ranked] == ["AAA", "BBB"]


def test_same_need_level_breaks_tie_on_passenger_pressure():
    a = _assessment("AAA", Level.HIGH, Level.MEDIUM)
    b = _assessment("BBB", Level.HIGH, Level.HIGH)
    ranked = rank_candidates([a, b], InvestmentFocus.TERMINAL, {})
    assert [x.airport.code for x in ranked] == ["BBB", "AAA"]


def test_direct_evidence_ranks_above_proxy_at_same_levels():
    a = _assessment("AAA", Level.HIGH, Level.HIGH, evidence=Evidence.PROXY)
    b = _assessment("BBB", Level.HIGH, Level.HIGH, evidence=Evidence.DIRECT)
    ranked = rank_candidates([a, b], InvestmentFocus.TERMINAL, {})
    assert [x.airport.code for x in ranked] == ["BBB", "AAA"]


def test_final_tiebreak_uses_passenger_volume():
    a = _assessment("AAA", Level.HIGH, Level.HIGH)
    b = _assessment("BBB", Level.HIGH, Level.HIGH)
    metrics = {
        "AAA": {"passenger_cagr": 0.05, "passenger_volume": 1_000_000},
        "BBB": {"passenger_cagr": 0.05, "passenger_volume": 2_000_000},
    }
    ranked = rank_candidates([a, b], InvestmentFocus.TERMINAL, metrics)
    assert [x.airport.code for x in ranked] == ["BBB", "AAA"]


def test_ranking_restricted_to_candidate_set_passed_in():
    """Ranking must never silently pull in airports beyond what was passed."""
    a = _assessment("AAA", Level.HIGH, Level.HIGH)
    ranked = rank_candidates([a], InvestmentFocus.TERMINAL, {})
    assert len(ranked) == 1
    assert ranked[0].airport.code == "AAA"


def test_identical_input_produces_identical_order():
    a = _assessment("AAA", Level.HIGH, Level.HIGH)
    b = _assessment("BBB", Level.MEDIUM, Level.HIGH)
    metrics = {
        "AAA": {"passenger_cagr": 0.05, "passenger_volume": 1_000_000},
        "BBB": {"passenger_cagr": 0.03, "passenger_volume": 500_000},
    }
    first = [x.airport.code for x in rank_candidates([a, b], InvestmentFocus.TERMINAL, metrics)]
    second = [x.airport.code for x in rank_candidates([a, b], InvestmentFocus.TERMINAL, metrics)]
    assert first == second
