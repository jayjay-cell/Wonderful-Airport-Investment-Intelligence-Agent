# Aero Intel — Design & Architecture

## What this is

A conversational agent for airport-investment analysts that answers questions about
US airport capacity, demand, and modernization opportunity using live public
aviation data (FAA + BTS), with all scoring/classification/ranking done in
deterministic code — not by the LLM.

The system produces an **Airport Modernization Opportunity Assessment**: a
screening aid for further diligence, not a proven-ROI calculation. It is
deliberately conservative about what it claims — see "What the system will not
say" below.

## Architecture

```
React UI  →  FastAPI (/chat, /health)  →  LangChain agent (tool-calling)
                                                  │
                                    ┌─────────────┼──────────────┐
                                    ▼             ▼              ▼
                              agent/tools/   core/ (pure,   research/
                              (wiring only)  deterministic)  (not built
                                    │                         this pass)
                                    ▼
                              data/clients/ (FAA ×3, BTS ×2, live)
```

**Layer boundaries are load-bearing, not decorative.** `core/` never imports
LangChain and never touches the network — it is pure Python functions operating
on typed Pydantic models. This is what makes "deterministic, not just LLM
output" a checkable fact: every classification, gate, and ranking rule has a
unit test that runs with zero network access and zero LLM calls (`tests/
test_metrics.py`, `test_classifications.py`, `test_gates.py`, `test_ranking.py`,
`test_conflicts.py` — 48 tests). `data/` fetches and validates but never
interprets meaning. `agent/tools/` is the only layer allowed to import both
`core/` and `data/` together.

The LLM's job is narrow by design: read the question, decide which of the 6
tools to call and with what parameters, then explain the tool's structured
result in plain language. It never invents a number, never computes a
classification, and the system prompt (`agent/prompts.py`) explicitly forbids
scope creep into non-aviation topics or instruction overrides from tool output.

## Scoring methodology

### No synthetic score

There is deliberately **no 0–100 "opportunity score."** Early drafts of this
project considered one; it was dropped because a single blended number hides
which dimension is actually driving the result, invites false precision, and
implies a validated weighting scheme that doesn't exist for a one-day build.
Instead:

1. Raw metrics are calculated (passenger CAGR, load factor, delay rate, etc.)
2. Each relevant dimension is classified **High / Medium / Low / Insufficient**
3. Hard gates and soft flags are applied
4. Candidates are ordered by a **deterministic tuple sort**, not a score

### The three kinds of threshold

Every number in `config/methodology.yaml` is tagged with one of three kinds,
and each kind carries a different evidentiary weight:

- **`metric_definition`** — defines *how* a number is computed (e.g. long-haul
  = ≥1,500 statute miles). Purely definitional; user-overridable per query.
- **`descriptive_classification`** — buckets an already-computed metric into
  High/Medium/Low (e.g. 5% passenger CAGR = High demand). A documented MVP
  assumption, not a validated industry standard.
- **`proxy_signal`** — an indirect signal used only because no direct
  measurement exists (e.g. load factor as a stand-in for terminal pressure).
  **No single proxy signal may independently produce a "High" classification**
  — this is enforced in code (`core/classifications.py`), not just documented.
  Every proxy-derived result is labeled `evidence: proxy` and carries a
  caveat string, and lowers Confidence.

