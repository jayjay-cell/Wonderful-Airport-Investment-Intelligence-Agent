"""LLM provider fallback: try the default provider, fall back to the next
one on a recognized "this provider is unavailable" error (rate limit,
quota, timeout, transport failure). A real bug (bad request, auth failure)
is not swallowed by the fallback loop -- it propagates so it stays visible.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Awaitable, Callable

from langchain_core.language_models import BaseChatModel

_REQUEST_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class ModelProvider:
    name: str
    model: BaseChatModel
    is_unavailable_error: Callable[[Exception], bool]


async def run_with_fallback(providers: list[ModelProvider], call: Callable[[ModelProvider], Awaitable]):
    """Tries each provider in order; on a RECOGNIZED unavailable error,
    moves to the next. Any other exception propagates immediately -- a
    real bug should fail loudly, not get silently swallowed by a fallback
    loop meant for a completely different kind of failure."""
    if not providers:
        raise RuntimeError(
            "No LLM provider is configured. Set at least one of "
            "GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY in the "
            "environment (see .env.example)."
        )
    last_error: Exception | None = None
    for provider in providers:
        try:
            return await call(provider)
        except Exception as err:  # noqa: BLE001 - deliberately broad; is_unavailable_error decides what to swallow
            if not provider.is_unavailable_error(err):
                raise
            last_error = err
            continue
    raise last_error  # type: ignore[misc]


def _is_groq_unavailable(err: Exception) -> bool:
    try:
        import groq
        return isinstance(err, (groq.RateLimitError, groq.APIConnectionError, groq.APITimeoutError, groq.InternalServerError))
    except ImportError:
        pass
    msg = str(err).lower()
    return "rate limit" in msg or "429" in msg or "timeout" in msg


def _is_gemini_unavailable(err: Exception) -> bool:
    try:
        from google.genai.errors import ClientError, ServerError
        if isinstance(err, ClientError) and err.code == 429:
            return True
        if isinstance(err, ServerError):
            return True
    except ImportError:
        pass
    msg = str(err).lower()
    return "rate limit" in msg or "quota" in msg or "429" in msg or "503" in msg


def _is_openrouter_unavailable(err: Exception) -> bool:
    try:
        import openai
        if isinstance(err, (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError)):
            return True
    except ImportError:
        pass
    # OpenRouter can return HTTP 200 with an error payload in the JSON body
    # (the underlying model provider -- e.g. Nvidia -- is overloaded).
    # langchain_openai raises a plain ValueError for that shape rather than
    # a typed openai.*Error, so it needs its own string-based check to be
    # recognized as retryable instead of propagating as an unhandled bug.
    msg = str(err).lower()
    return (
        "rate limit" in msg or "429" in msg or "timeout" in msg
        or "temporarily overloaded" in msg or "provider_unavailable" in msg
        or "'code': 502" in msg or "'code': 503" in msg
    )


def build_providers() -> list[ModelProvider]:
    """Groq first (default -- fast, generous free tier), Gemini second,
    OpenRouter third. Built lazily/fresh rather than at import time so a
    missing API key only breaks the provider that's actually missing one."""
    providers = []

    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        from langchain_groq import ChatGroq
        providers.append(ModelProvider(
            name="groq",
            model=ChatGroq(
                model="openai/gpt-oss-120b",
                api_key=groq_key,
                temperature=0,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                max_retries=0,
            ),
            is_unavailable_error=_is_groq_unavailable,
        ))

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        from langchain_google_genai import ChatGoogleGenerativeAI
        providers.append(ModelProvider(
            name="gemini",
            model=ChatGoogleGenerativeAI(
                model="gemini-3.6-flash",
                google_api_key=gemini_key,
                temperature=0,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                max_retries=0,
            ),
            is_unavailable_error=_is_gemini_unavailable,
        ))

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        from langchain_openai import ChatOpenAI
        providers.append(ModelProvider(
            name="openrouter",
            model=ChatOpenAI(
                model="nvidia/nemotron-3-super-120b-a12b:free",
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
                temperature=0,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                max_retries=0,
            ),
            is_unavailable_error=_is_openrouter_unavailable,
        ))

    if not providers:
        raise RuntimeError("No LLM provider configured -- set GROQ_API_KEY, GEMINI_API_KEY, and/or OPENROUTER_API_KEY.")
    return providers
