"""Main Agent: LangChain (1.x) tool-calling agent. Binds the 6 domain
tools + system prompt; the LLM extracts the user's decision objective and
picks a tool (Section 3 of the plan) rather than freeform planning.

NOTE: LangChain 1.x's agent API is create_agent(...) -> a compiled
LangGraph StateGraph, invoked with {"messages": [...]} and returning
{"messages": [...]} — not the older AgentExecutor/create_tool_calling_agent
pattern from LangChain 0.3.x. This module targets the installed 1.4.0 API.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from agent.llm_provider import get_default_model
from agent.prompts import SYSTEM_PROMPT
from agent.tools.langchain_tools import ALL_TOOLS

_graph = None


def get_graph():
    global _graph
    if _graph is None:
        from langchain.agents import create_agent
        model = get_default_model()
        _graph = create_agent(model=model, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT)
    return _graph


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
    graph = get_graph()
    messages = [*chat_history, HumanMessage(content=message)]
    result = graph.invoke({"messages": messages})
    final_messages = result["messages"]

    for msg in reversed(final_messages):
        if isinstance(msg, AIMessage) and msg.content:
            text = _extract_text(msg.content)
            if text:
                return text

    return "I wasn't able to produce a response for that question."
