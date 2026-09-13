"""Request/response models for the FastAPI HTTP boundary."""

from __future__ import annotations

import re
import uuid
from enum import Enum

from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    response: str


class ErrorCode(str, Enum):
    """Structured error taxonomy — replaces the previous behavior of
    collapsing every backend failure (network error, 503, 500, timeout,
    provider rate-limit) into one generic frontend message. Each code maps
    to a distinct, honest, user-facing explanation."""

    INVALID_AIRPORT = "INVALID_AIRPORT"
    DATA_NOT_COVERED = "DATA_NOT_COVERED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    ALL_PROVIDERS_FAILED = "ALL_PROVIDERS_FAILED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class StructuredError(BaseModel):
    error_code: ErrorCode
    source: str | None = None
    message: str
    retryable: bool
    request_id: str
    partial_result: dict | None = None


_SOURCE_NAME_PATTERN = re.compile(
    r"(BTS T-100 Segment|BTS Reporting Carrier On-Time Performance|"
    r"FAA (?:Airports and Runways|Commercial Service Enplanements|"
    r"Terminal Area Forecast \(TAF\)))"
)


def _detect_source(detail: str) -> str | None:
    match = _SOURCE_NAME_PATTERN.search(detail)
    return match.group(1) if match else None


def build_structured_error(exc: Exception) -> StructuredError:
    """Classifies a caught exception into a StructuredError, never exposing
    a stack trace, secret, or internal implementation detail — only the
    error code, an honest short message, and (where known) which external
    source was responsible.
    """
    detail = str(exc)
    request_id = str(uuid.uuid4())
    lower = detail.lower()

    source = _detect_source(detail)

    if "no llm provider is configured" in lower:
        return StructuredError(
            error_code=ErrorCode.INTERNAL_ERROR,
            source=None,
            message="No LLM provider is configured on the server.",
            retryable=False,
            request_id=request_id,
        )

    if "all configured llm providers failed" in lower:
        return StructuredError(
            error_code=ErrorCode.ALL_PROVIDERS_FAILED,
            source=None,
            message="Every configured AI provider failed to respond (rate limit, "
                     "timeout, or outage). Please try again shortly.",
            retryable=True,
            request_id=request_id,
        )

    if "429" in detail or "resource_exhausted" in lower or "rate limit" in lower or "quota" in lower:
        return StructuredError(
            error_code=ErrorCode.PROVIDER_RATE_LIMITED,
            source=None,
            message="The AI provider's rate limit was reached. Please try again shortly.",
            retryable=True,
            request_id=request_id,
        )

    if "timed out" in lower or "timeout" in lower:
        if source:
            return StructuredError(
                error_code=ErrorCode.SOURCE_UNAVAILABLE,
                source=source,
                message=f"{source} timed out and could not be retrieved for this request.",
                retryable=True,
                request_id=request_id,
            )
        return StructuredError(
            error_code=ErrorCode.PROVIDER_TIMEOUT,
            source=None,
            message="The AI provider took too long to respond.",
            retryable=True,
            request_id=request_id,
        )

    if source:
        return StructuredError(
            error_code=ErrorCode.SOURCE_UNAVAILABLE,
            source=source,
            message=f"{source} could not be retrieved for this request.",
            retryable=True,
            request_id=request_id,
        )

    return StructuredError(
        error_code=ErrorCode.INTERNAL_ERROR,
        source=None,
        message="An unexpected error occurred while processing this request.",
        retryable=True,
        request_id=request_id,
    )
