"""Regression tests for the comprehension input-context guard.

These tests pin the three behaviors that previously let an oversized enterprise
corpus overflow the provider context window and then be retried by every engine
for the length of the scan:

1. ``estimate_input_tokens`` is CJK-aware (a char/4 heuristic drastically
   under-counts Chinese text and silently passed oversized prompts).
2. ``_chat`` fails fast with a ``context_overflow`` receipt before any transport
   when the input budget is exceeded — never a provider 400 retried 25 minutes.
3. The graph-context mode resolves from either the canonical
   ``QUALIBUG_GRAPH_CONTEXT_MODE`` or the historical ``GRAPH_CONTEXT_MODE``.
"""
from __future__ import annotations

import os

import pytest

from ai_test_asset_center.llm_reasoning import (
    ReasoningClient,
    ReasoningClientError,
    ReasoningConfig,
    estimate_input_tokens,
)
from ai_test_asset_center.cognitive_memory_graph import _graph_context_mode


def test_estimate_input_tokens_is_cjk_aware() -> None:
    # Pure ASCII is ~1 token per 4 chars; CJK is ~1 token per char.
    assert estimate_input_tokens("a" * 4000) == 1000
    assert estimate_input_tokens("中" * 4000) == 4000
    # Mixed text counts both classes.
    assert estimate_input_tokens("a中") == 2  # 1 ascii char rounds to 1 + 1 cjk


def test_chat_fails_fast_on_input_context_overflow_without_transport() -> None:
    client = ReasoningClient(
        ReasoningConfig(
            base_url="http://127.0.0.1:1",  # would fail transport if reached
            api_key="x",
            model="m",
            max_input_tokens=100,
        )
    )
    with pytest.raises(ReasoningClientError) as exc_info:
        client._chat("中" * 500, call_point="test_engine")
    assert "context overflow" in str(exc_info.value)

    # The failure is recorded as a context_overflow observation (observability),
    # never an http_error, so a scan report can attribute the block precisely.
    snapshot = client._record_observation
    assert callable(snapshot)
    obs = [o for o in __import__("ai_test_asset_center.llm_reasoning", fromlist=["llm_observation_snapshot"]).llm_observation_snapshot() if o.get("call_point") == "test_engine"]
    assert obs, "context overflow must be recorded in the observation ledger"
    assert obs[-1]["failure_reason"] == "context_overflow"
    assert obs[-1]["failure_code"] == "QB-L007"


def test_graph_context_mode_resolves_both_env_names() -> None:
    for name in ("QUALIBUG_GRAPH_CONTEXT_MODE", "GRAPH_CONTEXT_MODE"):
        os.environ.pop("QUALIBUG_GRAPH_CONTEXT_MODE", None)
        os.environ.pop("GRAPH_CONTEXT_MODE", None)
        os.environ[name] = "active"
        assert _graph_context_mode() == "active", f"{name} must resolve to active"
    # Default remains shadow when neither is set.
    os.environ.pop("QUALIBUG_GRAPH_CONTEXT_MODE", None)
    os.environ.pop("GRAPH_CONTEXT_MODE", None)
    assert _graph_context_mode() == "shadow"
