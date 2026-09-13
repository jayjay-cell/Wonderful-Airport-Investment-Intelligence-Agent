"""Proves the conversation-context bug is fixed: a failed tool/turn is
written to session history, and a confused follow-up ("what") sees that
context rather than being treated as an unrelated out-of-scope message.

Mocks agent.executor.run_turn directly (no real LLM/network calls) so this
is fast and deterministic.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app
from api.session_store import get_history


def test_failed_turn_is_written_to_session_history():
    client = TestClient(app)

    with patch("agent.executor.run_turn", side_effect=RuntimeError(
        "BTS T-100 Segment unavailable: TranStats form submission failed for 2024-All: timed out"
    )):
        resp = client.post("/chat", json={"message": "What percentage of flights from ANC are long-haul?"})

    assert resp.status_code == 503
    body = resp.json()
    detail = body["detail"]
    assert detail["error_code"] == "SOURCE_UNAVAILABLE"
    assert detail["source"] == "BTS T-100 Segment"
    assert detail["retryable"] is True
    # no stack trace or secret leaked
    assert "Traceback" not in str(detail)


def test_session_history_contains_failure_note_after_error():
    client = TestClient(app)
    session_id = "test-session-failure-context"

    with patch("agent.executor.run_turn", side_effect=RuntimeError(
        "BTS T-100 Segment unavailable: timed out"
    )):
        client.post("/chat", json={
            "session_id": session_id,
            "message": "What percentage of flights from ANC are long-haul?",
        })

    history = get_history(session_id)
    assert len(history) == 2  # human message + system-note assistant message
    assert history[0].content == "What percentage of flights from ANC are long-haul?"
    assert "SYSTEM NOTE" in history[1].content
    assert "BTS T-100 Segment" in history[1].content


def test_followup_after_failure_receives_prior_failure_in_history():
    """Models the exact bug report scenario: a tool call fails, the user
    replies "what", and the SECOND request's history (as read by run_turn)
    must contain the first failure — this is what lets the agent explain
    the failure instead of treating "what" as a fresh out-of-scope message.
    """
    client = TestClient(app)
    session_id = "test-session-followup"

    with patch("agent.executor.run_turn", side_effect=RuntimeError("BTS T-100 Segment unavailable: timed out")):
        client.post("/chat", json={
            "session_id": session_id,
            "message": "What percentage of flights from ANC are long-haul?",
        })

    captured_history = {}

    def fake_run_turn(message, chat_history):
        # IMPORTANT: chat_history is the same list object the caller later
        # mutates via append_messages — snapshot a COPY at call time, not a
        # reference, so later appends don't retroactively change what we
        # assert was actually passed in.
        captured_history["history"] = list(chat_history)
        captured_history["message"] = message
        return "The previous request failed because BTS T-100 Segment timed out. I can retry."

    with patch("agent.executor.run_turn", side_effect=fake_run_turn):
        resp = client.post("/chat", json={"session_id": session_id, "message": "what"})

    assert resp.status_code == 200
    assert captured_history["message"] == "what"
    history = captured_history["history"]
    assert len(history) == 2, "run_turn must receive exactly the prior turn's 2 messages (human + failure note), not a fresh/empty history"
    assert "SYSTEM NOTE" in history[1].content
    assert "BTS T-100 Segment" in history[1].content
