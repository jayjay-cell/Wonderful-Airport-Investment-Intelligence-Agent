"""In-memory conversation store, keyed by session_id.

Documented limitation: process-lifetime only, lost on restart, not
multi-instance safe. A client can only ever resume a session_id the SERVER
generated and handed back on an earlier turn (secrets.token_urlsafe --
unguessable) -- there is no way to claim an arbitrary/guessed id.
"""

from __future__ import annotations

import secrets

from agent.state import ConversationState, initial_state

_SESSIONS: dict[str, ConversationState] = {}


def new_session_id() -> str:
    return secrets.token_urlsafe(16)


def get_state(session_id: str) -> ConversationState:
    state = _SESSIONS.get(session_id)
    if state is None:
        state = initial_state()
        _SESSIONS[session_id] = state
    return state


def save_state(session_id: str, state: ConversationState) -> None:
    _SESSIONS[session_id] = state
