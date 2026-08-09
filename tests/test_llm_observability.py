# -*- coding: utf-8 -*-
"""LLM call observability: per-call records + aggregated receipt.

Task 12: every LLM round trip (chat / embedding / response processing) is
recorded with counts, latency, status and failure reason — never prompt or
model-output content — and aggregated into a qualibug.llm-observability.v1
receipt. All provider I/O is mocked; no real LLM calls are made.
"""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError
import unittest.mock as mock

import pytest

import ai_test_asset_center.llm_reasoning as llm_reasoning
from ai_test_asset_center.llm_reasoning import (
    ReasoningClient,
    ReasoningConfig,
    build_llm_observability_receipt,
    llm_observation_snapshot,
    record_llm_observation,
    reset_llm_observations,
)


def _client(**overrides) -> ReasoningClient:
    config = ReasoningConfig(
        base_url="http://llm.test/v1",
        api_key="test-key",
        model="model-x",
        **overrides,
    )
    return ReasoningClient(config=config)


class _FakeResponse:
    def __init__(self, body: dict | bytes):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args) -> bool:
        return False


_OK_BODY = {
    "choices": [{"message": {"content": '{"ok": true, "note": "MODEL-OUTPUT-SECRET-88"}'}}],
    "usage": {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165},
}

_REASON_CONTEXT = {
    "prd_text": "PRD text",
    "api_schema": "{}",
    "observed_data": "[]",
    "heuristic_findings": "[]",
}


@pytest.fixture(autouse=True)
def _clean_ledger():
    reset_llm_observations()
    yield
    reset_llm_observations()


# ---------------------------------------------------------------------------
# Per-call observation records
# ---------------------------------------------------------------------------

def test_successful_chat_records_observation_with_provider_tokens():
    client = _client()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_OK_BODY)) as urlopen_mock:
        result = client.chat_json("summarize the spec", tier="strong")
    assert result == {"ok": True, "note": "MODEL-OUTPUT-SECRET-88"}
    urlopen_mock.assert_called_once()
    obs = llm_observation_snapshot()
    assert len(obs) == 1
    o = obs[0]
    assert o["call_point"] == "chat_json"
    assert o["kind"] == "chat"
    assert o["model"] == "model-x"
    assert o["success"] is True
    assert o["http_status"] == 200
    assert o["input_tokens"] == 120
    assert o["output_tokens"] == 45
    assert o["tokens_estimated"] is False
    assert o["retry_count"] == 0
    assert o["failure_reason"] is None
    assert o["latency_ms"] >= 0


def test_reason_records_engine_call_point():
    client = _client()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_OK_BODY)):
        client.reason("causality", _REASON_CONTEXT)
    obs = llm_observation_snapshot()
    assert [o["call_point"] for o in obs] == ["causality"]


def test_no_prompt_or_output_content_leaks():
    client = _client()
    secret_prompt = "TOP-SECRET-PROMPT-MARKER-77"
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_OK_BODY)):
        client.chat_json(secret_prompt)
    serialized = json.dumps(build_llm_observability_receipt(), ensure_ascii=False)
    serialized += json.dumps(llm_observation_snapshot(), ensure_ascii=False)
    assert "TOP-SECRET-PROMPT-MARKER-77" not in serialized
    assert "MODEL-OUTPUT-SECRET-88" not in serialized


def test_estimated_tokens_when_provider_omits_usage():
    body = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    client = _client()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        client.chat_json("hello world")
    o = llm_observation_snapshot()[0]
    assert o["tokens_estimated"] is True
    assert o["input_tokens"] is not None and o["input_tokens"] >= 1
    assert o["output_tokens"] is not None and o["output_tokens"] >= 0


# ---------------------------------------------------------------------------
# Failure recording
# ---------------------------------------------------------------------------

def test_http_failure_recorded():
    client = _client()
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=HTTPError(
            "http://llm.test/v1/chat/completions", 429, "rate limited",
            {}, io.BytesIO(b'{"error":"rate"}'),
        ),
    ):
        with pytest.raises(Exception):
            client.chat_json("hi")
    obs = llm_observation_snapshot()
    assert len(obs) == 1
    o = obs[0]
    assert o["kind"] == "chat"
    assert o["success"] is False
    assert o["http_status"] == 429
    assert o["failure_reason"] == "http_error"
    assert o["failure_code"] == "QB-L002"
    assert o["latency_ms"] >= 0