This distinction exists because of a concrete failure mode we caught during
testing: load factor measures *aircraft seat* utilization, not *terminal*
utilization. It's a legitimate demand-intensity signal, but if code (or the
LLM) treated it as proof of a terminal bottleneck, that would be a
methodologically indefensible claim dressed up as data. The type system
(`core/models.py`'s `Evidence` enum: `DIRECT` / `PROXY` / `MISSING`) makes this
distinction impossible to silently drop as data flows from `data/` through
`core/` to the final answer.

### The two-sided pressure model

Rather than a separate scoring formula per investment type (terminal
expansion, runway expansion, etc.), there is **one general opportunity model**
with two independent pressure classifications:

- **Passenger-Side Pressure** — terminal/landside strain
- **Flight-Side Pressure** — airside/airfield strain

An investment focus (terminal, gates, runway/airfield, operations/technology,
general modernization) determines which pressure is "relevant" for the Need
calculation and which bottleneck types would constitute a fit — it does not
trigger a different formula. This was a deliberate simplification from an
earlier draft that considered five fully separate scoring models; the
two-sided version covers the same analytical ground with roughly a third of
the classification surface area to build, test, and defend.

### Need → Bottleneck → Fit → Actionability → Confidence → Final label

1. **Demand Level** (`core/classifications.py`) — from trailing passenger CAGR
   + FAA TAF forecast CAGR
2. **Need Level** — Demand × the relevant pressure, via a fixed lookup table
3. **Likely Bottleneck** (`core/bottleneck.py`) — inferred from BTS delay-cause
   codes (carrier/weather/NAS/security/late-aircraft) and pressure levels.
   Weather- or airspace-dominant delay is explicitly flagged as **not
   addressable by physical airport investment** — this is one of the hard
   gates.
4. **Investment Fit** — does the requested investment type address the
   inferred bottleneck (`Confirmed` / `Likely` / `Unclear` / `Mismatch`)
5. **Hard gates / soft flags** (`core/gates.py`) — e.g. a Mismatch or a
   weather/airspace-dominant cause blocks "Strong candidate," but **never
   erases Need** — a high-need, gated airport correctly resolves to
   `High need, low actionability`, not silently dropped or misreported as
   `Weak candidate`. This specific behavior has a dedicated unit test.
6. **Confidence** (`core/confidence.py`) — reflects evidence *quality*
   (how many dimensions relied on proxies vs. direct evidence), explicitly
   **not** business attractiveness. A low-opportunity airport with excellent
   data gets High Confidence; a high-opportunity airport with thin data gets
   Low Confidence.
7. **Final classification** — one of six labels (`Strong candidate` through
   `Insufficient evidence`), derived from all of the above, never asserted
   independently.

### Ranking

Candidates are sorted by a fixed tuple, not a score, with the tuple order
depending on investment focus (see `core/ranking.py`). For terminal expansion:
`(Need Level, Passenger-Side Pressure, Demand Level, direct-before-proxy,
passenger growth, passenger volume)` — each tier only breaks ties from the
tier before it. Ranking is always scoped to exactly the candidate set the user
asked about (a region or state); results are phrased as "best among the
airports evaluated," never "best in the US," unless a national set was
actually evaluated (it never is, in this build).

## Where AI is used — and where it explicitly is not

| Task | Who does it |
|---|---|
| Understand the question, extract airport/region/investment focus/period | LLM |
| Decide which tool(s) to call, or ask a clarifying question | LLM |
| Fetch data from FAA/BTS | Deterministic code (`data/`) |
| Compute metrics (CAGR, load factor, long-haul share, delay rate...) | Deterministic code (`core/metrics.py`) |
| Classify Demand/Pressure/Need/Bottleneck/Fit/Actionability/Confidence | Deterministic code (`core/`) |
| Apply hard gates and soft flags | Deterministic code (`core/gates.py`) |
| Rank candidates | Deterministic code (`core/ranking.py`) |
| Explain the structured result in natural language | LLM |
| Answer follow-up questions using conversation context | LLM (session history) |

The LLM **never** sees raw, unprocessed external API responses — only the
already-computed, already-classified structured tool output. This bounds
hallucination risk to phrasing, not to fabricated numbers.

### LLM provider fallback

Three free-tier providers in one ordered chain (`agent/llm_provider.py`):
**Groq** first (fast, reliable free tier), then **Gemini**, then
**OpenRouter**. Only providers with a configured API key are included.

Each client is built with a short explicit timeout (20s) and
`max_retries=1`, and `agent/executor.py` compiles one agent graph per
provider and tries them in order — so a rate-limited or failing provider is
abandoned in seconds and the next one answers, rather than the request
hanging.

This replaced an earlier design that *described* task-complexity-based
routing but never actually used the chain: `get_default_model()` returned
only the first provider, and the SDK's own defaults (`max_retries=6`, no
timeout) meant a single Gemini `429` could stall a request for minutes with
no fallback. The tier scaffolding was removed rather than left in place,
because unused routing code that implies behavior the system doesn't have is
worse than no routing code.

