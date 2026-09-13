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

## Architecture — plan-and-execute, not free-running ReAct

```
React UI
   │  POST /chat {session_id, message}
   ▼
FastAPI ── loads ConversationState (per session_id) ─────────────┐
   │                                                             │
   ▼                                                             │
Planner LLM ──────────────→ QueryPlan (Pydantic structured output)│
   │                                                             │
   ▼                                                             │
plan_validator.py (pure Python; ≤1 structured repair, no loop)   │
   │                                                             │
   ▼                                                             │
executor_engine.py — runs 0–3 operations                         │
   │         ┌──────────────┬───────────────┐                    │
   ▼         ▼              ▼               ▼                    │
tools/ (8 operations,  core/ (pure, deterministic —           │
  incl. web research)   no LLM, no network)                     │
   │                                                             │
   ▼                                                             │
data/clients/ (FAA ×3, BTS ×2, live)                             │
   │                                                             │
   ▼                                                             │
EvidenceBundle ──→ Answer LLM ──→ reply ──→ saves state ─────────┘
```

### Why this instead of a ReAct agent

The previous build was a LangChain `create_agent` ReAct loop with 8 tools. It
failed in three ways that were structural, not promptable:

1. **Overlapping tools made selection unreliable.** `rank_airports` and
   `rank_airports_by_metric` both answered "rank airports"; `find_airports`
   and `get_airport_profile` duplicated work other tools did internally.
2. **The loop was unbounded.** Nothing in Python limited how many tools ran.
3. **Failure kinds were conflated.** Any exception anywhere in the loop —
   including a BTS data-source timeout — retried the *entire graph* with the
   next LLM provider, so one failing BTS call could execute three times for
   one question.

The plan-and-execute pipeline fixes each in code rather than in prose: the
operations have non-overlapping responsibilities, the validator caps the plan
at three operations, and a data-source failure is terminal for the turn
(recorded as a `SourceFailure`) while only genuine LLM failures trigger
provider fallback.

**Layer boundaries are load-bearing, not decorative.** `core/` never imports
LangChain and never touches the network — pure Python functions on typed
Pydantic models, runnable with zero network and zero LLM calls. That is what
makes "deterministic, not just LLM output" a checkable property rather than a
claim. `data/` fetches and validates but never interprets meaning.
`tools/` is the only layer allowed to compose `core/`, `data/` and
`tools/research_facts.py` together.

### What each LLM may and may not do

| Stage | May | May never |
|---|---|---|
| **Planner** (`agent/planner.py`) | choose question type, scope, metrics, ≤3 operations, state assumptions, decide evidence reuse | compute a value, state a fact, answer the user |
| **Answer** (`agent/answer.py`) | explain and combine the Evidence Bundle, choose format | recalculate, reclassify, invent a value, invent a citation |
| **Research** (`tools/research_facts.py`) | search official sources, return sourced claims | rank, score, recommend, or feed unsourced claims into scoring |

Python owns every calculation, classification, and ranking. Neither prompt
carries routing logic that code could enforce instead — the validator, not the
prompt, is what actually rejects a bad plan.

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
   `Weak candidate`.
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
| Decide which operations to run | LLM (Planner) — but the plan is validated in Python before anything executes |
| Explain the structured result in natural language | LLM (Answer) |
| Resolve follow-up references ("it", "the first one") | LLM (Planner), against server-owned `ConversationState` |
| Find facts FAA/BTS don't hold | LLM (Research) — sourced claims only, never fed into scoring |

The Answer LLM **never** sees raw, unprocessed external API responses — only
the `EvidenceBundle`, containing already-computed, already-classified
structured output. This bounds hallucination risk to phrasing, not to
fabricated numbers.

### LLM provider fallback

Three free-tier providers in one ordered chain (`agent/llm_provider.py`):
**Groq** first (fast, reliable free tier), then **Gemini**, then
**OpenRouter**. Only providers with a configured API key are included. Each
client is built with a short explicit timeout (20s) and `max_retries=1`, so
a rate-limited provider is abandoned in seconds rather than hanging.

Fallback runs **independently at each LLM stage** — planning, plan repair,
and answer generation each loop over the chain themselves. This matters:
if Groq rate-limits during answer generation, Gemini writes the answer from
the *same* Evidence Bundle. Nothing the Executor already fetched is re-run.

This replaced two earlier designs. The first *described* task-complexity
routing but never used the chain (`get_default_model()` returned only the
first provider, and SDK defaults of `max_retries=6` with no timeout meant a
single Gemini `429` could stall for minutes). The second — the ReAct
executor — did use the chain, but retried the whole graph on *any*
exception, so a BTS failure was re-executed once per provider. The current
design separates the two failure kinds explicitly.

### Failure taxonomy

| Failure | Response |
|---|---|
| LLM timeout / rate limit / quota / transport | try next provider, record which providers were attempted and which succeeded |
| FAA / BTS / research source failure | do **not** change LLM; preserve partial results; add a `SourceFailure` to the bundle; lower confidence; let the Answer LLM explain |
| Operation raises unexpectedly | captured as `INTERNAL_ERROR` for that operation; the rest of the turn continues |
| Internal / unrecoverable | structured error with a `request_id`; never a stack trace, key, or internal detail |

### Request routing

The Planner assigns each message a `question_type`, and Python enforces what
that type is allowed to do:

