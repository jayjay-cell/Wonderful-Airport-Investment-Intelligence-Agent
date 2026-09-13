# Design

## What this is

An agent for airport-investment analysts: US airport capacity, demand, and
modernization opportunity, from live FAA/BTS data, with every calculation
done in deterministic Python. It produces a screening signal for further
diligence — not a proven-ROI number. See "What it won't say" below.

## Architecture

```
React UI → POST /chat (or /chat/stream)
  → FastAPI loads ConversationState (per session_id, in-memory)
  → LangGraph create_react_agent, full history replayed each turn
      → tools/ (6, thin wiring)
          → core/ (pure calculation — no LLM, no network)
          → data/clients/ (FAA ×3, BTS ×2, live, cached)
  → reply, state saved
```

Plain ReAct loop, not a planner/executor split. The model reasons over the
conversation, calls whatever tools the question needs, and writes the
answer itself from what they return. `core/` never imports LangChain or
touches the network — pure functions on Pydantic models, which is what
makes "deterministic, not LLM output" checkable rather than a claim in a
docstring.

| Who does what | |
|---|---|
| Decide which tool(s) to call, resolve follow-ups | LLM |
| Fetch FAA/BTS data | `data/` |
| Compute metrics, scores, ranking | `core/` |
| Explain the result in prose | LLM |
| Find facts FAA/BTS don't hold | LLM + web search, sourced only |

The model never computes or restates a score — it relays the number a tool
returns, or states a limitation. That's what keeps hallucination risk
confined to phrasing rather than to invented numbers.

## Scoring

Two scores, both in `core/scoring.py`, both 0–100:

- **`congestion_score`** — average of normalized delay rate, taxi-out time,
  cancellation rate. Used in airport-vs-airport comparisons.
- **`opportunity_score`** — weighted blend of passenger growth, load
  factor, congestion, and (terminal/gates only) long-haul share. Weights
  differ by investment focus:

  | Focus | growth | load factor | congestion | long-haul share |
  |---|---|---|---|---|
  | terminal | .30 | .30 | .25 | .15 |
  | gates | .30 | .35 | .20 | .15 |
  | runway/airfield | .25 | .15 | .60 | — |
  | operations/tech | .25 | .20 | .55 | — |
  | general | .30 | .25 | .45 | — |

  (`core/scoring.py::_FOCUS_WEIGHTS`, mirrored in `config/methodology.yaml`)

A missing input drops out and the remaining weights renormalize to sum to
1.0 — never coerced to zero. If every input is missing, the score is `None`.

This replaced an earlier multi-stage pipeline (Demand → Pressure → Need →
Bottleneck → Fit → Confidence → a label). Most of its later stages gated on
research evidence nothing in the system ever populated, so those branches
never actually fired. Two scores with a stated basis cover the same ground
and are easier to check.

**Coverage.** Renormalizing keeps scores comparable, but it also means two
airports with very different amounts of underlying data can land on a
similar score. Every `Score` carries `inputs_available`, `inputs_expected`,
and `coverage_ratio`, returned alongside `compare_airports_tool` and
`rank_airports_tool` results — a top-ranked candidate at 50% coverage is
resting on thinner evidence than one at 100%, even at the same value.

**Provenance.** Every metric (`core.models.MetricValue`) is tagged `direct`,
`proxy`, or `missing`. Load factor, for instance, is a proxy for terminal
pressure — it actually measures aircraft seat utilization — and is labeled
as such, never silently upgraded to direct evidence.

## The six tools

| Tool | Does |
|---|---|
| `find_airports_tool` | region/state/city/name → candidate airports |
| `get_airport_profile_tool` | one airport's full metric set, each field labeled direct/proxy/missing |
| `compare_airports_tool` | 2+ airports, common period, congestion score |
| `rank_airports_tool` | opportunity ranking (screens by volume, scores a shortlist) or single-metric ranking |
| `find_nearby_airports_tool` | computed great-circle distance |
| `research_airport_facts_tool` | citation-required web research, Gemini + Google Search grounding |