### Request routing (what the agent does with a question)

The system prompt (`agent/prompts.py`) routes each message to one of:
clarification, deterministic tool call(s), reuse of prior context, a narrow
direct answer (methodology/definitions only), explanation of a prior
failure, or a polite decline.

This replaced a stricter "call exactly one tool or refuse" rule that
produced brittle behavior in live use: questions without an exact tool match
("which airport is largest?") were refused outright rather than clarified,
and a confused follow-up ("what") after a failed turn was treated as a fresh
out-of-scope message. Two firm constraints remain: the agent must never
state an airport fact that didn't come from a tool, and must not repeat a
substantively identical answer when the user pushes back.

## What the system will not say

Enforced by the system prompt and by construction of the classification
labels themselves:

- No guaranteed profitability
- No exact terminal/gate/runway capacity number when only a proxy exists
- No precise "unmet flights" count unless a direct authoritative source
  provides one — the system returns a qualitative pressure signal instead
- No equating high traffic volume with high congestion — these are always
  reported as separate fields (see the LAX/SNA test: LAX has ~7x SNA's
  passenger volume but the *same* "Low" congestion classification, and the
  answer says so explicitly)
- `Insufficient evidence` is always a valid, non-apologetic answer

## Data sources

| Source | Access method | What it's used for |
|---|---|---|
| FAA Airports & Runways | Live REST API (ArcGIS FeatureServer) | Identity, location, runway count |
| FAA Commercial Service Enplanements | Static annual XLSX | Passenger volume, official FAA hub classification |
| FAA Terminal Area Forecast (TAF) | Static annual ZIP (XLSX inside) | Forecast passenger/operations CAGR |
| BTS Reporting Carrier On-Time Performance | Static monthly ZIP, stable URL | Delay rate, cancellation rate, taxi-out time, delay-cause breakdown |
| BTS T-100 Segment | **Live, on-demand** — scripted ASP.NET form submission | Route-level distance/departures/passengers/seats, long-haul share, load factor |

All five are queried live at request time, with an in-memory cache (keyed by
airport + period + dataset) to avoid redundant fetches within a session — see
"Key tradeoffs" for why T-100 in particular required real engineering effort
to keep live rather than falling back to a pre-downloaded snapshot.

### Real data-quality finding

