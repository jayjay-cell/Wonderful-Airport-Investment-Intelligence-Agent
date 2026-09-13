"""LangChain @tool wrappers over the 6 domain tools. Thin adapters only —
they convert LangChain's string/JSON tool-call args into the domain
functions' native Python args and serialize results back to text for the
LLM. No scoring or business logic lives here (that's in core/ and the
domain-tool modules themselves).
"""

from __future__ import annotations

import json

from langchain_core.tools import tool
from pydantic import BaseModel


def _serialize(obj):
    """Converts pydantic models (Airport, OpportunityAssessment, etc.) and
    nested structures to plain JSON-able dicts for the LLM to read."""
    if isinstance(obj, BaseModel):
        return json.loads(obj.model_dump_json())
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    return obj


@tool
def find_airports(region: str = "", state: str = "", commercial_only: bool = True) -> str:
    """Find commercial-service US airports by region (e.g. "New England") or
    by a single state name/code. Provide either region or state, not both.
    Returns airport identities, location, and hub class. Use this first
    when a question refers to a region or state rather than a specific
    airport code."""
    from agent.tools.find_airports import find_airports as _impl
    result = _impl(region=region or None, state=state or None, commercial_only=commercial_only)
    return json.dumps(_serialize(result), default=str)


@tool
def get_airport_profile(airport_code: str) -> str:
    """Get a structured profile for one airport: passenger/flight volumes,
    growth, load factor, delays, cancellations, and FAA forecast, each
    labeled direct/proxy/missing. Use for a single-airport factual lookup
    that isn't specifically about long-haul share or a full opportunity
    assessment."""
    from agent.tools.airport_profile import get_airport_profile as _impl
    result = _impl(airport_code)
    return json.dumps(_serialize(result), default=str)


@tool
def calculate_long_haul_share(
    airport_code: str,
    threshold_miles: float = 1500,
    basis: str = "departures",
) -> str:
    """Calculate the share of an airport's scheduled passenger flights that
    are "long-haul" (distance >= threshold_miles, default 1500 statute
    miles). basis is "departures" or "passengers". Cargo-only operations
    are excluded. Use this for any question about long-haul percentage or
    share."""
    from agent.tools.long_haul import calculate_long_haul_share as _impl
    result = _impl(airport_code, threshold_miles=threshold_miles, basis=basis)
    return json.dumps(_serialize(result), default=str)


@tool
def compare_airports(airport_codes: list[str]) -> str:
    """Compare congestion/pressure between 2 or more airports over the same
    period, using consistent metric definitions. Use for direct
    airport-vs-airport comparison questions (e.g. "compare LAX and SNA
    congestion")."""
    from agent.tools.compare_airports import compare_airports as _impl
    result = _impl(airport_codes)
    return json.dumps(_serialize(result), default=str)


@tool
def rank_airports(
    region: str = "",
    state: str = "",
    investment_focus: str = "general_modernization",
    top_n: int = 10,
) -> str:
    """Rank airports within a region or state as modernization investment
    candidates. investment_focus is one of: terminal, gates,
    runway_airfield, operations_technology, general_modernization. Use for
    "which airports are candidates for X expansion" type questions."""
    from agent.tools.rank_airports import rank_airports as _impl
    result = _impl(region=region or None, state=state or None,
                    investment_focus=investment_focus, top_n=top_n)
    return json.dumps(_serialize(result), default=str)


@tool
def assess_airport_opportunity(
    airport_code: str,
    investment_focus: str = "general_modernization",
) -> str:
    """Run a full Airport Modernization Opportunity Assessment for ONE
    airport: demand, passenger-side pressure, flight-side pressure, likely
    bottleneck, investment fit, actionability, confidence, and final
    classification. Use for "why is X a good/bad candidate" or "what's the
    unmet demand at X and why" type questions about a single airport."""
    from agent.tools.assess_opportunity import assess_airport_opportunity as _impl
    result = _impl(airport_code, investment_focus=investment_focus, research=False)
    return json.dumps(_serialize(result), default=str)


ALL_TOOLS = [
    find_airports,
    get_airport_profile,
    calculate_long_haul_share,
    compare_airports,
    rank_airports,
    assess_airport_opportunity,
]