No single-airport scoring tool. "Is SFO a good investment" is answered by
combining `get_airport_profile_tool` and `research_airport_facts_tool` and
explaining the result directly — arithmetic on the returned numbers is fine;
inventing a new label or score isn't.

## LLM fallback

Groq → Gemini → OpenRouter, in that order, only configured providers
included. 20s timeout, no SDK retries — a rate-limited provider is dropped
in seconds, not retried into the same wall. A recognized "unavailable"
error (rate limit, quota, timeout, including OpenRouter's HTTP-200-with-
error-body shape) moves to the next provider; anything else propagates,
since a real bug should fail loudly. If every provider fails, the turn
returns a fixed fallback reply and leaves history untouched so the next
turn can retry clean.

## Conversation memory

Full message history replayed every turn — no summarization step to drift
out of sync with what was actually said. State is a process-local dict
(`api/session_store.py`), keyed by a server-generated unguessable
`session_id`. Lost on restart, not multi-instance safe — fine for a
single-instance build; the same shape moves to Redis without touching
anything above it.

The system prompt is never written into stored state — `build_agent` passes
it via `create_react_agent`'s `prompt=`, which LangGraph prepends only to
what's sent to the model, not to the graph's own message list. Stored
messages are stripped of any system-role entry as a second safeguard, so
the prompt can't silently accumulate turn over turn.

Turn length is capped by LangGraph's `recursion_limit` (`MAX_STEPS`,
`AGENT_MAX_STEPS`, default 8). Hitting it is handled like any other
provider failure — safe fallback reply, history untouched.

## What it won't say

- No guaranteed profitability
- No exact capacity number where only a proxy exists
- No precise "unmet flights" count without a direct source
- Traffic volume and congestion are always reported separately, never conflated
- "Insufficient evidence" is a valid, plain answer

## Data sources

| Source | Access | Used for |
|---|---|---|
| FAA Airports & Runways | REST API, per airport | identity, location |
| FAA Enplanements | annual XLSX, cached whole | passenger volume, hub class |
| FAA TAF | annual ZIP, cached whole | forecast growth |
| BTS On-Time Performance | 12 monthly ZIPs, merged annually | delays, cancellations, taxi-out |
| BTS T-100 Segment | live scripted form submission, no public API | routes, long-haul share, load factor |

The two nationwide sources (T-100, On-Time) are cached by the shared
resource — year, not airport — so one download serves every airport asked
about for that period.

**Long-haul share**: share of *performed* passenger departures on routes
≥1,500 statute miles, cargo-only excluded. Two details that matter:

- **Performed, not scheduled** — T-100 publishes both; this system uses
  `DEPARTURES_PERFORMED`, flights that actually flew.
- **Passenger, not all ops** — T-100 includes cargo-only routes. At a cargo
  hub like Anchorage they're roughly half of departures; leaving them in
  overstates long-haul share materially (observed: 45% vs. the correct ~14%
  for ANC 2024). Filtered out unconditionally
  (`data/clients/bts_t100.py::filter_passenger_routes`).

## Known limitations

- Conversation state doesn't survive a restart, single-instance only.
- Cold queries are slow (T-100's form + On-Time's 12-month download can run
  30–90s combined); repeat queries for the same period hit cache.
- Thresholds in `config/methodology.yaml` are stated defaults, not backtested.
- Sources cover different periods — flagged explicitly, not merged silently.
  A partial BTS month shows up in that metric's own coverage string rather
  than being presented as a full year.
- A high score can still rest on partial data — check `coverage_ratio`
  alongside `value`, not `value` alone.
- `passenger_yoy_growth` is one year, not a CAGR; `faa_forecast_passenger_cagr` is.
- Research findings are relayed with citations, never fed into scoring.
- Voice input transcribes into the composer; doesn't auto-send or speak replies.
