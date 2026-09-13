# Aero Intel — Design & Architecture

## What this is

A conversational agent for airport-investment analysts that answers questions
about US airport capacity, demand, and modernization opportunity using live
public aviation data (FAA + BTS), with every calculation done in deterministic
Python — never by the LLM.

The system produces an investment-screening signal: a starting point for
further diligence, not a proven-ROI calculation. It is deliberately
conservative about what it claims — see "What the system will not say" below.

## Architecture — a ReAct agent with a fixed tool surface

```
React UI
   │  POST /chat {session_id, message}   (or POST /chat/stream for SSE)
   ▼
FastAPI ── loads ConversationState (per session_id, in-memory)
   ▼
LangGraph create_react_agent, full message history replayed every turn
   │
   ├─→ tools/ (6 tools — thin wiring, no calculation logic of their own)
   │      │
   │      ├─→ core/ (pure, deterministic — no LLM, no network)
   │      │      congestion_score(), opportunity_score()
   │      │
   │      └─→ data/clients/ (FAA ×3, BTS ×2, live, cached)
   │
   ▼
reply ──→ saves updated ConversationState
```

The agent is a standard ReAct loop (`agent/graph.py`), not a custom
plan-validate-execute pipeline: the model reasons over the full conversation,
calls whichever of the six tools the question needs (zero or more, in
sequence), and composes the final answer itself from what the tools return.
This is a deliberate simplification — an earlier iteration used a
planner/executor split with a separate structured `QueryPlan` and a capped
operation count, which added real implementation complexity without a
corresponding gain: the model does not need to be prevented from calling
tools freely when the tools themselves are the only place a value can be
computed, and step growth is already bounded by LangGraph's recursion limit
(`MAX_STEPS`, `agent/graph.py`).

**Layer boundaries are load-bearing, not decorative.** `core/` never imports
LangChain and never touches the network — pure functions on typed Pydantic
models, runnable with zero network and zero LLM calls. That is what makes
"deterministic, not just LLM output" a checkable property rather than a
claim. `data/` fetches and validates but never interprets meaning. `tools/`
is the only layer that composes `core/`, `data/`, and the research tool
together; a tool's job ends at returning a structured result, never at
deciding the final answer's wording.

### What the LLM may and may not do

| Responsibility | Who does it |
|---|---|
| Understand the question, decide which tool(s) to call | LLM |
| Resolve follow-ups ("it", "the first one") against conversation history | LLM |
| Fetch data from FAA/BTS | Deterministic code (`data/`) |
| Compute metrics (CAGR, load factor, long-haul share, delay rate...) | Deterministic code (`core/metrics.py`) |
| Compute Congestion Score / Opportunity Score | Deterministic code (`core/scoring.py`) |
| Rank candidates | Deterministic code (`core/ranking.py`) |
| Explain the structured result in natural language | LLM |
| Find facts FAA/BTS don't hold | LLM (research tool) — sourced claims only, never fed into scoring |

The model never computes, estimates, or restates a score itself — it relays
the number and basis/missing fields a tool returns. This bounds hallucination
risk to phrasing, not to fabricated numbers: a tool result is either used
verbatim or the model states a limitation, never a plausible-sounding
invented value.

## Calculation model

### No multi-stage classification pipeline

There are exactly two computed scores in this system, both in
`core/scoring.py`, both 0–100:

- **`congestion_score(profile)`** — how congested an airport's operations
  currently are. An average of normalized departure delay rate, median
  taxi-out time, and cancellation rate. Used by `compare_airports_tool`.
- **`opportunity_score(profile, investment_focus)`** — how strong a
  modernization-investment candidate an airport is for a given focus
  (terminal, gates, runway/airfield, operations/technology, general
  modernization). A weighted blend of passenger growth, load factor,
  congestion, and (for terminal/gates only) long-haul share. Weights are
  named per focus so the result is always traceable to a specific number,
  not an opaque judgment call — see `core/scoring.py`'s `_FOCUS_WEIGHTS` and
  `config/methodology.yaml`.

