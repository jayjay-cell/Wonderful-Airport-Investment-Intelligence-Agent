"""LLM provider router: task-type-based routing across Gemini/Groq/
OpenRouter free tiers, with fallback on failure. Per the plan Section 3.

Routing rule: simple lookup/comparison tools -> fast/cheap model (Groq).
Complex reasoning tools (ranking, opportunity assessment, research
synthesis) -> a stronger model (Gemini). Known in advance from which tool
tier is being invoked — no runtime load-tracking needed.

Both the Main Agent and the Research Agent use this same router/interface,
so provider configuration lives in exactly one place.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any

from langchain_core.language_models import BaseChatModel

SIMPLE_TOOLS = {"find_airports", "get_airport_profile", "calculate_long_haul_share", "compare_airports"}
COMPLEX_TOOLS = {"rank_airports", "assess_airport_opportunity", "research_airport_context"}


class TaskTier(str, Enum):
    SIMPLE = "simple"
    COMPLEX = "complex"


def _build_gemini(model: str = "gemini-3.6-flash") -> BaseChatModel | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=model, google_api_key=key, temperature=0)


def _build_groq(model: str = "openai/gpt-oss-120b") -> BaseChatModel | None:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    from langchain_groq import ChatGroq
    return ChatGroq(model=model, groq_api_key=key, temperature=0)


def _build_openrouter(model: str = "nvidia/nemotron-3-super-120b-a12b:free") -> BaseChatModel | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model,
        api_key=key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
    )


# Fallback order per tier: primary first, then the rest as fallback on
# failure/rate-limit. Complex tier leads with Gemini (stronger reasoning);
# simple tier leads with Groq (fast/cheap) — both fall back through the
# remaining two providers if the leader is unavailable.
_TIER_ORDER: dict[TaskTier, list[str]] = {
    TaskTier.COMPLEX: ["gemini", "groq", "openrouter"],
    TaskTier.SIMPLE: ["groq", "gemini", "openrouter"],
}

_BUILDERS = {
    "gemini": _build_gemini,
    "groq": _build_groq,
    "openrouter": _build_openrouter,
}


def tier_for_tool(tool_name: str) -> TaskTier:
    if tool_name in COMPLEX_TOOLS:
        return TaskTier.COMPLEX
    return TaskTier.SIMPLE


def get_model_chain(tier: TaskTier) -> list[BaseChatModel]:
    """Returns the ordered list of available (API-key-configured) models
    for this tier, primary first. Caller should try each in order and fall
    back to the next on failure."""
    chain = []
    for provider_name in _TIER_ORDER[tier]:
        model = _BUILDERS[provider_name]()
        if model is not None:
            chain.append(model)
    return chain


def get_default_model() -> BaseChatModel:
    """Convenience accessor for a single default model (used by the Main
    Agent's top-level executor, which itself decides tool tiers per-call;
    this just needs *a* working model to drive the LangChain agent loop).
    Prefers the complex tier's primary, falling back through the chain."""
    chain = get_model_chain(TaskTier.COMPLEX)
    if not chain:
        raise RuntimeError(
            "No LLM provider is configured. Set at least one of "
            "GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY in the "
            "environment (see .env.example)."
        )
    return chain[0]
