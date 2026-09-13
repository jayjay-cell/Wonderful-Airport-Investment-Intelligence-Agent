"""Per-conversation agent state.

`messages` holds the raw conversation history and is replayed to the model
in full on every turn (see agent/graph.py's run_turn) rather than being
summarized into extracted fields. A summarization step is one more place
context can silently drift from what was actually said; replaying the real
messages avoids that class of bug entirely.
"""

from __future__ import annotations

from typing import Annotated, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class ConversationState(TypedDict):
    messages: Annotated[list, add_messages]

    # Set when the last turn failed (a data-source or provider failure), so
    # a follow-up like "what happened?" can be answered from this field
    # rather than requiring the failure to still be in the message window.
    last_failure: Optional[str]


def initial_state() -> ConversationState:
    return ConversationState(
        messages=[],
        last_failure=None,
    )