| Type | Behavior |
|---|---|
| `definition` | answered directly from methodology — no data call |
| `lookup` / `calculation` / `comparison` / `ranking` / `airport_opportunity` | deterministic operations |
| `analytical` | multi-operation plan (≤3) |
| `clarification_needed` | exactly one question, returned verbatim, no operations run |
| `out_of_scope` | polite decline — only for genuinely non-aviation requests |

Scope policy: **every question reasonably related to airports or aviation is
accepted.** A question is not out of scope merely because no deterministic
operation matches it — if structured data can't answer it, research can, and
if neither can, the answer says so plainly. Minor ambiguity uses a stated
default ("largest" → annual passengers, assumption stated) rather than a
clarifying question; only ambiguity that would *materially* change the answer
asks.

## What the system will not say

Enforced by the Answer prompt and — more importantly — by construction of the
classification labels themselves and by the fact that the Answer LLM only
ever sees an already-computed Evidence Bundle:

- No guaranteed profitability
- No exact terminal/gate/runway capacity number when only a proxy exists
- No precise "unmet flights" count unless a direct authoritative source
  provides one — the system returns a qualitative pressure signal instead
- No equating high traffic volume with high congestion — these are always
  reported as separate fields (LAX has ~7x SNA's passenger volume but can
  carry the *same* congestion classification, and the answer says so)
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

6. **Research returns narrative evidence, not typed scoring inputs.** The
   Research Agent (`tools/research_facts.py`, Gemini + Google Search grounding) is
   now built and wired as the `research_airport_facts` operation. It answers
   questions structured data can't — terminal/gate counts, expansion
   projects, funding status, land area, documented capacity constraints —
   and returns citation-backed claims the Answer LLM relays *with
   attribution*, clearly separated from deterministic findings.

   It deliberately does **not** feed `core/`'s classification path. Doing
   that would require populating the typed
   `core.models.ResearchEvidence` fields (`confirmed_bottleneck`,
   `legal_capacity_cap`, `funding_status`...) that `core/gates.py` already
   knows how to consume — and a loosely-parsed or weakly-sourced claim
   silently flipping a hard gate is a worse failure than the current
   conservative default. So `Actionability` still defaults to `Unknown` in
   the deterministic assessment, and Investment Fit reaches `Likely` rather
   than `Confirmed` without typed research. Closing that loop is the single
   highest-value next step.

   Retrieved web content is treated as untrusted data throughout: the
   research prompt explicitly instructs the model to ignore instructions
   embedded in search results, and nothing retrieved can alter application
   behavior.

7. **Server-owned conversation state, in memory.** `ConversationState`
   (`agent/schemas.py`) holds messages, active airports/region/focus, the
   last plan, the last Evidence Bundle, and structured failure context —
   keyed by `session_id` in `api/session_store.py`. This is what makes
   follow-ups work structurally rather than by re-reading prose: "are you
   sure?" reuses `last_evidence` with no data call, and "what?" after a
   failure explains `last_failure`.

   Full structured results stay server-side; only compact text goes into
   message history, and the Planner receives a *summary* of state rather
   than raw findings. The store is a process-local dict — lost on restart,
   not multi-instance safe. Acceptable for a one-day, single-instance
   deliverable; a real deployment moves the same shape to Redis or a
   database without changing anything above it.

## Known limitations (honest accounting)

- **Research evidence is not typed into deterministic scoring** (see
  tradeoff 6). Research findings are relayed with citations but do not
  drive gates, so Actionability defaults to `Unknown` and Investment Fit
  tops out at `Likely` in the deterministic assessment.
- **Classification thresholds are unvalidated MVP defaults** (see
  tradeoff 3).
- **Period coverage differs by source.** FAA enplanements are annual
  (currently CY2024); BTS On-Time is a single month; BTS T-100 is a year.
  Each `MetricValue` now carries its own `source.coverage_period` and
  profiles emit an explicit period-mismatch limitation, and
  `compare_airports` flags when compared airports don't share a period —
  but the system does not yet aggregate a bounded full-year BTS window.
- **`passenger_yoy_growth` is a one-year change, not a multi-year CAGR.**
  Renamed from `passenger_cagr` so the name matches the computation. FAA
  TAF forecast growth (`faa_forecast_passenger_cagr`) *is* a genuine
  multi-year CAGR and keeps that name.
- A few small commercial-service airports show `Hub Class: Unknown` even
  after passing the FAA enplanement threshold — a handful of rows in the
  source FAA workbook have a blank hub-class cell; the system reports
  `Unknown` honestly rather than guessing.
- **Conversation state does not survive a server restart** and is not
  multi-instance safe (see tradeoff 7).
- Voice input: the composer UI shows a microphone icon (matching the
  provided Figma design) but it is inert — explicitly deferred, not
  half-built.
- Cold-query latency for region-wide rankings is still the slowest path.
  It is now bounded three ways (≤3 operations per request, a capped
  shortlist for deep assessment, and evidence reuse on follow-ups), but a
  cold region-wide ranking still pays real BTS download cost.
- **The Planner is a single LLM call with no self-correction beyond one
  structured repair.** If both the initial plan and the repair fail
  validation, the system asks the user to rephrase rather than guessing —
  a deliberate choice over an unbounded correction loop.
