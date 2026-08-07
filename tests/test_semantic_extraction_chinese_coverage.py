from __future__ import annotations

from types import SimpleNamespace

from ai_test_asset_center.enterprise_knowledge_center import _semantic_extraction as semantic


class _Client:
    def __init__(self, responder):
        self.config = SimpleNamespace(enabled=True, model="test-model")
        self._responder = responder
        self.calls: list[tuple[str, str]] = []

    def chat_json(self, prompt: str, *, system_prompt: str, tier: str = "strong"):
        self.calls.append((prompt, system_prompt))
        return self._responder(prompt)


def test_long_chinese_source_extracts_candidate_beyond_first_6000_chars(monkeypatch) -> None:
    prefix = "前置业务说明" * 1100
    marker = "后半段订单只能由区域经理审批"
    source = prefix + marker
    marker_start = source.index(marker)
    assert marker_start > 6000

    def responder(prompt: str) -> dict:
        if marker in prompt:
            return {
                "candidates": [
                    {
                        "kind": "entity",
                        "name": "后半段订单",
                        "source_locator": "",
                        "verbatim_quote": marker,
                        "confidence": 0.9,
                    }
                ]
            }
        return {"candidates": []}

    client = _Client(responder)
    monkeypatch.setattr("ai_test_asset_center.llm_reasoning._get_client", lambda: client)

    receipt = semantic.run_semantic_extraction(
        source,
        source_id="prd-long",
        filename="长篇中文PRD.md",
    )

    assert receipt.status == "COMPLETED"
    assert receipt.chunks_attempted >= 2
    assert len(client.calls) == receipt.chunks_attempted
    candidate = receipt.candidates_validated[0]
    assert candidate["name"] == "后半段订单"
    assert candidate["quote_start"] == marker_start
    assert candidate["language_contract"] == "ORIGINAL_CHINESE_PRESERVED"
    assert "不得先翻译成英文" in client.calls[0][1]


def test_chunk_budget_exhaustion_is_visible_not_silent(monkeypatch) -> None:
    client = _Client(lambda _prompt: {"candidates": []})
    monkeypatch.setattr("ai_test_asset_center.llm_reasoning._get_client", lambda: client)

    source = "中文业务资料" * 10000
    receipt = semantic.run_semantic_extraction(
        source,
        source_id="prd-over-budget",
        filename="超长制度.md",
    )

    assert receipt.status == "COMPLETED_WITH_GAPS"
    assert receipt.error == "source_chunk_budget_exhausted"
    assert receipt.unprocessed_ranges
    assert receipt.unprocessed_ranges[0]["end"] == len(source)
    payload = receipt.to_dict()
    assert payload["unprocessed_ranges"] == receipt.unprocessed_ranges
    assert payload["translation_as_fact_authority"] is False


def test_force_allows_coverage_ledger_to_extract_uncovered_span(monkeypatch) -> None:
    client = _Client(lambda _prompt: {"candidates": []})
    monkeypatch.setattr("ai_test_asset_center.llm_reasoning._get_client", lambda: client)

    default_receipt = semantic.run_semantic_extraction(
        "订单规则",
        source_id="prd-structured",
        existing_tables=1,
    )
    forced_receipt = semantic.run_semantic_extraction(
        "订单规则",
        source_id="prd-structured",
        existing_tables=1,
        force=True,
    )

    assert default_receipt.status == "NOT_TRIGGERED_HAS_OUTPUT"
    assert forced_receipt.status == "COMPLETED"
    assert forced_receipt.triggered is True


def test_single_chunk_malformed_status_remains_backward_compatible(monkeypatch) -> None:
    client = _Client(lambda _prompt: {"wrong": []})
    monkeypatch.setattr("ai_test_asset_center.llm_reasoning._get_client", lambda: client)

    receipt = semantic.run_semantic_extraction(
        "订单由管理员审批",
        source_id="prd-malformed",
    )

    assert receipt.status == "FAILED_MALFORMED_RESPONSE"
    assert "missing 'candidates' list" in receipt.error