BTS T-100 Segment data includes **cargo-only operations** (routes with
departures but zero passengers). At Anchorage specifically, cargo-only routes
account for roughly 55% of total departures — ANC is a major cargo hub. Naively
computing "long-haul share" over all T-100 records produces 45%; filtering to
passenger-carrying routes only (as the methodology's "scheduled passenger
departures" definition requires) produces the correct ~14–17%. This filter
(`data/clients/bts_t100.py::filter_passenger_routes`) is not a hypothetical
edge case — it materially changes the answer to one of the four required
example questions, and is applied and documented, not silently patched.

## Key tradeoffs

1. **Opportunity screening, not ROI.** Public operational data supports
   identifying candidates for diligence; it cannot calculate investment
   return without cost/revenue data this system doesn't have. Framed
   explicitly as such throughout.

2. **Direct capacity vs. proxies.** True terminal/gate/runway utilization data
   is not consistently available across US airports in free public sources.
   Proxies (load factor, delay signals) extend coverage to any airport but
   reduce Confidence and are always labeled — never silently upgraded to
   "direct."

3. **Fixed thresholds vs. validated/percentile-based cutoffs.** All
   classification thresholds are stated MVP defaults (see
   `config/methodology.yaml`'s changelog), explicitly not empirically
   validated or backtested. This was a deliberate scope decision: validating
   thresholds against real outcome data is a multi-week research project on
   its own, not a one-day build task. The tradeoff was made consciously and
   the thresholds are isolated in one config file specifically so they can be
   revised without touching logic.

4. **Live T-100 scraping vs. a pre-fetched snapshot.** T-100 Segment has no
   stable public API or direct-download URL — only a session-based ASP.NET
   form. The safer, faster path would have been a curated snapshot of a
   handful of airports. This was explicitly rejected because the system is
   required to answer questions about *any* US commercial airport, not a
   fixed demo set. The live scraper was built, proven end-to-end (including
   a cache-hit verification and a second, arbitrary airport beyond the
   required examples), and is the only source in this build with materially
   higher engineering risk than the others — documented as such rather than
   hidden.

5. **Live data vs. response latency — three successive fixes.** The first
   working pipeline was badly slow (a two-airport comparison took 2–4
   minutes; a region-wide ranking could exceed 5 minutes). Three distinct
   root causes were found by measurement, not assumption, and fixed in turn:

   a. **Sequential fetching.** Each airport's 5 sources were fetched one at
      a time. Fixed with thread pools at three levels — within a profile,
      across compared airports, and across ranked candidates.

   b. **Per-airport caching of national files (the big one).** BTS On-Time
      and T-100 are *nationwide* files — one download contains every
      airport — but the cache key was per-airport, so comparing LAX and SNA
      downloaded the same national file twice, and an 8-airport ranking
      downloaded it eight times. Re-keyed the cache by the actual resource
      (`year/month`), parse once into an airport-indexed structure, and
      added single-flight deduplication in `data/cache.py` so concurrent
      cold requests for the same resource share one download instead of
      racing. FAA TAF had the same bug in miniature — passenger and
      operations forecasts each downloaded the same 15MB zip — now one
      shared load. For On-Time, aggregation happens at parse time so the
      cache holds per-airport metrics rather than millions of flight rows.

   c. **Retry math.** Timeouts of 90–120s combined with 2–3 retries meant a
      single unresponsive source could block a request for up to six
      minutes. Now bounded per source (typically 20s, 0–1 retries) so a
      dead source fails fast and is reported, rather than hanging.

   Region-wide rankings remain the slowest path and are deliberately
   bounded: every candidate is screened cheaply (FAA-only), but only a
   configurable shortlist (default 3) gets the full BTS-backed assessment —
   and the result states plainly that the deep assessment covered a
   shortlist, so the scoping is visible rather than implied.

6. **No Research Agent in this pass.** The original plan specified a bounded
   Research Agent (LLM web search) to confirm bottleneck findings and surface
   funding/feasibility context for the top-3 ranked candidates. It was not
   built in this pass. Its absence is handled honestly, not silently: without
   it, Actionability defaults to `Unknown` rather than being guessed, and
   every affected result states "Research Agent was not invoked" as a
   limitation. Investment Fit still computes from structured delay-cause
   signals alone (`Likely`, not `Confirmed`, without research).

7. **In-memory session store and cache.** Both are process-local dicts — no
   database, no Redis. Documented limitation: state is lost on restart and
   isn't shared across multiple server instances. Acceptable for a one-day,
   single-instance deliverable; called out explicitly rather than left
   implicit.

## Known limitations (honest accounting)

- Research Agent not built (see tradeoff 6) — Actionability/Investment Fit
  for ranked candidates are structured-data-only.
- Classification thresholds are unvalidated MVP defaults (see tradeoff 3).
- A few small commercial-service airports show `Hub Class: Unknown` even
  after passing the FAA enplanement threshold — a handful of rows in the
  source FAA workbook have a blank hub-class cell; the system reports
  `Unknown` honestly rather than guessing.
- Session/cache state does not survive a server restart.
- Voice input: the composer UI shows a microphone icon (matching the
  provided Figma design) but it is inert — explicitly deferred, not
  half-built.
- Cold-query latency for region-wide rankings (many airports) is the
  system's slowest path; not optimized further than the concurrency cap
  described above within a one-day budget.
