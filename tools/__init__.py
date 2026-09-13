"""Aero Intel's domain tools, LangChain @tool-decorated directly on the
functions that do the real work -- one file per capability, no separate
wrapper layer. ALL_TOOLS is the registry the ReAct loop (agent/graph.py)
binds and calls directly.

Each tool fetches from data/ and, where relevant, scores via
core/scoring.py. A tool's job ends at returning a structured result -- no
tool decides the final answer's wording; that's the model's job.

Six tools cover the full surface: lookup (find_airports,
get_airport_profile, find_nearby_airports), comparison (compare_airports),
ranking (rank_airports, which also handles single-metric ranking via its
metric parameter -- tools/rank_by_metric.py holds that logic as a plain
function called internally, not a separate registered tool), and research
(research_airport_facts). Long-haul share is a field returned by
get_airport_profile rather than its own tool, since it is derived from the
same T-100 route data that tool already fetches.

There is no dedicated single-airport opportunity-scoring tool. A
single-airport investment question is answered by the model composing
get_airport_profile_tool (real numbers) with research_airport_facts_tool
(qualitative context) and explaining the result itself -- the only
computed scores in this codebase are congestion_score() and
opportunity_score() (core/scoring.py), used by compare_airports_tool and
rank_airports_tool respectively.
"""

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
    research_airport_facts_tool,
]
