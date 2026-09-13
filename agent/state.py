# ORIENTATION: Defines the shape of "one conversation's data" (message history + last-discussed airport context).
"""Per-conversation agent state, built the same way as the NexaTel project's
agent/state.py: a TypedDict with `messages: Annotated[list, add_messages]`.

THE MEMORY FIX THIS REBUILD IS FOR: the previous version summarized
conversation state into a few extracted fields (active_airports,
active_region, ...) and handed the LLM that summary instead of the real
conversation. That summary logic had bugs and lost context turn to turn
("washington" -> "dc" failed to resolve).

This version does what NexaTel does instead: `messages` holds the REAL
conversation, and the full list is sent back to the model on every turn
(see agent/graph.py's run_turn). The model re-reads the actual conversation
each time rather than trusting an extracted summary to stay in sync -- no
separate state-summarization step to get wrong.
"""

from __future__ import annotations

from typing import Annotated, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class ConversationState(TypedDict):
    messages: Annotated[list, add_messages]

    # Steps taken in the CURRENT turn's reasoning loop -- separate from
    # LangGraph's own recursion_limit (belt-and-suspenders: recursion_limit
    # is the hard framework-level cap; this is an explicit, inspectable
    # count set after each turn completes).
    step_count: int

    # Set when the last turn failed (a data-source or provider failure) so
    # a confused follow-up ("what", "why") can be answered by explaining
    # what happened, rather than the agent losing that context the moment
    # the failure message scrolls out of the recent-messages window.
    last_failure: Optional[str]


def initial_state() -> ConversationState:
    return ConversationState(
        messages=[],
        step_count=0,
        last_failure=None,
    )
