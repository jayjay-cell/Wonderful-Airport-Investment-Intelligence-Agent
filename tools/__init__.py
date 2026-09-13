# ORIENTATION: THE TOOL REGISTRY. ALL_TOOLS is the list the ReAct agent binds and calls directly.
"""Aero Intel's domain tools, LangChain @tool-decorated directly on the
functions that do the real work -- one file per capability, no separate
wrapper layer. The ReAct loop (agent/graph.py) calls these directly.

Each tool fetches from data/ and, where relevant, scores via core/scoring.py.
A tool's job ends at returning the structured result -- no tool decides the
final answer's wording; that's the model's job.

6 tools. Two deletions since the previous count of 7/9, both because
the underlying calculation they depended on was removed on explicit
instruction ("only calculation is comparison and ranking"):

1. assess_airport_opportunity_tool deleted outright. It ran a 7-stage
   classification pipeline (Demand -> Pressure -> Need -> Bottleneck ->
   Fit -> Gates -> Actionability -> Confidence -> Final label) most of
   which gated on ResearchEvidence that was never actually populated
   anywhere, so most branches were structurally unreachable in real use.
   A single-airport investment question ("is SFO a good investment", "what's
   the unmet demand at SFO and why") is now answered by the model composing
   get_airport_profile_tool (real numbers) + research_airport_facts_tool
   (real context), not a dedicated scoring tool -- per explicit instruction
   that simple ad hoc explanation from structured data is the LLM's job,
   not a new core/ function's.

2. rank_airports_by_metric was already merged into rank_airports_tool (one
   dispatcher, metric/investment_focus switch) in an earlier pass;
   tools/rank_by_metric.py still holds that single-metric logic as a plain
   function called internally.

3. calculate_long_haul_share_tool was already deleted in an earlier pass --
   long-haul share is computed once inside get_airport_profile and returned
   as two of its fields.

The two remaining calculations -- congestion_score() and opportunity_score()
(core/scoring.py) -- are used ONLY by compare_airports_tool and
rank_airports_tool respectively. Nothing else in this codebase computes a
score.
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