An earlier design used a multi-stage classification pipeline instead —
Demand Level → Passenger/Flight-Side Pressure → Need Level → Likely
Bottleneck → Investment Fit → hard gates/soft flags → Confidence → a final
label like "Strong candidate." That pipeline was removed, not folded into
the current one: most of its later stages gated on typed research evidence
that nothing in the system ever populated, so those branches were
structurally unreachable in real use. The two-score model covers the same
underlying signals (growth, pressure, congestion) with a result that is
easier to verify end-to-end, since every score traces to a stated basis and
a stated set of missing inputs rather than to a chain of classification
labels.

**Missing data is never coerced to zero.** Every `Score` carries `value`
(`float | None`), `basis` (which metrics were used), `missing` (which were
unavailable), and `limitations` (plain-language caveats). If every relevant
input is missing, `value` is `None` — a visible, honest "not scoreable,"
never a silently wrong number.

**A missing input is dropped and the remaining weights renormalized, not
zeroed** — so a score stays comparable across candidates even with partial
data, but this also means two airports with very different amounts of
underlying data can land on similar-looking scores. Every `Score` therefore
also carries `inputs_available`, `inputs_expected`, and `coverage_ratio`
(`inputs_available / inputs_expected`), surfaced in `compare_airports_tool`'s
and `rank_airports_tool`'s output alongside the score itself. A high score
computed from 2 of 4 expected inputs is a genuinely weaker basis for a
decision than the same score computed from 4 of 4 — `coverage_ratio` makes
that visible instead of letting `value` alone imply equal confidence.

### Provenance on every metric

Every fetched or computed value (`core.models.MetricValue`) carries an
`Evidence` tag: `direct` (measured), `proxy` (an indirect signal used only
because no direct measurement exists — e.g. load factor as a stand-in for
terminal pressure, which measures aircraft seat utilization, not terminal
capacity), or `missing`. This distinction is carried through unchanged from
`data/` to the final answer and can never be silently upgraded from proxy to
direct.

### Ranking

`rank_airports_tool` (`tools/rank_airports.py`) scores every candidate with
`opportunity_score()` and sorts by the result — there is no separate
per-focus sort-key logic, since the score itself already encodes which
factors matter most for the requested focus. A profile that scores `None`
(unscoreable) sorts last, visibly, never silently dropped. Results are
phrased as "best among the airports evaluated," never "best in the US,"
unless a genuinely national candidate set was evaluated
(`core/ranking.py::scope_label`).

## The six tools

Thin LangChain `@tool`-decorated wrappers, one file per capability, no
separate wrapper layer — the ReAct loop calls these directly (`tools/`):

| Tool | Responsibility |
|---|---|
| `find_airports_tool` | Resolve a region/state/city/name into candidate airport identities |
| `get_airport_profile_tool` | Full metrics for one airport — demand, load factor, delays, long-haul share (both by departures and by passengers), FAA forecast — each field labeled direct/proxy/missing |
| `compare_airports_tool` | Side-by-side comparison for 2+ airports over a common period, including `congestion_score()`, with period mismatches flagged |
| `rank_airports_tool` | Opportunity ranking within a region/state (screens cheaply by passenger volume, then scores a bounded shortlist), or single-metric ranking (e.g. "largest airport by passengers") |
| `find_nearby_airports_tool` | Real computed great-circle distance between airports — never a recalled figure |
| `research_airport_facts_tool` | Bounded, citation-required web research (Gemini + Google Search grounding) for facts FAA/BTS don't hold — terminal counts, expansion projects, funding |

