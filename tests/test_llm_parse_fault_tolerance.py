# -*- coding: utf-8 -*-
"""LLM output parse tolerance (Task 30).

Root-cause fix for run10's 62% parse failure rate on chat_json: the parser
only accepted exact-JSON content (optionally fenced), so DeepSeek outputs
with explanation prefix/suffix text, fenced JSON with language tags, JSONC
comments, content-part lists, or max_tokens truncation all collapsed into a
blanket ``response_parse_error`` and every caller degraded (heuristic
fallback / chunk skip).

This module verifies the tolerant pipeline: JSON substring extraction,
fence/comment stripping, truncation closure, granular failure reasons
(shape_error / not_json / prefix_text / truncated), the bounded one-retry
contract, and receipt aggregation of recoveries. All provider I/O is
mocked; no real LLM calls are made.
"""
from __future__ import annotations

import json
import unittest.mock as mock

import pytest

from ai_test_asset_center.llm_reasoning import (
    ReasoningClient,
    ReasoningClientError,
    ReasoningConfig,
    build_llm_observability_receipt,
    llm_observation_snapshot,
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


def _envelope(content: object, *, finish_reason: str = "stop") -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
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


def _processing_recoveries() -> list[dict]:
    return [
        o for o in llm_observation_snapshot()
        if o["kind"] == "response_processing" and o["success"]
    ]


# ---------------------------------------------------------------------------
# JSON substring extraction: prefix / suffix / prose-wrapped output
# ---------------------------------------------------------------------------

def test_clean_json_parses_without_recovery_marker():
    client = _client()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope('{"ok": true}'))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True}
    assert _processing_recoveries() == []


def test_prefix_text_before_json_is_extracted():
    client = _client()
    content = 'Here is my analysis of the business data:\n{"findings": [], "insufficient_evidence": true}'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"findings": [], "insufficient_evidence": True}
    recoveries = _processing_recoveries()
    assert len(recoveries) == 1
    assert recoveries[0]["failure_reason"] == "recovered:extracted"


def test_suffix_text_after_json_is_ignored():
    client = _client()
    content = '{"ok": true}\nThis concludes my audit. No further issues found.'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True}


def test_prefix_and_suffix_text_together():
    client = _client()
    content = "Sure, the JSON is:\n{\"a\": 1}\n\nLet me know if you need more."
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"a": 1}


def test_prose_with_placeholder_braces_skips_to_real_json():
    client = _client()
    content = 'Use the {placeholder} style. Actually here: {"answer": 42} and that is final.'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"answer": 42}


# ---------------------------------------------------------------------------
# Fence variants
# ---------------------------------------------------------------------------

def test_fence_with_language_tag_stripped():
    client = _client()
    content = '```json\n{"ok": true}\n```'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True}
    recoveries = _processing_recoveries()
    assert len(recoveries) == 1
    assert recoveries[0]["failure_reason"] == "recovered:fenced"


def test_fence_bare_triple_backtick_stripped():
    client = _client()
    content = '```\n{"ok": true}\n```'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True}


def test_unclosed_fence_keeps_content():
    client = _client()
    content = '```json\n{"ok": true}'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True}


def test_explanation_before_fence_is_extracted():
    client = _client()
    content = 'Result:\n```json\n{"ok": true}\n```\nHope this helps.'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True}


# ---------------------------------------------------------------------------
# JSONC comments and trailing commas
# ---------------------------------------------------------------------------

def test_jsonc_line_comments_stripped():
    client = _client()
    content = '{\n  "a": 1, // item count\n  "b": 2\n}'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"a": 1, "b": 2}
    recoveries = _processing_recoveries()
    assert recoveries[0]["failure_reason"] == "recovered:comments"


def test_jsonc_block_comments_stripped():
    client = _client()
    content = '{"a": 1, /* block\ncomment */ "b": 2}'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"a": 1, "b": 2}


def test_trailing_commas_stripped():
    client = _client()
    content = '{"a": [1, 2,], "b": {"c": 3,}}'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"a": [1, 2], "b": {"c": 3}}


