"""Main Agent: LangChain (1.x) tool-calling agent with real provider
fallback. Binds the 6 domain tools + system prompt; the LLM extracts the
user's decision objective and picks a tool (Section 3 of the plan) rather
than freeform planning.

NOTE: LangChain 1.x's agent API is create_agent(...) -> a compiled
LangGraph StateGraph, invoked with {"messages": [...]} and returning
{"messages": [...]} — not the older AgentExecutor/create_tool_calling_agent
pattern from LangChain 0.3.x. This module targets the installed 1.4.0 API.

FALLBACK DESIGN: a full agent turn can involve multiple LLM calls (the
tool-loop: decide tool -> read tool result -> decide next step -> ...).
True fallback therefore means retrying the WHOLE graph run with the next
provider's model if a provider fails partway through, not just swapping
the model for a single call. One compiled graph is built per configured
provider (cheap — graph compilation has no network cost) and cached; a
turn tries each provider's graph in order, stopping at the first one that
completes the full run successfully.
"""

from __future__ import annotations

import logging
import time

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from agent.llm_provider import ProviderResult, build_provider_chain
from agent.prompts import SYSTEM_PROMPT
from agent.tools.langchain_tools import ALL_TOOLS
from core.timing import timed

logger = logging.getLogger("agent.executor")

_graphs_by_provider: dict[str, object] | None = None


def _get_graphs_by_provider() -> dict[str, object]:
    """Builds (once, cached) one compiled agent graph per configured
    provider. Graph compilation is local/cheap — no network call happens
    until .invoke() runs — so pre-building one per provider costs nothing
    up front and makes per-provider fallback at invoke time trivial."""
    global _graphs_by_provider
    if _graphs_by_provider is None:
        from langchain.agents import create_agent
        chain = build_provider_chain()
        _graphs_by_provider = {
            name: create_agent(model=model, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT)
            for name, model in chain
        }
    return _graphs_by_provider


def _extract_text(content: str | list) -> str:
    """AIMessage.content is a plain string for most providers, but Gemini's
    client returns a list of content blocks (e.g.
    [{"type": "text", "text": "...", "extras": {"signature": "..."}}]) —
    extract and join just the text parts, discarding non-text blocks
    (signatures, tool-use echoes, etc.)."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and block.get("text"):
                parts.append(block["text"])
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def run_turn(message: str, chat_history: list[BaseMessage]) -> str:
    graphs = _get_graphs_by_provider()
    if not graphs:
        raise RuntimeError(
            "No LLM provider is configured. Set at least one of "
            "GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY in the "
            "environment (see .env.example)."
        )

    messages = [*chat_history, HumanMessage(content=message)]
    attempted: list[str] = []
    start = time.monotonic()
    last_exc: Exception | None = None

    for provider_name, graph in graphs.items():
        attempted.append(provider_name)
        try:
            with timed("agent.graph_invoke", provider=provider_name):
                result = graph.invoke({"messages": messages})
            elapsed = time.monotonic() - start
            fallback_occurred = len(attempted) > 1
            logger.info(
                "agent_turn: provider=%s elapsed=%.2fs fallback=%s attempted=%s",
                provider_name, elapsed, fallback_occurred, attempted,
            )
            final_messages = result["messages"]
            for msg in reversed(final_messages):
                if isinstance(msg, AIMessage) and msg.content:
                    text = _extract_text(msg.content)
                    if text:
                        return text
            return "I wasn't able to produce a response for that question."
        except Exception as exc:  # noqa: BLE001 - any provider/transport failure triggers fallback to the next provider
            last_exc = exc
            logger.warning(
                "agent_turn: provider=%s failed (%s), trying next provider",
                provider_name, type(exc).__name__,
            )
            continue

    elapsed = time.monotonic() - start
    raise RuntimeError(
        f"All configured LLM providers failed after {elapsed:.1f}s "
        f"(tried: {attempted}). Last error: {type(last_exc).__name__}: {last_exc}"
    ) from last_exc