def test_auth_failure_maps_to_qb_l006():
    client = _client()
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=HTTPError(
            "http://llm.test/v1/chat/completions", 401, "unauthorized",
            {}, io.BytesIO(b"nope"),
        ),
    ):
        with pytest.raises(Exception):
            client.chat_json("hi")
    o = llm_observation_snapshot()[0]
    assert o["failure_code"] == "QB-L006"


def test_timeout_failure_recorded():
    client = _client()
    with mock.patch("urllib.request.urlopen", side_effect=URLError("timed out")):
        with pytest.raises(Exception):
            client.chat_json("hi")
    o = llm_observation_snapshot()[0]
    assert o["success"] is False
    assert o["failure_reason"] == "timeout"
    assert o["failure_code"] == "QB-L001"


def test_parse_failure_recorded_as_processing():
    client = _client()
    body = {
        "choices": [{"message": {"content": "not json at all"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(Exception):
            client.chat_json("hi")
    obs = llm_observation_snapshot()
    assert len(obs) == 2
    kinds = {o["kind"] for o in obs}
    assert kinds == {"chat", "response_processing"}
    proc = [o for o in obs if o["kind"] == "response_processing"][0]
    assert proc["success"] is False
    assert proc["failure_reason"] == "response_parse_error"
    assert proc["call_point"] == "chat_json"


def test_reason_swallows_failure_but_records_observation():
    client = _client()
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=HTTPError(
            "http://llm.test/v1/chat/completions", 500, "boom",
            {}, io.BytesIO(b"oops"),
        ),
    ):
        result = client.reason("causality", _REASON_CONTEXT)
    assert result is None
    obs = llm_observation_snapshot()
    assert len(obs) == 1
    assert obs[0]["success"] is False
    assert obs[0]["failure_code"] == "QB-L001"
    assert obs[0]["call_point"] == "causality"


# ---------------------------------------------------------------------------
# Receipt aggregation
# ---------------------------------------------------------------------------

def test_receipt_aggregation_and_cost():
    client = _client(cost_per_1m_input_usd=3.0, cost_per_1m_output_usd=15.0)
    body_a = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    }
    body_b = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 2000, "completion_tokens": 1000, "total_tokens": 3000},
    }
    with mock.patch("urllib.request.urlopen", side_effect=[_FakeResponse(body_a), _FakeResponse(body_b)]):
        client.chat_json("a")
        client.chat_json("b")
    receipt = build_llm_observability_receipt()
    assert receipt["schema_version"] == "qualibug.llm-observability.v1"
    s = receipt["summary"]
    assert s["total_calls"] == 2
    assert s["successful_calls"] == 2
    assert s["failed_calls"] == 0
    assert s["total_input_tokens"] == 3000
    assert s["total_output_tokens"] == 1500
    assert s["tokens_estimated_calls"] == 0
    assert s["cost_basis"] == "configured_unit_prices"
    expected_cost = 3000 / 1e6 * 3.0 + 1500 / 1e6 * 15.0
    assert s["total_estimated_cost_usd"] == pytest.approx(expected_cost, abs=1e-6)
    assert s["latency_p50_ms"] is not None
    assert s["latency_p95_ms"] is not None
    assert s["latency_max_ms"] == max(
        o["latency_ms"] for o in llm_observation_snapshot()
    )
    cp = receipt["by_call_point"]["chat_json"]
    assert cp["calls"] == 2
    assert cp["failed"] == 0
    assert cp["total_input_tokens"] == 3000
    assert len(receipt["top_slow_calls"]) == 2


def test_receipt_without_unit_price_records_tokens_only():
    client = _client()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_OK_BODY)):
        client.chat_json("hi")
    receipt = build_llm_observability_receipt()
    assert receipt["summary"]["total_estimated_cost_usd"] is None
    assert receipt["summary"]["cost_basis"] == "not_configured"
    assert receipt["summary"]["total_input_tokens"] == 120
    assert receipt["summary"]["total_output_tokens"] == 45
    obs = llm_observation_snapshot()[0]
    assert obs["cost_estimate_usd"] is None