def test_comment_markers_inside_strings_are_preserved():
    client = _client()
    content = '{"url": "http://example.com/x", "path": "a//b", "note": "/* not a comment */"}'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result["url"] == "http://example.com/x"
    assert result["path"] == "a//b"
    assert result["note"] == "/* not a comment */"


# ---------------------------------------------------------------------------
# Truncation recovery (max_tokens cut)
# ---------------------------------------------------------------------------

def test_truncated_object_mid_array_is_closed():
    client = _client()
    content = '{"a": 1, "b": [1, 2'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"a": 1, "b": [1, 2]}
    recoveries = _processing_recoveries()
    assert recoveries[0]["failure_reason"] == "recovered:truncated_closed"


def test_truncated_mid_string_is_closed():
    client = _client()
    content = '{"a": "unterminated'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"a": "unterminated"}


def test_truncated_nested_structures_closed_in_reverse_order():
    client = _client()
    content = '{"a": {"b": [1, {"c": 2'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"a": {"b": [1, {"c": 2}]}}


def test_finish_reason_length_flags_clean_parse_as_truncated():
    client = _client()
    body = _envelope('{"ok": true}', finish_reason="length")
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True}
    recoveries = _processing_recoveries()
    assert len(recoveries) == 1
    assert recoveries[0]["failure_reason"] == "recovered:truncated_flagged"


def test_finish_reason_length_with_unbalanced_content_closes_and_flags():
    client = _client()
    body = _envelope('{"ok": true, "tail": [1, 2', finish_reason="length")
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True, "tail": [1, 2]}


# ---------------------------------------------------------------------------
# DeepSeek-style content part lists and nested JSON
# ---------------------------------------------------------------------------

def test_content_as_part_list_is_joined():
    client = _client()
    content = [
        {"type": "text", "text": '{"ok": true}'},
        {"type": "text", "text": ""},
    ]
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True}


def test_content_as_single_part_dict():
    client = _client()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope({"text": '{"ok": true}'}))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True}


def test_braces_inside_string_values_do_not_confuse_matching():
    client = _client()
    content = '{"pattern": "a{2,3}", "nested": {"x": "{not json}", "y": [{"z": 1}]}}'
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(_envelope(content))):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"pattern": "a{2,3}", "nested": {"x": "{not json}", "y": [{"z": 1}]}}


# ---------------------------------------------------------------------------
# Granular failure reasons (still raise ReasoningClientError)
# ---------------------------------------------------------------------------

def test_not_json_content_raises_with_reason():
    client = _client()
    body = _envelope("not json at all")
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(ReasoningClientError) as exc_info:
            client.chat_json("hi", caller="parse_tolerance_test")
    assert "parse_reason=not_json" in str(exc_info.value)
    proc = [o for o in llm_observation_snapshot() if o["kind"] == "response_processing"]
    assert all(o["failure_reason"] == "not_json" for o in proc)


def test_empty_content_keeps_legacy_message_contract():
    client = _client()
    body = _envelope("")
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(ReasoningClientError) as exc_info:
            client.chat_json("hi", caller="parse_tolerance_test")
    # agent_semantic_linker classifies this exact message as transient
    # (it lowercases the message before matching).
    assert "did not include json content" in str(exc_info.value).lower()


def test_envelope_not_json_is_shape_error():
    client = _client()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b"<html>502 Bad Gateway</html>")):
        with pytest.raises(ReasoningClientError) as exc_info:
            client.chat_json("hi", caller="parse_tolerance_test")
    assert "response shape" in str(exc_info.value)
    proc = [o for o in llm_observation_snapshot() if o["kind"] == "response_processing"]
    assert all(o["failure_reason"] == "shape_error" for o in proc)


def test_envelope_missing_choices_is_shape_error():
    client = _client()
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse({"usage": {}})):
        with pytest.raises(ReasoningClientError):
            client.chat_json("hi", caller="parse_tolerance_test")
    proc = [o for o in llm_observation_snapshot() if o["kind"] == "response_processing"]
    assert all(o["failure_reason"] == "shape_error" for o in proc)


