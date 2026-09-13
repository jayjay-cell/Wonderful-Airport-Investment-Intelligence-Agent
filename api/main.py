# ORIENTATION: THE FRONT DOOR. Defines POST /chat, GET /health. Same shape as NexaTel's app/main.py.
"""FastAPI app -- the real HTTP boundary. POST /chat, GET /health. The
React UI (and any future UI) talks to this over HTTP only; it never
imports the agent directly.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv

load_dotenv()  # must run before agent.providers.build_providers reads os.environ

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
