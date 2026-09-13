"""FastAPI app — the real HTTP boundary. POST /chat, GET /health.
The React UI (and any future UI) talks to this over HTTP only; it never
imports the agent directly.
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()  # populates os.environ from .env before any provider is built

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from api.schemas import ChatRequest, ChatResponse
from api.session_store import append_messages, get_history, new_session_id

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

    try:
        response_text = run_turn(request.message, history)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    append_messages(session_id, [
        HumanMessage(content=request.message),
        AIMessage(content=response_text),
    ])

    return ChatResponse(session_id=session_id, response=response_text)