def test_unsalvageable_truncation_raises_truncated():
    client = _client()
    body = _envelope('{"a":')  # closing yields {"a":} which is invalid
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(ReasoningClientError) as exc_info:
            client.chat_json("hi", caller="parse_tolerance_test")
    assert "parse_reason=truncated" in str(exc_info.value)
    proc = [o for o in llm_observation_snapshot() if o["kind"] == "response_processing"]
    assert all(o["failure_reason"] == "truncated" for o in proc)


def test_finish_reason_length_with_prose_failure_is_truncated():
    client = _client()
    body = _envelope("I could not finish because the output was cut off here", finish_reason="length")
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(ReasoningClientError) as exc_info:
            client.chat_json("hi", caller="parse_tolerance_test")
    assert "parse_reason=truncated" in str(exc_info.value)
    proc = [o for o in llm_observation_snapshot() if o["kind"] == "response_processing"]
    assert all(o["failure_reason"] == "truncated" for o in proc)


def test_malformed_wrapped_json_raises_prefix_text():
    client = _client()
    body = _envelope('The answer is {"a": 1,,,} more text')
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(ReasoningClientError) as exc_info:
            client.chat_json("hi", caller="parse_tolerance_test")
    assert "parse_reason=prefix_text" in str(exc_info.value)
    proc = [o for o in llm_observation_snapshot() if o["kind"] == "response_processing"]
    assert all(o["failure_reason"] == "prefix_text" for o in proc)


def test_root_array_is_shape_error():
    client = _client()
    body = _envelope("[1, 2, 3]")
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(ReasoningClientError) as exc_info:
            client.chat_json("hi", caller="parse_tolerance_test")
    assert "root must be an object" in str(exc_info.value)
    proc = [o for o in llm_observation_snapshot() if o["kind"] == "response_processing"]
    assert all(o["failure_reason"] == "shape_error" for o in proc)


# ---------------------------------------------------------------------------
# Bounded retry: one identical-payload retry on parse failure only
# ---------------------------------------------------------------------------

def test_chat_json_retries_once_and_recovers():
    client = _client()
    bad = _envelope("totally not json")  # unparseable -> triggers the retry
    good = _envelope('{"ok": true}')
    with mock.patch("urllib.request.urlopen", side_effect=[_FakeResponse(bad), _FakeResponse(good)]):
        result = client.chat_json("hi", caller="parse_tolerance_test")
    assert result == {"ok": True}
    obs = llm_observation_snapshot()
    assert len([o for o in obs if o["kind"] == "chat"]) == 2
    proc_failures = [o for o in obs if o["kind"] == "response_processing" and not o["success"]]
    assert len(proc_failures) == 1
    assert proc_failures[0]["failure_reason"] == "not_json"


def test_chat_json_raises_after_two_parse_failures_no_third_call():
    client = _client()
    bad = _envelope("not json at all")
    with mock.patch("urllib.request.urlopen", side_effect=[_FakeResponse(bad), _FakeResponse(bad)]):
        with pytest.raises(ReasoningClientError):
            client.chat_json("hi", caller="parse_tolerance_test")
    obs = llm_observation_snapshot()
    assert len([o for o in obs if o["kind"] == "chat"]) == 2  # exactly one retry, no more
    assert len([o for o in obs if o["kind"] == "response_processing"]) == 2


def test_reason_retries_and_returns_result():
    client = _client()
    bad = _envelope("no json here at all")  # unparseable -> retry inside reason()
    good = _envelope('{"findings": [], "insufficient_evidence": true}')
    with mock.patch("urllib.request.urlopen", side_effect=[_FakeResponse(bad), _FakeResponse(good)]):
        result = client.reason("causality", _REASON_CONTEXT)
    assert result == {"findings": [], "insufficient_evidence": True}
    obs = llm_observation_snapshot()
    assert len([o for o in obs if o["kind"] == "chat"]) == 2


