"""In-memory conversation session store, keyed by an unguessable session_id.

Documented limitation (per the plan): does not survive a server restart and
is not multi-instance safe. Acceptable for this one-day assignment.
"""

from __future__ import annotations

import secrets

from langchain_core.messages import BaseMessage

_SESSIONS: dict[str, list[BaseMessage]] = {}


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def get_history(session_id: str) -> list[BaseMessage]:
    return _SESSIONS.setdefault(session_id, [])


def append_messages(session_id: str, messages: list[BaseMessage]) -> None:
    _SESSIONS.setdefault(session_id, []).extend(messages)


def session_exists(session_id: str) -> bool:
    return session_id in _SESSIONS
