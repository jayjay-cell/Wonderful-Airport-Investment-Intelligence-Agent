"""FastAPI app — the real HTTP boundary. POST /chat, GET /health.
The React UI (and any future UI) talks to this over HTTP only; it never
imports the agent directly.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv

load_dotenv()  # populates os.environ from .env before any provider is built

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from api.schemas import ChatRequest, ChatResponse, build_structured_error
from api.session_store import append_messages, get_history, new_session_id
from core.timing import timed

app = FastAPI(title="Aero Intel — Airport Investment Intelligence Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # single-user local/demo deployment; tighten if deployed
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    from agent.executor import run_turn

    session_id = request.session_id or new_session_id()
    history = get_history(session_id)

    with timed("chat.total_request", session_id=session_id[:8]):
        try:
            response_text = run_turn(request.message, history)
        except Exception as exc:  # noqa: BLE001 - intentional: this is the
            # top-level request boundary. A T-100 failure, a provider
            # failure, or any unhandled exception inside a tool/graph must
            # never crash the backend process or leave the frontend with a
            # raw 500/connection-reset — it must always produce a clean
            # structured error AND a written history turn so the next
            # message ("what") has context about what just failed.
            failure_summary = build_structured_error(exc)
            logging.getLogger("api.main").warning(
                "chat_turn_failed: error_code=%s source=%s request_id=%s",
                failure_summary.error_code, failure_summary.source, failure_summary.request_id,
            )
            append_messages(session_id, [
                HumanMessage(content=request.message),
                AIMessage(content=(
                    f"[SYSTEM NOTE — this turn failed, no answer was produced] "
                    f"{failure_summary.message} (source: {failure_summary.source or 'unknown'})"
                )),
            ])
            raise HTTPException(
                status_code=503,
                detail=failure_summary.model_dump(),
            ) from exc

    append_messages(session_id, [
        HumanMessage(content=request.message),
        AIMessage(content=response_text),
    ])

    return ChatResponse(session_id=session_id, response=response_text)
