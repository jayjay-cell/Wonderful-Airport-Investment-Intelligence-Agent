"""Confidence and final candidate classification.

Confidence reflects evidence QUALITY, not business attractiveness — a
low-opportunity airport with excellent data still gets High Confidence, and
a high-opportunity airport with weak data gets Low Confidence.
"""

from __future__ import annotations

from core.models import (
    Actionability,
    ClassificationResult,
    Confidence,
    Evidence,
    FinalClassification,
    HardGate,
    InvestmentFit,
    Level,
)


def derive_confidence(
    demand: ClassificationResult,
    passenger_pressure: ClassificationResult,
    flight_pressure: ClassificationResult,
    critical_data_conflict: bool,
) -> tuple[Confidence, str]:
    if critical_data_conflict:
        return Confidence.INSUFFICIENT_EVIDENCE, (
            "A critical field required for this classification is "
            "unresolvably conflicted or missing."
        )

    dimensions = [demand, passenger_pressure, flight_pressure]

    if any(d.level == Level.INSUFFICIENT for d in dimensions):
        return Confidence.LOW, (
            "At least one core dimension could not be classified due to "
            "missing data coverage."
        )

    proxy_count = sum(1 for d in dimensions if d.evidence == Evidence.PROXY)

    if proxy_count == 0:
        return Confidence.HIGH, (
            "Core data is current and complete, and all central "
            "conclusions rely on direct evidence with no unresolved "
            "conflict."
        )
    if proxy_count <= 1:
        return Confidence.MEDIUM, (
            "Core data exists, but at least one central conclusion relies "
            "on a proxy signal rather than direct capacity evidence."
        )
    return Confidence.LOW, (
        f"{proxy_count} of {len(dimensions)} core dimensions rely on proxy "
        f"signals rather than direct evidence, and/or coverage is limited."
    )


def derive_final_classification(
    need_level: Level,
    investment_fit: InvestmentFit,
    actionability: Actionability,
    hard_gates: list[HardGate],
    confidence: Confidence,
) -> FinalClassification:
    gate_triggered = any(g.triggered for g in hard_gates)

    if confidence == Confidence.INSUFFICIENT_EVIDENCE:
        return FinalClassification.INSUFFICIENT_EVIDENCE

    if need_level == Level.LOW or investment_fit == InvestmentFit.MISMATCH:
        return FinalClassification.WEAK_CANDIDATE

    if need_level == Level.HIGH and (gate_triggered or actionability == Actionability.BLOCKED):
        return FinalClassification.HIGH_NEED_LOW_ACTIONABILITY

    if (
        need_level == Level.HIGH
        and investment_fit in (InvestmentFit.CONFIRMED, InvestmentFit.LIKELY)
        and actionability == Actionability.ACTIONABLE
        and not gate_triggered
        and confidence in (Confidence.HIGH, Confidence.MEDIUM)
    ):
        return FinalClassification.STRONG_CANDIDATE

    if (
        need_level in (Level.MEDIUM, Level.HIGH)
        and investment_fit in (InvestmentFit.CONFIRMED, InvestmentFit.LIKELY)
        and actionability in (Actionability.ACTIONABLE, Actionability.CONDITIONAL)
    ):
        return FinalClassification.PROMISING

    return FinalClassification.MIXED
