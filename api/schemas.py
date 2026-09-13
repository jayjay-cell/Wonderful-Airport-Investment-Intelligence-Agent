# ORIENTATION: Request/response shapes for the HTTP boundary.
"""Request/response models for the FastAPI HTTP boundary."""

from __future__ import annotations

from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    response: str
