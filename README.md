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

## Example questions

- "Which airports in New England are strong candidates for terminal expansion?"
- "Compare congestion at LAX and SNA"
- "What percentage of flights out of Anchorage are long-haul?"
- "What is the unmet flight demand at SFO airport and why?"

The agent supports conversational follow-ups (e.g. "what about just Boston?"
after a regional ranking) within the same session.

## A note on response time

First-time queries about a new airport can take 30–110 seconds — this is
real network time against live FAA/BTS sources (one source, BTS T-100, has no
public API and is queried via a scripted form submission; another involves a
10–30MB monthly file download). Repeat queries about the same airport within
a session hit an in-memory cache and return in a few seconds. Region-wide
ranking queries (e.g. "all of New England," ~20+ airports) are the slowest
path since they assess every candidate airport — this is documented as a
known tradeoff in DESIGN.md, not an oversight.

## Tests

```bash
# Fast, no network — the deterministic core (48 tests)
python -m pytest tests/ -m "not live"

# Full suite including live integration tests against real FAA/BTS data
# and real domain-tool wiring (slower, requires internet access)
python -m pytest tests/
```

## Project structure

```
agent/          LangChain agent, system prompt, LLM provider router, domain tools
api/            FastAPI app (POST /chat, GET /health), session store
core/           Deterministic scoring/classification/ranking — no LLM, no I/O
data/           Live FAA/BTS connectors, in-memory cache, graceful degradation
config/         methodology.yaml — every threshold, named and typed
ui/             React frontend (Vite)
research/       Reserved for a future bounded Research Agent (not built — see DESIGN.md)
tests/          Unit tests (core/) + live integration tests (data/, agent/tools/)
```

## Known limitations

See "Known limitations" in [DESIGN.md](DESIGN.md) — summarized: no Research
Agent in this build (Actionability defaults to `Unknown` without it, by
design, not by omission), classification thresholds are stated MVP
assumptions pending validation, session/cache state is in-memory only (lost
on restart), and voice input is a visible but intentionally inert UI element
for now.