There is no dedicated single-airport opportunity-scoring tool. A
single-airport investment question ("is SFO a good investment", "what's the
unmet demand at SFO and why") is answered by the model composing
`get_airport_profile_tool` (real numbers) with `research_airport_facts_tool`
(qualitative context where relevant) and explaining the result directly —
simple arithmetic or comparison across the returned numbers is within the
model's role; inventing a new classification label or score is not.

## LLM provider fallback

Three free-tier providers in a fixed order (`agent/providers.py`): **Groq**
first (fast, generous free tier), then **Gemini**, then **OpenRouter**. Only
providers with a configured API key are included. Each client uses a short
explicit timeout (20s) and zero SDK-level retries, so a rate-limited or
unavailable provider is abandoned in seconds and the next one tried,
rather than hanging or retrying into the same wall.

A recognized "this provider is unavailable" error (rate limit, quota,
timeout, transport failure — including OpenRouter's HTTP-200-with-error-body
shape, which surfaces as a plain `ValueError` rather than a typed exception)
moves to the next provider. Any other exception propagates immediately: a
real bug should fail loudly, not be silently swallowed by a fallback loop
meant for provider outages.

If every provider fails, the turn returns a fixed, safe fallback message and
leaves the conversation history untouched so the next turn can retry
cleanly. The failure is logged server-side (never shown to the user) so a
real bug stays diagnosable rather than indistinguishable from an outage.

## Conversation memory

`ConversationState` (`agent/state.py`) holds the raw message history, and the
full list is replayed to the model on every turn (`agent/graph.py`) rather
than being condensed into an extracted summary. This is a deliberate choice:
a summarization step is one more place context can silently drift from what
was actually said, and replaying the real messages avoids that class of bug
entirely — a follow-up like "what about Boston" or "are you sure" is
resolved by the model re-reading the actual prior turns, not by trusting a
derived state object to have captured the relevant part correctly.

State is a process-local in-memory dict (`api/session_store.py`), keyed by an
unguessable `session_id` the server generates and hands back on the first
turn. Lost on restart, not multi-instance safe — acceptable for a
single-instance deliverable; a real deployment would move the same shape to
Redis or a database without changing anything above it.

**The system prompt is never written into stored conversation state.**
`build_agent` (`agent/graph.py`) passes the system prompt to
`create_react_agent` via its `prompt=` parameter, which LangGraph prepends
only to what's sent to the model on each call -- it does not become part of
the graph's own message list. `ConversationState.messages` holds only
user/assistant/tool turns. As a second safeguard, any system-role message is
explicitly filtered out before a turn's result is saved
(`_strip_system_messages`), so the system prompt can never accumulate into
stored state turn over turn.

**The one real, enforced limit on a turn's length is LangGraph's
`recursion_limit`** (`MAX_STEPS`, configurable via `AGENT_MAX_STEPS`,
default 8 -- doubled when passed to LangGraph since it counts a model step
and a tool step as separate nodes). Hitting it raises `GraphRecursionError`,
handled the same way as any other provider failure: a safe fallback reply,
conversation history left untouched.

## What the system will not say

- No guaranteed profitability
- No exact terminal/gate/runway capacity number when only a proxy signal exists
- No precise "unmet flights" count unless a direct authoritative source
  provides one
- No equating high traffic volume with high congestion — these are always
  reported as separate fields
- A tool result explicitly stating a limitation, or the model saying "I
  don't have enough information," is always a valid, non-apologetic answer

## Data sources

| Source | Access method | Used for |
|---|---|---|
| FAA Airports & Runways | Live REST API (ArcGIS FeatureServer), queried per airport | Identity, location, ownership |
| FAA Commercial Service Enplanements | Static annual XLSX, whole table cached once | Passenger volume, official FAA hub classification |
| FAA Terminal Area Forecast (TAF) | Static annual ZIP (XLSX inside), whole release cached once | Forecast passenger/operations growth |
| BTS Reporting Carrier On-Time Performance | 12 static monthly ZIPs, merged into one annual aggregate, cached once per year | Delay rate, cancellation rate, taxi-out time, delay-cause breakdown |
| BTS T-100 Segment | **Live, on-demand** — scripted submission of BTS's TranStats ASP.NET form (no public API exists for this dataset) | Route-level distance/departures/passengers/seats, long-haul share, load factor |

All five are fetched live, with the two nationwide sources (BTS T-100 and
BTS On-Time) cached by the shared national resource (year/month), not by
airport — one download serves every airport requested for that period,
single-flight-protected so concurrent requests for a cold resource don't
trigger duplicate downloads. See `data/cache.py` and the module docstrings in
`data/clients/bts_t100.py` and `data/clients/bts_on_time.py`.

### Long-haul share: the exact definition

**Share of performed passenger departures on routes of at least 1,500
statute miles, excluding cargo-only operations** (`get_airport_profile`,
`config/methodology.yaml`). Two words matter and are used consistently
everywhere in this system — the system prompt, tool docstrings, metric
definitions, and this document:

- **Performed**, not scheduled: BTS T-100 Segment publishes both
  `DEPARTURES_SCHEDULED` and `DEPARTURES_PERFORMED`; this system uses
  performed departures (flights that actually operated) as the denominator
  and numerator, per `data/clients/bts_t100.py::adapt_for_long_haul_share`.
- **Passenger, not all operations**: BTS T-100 Segment includes cargo-only
  operations (routes with departures but zero passengers). At a major cargo
  hub like Anchorage, cargo-only routes can account for roughly half of
  total departures. Naively computing long-haul share over all T-100
  records materially overstates the answer; filtering to passenger-carrying
  routes only (`data/clients/bts_t100.py::filter_passenger_routes`) is what
  makes the number correct, and is applied unconditionally for any
  long-haul or passenger-demand calculation.

## Known limitations

- **Conversation state does not survive a server restart** and is not
  multi-instance safe. Acceptable for a single-instance deliverable.
- **Cold-query latency.** All five data sources are fetched live with no
  pre-baked snapshot, so the first query touching a given year/month pays
  real network cost — the BTS T-100 form submission and BTS On-Time's
  12-month download in particular can each take tens of seconds on a cold
  cache. Subsequent queries for the same period are served from the
  in-memory cache and are fast. This is a deliberate tradeoff: the
  alternative (a curated snapshot of a handful of airports) would have
  meant the system could only answer questions about a fixed demo set,
  which contradicts the requirement to handle any US commercial airport.
- **Classification thresholds are unvalidated MVP defaults**
  (`config/methodology.yaml`) — chosen to produce reasonable score
  variation across large/medium/small-hub airports, not backtested against
  real outcome data.
- **Period coverage differs by source.** FAA enplanements are annual; BTS
  On-Time and T-100 are annual aggregates built from monthly files. Each
  `MetricValue` carries its own `source.coverage_period` -- reflecting
  actual coverage (e.g. "2025 (full year)" vs. "2025 (months [...]; missing
  [...])" when a BTS month failed to download) -- and `get_airport_profile`
  and `compare_airports` both surface an explicit limitation when sources
  don't share a period.
- **A high score can still rest on partial data.** Renormalizing weights
  around whatever inputs are available (see "Missing data is never coerced
  to zero" above) keeps scores comparable, but two candidates with very
  different `coverage_ratio` values can score similarly. Ranking by `value`
  alone, without checking `coverage_ratio`, can make a thinly-evidenced
  candidate look stronger than it is.
- **`passenger_yoy_growth` is a one-year change, not a multi-year CAGR** —
  named accordingly rather than overstated. FAA TAF forecast growth
  (`faa_forecast_passenger_cagr`) is a genuine multi-year CAGR.
- **Research findings do not feed scoring.** `research_airport_facts_tool`
  returns citation-backed claims the model relays with attribution, but
  nothing in `core/scoring.py` consumes them — an unsourced or
  loosely-parsed claim silently changing a computed score would be a worse
  failure than simply not having that signal. The model may use research
  findings as context in its own explanation, never as an input to
  `congestion_score` or `opportunity_score`.
- Voice input (the composer's mic button) is real speech-to-text via the
  browser's native `SpeechRecognition` API — it fills the text field, but
  does not auto-send or read replies aloud.
