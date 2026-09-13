from core.confidence import derive_confidence, derive_final_classification
from core.gates import derive_actionability, evaluate_hard_gates
from core.models import (
    Actionability,
    Bottleneck,
    ClassificationResult,
    Confidence,
    Evidence,
    FinalClassification,
    InvestmentFit,
    Level,
)


def test_hard_gate_does_not_erase_need_level():
    """A High-Need airport with a triggered hard gate must resolve to
    'High need, low actionability', not be silently dropped or misreported
    as Weak."""
    gates = evaluate_hard_gates(
        investment_fit=InvestmentFit.LIKELY,
        likely_bottleneck=Bottleneck.WEATHER,
        research_evidence=[],
        critical_data_conflict=False,
    )
    assert any(g.triggered for g in gates)

    actionability = derive_actionability(InvestmentFit.LIKELY, gates, research_invoked=True)
    assert actionability == Actionability.BLOCKED

    final = derive_final_classification(
        need_level=Level.HIGH,
        investment_fit=InvestmentFit.LIKELY,
        actionability=actionability,
        hard_gates=gates,
        confidence=Confidence.HIGH,
    )
    assert final == FinalClassification.HIGH_NEED_LOW_ACTIONABILITY
    assert final != FinalClassification.WEAK_CANDIDATE


def test_no_hard_gate_and_strong_fit_reaches_strong_candidate():
    gates = evaluate_hard_gates(
        investment_fit=InvestmentFit.CONFIRMED,
        likely_bottleneck=Bottleneck.TERMINAL,
        research_evidence=[],
        critical_data_conflict=False,
    )
    assert not any(g.triggered for g in gates)

    actionability = derive_actionability(InvestmentFit.CONFIRMED, gates, research_invoked=True)
    assert actionability == Actionability.ACTIONABLE

    final = derive_final_classification(
        need_level=Level.HIGH,
        investment_fit=InvestmentFit.CONFIRMED,
        actionability=actionability,
        hard_gates=gates,
        confidence=Confidence.HIGH,
    )
    assert final == FinalClassification.STRONG_CANDIDATE


def test_no_research_invoked_yields_unknown_actionability_not_a_guess():
    gates = evaluate_hard_gates(
        investment_fit=InvestmentFit.LIKELY,
        likely_bottleneck=Bottleneck.TERMINAL,
        research_evidence=[],
        critical_data_conflict=False,
    )
    actionability = derive_actionability(InvestmentFit.LIKELY, gates, research_invoked=False)
    assert actionability == Actionability.UNKNOWN


def test_critical_conflict_forces_insufficient_evidence_confidence():
    demand = ClassificationResult(level=Level.HIGH, evidence=Evidence.DIRECT, reasoning="x")
    pax = ClassificationResult(level=Level.HIGH, evidence=Evidence.DIRECT, reasoning="x")
    flight = ClassificationResult(level=Level.HIGH, evidence=Evidence.DIRECT, reasoning="x")

    confidence, reason = derive_confidence(demand, pax, flight, critical_data_conflict=True)
    assert confidence == Confidence.INSUFFICIENT_EVIDENCE

    final = derive_final_classification(
        need_level=Level.HIGH,
        investment_fit=InvestmentFit.CONFIRMED,
        actionability=Actionability.ACTIONABLE,
        hard_gates=[],
        confidence=confidence,
    )
    assert final == FinalClassification.INSUFFICIENT_EVIDENCE


def test_weak_candidate_when_need_is_low():
    demand = ClassificationResult(level=Level.LOW, evidence=Evidence.DIRECT, reasoning="x")
    pax = ClassificationResult(level=Level.LOW, evidence=Evidence.DIRECT, reasoning="x")
    flight = ClassificationResult(level=Level.LOW, evidence=Evidence.DIRECT, reasoning="x")
    confidence, _ = derive_confidence(demand, pax, flight, critical_data_conflict=False)

    final = derive_final_classification(
        need_level=Level.LOW,
        investment_fit=InvestmentFit.LIKELY,
        actionability=Actionability.ACTIONABLE,
        hard_gates=[],
        confidence=confidence,
    )
    assert final == FinalClassification.WEAK_CANDIDATE