def test_reason_still_swallows_total_parse_failure():
    client = _client()
    bad = _envelope("not json at all")
    with mock.patch("urllib.request.urlopen", side_effect=[_FakeResponse(bad), _FakeResponse(bad)]):
        result = client.reason("causality", _REASON_CONTEXT)
    assert result is None
    obs = llm_observation_snapshot()
    assert len([o for o in obs if o["kind"] == "chat"]) == 2
    assert len([o for o in obs if o["kind"] == "response_processing"]) == 2


def test_no_retry_after_http_error():
    client = _client()
    from urllib.error import HTTPError
    import io
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=HTTPError("http://llm.test/v1/chat/completions", 500, "boom", {}, io.BytesIO(b"oops")),
    ):
        result = client.reason("causality", _REASON_CONTEXT)
    assert result is None
    obs = llm_observation_snapshot()
    assert len(obs) == 1  # no retry on HTTP failures


# ---------------------------------------------------------------------------
# Receipt aggregation of parse recoveries
# ---------------------------------------------------------------------------

def test_receipt_aggregates_parse_recoveries():
    client = _client()
    bodies = [
        _envelope('{"a": 1} trailing prose'),
        _envelope('```json\n{"b": 2}\n```'),
        _envelope('{"c": 3, "d": [4, 5'),
        _envelope('{"e": 6}', finish_reason="length"),
    ]
    with mock.patch("urllib.request.urlopen", side_effect=[_FakeResponse(b) for b in bodies]):
        for _ in range(4):
            client.chat_json("hi", caller="parse_tolerance_test")
    receipt = build_llm_observability_receipt()
    recoveries = receipt["parse_recoveries"]
    assert recoveries["count"] == 4
    assert recoveries["by_method"]["recovered:extracted"] == 1
    assert recoveries["by_method"]["recovered:fenced"] == 1
    assert recoveries["by_method"]["recovered:truncated_closed"] == 1
    assert recoveries["by_method"]["recovered:truncated_flagged"] == 1
    assert receipt["summary"]["parse_recovered_calls"] == 4
    # recovered parses are not failures
    assert receipt["response_processing_failures"]["count"] == 0
    assert receipt["summary"]["successful_calls"] == 4


# ---------------------------------------------------------------------------
# run10-style reproduction: the real failure shapes as one batch
# ---------------------------------------------------------------------------

def test_run10_deepseek_failure_shapes_now_parse():
    """Every shape that contributed to run10's 62% response_parse_error on
    chat_json must parse. Before the fix all six shapes raised
    JSONDecodeError and the callers degraded (heuristic fallback / chunk
    skip / linker abort)."""
    client = _client()
    shapes = [
        # 1. explanation prefix before the JSON
        '根据分析，结果如下：\n{"candidates": [], "insufficient_evidence": true}',
        # 2. suffix text after the JSON
        '{"candidates": [], "insufficient_evidence": true}\n以上就是全部结论。',
        # 3. fenced JSON with a language tag and prose around it
        'Result:\n```json\n{"candidates": []}\n```\nHope this helps.',
        # 4. JSONC comments + trailing commas inside the object
        '{"candidates": [{"id": "C1", "rule": "conservation", // primary rule\n"severity": "P1",}], "insufficient_evidence": false,}',
        # 5. truncated mid-object at max_tokens
        '{"candidates": [{"id": "C1", "rule": "conservation", "severity": "P1", "title": "leak", "evidence": [',
        # 6. DeepSeek thinking-style content part list
        [{"type": "text", "text": '{"candidates": [], "insufficient_evidence": false}'}],
    ]
    with mock.patch("urllib.request.urlopen", side_effect=[_FakeResponse(_envelope(s)) for s in shapes]):
        for index, shape in enumerate(shapes):
            result = client.chat_json(f"query {index}", caller="parse_tolerance_test")
            assert isinstance(result, dict), f"shape {index} failed to parse: {shape!r}"
    # shapes 1-5 need content-level recovery; shape 6 (part list) parses as a
    # clean content string, so it carries no recovery marker.
    assert len(_processing_recoveries()) == 5
    proc_failures = [o for o in llm_observation_snapshot() if o["kind"] == "response_processing" and not o["success"]]
    assert proc_failures == []
