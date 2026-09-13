# ORIENTATION: THE TOOL REGISTRY. ALL_TOOLS is the list the ReAct agent binds and calls directly.
"""Aero Intel's domain tools, LangChain @tool-decorated directly on the
functions that do the real work -- one file per capability, no separate
wrapper layer. The ReAct loop (agent/graph.py) calls these directly; there
is no Planner/Executor/Answer staging in front of them (see agent/graph.py's
docstring for why that staging was removed).

Each tool: fetches from data/, computes/classifies via core/, returns a
plain dict the model reads directly. No tool here decides what the FINAL
answer is -- core/ owns every calculation and classification; a tool's job
ends at returning the structured result.

7 tools, not 9: two mergers, both for the same reason -- eliminate two
similarly-shaped tools competing for the same question, which is what
caused a real bug (the model skipping compare_airports_tool in favor of
calling get_airport_profile_tool twice).

1. rank_airports_by_metric merged into rank_airports_tool as one dispatcher
   with a metric/investment_focus switch. tools/rank_by_metric.py still
   holds the single-metric logic as a plain function, called internally --
   same pattern as get_airport_profile (a real building block, not a
   duplicate entry point the model can pick wrong).

2. calculate_long_haul_share_tool was deleted outright. get_airport_profile
   already fetched the exact per-route T-100 data long-haul share needs
   (to compute departures/passengers/seats totals) and threw the route-level
   detail away right after -- a second tool was re-fetching the same data
   just to re-derive it. Long-haul share is now computed once, inside
   get_airport_profile, and returned as two of its fields
   (long_haul_share_departures, long_haul_share_passengers).
"""

from tools.assess_opportunity import assess_airport_opportunity_tool
from tools.compare_airports import compare_airports_tool
from tools.find_airports import find_airports_tool
from tools.find_nearby_airports import find_nearby_airports_tool
from tools.get_airport_profile import get_airport_profile_tool
from tools.rank_airports import rank_airports_tool
from tools.research_facts import research_airport_facts_tool

ALL_TOOLS = [
    find_airports_tool,
    get_airport_profile_tool,
    compare_airports_tool,
    rank_airports_tool,
    find_nearby_airports_tool,
    assess_airport_opportunity_tool,
    research_airport_facts_tool,
]