def test_receipt_top_slow_calls_bounded_and_sorted():
    for i in range(25):
        record_llm_observation({
            "call_point": f"cp{i}",
            "kind": "chat",
            "model": "m",
            "success": True,
            "http_status": 200,
            "latency_ms": i,
            "input_tokens": 1,
            "output_tokens": 1,
            "tokens_estimated": False,
            "retry_count": 0,
            "failure_reason": None,
            "failure_code": None,
            "cost_estimate_usd": None,
            "started_at_utc": "2026-01-01T00:00:00Z",
        })
    receipt = build_llm_observability_receipt()
    assert len(receipt["top_slow_calls"]) == 20
    assert receipt["top_slow_calls"][0]["latency_ms"] == 24
    assert receipt["top_slow_calls"][0]["call_point"] == "cp24"
    assert receipt["summary"]["total_calls"] == 25
    assert receipt["observations_truncated"] is False


def test_ledger_cap_marks_truncation():
    original_max = llm_reasoning.LLM_OBSERVATION_LEDGER_MAX
    llm_reasoning.LLM_OBSERVATION_LEDGER_MAX = 5
    try:
        for i in range(7):
            record_llm_observation({
                "call_point": "chat_json",
                "kind": "chat",
                "model": "m",
                "success": True,
                "http_status": 200,
                "latency_ms": i,
                "input_tokens": 1,
                "output_tokens": 1,
                "tokens_estimated": False,
                "retry_count": 0,
                "failure_reason": None,
                "failure_code": None,
                "cost_estimate_usd": None,
                "started_at_utc": "2026-01-01T00:00:00Z",
            })
    finally:
        llm_reasoning.LLM_OBSERVATION_LEDGER_MAX = original_max
    assert len(llm_observation_snapshot()) == 5
    receipt = build_llm_observability_receipt()
    assert receipt["observations_truncated"] is True
    assert receipt["summary"]["total_calls"] == 5


def test_reset_clears_ledger():
    client = _client()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_OK_BODY)):
        client.chat_json("hi")
    assert len(llm_observation_snapshot()) == 1
    reset_llm_observations()
    assert llm_observation_snapshot() == []


# ---------------------------------------------------------------------------
# Observation does not interfere with calls / existing behavior preserved
# ---------------------------------------------------------------------------

def test_observation_does_not_change_call_results():
    body = {
        "choices": [{"message": {"content": '{"answer": 42}'}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    }
    client = _client()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        first = client.chat_json("question")
    reset_llm_observations()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        second = client.chat_json("question")
    assert first == {"answer": 42}
    assert second == {"answer": 42}
    assert first == second


def test_legacy_usage_snapshot_still_aggregates():
    client = _client()
    body = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "cost_usd": 0.001,
    }
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        client.chat_json("hi")
    snapshot = client.usage_snapshot()
    assert snapshot["request_count"] == 1
    assert snapshot["prompt_tokens"] == 10
    assert snapshot["completion_tokens"] == 5
    assert snapshot["total_tokens"] == 15
    assert snapshot["cost_usd"] == pytest.approx(0.001)
    assert snapshot["responses_with_cost"] == 1


def test_reason_layered_records_stage_call_points():
    body = {
        "choices": [{"message": {"content": '{"entities": []}'}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }
    client = _client()
    with mock.patch("urllib.request.urlopen", side_effect=[_FakeResponse(body), _FakeResponse(body)]):
        with mock.patch.object(llm_reasoning, "_get_client", return_value=client):
            result = llm_reasoning.reason_layered("causality", _REASON_CONTEXT, api_responses="{}")
    assert result is not None
    assert [o["call_point"] for o in llm_observation_snapshot()] == ["reasoner", "verifier"]


# ---------------------------------------------------------------------------
# Embedding observations
# ---------------------------------------------------------------------------

def test_embedding_success_recorded():
    body = {"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}
    client = _client(embedding_model="emb-x")
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        vectors = client.embed(["alpha", "beta"])
    assert vectors is not None
    o = llm_observation_snapshot()[0]
    assert o["kind"] == "embedding"
    assert o["model"] == "emb-x"
    assert o["success"] is True
    assert o["output_tokens"] == 0
    assert o["input_tokens"] is not None and o["input_tokens"] >= 1


def test_embedding_failure_recorded_and_fail_soft():
    client = _client(embedding_model="emb-x")
    with mock.patch("urllib.request.urlopen", side_effect=URLError("boom")):
        assert client.embed(["alpha"]) is None
    o = llm_observation_snapshot()[0]
    assert o["kind"] == "embedding"
    assert o["success"] is False
    assert o["failure_reason"] == "embedding_error"
    assert o["latency_ms"] >= 0
