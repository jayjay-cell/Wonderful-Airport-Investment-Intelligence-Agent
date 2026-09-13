"""Proves real provider fallback: a failing provider (simulating a 429/
rate-limit/quota error) is skipped and the next configured provider
answers instead. No live network calls — providers are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.llm_provider import ProviderResult, invoke_with_fallback


class _FakeRateLimitError(Exception):
    """Stand-in for a provider SDK's 429/RESOURCE_EXHAUSTED exception."""


def _mock_model(name: str, *, raises: Exception | None = None, returns: str = "ok"):
    model = MagicMock()
    if raises is not None:
        model.invoke.side_effect = raises
    else:
        model.invoke.return_value = returns
    return model


def test_first_provider_429_falls_back_to_second():
    failing = _mock_model("groq", raises=_FakeRateLimitError("429 RESOURCE_EXHAUSTED"))
    working = _mock_model("gemini", returns="answer from gemini")
    chain = [("groq", failing), ("gemini", working)]

    result, info = invoke_with_fallback(chain, "hello")

    assert result == "answer from gemini"
    assert info.provider_name == "gemini"
    assert info.fallback_occurred is True
    assert info.attempted_providers == ["groq", "gemini"]
    failing.invoke.assert_called_once()
    working.invoke.assert_called_once()


def test_no_fallback_when_first_provider_succeeds():
    working = _mock_model("groq", returns="answer from groq")
    unused = _mock_model("gemini", returns="should not be called")
    chain = [("groq", working), ("gemini", unused)]

    result, info = invoke_with_fallback(chain, "hello")

    assert result == "answer from groq"
    assert info.provider_name == "groq"
    assert info.fallback_occurred is False
    unused.invoke.assert_not_called()


def test_falls_through_multiple_failures_to_third_provider():
    f1 = _mock_model("groq", raises=_FakeRateLimitError("429"))
    f2 = _mock_model("gemini", raises=TimeoutError("timed out"))
    ok = _mock_model("openrouter", returns="answer from openrouter")
    chain = [("groq", f1), ("gemini", f2), ("openrouter", ok)]

    result, info = invoke_with_fallback(chain, "hello")

    assert result == "answer from openrouter"
    assert info.provider_name == "openrouter"
    assert info.attempted_providers == ["groq", "gemini", "openrouter"]


def test_all_providers_failing_raises_clear_error_naming_all_attempts():
    f1 = _mock_model("groq", raises=_FakeRateLimitError("429"))
    f2 = _mock_model("gemini", raises=_FakeRateLimitError("429"))
    chain = [("groq", f1), ("gemini", f2)]

    with pytest.raises(RuntimeError) as exc_info:
        invoke_with_fallback(chain, "hello")

    message = str(exc_info.value)
    assert "groq" in message
    assert "gemini" in message
    # Must never leak a key even if one were present in a mocked config
    assert "GROQ_API_KEY" not in message
    assert "GEMINI_API_KEY" not in message


def test_empty_chain_raises_immediately_no_network_attempt():
    with pytest.raises(RuntimeError, match="No LLM provider is configured"):
        invoke_with_fallback([], "hello")
