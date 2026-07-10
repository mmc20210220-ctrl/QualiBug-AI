from __future__ import annotations

import json

from ai_test_asset_center.llm_reasoning import ReasoningClient, ReasoningConfig


def test_reasoning_client_aggregates_provider_reported_usage_without_price_guessing() -> None:
    client = ReasoningClient(ReasoningConfig())
    client._record_usage(json.dumps({
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "cost_usd": 0.004,
        },
        "choices": [{"message": {"content": "{}"}}],
    }))
    client._record_usage(json.dumps({
        "usage": {
            "input_tokens": 40,
            "output_tokens": 10,
            "total_tokens": 50,
        },
        "choices": [{"message": {"content": "{}"}}],
    }))

    usage = client.usage_snapshot()
    assert usage["request_count"] == 2
    assert usage["prompt_tokens"] == 140
    assert usage["completion_tokens"] == 35
    assert usage["total_tokens"] == 175
    assert usage["cost_usd"] == 0.004
    assert usage["responses_with_cost"] == 1
