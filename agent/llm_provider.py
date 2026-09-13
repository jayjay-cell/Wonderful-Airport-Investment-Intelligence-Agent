"""LLM provider chain with REAL runtime fallback and fast failure.

Root cause this fixes: get_default_model() previously returned only the
first provider in the "complex" tier (Gemini) and the chain was never
actually used for fallback — a Gemini 429 (rate-limit/quota) would hang
for minutes (SDK default max_retries=6, no timeout set) with no fallback
to Groq/OpenRouter ever happening, despite the chain existing in code.

Fix: one flat provider chain (Groq first — fast, reliable free tier;
Gemini and OpenRouter as fallback), each client built with a short
explicit timeout and max_retries=0-1 so a stuck/rate-limited provider
fails fast instead of hanging, and a thin wrapper (FallbackChatModel)
that actually calls .invoke() on each provider in order until one
succeeds, recording which provider answered and whether fallback
occurred.

The previous SIMPLE/COMPLEX tier concept is removed — it was unused
"routing" code that suggested behavior (task-complexity-aware model
selection) that never actually executed at request time. A single
ordered fallback chain is what the code actually needs to do; keeping
the tier scaffolding would misrepresent real behavior.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

logger = logging.getLogger("agent.llm_provider")

# Fast-fail settings: short per-call timeout, minimal SDK-internal retries.
# A provider that is rate-limited or down should be skipped in seconds, not
# minutes — the chain-level fallback below is what handles unavailability,
# not the SDK's own retry loop.
_REQUEST_TIMEOUT_SECONDS = 20.0
_PROVIDER_MAX_RETRIES = 1


def _build_groq(model: str = "openai/gpt-oss-120b") -> BaseChatModel | None:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    from langchain_groq import ChatGroq
    return ChatGroq(
        model=model,
        groq_api_key=key,
        temperature=0,
        timeout=_REQUEST_TIMEOUT_SECONDS,
        max_retries=_PROVIDER_MAX_RETRIES,
    )


def _build_gemini(model: str = "gemini-3.6-flash") -> BaseChatModel | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=key,
        temperature=0,
        timeout=_REQUEST_TIMEOUT_SECONDS,
        max_retries=_PROVIDER_MAX_RETRIES,
    )


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
        timeout=_REQUEST_TIMEOUT_SECONDS,
        max_retries=_PROVIDER_MAX_RETRIES,
    )


# Fallback order: Groq first (fast, generous free tier, observed reliable),
# then Gemini, then OpenRouter. Only configured providers (API key present)
# are included.
_PROVIDER_BUILDERS: list[tuple[str, Any]] = [
    ("groq", _build_groq),
    ("gemini", _build_gemini),
    ("openrouter", _build_openrouter),
]


class ProviderResult:
    """Records which provider actually answered a call, for instrumentation
    (Section 8 — provider used / fallback occurred)."""

    __slots__ = ("provider_name", "attempted_providers", "fallback_occurred", "elapsed_seconds")

    def __init__(self, provider_name: str, attempted_providers: list[str], elapsed_seconds: float):
        self.provider_name = provider_name
        self.attempted_providers = attempted_providers
        self.fallback_occurred = len(attempted_providers) > 1
        self.elapsed_seconds = elapsed_seconds


def build_provider_chain() -> list[tuple[str, BaseChatModel]]:
    """Returns the ordered list of (provider_name, model) for every
    configured provider (API key present). Built fresh per call site that
    needs it — client construction is cheap and this avoids any risk of a
    stale cached client outliving a config change."""
    chain = []
    for name, builder in _PROVIDER_BUILDERS:
        model = builder()
        if model is not None:
            chain.append((name, model))
    return chain


def invoke_with_fallback(chain: list[tuple[str, BaseChatModel]], *args: Any, **kwargs: Any) -> tuple[Any, ProviderResult]:
    """Tries each provider in order, moving to the next immediately on any
    exception (rate-limit, quota, timeout, transient failure). Returns the
    first successful result plus a ProviderResult recording which provider
    answered and whether fallback occurred. Raises a single clear error,
    naming every attempted provider, if all fail — never leaks API keys.
    """
    if not chain:
        raise RuntimeError(
            "No LLM provider is configured. Set at least one of "
            "GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY in the "
            "environment (see .env.example)."
        )

    attempted: list[str] = []
    start = time.monotonic()
    last_exc: Exception | None = None

    for name, model in chain:
        attempted.append(name)
        try:
            result = model.invoke(*args, **kwargs)
            elapsed = time.monotonic() - start
            if len(attempted) > 1:
                logger.warning(
                    "llm_fallback: provider=%s succeeded after failures on %s (%.2fs total)",
                    name, attempted[:-1], elapsed,
                )
            return result, ProviderResult(name, attempted, elapsed)
        except Exception as exc:  # noqa: BLE001 - intentional: any provider failure triggers fallback
            last_exc = exc
            logger.warning("llm_fallback: provider=%s failed (%s), trying next", name, type(exc).__name__)
            continue

    elapsed = time.monotonic() - start
    raise RuntimeError(
        f"All configured LLM providers failed after {elapsed:.1f}s "
        f"(tried: {attempted}). Last error: {type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def get_default_model() -> BaseChatModel:
    """Back-compat accessor: returns the first configured provider's raw
    model (used where a single LangChain-native BaseChatModel object is
    required, e.g. binding into create_agent). Real fallback happens at the
    graph-invocation layer in agent/executor.py via invoke_with_fallback +
    a small per-provider graph cache — see that module."""
    chain = build_provider_chain()
    if not chain:
        raise RuntimeError(
            "No LLM provider is configured. Set at least one of "
            "GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY in the "
            "environment (see .env.example)."
        )
    return chain[0][1]
