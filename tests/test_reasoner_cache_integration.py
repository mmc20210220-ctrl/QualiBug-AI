# -*- coding: utf-8 -*-
"""Reasoner 缓存集成：_run_reasoner_engine 命中/未命中路径与诚实计数。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_test_asset_center.stage_reason_all_v2 import _run_reasoner_engine
from ai_test_asset_center.reasoner_response_cache import cache_key, store


_FAKE_HYPOTHESES = {
    "hypotheses": [
        {
            "title": "资金守恒：支付金额必须等于订单应付金额",
            "description": "pay 操作可能绕过应付金额校验",
            "risk_family": "conservation",
            "entity": "payment",
            "evidence": "payable_amount 与 amount 不一致",
        }
    ]
}


def _api_envelope() -> str:
    """Simulate the DeepSeek API response envelope the parser consumes."""
    inner = json.dumps(_FAKE_HYPOTHESES, ensure_ascii=False)
    return json.dumps({
        "choices": [{"message": {"content": inner}}]
    }, ensure_ascii=False)


class _FakeConfig:
    model = "fake-model"
    temperature = "0.3"
    timeout_seconds = 30
    max_tokens = 4096
    response_format = "json_object"


class _CountingClient:
    """Records whether _chat was actually invoked."""

    calls = 0

    def __init__(self, config):
        self.config = config

    def _chat(self, prompt, system_prompt="", call_point=""):
        _CountingClient.calls += 1
        return _api_envelope()

    def usage_snapshot(self):
        return {}


def _run(prompt: str) -> dict:
    result = _run_reasoner_engine(
        "causality",
        "template",
        prompt,
        "system-prompt",
        _FakeConfig(),
        retry_count=1,
        retry_delay_seconds=0.0,
        max_hypotheses=40,
        max_hypothesis_chars=500,
    )
    return result


def test_uncached_calls_llm_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUALIBUG_SEMANTIC_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("QUALIBUG_DISABLE_REASONER_CACHE", raising=False)
    _CountingClient.calls = 0
    monkeypatch.setattr(
        "ai_test_asset_center.llm_reasoning.ReasoningClient",
        _CountingClient,
    )
    result = _run("prompt-uncached-1")
    assert result["status"] == "success"
    assert result["cache_hit"] is False
    assert result["cache_source"] == "llm"
    assert result["model_attempt_count"] == 1
    assert result["model_response_count"] == 1
    assert len(result["hypotheses"]) == 1
    assert _CountingClient.calls == 1


def test_cached_skips_llm_and_counts_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUALIBUG_SEMANTIC_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("QUALIBUG_DISABLE_REASONER_CACHE", raising=False)
    # prime the cache with the exact key this invocation will compute
    key = cache_key("causality", "prompt-cached-1", "system-prompt", "fake-model", "0.3")
    store(key, _api_envelope(), model="fake-model", temperature="0.3")
    _CountingClient.calls = 0
    monkeypatch.setattr(
        "ai_test_asset_center.llm_reasoning.ReasoningClient",
        _CountingClient,
    )
    result = _run("prompt-cached-1")
    assert result["status"] == "success"
    assert result["cache_hit"] is True
    assert result["cache_source"] == "content_addressed"
    assert result["model_attempt_count"] == 0
    assert result["model_response_count"] == 0
    assert len(result["hypotheses"]) == 1
    # hypothesis metadata is identical to a real LLM call
    assert result["hypotheses"][0]["_reasoner_engine"] == "causality"
    assert _CountingClient.calls == 0


def test_prompt_change_misses_even_with_same_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUALIBUG_SEMANTIC_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("QUALIBUG_DISABLE_REASONER_CACHE", raising=False)
    key = cache_key("causality", "prompt-A", "system-prompt", "fake-model", "0.3")
    store(key, _api_envelope(), model="fake-model", temperature="0.3")
    _CountingClient.calls = 0
    monkeypatch.setattr(
        "ai_test_asset_center.llm_reasoning.ReasoningClient",
        _CountingClient,
    )
    result = _run("prompt-B")  # different material → different key
    assert result["cache_hit"] is False
    assert result["model_response_count"] == 1
    assert _CountingClient.calls == 1


def test_disabled_cache_always_calls_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUALIBUG_SEMANTIC_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("QUALIBUG_DISABLE_REASONER_CACHE", "1")
    key = cache_key("causality", "prompt-disabled-1", "system-prompt", "fake-model", "0.3")
    store(key, _api_envelope(), model="fake-model", temperature="0.3")
    _CountingClient.calls = 0
    monkeypatch.setattr(
        "ai_test_asset_center.llm_reasoning.ReasoningClient",
        _CountingClient,
    )
    result = _run("prompt-disabled-1")
    assert result["cache_hit"] is False
    assert result["model_response_count"] == 1
    assert _CountingClient.calls == 1
