# Aero Intel

Conversational agent for airport-investment analysts. Answers questions about
US airport capacity, demand, and modernization opportunity using live FAA/BTS
data and deterministic scoring in Python — not numbers the LLM makes up.

See [DESIGN.md](DESIGN.md) for architecture and methodology.

## Setup

Requirements: Python 3.11+, Node 18+, and one LLM API key ([Groq](https://console.groq.com/keys),
[Gemini](https://aistudio.google.com/apikey), or [OpenRouter](https://openrouter.ai/keys)).

```bash
python -m pip install -r requirements.txt
cp .env.example .env   # add at least one API key

cd ui && npm install
```

## Run

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

```bash
cd ui && npm run dev
```

Open the printed URL (usually `http://localhost:5173`). The Vite dev server
proxies `/api/*` to the backend on port 8000.

```bash
curl http://127.0.0.1:8000/health   # {"status":"ok"}
```

## How it works

A LangGraph ReAct agent with six tools. Full conversation history goes back
to the model every turn — no summarization to drift out of sync. The model
picks tools; every number it reports comes from deterministic Python, never
from the model itself.

```
message → FastAPI → ReAct agent → tools → core/ (scoring) → data/clients/ (live FAA/BTS)
                                                                  ↓
                                                    reply, saved to conversation state
```

| Tool | Does |
|---|---|
| `find_airports_tool` | region/state/city/name → candidate airports |
| `get_airport_profile_tool` | one airport's metrics — demand, delays, load factor, long-haul share, forecast |
| `compare_airports_tool` | 2+ airports side by side, with a congestion score |
| `rank_airports_tool` | opportunity ranking in a region, or single-metric ranking |
| `find_nearby_airports_tool` | great-circle distance between airports |
| `research_airport_facts_tool` | web research for facts FAA/BTS don't have |

No dedicated single-airport scoring tool — for "is SFO a good investment,"
the model pulls `get_airport_profile_tool` (+ research if useful) and
explains it directly from the numbers.

## Try it

- "Which airports in New England are strong candidates for terminal expansion?"
- "Compare congestion at LAX and SNA"
- "What percentage of flights out of Anchorage are long-haul?"
- "What is the unmet flight demand at SFO airport and why?"

Works for any US commercial airport, not just these four — all data is
queried live, not from a fixed snapshot.

Follow-ups work because the model sees the real conversation:

```
Which New England airports are strong terminal candidates?
What about just Boston?        → narrows scope
Why is the first one better?   → explains from the prior answer
Compare it with JFK.           → resolves "it"
```

## Response time

All five data sources are live — no cached snapshot shipped with the repo —
so the first query touching a given year pays real cost. BTS T-100 has no
API (it's a scripted government form submission) and BTS On-Time means
downloading and merging 12 monthly files. Both are cached by the shared
national resource, not per airport, so this is paid once per year, not once
per question. A cold query can take 30–90s; a repeat query for the same
period is a couple of seconds.

## API

**`POST /chat`**
```json
// → {"session_id": null | "…", "message": "What % of ANC flights are long-haul?"}
// ← {"session_id": "…", "response": "About 17.7% of performed passenger departures…"}
```
Pass the returned `session_id` back to continue a conversation.

**`POST /chat/stream`** — same thing over SSE: a `session` event, then
`status` events as tools run ("Ranking candidates"), then `done` with the
answer (or `error`).

**`GET /health`** → `{"status": "ok"}`

## Layout

```
agent/    graph.py (agent loop + prompt), providers.py (Groq→Gemini→OpenRouter fallback), state.py
api/      main.py (FastAPI), schemas.py, session_store.py (in-memory)
core/     models.py, metrics.py, scoring.py (the two scores), ranking.py — no LLM, no I/O
data/     clients/ (5 FAA/BTS connectors), cache.py, degradation.py, regions.py
tools/    the six agent tools
config/   methodology.yaml — thresholds and weights
ui/       React + TypeScript + Vite
```

## Config

`GROQ_API_KEY` (tried first), `GEMINI_API_KEY` (fallback, also needed for
research), `OPENROUTER_API_KEY` (last fallback). One is required. Without
Gemini, research questions return a stated limitation instead of an answer.

## Limitations

- Conversation state is in-memory — lost on restart, single-instance only.
- Research findings never feed scoring; the model may cite them, but
  `congestion_score`/`opportunity_score` never consume them.
- Data sources cover different periods (FAA annual, BTS monthly/annual) —
  mismatches are flagged, not silently merged.
- A score with missing inputs is renormalized rather than zeroed, so two
  airports with different amounts of data can score similarly — each score
  carries a `coverage_ratio` for exactly this reason.
- `passenger_yoy_growth` is one year, not a CAGR — named accordingly.
- Turn length is capped by LangGraph's `recursion_limit` (`AGENT_MAX_STEPS`, default 8).
- Thresholds in `config/methodology.yaml` are stated defaults, not validated.
- Voice input transcribes speech into the composer; it doesn't auto-send or speak replies.

More detail in [DESIGN.md](DESIGN.md).
