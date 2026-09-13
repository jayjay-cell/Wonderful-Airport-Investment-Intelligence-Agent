# Aero Intel — Airport Investment Intelligence Agent

A conversational agent that helps airport-investment analysts identify US
airports where modernization could unlock additional passenger or flight
capacity, using live public FAA/BTS data and deterministic scoring — not
LLM-invented numbers.

See [DESIGN.md](DESIGN.md) for methodology, architecture, and tradeoffs.

## Setup

### Requirements
- Python 3.11+
- Node.js 18+
- At least one free-tier LLM API key: [Google AI Studio](https://aistudio.google.com/apikey) (Gemini),
  [Groq](https://console.groq.com/keys), or [OpenRouter](https://openrouter.ai/keys)

### 1. Backend

```bash
python -m pip install -r requirements.txt
cp .env.example .env
# edit .env and add at least one of GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY
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

Open the printed local URL (typically `http://localhost:5173`). The React
dev server proxies `/api/*` to the FastAPI backend on port 8000.

Verify the backend is up directly:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

## Architecture — a controlled plan-and-execute pipeline

This is **not** an unrestricted ReAct agent looping freely over many tools.
The server, not the LLM, controls the flow:

```
User message
    ↓
FastAPI loads ConversationState (per session_id)
    ↓
Planner LLM  →  structured QueryPlan (Pydantic, never prose)
    ↓
Python validates the plan  (≤1 structured repair attempt, never a loop)
    ↓
Deterministic Executor runs 0–3 operations
    ↓
EvidenceBundle (the only thing the Answer LLM sees)
    ↓
Answer LLM writes the reply
    ↓
FastAPI saves the updated ConversationState
```

The Planner decides *what to fetch* and never computes a value. Python owns
every calculation, classification and ranking. The Answer LLM explains the
evidence and may never recalculate, reclassify, or invent a value or citation.

### The eight operations

| Operation | Responsibility |
|---|---|
| `resolve_airports` | codes / city / state / region → canonical identities |
| `get_airport_metrics` | named metrics for named airports, each with unit, evidence type, source, actual coverage period |
| `calculate_long_haul_share` | long-haul share by departures or passengers (cargo excluded) |
| `compare_airports` | 2+ airports on consistent definitions, period mismatches flagged |
| `rank_airports` | investment-opportunity ranking *or* single-metric ranking |
| `assess_airport_opportunity` | the full deterministic Need/Pressure/Bottleneck/Fit/Actionability/Confidence methodology |
| `find_nearby_airports` | great-circle distance from FAA coordinates |
| `research_airport_facts` | official-source web research for facts FAA/BTS don't hold |

Maximum three operations per request.

### Failure separation

An **LLM** failure (timeout, rate limit, quota, transport) falls back to the
next configured provider — independently for planning, plan repair, and
answer generation. A **data-source** failure does *not* retry with another
LLM: it becomes a structured `SourceFailure` in the Evidence Bundle, partial
results are preserved, and the Answer LLM explains what is and isn't
available. This replaces earlier behavior where a single failing BTS call
could be re-executed once per provider.

## Example questions

The four assignment examples are illustrative, not the boundary:

- "Which airports in New England are strong candidates for terminal expansion?"
- "Compare congestion at LAX and SNA"
- "What percentage of flights out of Anchorage are long-haul?"
- "What is the unmet flight demand at SFO airport and why?"

It also handles, for example:

- "Which airport is largest in the United States?" → defaults to annual
  passengers and states that assumption rather than refusing
- "How many terminals does JFK have?" → official-source research
- "What does long-haul mean?" → answered directly, no BTS call
- "Which airport is nearest to JFK?" → deterministic great-circle distance
- "Which growing West Coast airports also have high delays?" → a
  multi-operation analytical plan

### Conversational follow-ups

Follow-ups resolve against real server-owned state (active airports, region,
investment focus, last plan, last evidence, last failure) — not by re-reading
prose:

```
Which airports in New England are strong terminal expansion candidates?
What about just Boston?          → narrows scope, keeps the terminal focus
Why is the first one better?     → reuses the saved ranking, no re-fetch
Compare it with JFK.             → resolves "it" from active airports
Are you sure?                    → reuses saved evidence, no re-fetch
```

If a turn fails, a following "What?" explains the saved failure rather than
treating the message as a new out-of-scope question.

## A note on response time

All analytical data is fetched **live** from official FAA/BTS sources — no
pre-baked snapshots — so the first query that touches a given dataset pays
real network cost:

| Query type | Cold (first time) | Warm (cached in session) |
|---|---|---|
| Airport lookup / long-haul share | ~30–45s | ~2–10s |
| Two-airport comparison | ~40–60s | ~8s |
| Region-wide ranking | slowest path | much faster |

Two of the five sources are inherently slow: BTS T-100 has no public API
(it's queried through a scripted government web form), and BTS On-Time
Performance is a 10–30MB monthly file. Both are **national** files, so the
system downloads and parses each one *once* per period and serves every
airport from that shared index — comparing two airports does not download
the same file twice.

Three pipeline properties bound the cost of a turn:

- **At most 3 operations per request**, enforced in Python by the plan
  validator — the LLM cannot decide to keep fetching.
- **Follow-ups that reuse saved evidence make no data calls at all.** "Are
  you sure?" and "why is the first one better?" answer from
  `ConversationState.last_evidence`.
- **A failed source is not retried per LLM provider.** It fails once, and
  the answer says so.

Region-wide rankings screen every candidate airport cheaply (FAA data only),
then run the full BTS-backed assessment on a small configurable shortlist
(default 3). The response states plainly that the deep assessment covered a
shortlist — see DESIGN.md for why this tradeoff exists.

## How to check what the agent actually did

Every `/chat` response carries a `trace` reporting what really ran — so the
agent's behavior can be verified directly instead of inferred from its prose.
This matters because the failure that's hardest to catch is a *plausible*
answer produced by the wrong operations, or by none at all.

```bash
curl -s -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"What percentage of ANC flights are long-haul?"}' | jq
```

```json
{
  "session_id": "…",
  "response": "About 13.8% of scheduled passenger departures…",
  "trace": {
    "question_type": "calculation",
    "operations_run": ["calculate_long_haul_share"],
    "airports": ["ANC"],
    "reused_previous_evidence": false,
    "assumptions": [],
    "source_failures": [],
    "plan_provider": "groq",
    "answer_provider": "groq",
    "plan_repaired": false,
    "elapsed_seconds": 12.4
  }
}
```

What to look for:

| Field | Tells you |
|---|---|
| `operations_run` | which operations really executed — **empty means none ran** |
| `reused_previous_evidence` | `true` = answered from saved evidence, zero data calls |
| `airports` / `region` | what scope the Planner resolved from your wording |
| `assumptions` | any default it chose instead of asking you |
| `source_failures` | which source failed, if the answer is hedged |
| `plan_provider` / `answer_provider` | which LLM answered (differ ⇒ fallback happened) |
| `plan_repaired` | the first plan failed validation and was repaired once |

Things worth checking by hand:

- **"What does long-haul mean?"** → `operations_run: []`, no BTS call.
- **"Which airport is largest?"** → runs `rank_airports`, and `assumptions`
  names the passenger-volume default rather than asking you.
- **"How many terminals does JFK have?"** → `research_airport_facts`, not a
  fabricated number.
- **Any follow-up** ("are you sure?", "why is the first one better?") →
  `reused_previous_evidence: true` and `operations_run: []`.
- **"Get ice cream"** → `question_type: out_of_scope`, nothing runs.

To keep one conversation going, pass the returned `session_id` back on the
next request.

## Project structure

```
agent/
  pipeline.py         orchestrates the flow; FastAPI's entry point
  schemas.py          QueryPlan / EvidenceBundle / ConversationState / OperationResult
  planner.py          Planner LLM → structured QueryPlan
  plan_validator.py   deterministic plan validation (+ one repair attempt)
  executor_engine.py  runs 0–3 operations, builds the EvidenceBundle
  answer.py           Answer LLM — writes from the bundle only
  llm_provider.py     provider chain (Groq → Gemini → OpenRouter)
tools/                  the eight operations — one file each
    __init__.py           the operation registry
    find_airports.py      resolve_airports
    airport_metrics.py    get_airport_metrics
    long_haul.py          calculate_long_haul_share
    compare_airports.py   compare_airports
    rank_airports.py      rank_airports
    assess_opportunity.py assess_airport_opportunity
    nearby_airports.py    find_nearby_airports
    research_facts.py     research_airport_facts
    airport_profile.py    (shared profile builder — not an operation)
    rank_by_metric.py     (single-metric path — called by rank_airports)
api/                  FastAPI (POST /chat, GET /health) + ConversationState store
core/                 Deterministic metrics/classification/ranking — no LLM, no I/O
data/                 Live FAA/BTS connectors, cache, graceful degradation
config/               methodology.yaml — every threshold, named and typed
ui/                   React frontend (Vite)
```

## Configuration

At least one LLM provider key is required in `.env`:

| Key | Used for | Required |
|---|---|---|
| `GROQ_API_KEY` | planning + answering (tried first) | one of the three |
| `GEMINI_API_KEY` | planning + answering fallback, **and** all `research_airport_facts` (Google Search grounding) | required for research |
| `OPENROUTER_API_KEY` | planning + answering fallback | optional |

Without `GEMINI_API_KEY` the system still answers every structured question;
research-dependent questions return an explicit limitation ("this needs
information beyond FAA/BTS data and no research source is configured")
rather than a refusal or an invented answer.

## Known limitations

- **Research evidence is narrative, not typed into scoring.** The Research
  Agent returns citation-backed claims the Answer LLM relays with
  attribution. It does not yet populate the typed
  `core.models.ResearchEvidence` fields that would let `core/gates.py` turn
  a documented legal cap or funded project into a hard gate — so
  `Actionability` still defaults to `Unknown` in the deterministic
  assessment. Deliberate: an unsourced or loosely-parsed claim must not
  silently drive a deterministic classification.
- **Conversation state is in-memory.** Lost on server restart, and not
  multi-instance safe. A real deployment would move the same
  `ConversationState` shape to Redis or a database keyed by `session_id`.
- **BTS On-Time covers one month**, while FAA enplanements are annual. Each
  metric now carries its own `source.coverage_period` and profiles emit an
  explicit period-mismatch limitation, but the system does not yet aggregate
  a full BTS year.
- **`passenger_yoy_growth` is a one-year change**, not a multi-year CAGR —
  named accordingly rather than overstated. FAA TAF forecast growth
  (`faa_forecast_passenger_cagr`) *is* a genuine multi-year CAGR.
- Classification thresholds are stated MVP assumptions pending validation
  (see `config/methodology.yaml`).
- Voice input is a visible but intentionally inert UI element for now.

See [DESIGN.md](DESIGN.md) for methodology detail and tradeoffs.
