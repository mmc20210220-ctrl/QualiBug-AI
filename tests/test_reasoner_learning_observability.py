"""Tests for reasoner learning-loop observability passthrough.

``collect_reasoner_hypotheses`` previously dropped the closed-loop
consumption receipts (``learned_memory_receipt``, ``engine_attention_receipt``,
``fact_retrieval_receipt``, ``semantic_dedup_receipt``, ``graph_context``) at
its boundary, so scan results could neither prove nor disprove that the
reasoner consumed learned knowledge. These tests pin the passthrough.
"""

from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import llm_reasoning as _llm_mod
from ai_test_asset_center import stage_reason_all_v2 as _reasoner_mod


def test_meta_carries_learning_receipts(monkeypatch) -> None:
    """When the reasoner ran, the closed-loop receipts ride in meta."""

    class _FakeConfig:
        enabled = True
        timeout_seconds = 300
        max_tokens = 32768

    class _FakeClientConfig:
        timeout_seconds = 300
        max_tokens = 32768

    def _fake_stage(host, prd, api, reader, prior):
        host._last_engine_report = {
            "total_engines": 3,
            "successful_engines": ["causality"],
            "failed_engines": [],
            "learned_memory_receipt": {
                "status": "CONSUMED",
                "pattern_count": 8,
                "authority": "comprehension_attention_guidance_only",
            },
            "engine_attention_receipt": {
                "status": "CONSUMED",
                "boosted": ["causality"],
            },
            "fact_retrieval_receipt": {"status": "SKIPPED", "facts": 0},
            "semantic_dedup_receipt": {"status": "SKIPPED"},
            "graph_context_mode": "selected",
            "graph_context_ready": True,
            "graph_context_active": True,
            "graph_context_chars": 1024,
            "engine_error_classes": {},
            "engine_error_codes": {},
            "model_attempt_count": 1,
            "model_response_count": 1,
        }
        return [{"hypothesis_id": "h1"}]

    monkeypatch.setattr(_llm_mod.ReasoningConfig, "from_env", staticmethod(lambda: _FakeConfig()))
    monkeypatch.setattr(_llm_mod, "ReasoningClient", lambda config: None)
    monkeypatch.setattr(_reasoner_mod, "_stage_reason_all_v2", _fake_stage)

    hypotheses, meta = _reasoner_mod.collect_reasoner_hypotheses("prd", "api")
    assert hypotheses and hypotheses[0]["hypothesis_id"] == "h1"
    assert meta["status"] == "ok"
    assert meta["learned_memory_receipt"]["status"] == "CONSUMED"
    assert meta["engine_attention_receipt"]["boosted"] == ["causality"]
    assert meta["fact_retrieval_receipt"]["status"] == "SKIPPED"
    assert meta["graph_context"]["active"] is True


def test_collect_reasoner_binds_project_and_root_to_retrieval_host(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The mainline project identity must reach chunk and memory retrieval."""

    class _FakeConfig:
        enabled = True
        timeout_seconds = 300
        max_tokens = 32768

    observed = {}

    def _fake_stage(host, prd, api, reader, prior):
        observed["project"] = host._project
        observed["root"] = host._root
        host._last_engine_report = {
            "total_engines": 0,
            "successful_engines": [],
            "failed_engines": [],
        }
        return []

    monkeypatch.setattr(
        _llm_mod.ReasoningConfig,
        "from_env",
        staticmethod(lambda: _FakeConfig()),
    )
    monkeypatch.setattr(_llm_mod, "ReasoningClient", lambda config: None)
    monkeypatch.setattr(_reasoner_mod, "_stage_reason_all_v2", _fake_stage)

    _reasoner_mod.collect_reasoner_hypotheses(
        "prd",
        "api",
        project_id="project-current",
        root=tmp_path,
    )

    assert observed == {"project": "project-current", "root": tmp_path}


def test_provider_unavailable_path_is_unchanged(monkeypatch) -> None:
    """Without a provider the meta stays minimal and never crashes."""

    class _DisabledConfig:
        enabled = False

    monkeypatch.setattr(
        _llm_mod.ReasoningConfig, "from_env", staticmethod(lambda: _DisabledConfig())
    )
    hypotheses, meta = _reasoner_mod.collect_reasoner_hypotheses("prd", "api")
    assert hypotheses == []
    assert meta["status"] == "provider_unavailable"
    # The new keys are simply absent — no fabricated consumption state.
    assert "learned_memory_receipt" not in meta
