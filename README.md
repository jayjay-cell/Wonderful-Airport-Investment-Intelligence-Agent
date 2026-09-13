# Aero Intel — Airport Investment Intelligence Agent

A conversational agent that helps airport-investment analysts identify US
airports where modernization could unlock additional passenger or flight
capacity, using live public FAA/BTS data and deterministic scoring — not
LLM-invented numbers.

See [DESIGN.md](DESIGN.md) for architecture, methodology, and tradeoffs.

## Setup

### Requirements
- Python 3.11+
- Node.js 18+
- At least one free-tier LLM API key: [Groq](https://console.groq.com/keys),
  [Google AI Studio](https://aistudio.google.com/apikey) (Gemini), or
  [OpenRouter](https://openrouter.ai/keys)

### 1. Backend

```bash
python -m pip install -r requirements.txt
cp .env.example .env
# edit .env and add at least one of GROQ_API_KEY / GEMINI_API_KEY / OPENROUTER_API_KEY
```

### 2. Frontend

```bash
cd ui
npm install
```

## Running

Start the backend (from the project root):

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Start the frontend (in a second terminal, from `ui/`):

```bash
npm run dev
```

Open the printed local URL (typically `http://localhost:5173`). The Vite dev
server proxies `/api/*` to the FastAPI backend on port 8000.

Verify the backend is up directly:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

## Architecture

A LangGraph ReAct agent (`agent/graph.py`) with a fixed set of six tools. The
full conversation history is replayed to the model on every turn — no
summarization step to drift out of sync with what was actually said. The
model calls whichever tools a question needs; every calculation the tools
report is computed in deterministic Python (`core/`), never by the LLM
itself.

```
User message
    ↓
FastAPI loads ConversationState (per session_id, in-memory)
    ↓
LangGraph create_react_agent — full message history every turn
    ↓
tools/ (6 tools)  →  core/ (pure calculation)  →  data/clients/ (live FAA/BTS)
    ↓
Reply, saved back to ConversationState
```

See [DESIGN.md](DESIGN.md) for the full architecture, the two-score
calculation model, and the provider-fallback design.

### The six tools

| Tool | Responsibility |
|---|---|
| `find_airports_tool` | Resolve a region/state/city/name into candidate airports |
| `get_airport_profile_tool` | Full metrics for one airport — demand, load factor, delays, long-haul share, FAA forecast |
| `compare_airports_tool` | Side-by-side comparison for 2+ airports, with a computed congestion score |
| `rank_airports_tool` | Opportunity ranking within a region, or single-metric ranking |
| `find_nearby_airports_tool` | Computed great-circle distance between airports |
| `research_airport_facts_tool` | Sourced web research for facts FAA/BTS don't hold |

There is no dedicated single-airport "opportunity assessment" tool — a
single-airport investment question is answered by the model composing
`get_airport_profile_tool` with `research_airport_facts_tool` and explaining
the result itself from the returned numbers.

## Example questions

- "Which airports in New England are strong candidates for terminal expansion?"
- "Compare congestion at LAX and SNA"
- "What percentage of flights out of Anchorage are long-haul?"
- "What is the unmet flight demand at SFO airport and why?"

The agent isn't limited to a fixed airport list — any US commercial airport
resolvable through FAA data works, since all five data sources are queried
live rather than from a pre-baked snapshot.

### Conversational follow-ups

The full message history is available to the model on every turn, so
follow-ups resolve naturally against what was actually said:

```
Which airports in New England are strong terminal expansion candidates?
What about just Boston?          → narrows scope, same terminal focus
Why is the first one better?     → explains from the prior answer
Compare it with JFK.             → resolves "it" from the conversation
```

## A note on response time

All analytical data is fetched live from official FAA/BTS sources — no
pre-baked snapshots — so the first query that touches a given year/month
pays real network cost. Two of the five sources are inherently slow: BTS
T-100 has no public API (it's queried through a scripted government web
form), and BTS On-Time Performance requires downloading and merging 12
monthly files to build a full-year aggregate. Both are cached by the shared
national resource (not per airport), so this cost is paid once per
year/month, not once per airport or per question — a region-wide ranking of
8 airports triggers one BTS download, not eight, and any later query for the
same period is served from the in-memory cache in a couple of seconds.

## API

### `POST /chat`

```json
// Request
{"session_id": "…" | null, "message": "What percentage of ANC flights are long-haul?"}

// Response
{"session_id": "…", "response": "About 17.7% of performed passenger departures…"}
```

Pass the returned `session_id` back on the next request to continue the same
conversation. Omit it (or pass `null`) to start a new one.

### `POST /chat/stream`

Server-Sent Events variant of `/chat`, used by the UI for live tool-status
updates. Emits a `session` event first, then zero or more `status` events as
each tool actually runs (e.g. "Ranking candidates"), then one `done` event
with the final answer (or `error` with a safe failure message).

### `GET /health`

```json
{"status": "ok"}
```

## Project structure

```
agent/
  graph.py      the ReAct agent loop, system prompt, streaming
  providers.py  Groq -> Gemini -> OpenRouter fallback chain
  state.py      per-conversation state (message history)
api/
  main.py           FastAPI: POST /chat, POST /chat/stream, GET /health
  schemas.py        request/response models
  session_store.py  in-memory session store
core/                 deterministic calculation — no LLM, no I/O
  models.py      typed domain models (Airport, AirportProfile, Score, ...)
  metrics.py     raw metric formulas (CAGR, load factor, long-haul share, ...)
  scoring.py     congestion_score() and opportunity_score() — the only two scores
  ranking.py     opportunity-score-based ranking helpers
data/                 live data fetching, caching, graceful degradation
  clients/       one file per source — FAA Airports, FAA Enplanements, FAA TAF,
                 BTS On-Time Performance, BTS T-100 Segment
  cache.py       thread-safe in-memory cache with single-flight deduplication
  degradation.py bounded retry + SourceUnavailable
  regions.py     US region -> state code mapping
tools/                the six LangChain-tool-decorated functions the agent calls
config/
  methodology.yaml   documented thresholds used by core/scoring.py
ui/                   React + TypeScript + Vite frontend
```

## Configuration

At least one LLM provider key is required in `.env`:

| Key | Used for | Required |
|---|---|---|
| `GROQ_API_KEY` | primary provider (tried first) | one of the three |
| `GEMINI_API_KEY` | fallback provider, and required for `research_airport_facts_tool` (Google Search grounding) | required for research |
| `OPENROUTER_API_KEY` | final fallback provider | optional |

Without `GEMINI_API_KEY`, every structured question still works; research
questions return an explicit limitation instead of an answer.

## Known limitations

- **Conversation state is in-memory.** Lost on server restart, and not
  multi-instance safe. A real deployment would move the same
  `ConversationState` shape to Redis or a database keyed by `session_id`.
- **Research findings do not feed scoring.** `research_airport_facts_tool`
  returns sourced claims the model may relay with attribution, but nothing
  in `core/scoring.py` consumes them — an unsourced claim must never
  silently change a computed score.
- **Period coverage differs by source.** FAA enplanements are annual; BTS
  On-Time and T-100 are annual aggregates built from monthly files. Each
  metric carries its own coverage period, and mismatches are flagged
  explicitly rather than silently merged. If a BTS On-Time month fails to
  download, that gap is named explicitly in the affected metric's coverage
  period rather than silently presented as full-year data.
- **A missing scoring input is dropped and the remaining weights are
  renormalized**, not treated as zero — this keeps a score comparable
  across candidates, but means two airports with very different amounts of
  underlying data can land on similar-looking scores. Every score also
  reports `coverage_ratio` (how many of its expected inputs were actually
  available) for exactly this reason — a high score with a low
  `coverage_ratio` rests on thinner data than the same score at 100%
  coverage, and callers should treat both numbers together.
- **`passenger_yoy_growth` is a one-year change**, not a multi-year CAGR —
  named accordingly. FAA TAF forecast growth (`faa_forecast_passenger_cagr`)
  is a genuine multi-year CAGR.
- **The one hard limit on how many tool/model steps a turn can take is
  LangGraph's `recursion_limit`** (configurable via `AGENT_MAX_STEPS`,
  default 8 steps). Hitting it ends the turn with a safe fallback reply
  rather than an unbounded loop.
- Classification thresholds in `config/methodology.yaml` are stated MVP
  defaults, not empirically validated.
- Voice input transcribes speech into the composer via the browser's native
  Web Speech API; it does not auto-send or read replies aloud.

See [DESIGN.md](DESIGN.md) for methodology detail and tradeoffs.
