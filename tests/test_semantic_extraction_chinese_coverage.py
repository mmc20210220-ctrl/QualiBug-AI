from __future__ import annotations

import threading
import time
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


def test_default_semantic_extraction_covers_the_entire_source(monkeypatch) -> None:
    client = _Client(lambda _prompt: {"candidates": []})
    monkeypatch.setattr("ai_test_asset_center.llm_reasoning._get_client", lambda: client)

    source = "中文业务资料" * 10000
    receipt = semantic.run_semantic_extraction(
        source,
        source_id="prd-over-budget",
        filename="超长制度.md",
    )

    assert receipt.status == "COMPLETED"
    assert receipt.chunks_attempted > 8
    assert receipt.unprocessed_ranges == []
    payload = receipt.to_dict()
    assert payload["unprocessed_ranges"] == receipt.unprocessed_ranges
    assert payload["translation_as_fact_authority"] is False


def test_operator_chunk_budget_is_explicit_and_cache_identity_bound(monkeypatch, tmp_path) -> None:
    client = _Client(lambda _prompt: {"candidates": []})
    monkeypatch.setattr("ai_test_asset_center.llm_reasoning._get_client", lambda: client)
    monkeypatch.setenv("QUALIBUG_SEMANTIC_CACHE_DIR", str(tmp_path))

    source = "中文业务资料" * 10000
    bounded = semantic.run_semantic_extraction(
        source,
        source_id="prd-operator-budget",
        filename="超长制度.md",
        max_chunks=2,
    )
    complete = semantic.run_semantic_extraction(
        source,
        source_id="prd-operator-budget",
        filename="超长制度.md",
    )

    assert bounded.status == "COMPLETED_WITH_GAPS"
    assert bounded.error == "operator_chunk_budget_exhausted"
    assert bounded.unprocessed_ranges
    assert bounded.to_dict()["chunk_budget"] == {
        "authority": "operator",
        "max_chunks": 2,
        "unbounded": False,
    }
    assert complete.status == "COMPLETED"
    assert complete.unprocessed_ranges == []
    assert len(client.calls) > bounded.chunks_attempted


def test_model_candidates_are_not_truncated_by_a_product_side_count_cap(monkeypatch) -> None:
    names = [f"业务对象{i}" for i in range(60)]
    source = " ".join(names)
    candidates = [
        {
            "kind": "entity",
            "name": name,
            "source_locator": "",
            "verbatim_quote": name,
            "confidence": 0.9,
        }
        for name in names
    ]
    client = _Client(lambda _prompt: {"candidates": candidates})
    monkeypatch.setattr("ai_test_asset_center.llm_reasoning._get_client", lambda: client)

    receipt = semantic.run_semantic_extraction(
        source,
        source_id="prd-many-candidates",
        filename="规则清单.md",
    )

    assert receipt.status == "COMPLETED"
    assert len(receipt.candidates_raw) == 60
    assert len(receipt.candidates_validated) == 60
    assert receipt.to_dict()["candidate_budget"]["product_side_limit"] is None


def test_multi_source_batch_attempts_every_source_under_one_global_concurrency_limit(
    monkeypatch,
) -> None:
    lock = threading.Lock()
    active = 0
    max_active = 0

    class ConcurrentClient(_Client):
        def chat_json(self, prompt: str, *, system_prompt: str, tier: str = "strong"):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.01)
                return {"candidates": []}
            finally:
                with lock:
                    active -= 1

    client = ConcurrentClient(lambda _prompt: {"candidates": []})
    monkeypatch.setattr("ai_test_asset_center.llm_reasoning._get_client", lambda: client)
    targets = [
        (
            {"source_id": f"source-{index}", "original_name": f"资料{index}.md"},
            (f"资料{index}规则 " * 900),
        )
        for index in range(13)
    ]

    results, batch_receipt = semantic.run_semantic_extraction_batch(targets)

    assert [row[0]["source_id"] for row in results] == [
        f"source-{index}" for index in range(13)
    ]
    assert batch_receipt["target_source_count"] == 13
    assert batch_receipt["attempted_source_count"] == 13
    assert batch_receipt["skipped_source_count"] == 0
    assert batch_receipt["source_limit"] is None
    assert batch_receipt["source_ids"] == [f"source-{index}" for index in range(13)]
    assert batch_receipt["receipt_id"].startswith("semantic-extraction-batch:")
    assert max_active <= batch_receipt["provider_concurrency_limit"] == 4


def test_multi_source_batch_projects_terminal_source_failure(monkeypatch) -> None:
    class FailingClient(_Client):
        def chat_json(self, prompt: str, *, system_prompt: str, tier: str = "strong"):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        "ai_test_asset_center.llm_reasoning._get_client",
        lambda: FailingClient(lambda _prompt: {"candidates": []}),
    )

    _, batch_receipt = semantic.run_semantic_extraction_batch(
        [({"source_id": "failed-source", "original_name": "资料.md"}, "订单规则")]
    )

    assert batch_receipt["status"] == "COMPLETED_WITH_GAPS"
    assert batch_receipt["gap_source_ids"] == ["failed-source"]


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
