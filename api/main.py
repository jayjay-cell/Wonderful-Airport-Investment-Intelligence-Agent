"""FastAPI app -- the real HTTP boundary. POST /chat, GET /health. The
React UI (and any future UI) talks to this over HTTP only; it never
imports the agent directly.
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()  # must run before agent.providers.build_providers reads os.environ

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

import json
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from api.schemas import ChatRequest, ChatResponse
from api.session_store import get_state, new_session_id, save_state

logger = logging.getLogger("api.main")

app = FastAPI(title="Aero Intel -- Airport Investment Intelligence Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # single-user local/demo deployment; tighten if deployed
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def warm_cache() -> None:
    """Pre-fetches the three slow shared-resource data sources on server
    startup, in the background: BTS T-100, BTS On-Time Performance, and
    FAA TAF. All three are cached as one shared resource (T-100/On-Time
    keyed by year, TAF by release -- see data/cache.py), so every
    airport/comparison/ranking question pays each cost exactly once
    regardless of which airport is asked about first, or how many. A cold
    fetch of any one of them can take from several seconds (TAF) to
    several minutes (BTS On-Time downloads and parses 12 monthly files);
    warming all three here means the first real user question doesn't
    have to wait on any of them. (An earlier version of this warm-up
    covered only T-100 and On-Time -- a still-cold TAF meant every profile
    in a region-wide ranking queued behind one ~35s TAF fetch anyway.)

    Runs in a thread (all three connectors are synchronous httpx calls)
    and never blocks the server from accepting requests -- /health and the
    UI are reachable immediately; a question asked before the warm-up
    finishes just pays the normal cold-fetch cost once, same as today.
    Failures are logged and swallowed: a warm-up failure must never crash
    the server or block real traffic.
    """
    def _warm():
        from core.metrics import latest_complete_year
        from data.clients import bts_on_time, bts_t100, faa_taf

        year = latest_complete_year()
        try:
            bts_t100.get_annual_routes("ATL", year)  # airport code is unused by the shared load; any valid one works
            logger.info("cache warm-up: BTS T-100 %s ready", year)
        except Exception as exc:
            logger.warning("cache warm-up: BTS T-100 %s failed (%s)", year, exc)
        try:
            bts_on_time.get_on_time_records("ATL", year)
            logger.info("cache warm-up: BTS On-Time %s ready", year)
        except Exception as exc:
            logger.warning("cache warm-up: BTS On-Time %s failed (%s)", year, exc)
        try:
            faa_taf.get_forecast_series("ATL")
            logger.info("cache warm-up: FAA TAF ready")
        except Exception as exc:
            logger.warning("cache warm-up: FAA TAF failed (%s)", exc)

    asyncio.get_event_loop().run_in_executor(None, _warm)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = str(uuid.uuid4())
    return JSONResponse(status_code=400, content={"error": "INVALID_REQUEST", "detail": "message is required.", "request_id": request_id})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Last-resort safety net: never let a raw stack trace or exception
    # message reach the client. Logged server-side with the real
    # exception; the client gets a generic, safe message + request_id.
    request_id = str(uuid.uuid4())
    logger.error("Unhandled exception, request_id=%s", request_id, exc_info=True)
    return JSONResponse(status_code=500, content={"error": "INTERNAL_ERROR", "detail": "An unexpected error occurred.", "request_id": request_id})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    from agent.graph import run_turn

    if not request.message or not request.message.strip():
        return JSONResponse(status_code=400, content={"error": "INVALID_REQUEST", "detail": "message is required."})

    session_id = request.session_id or new_session_id()
    state = get_state(session_id)

    state = await run_turn(state, session_id, request.message.strip())
    save_state(session_id, state)

    last_message = state["messages"][-1]
    if isinstance(last_message, dict):
        reply_text = last_message.get("content")
    else:
        reply_text = getattr(last_message, "content", None)
    if isinstance(reply_text, list):
        # Some providers (Gemini) return content as a list of blocks
        # rather than a plain string.
        reply_text = "".join(b.get("text", "") for b in reply_text if isinstance(b, dict))
    reply_text = reply_text or ""

    return ChatResponse(session_id=session_id, response=reply_text)


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Server-Sent Events variant of /chat: emits live status lines as the
    agent calls each tool (see agent.graph.run_turn_streaming), then a
    final "done" event with the full answer. Same session/state semantics
    as /chat -- this is an additive endpoint, not a replacement; /chat
    still works unchanged for any caller that doesn't need live status.
    """
    from agent.graph import run_turn_streaming

    if not request.message or not request.message.strip():
        return JSONResponse(status_code=400, content={"error": "INVALID_REQUEST", "detail": "message is required."})

    session_id = request.session_id or new_session_id()
    state = get_state(session_id)

    async def event_source():
        # session_id is sent first so the client learns it even if the
        # request started without one (a fresh conversation) -- mirrors
        # what the non-streaming /chat response carries in its body.
        yield f"event: session\ndata: {json.dumps({'session_id': session_id})}\n\n"
        try:
            async for event in run_turn_streaming(state, session_id, request.message.strip()):
                yield f"event: {event['type']}\ndata: {json.dumps({'text': event['text']})}\n\n"
        finally:
            # Persist whatever state run_turn_streaming left behind even if
            # the client disconnects mid-stream (e.g. page navigated away)
            # -- the turn's tool calls already happened and their cost was
            # already paid, so the result should not be silently dropped.
            save_state(session_id, state)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
