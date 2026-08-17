"""Content-addressed mainline Reasoner reuse gate.

The 11-engine Reasoner is the only mainline stage that re-invokes LLM
comprehension on every run regardless of source revision.  These tests pin
the fingerprint gate contract: deterministic content addressing over the full
Reasoner input, persist/load round-trip, schema safety, and the explicit
``QUALIBUG_MAINLINE_REASONER_REUSE_DISABLED`` escape hatch (fail-open to a
fresh LLM run).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import reasoner_input_fingerprint as rf


def _enable_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")


def test_fingerprint_deterministic_and_sensitive_to_all_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_provider(monkeypatch)
    world = {"entities": [{"name": "order"}], "documented_rules": [{"text": "r1"}]}

    fp1 = rf.compute_reasoner_input_fingerprint("prd", "api", world)
    fp2 = rf.compute_reasoner_input_fingerprint("prd", "api", world)
    assert fp1 is not None and fp2 is not None
    assert fp1["sha256"] == fp2["sha256"]

    assert (
        rf.compute_reasoner_input_fingerprint(
            "prd-changed", "api", world
        )["sha256"]
        != fp1["sha256"]
    )
    assert (
        rf.compute_reasoner_input_fingerprint(
            "prd", "api-changed", world
        )["sha256"]
        != fp1["sha256"]
    )
    assert (
        rf.compute_reasoner_input_fingerprint(
            "prd", "api", {"entities": []}
        )["sha256"]
        != fp1["sha256"]
    )


def test_fingerprint_model_and_temperature_are_part_of_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_provider(monkeypatch)
    fp1 = rf.compute_reasoner_input_fingerprint("prd", "api", {})
    monkeypatch.setenv("LLM_MODEL", "other-model")
    fp2 = rf.compute_reasoner_input_fingerprint("prd", "api", {})
    assert fp1 is not None and fp2 is not None
    assert fp1["sha256"] != fp2["sha256"]


def test_fingerprint_none_without_configured_provider(monkeypatch) -> None:
    # The host may carry a configured provider; force-disable it to prove the
    # gate no-ops (there is nothing to reuse) when the Reasoner itself would.
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    assert (
        rf.compute_reasoner_input_fingerprint("prd", "api", {}) is None
    )


def test_reuse_state_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(
        rf.DISABLE_ENV, raising=False
    )
    state = {
        "sha256": "abc123",
        "status": "ok",
        "hypotheses_generated": 2,
        "hypotheses": [{"hypothesis": "h1"}, {"hypothesis": "h2"}],
        "run_id": "RUN-9",
        "campaign_id": "CMP-9",
        "strategy_fingerprint": "a" * 64,
        "project_id": "PROBE-1",
        "persisted_at_utc": "2026-08-18T00:00:00Z",
    }
    assert rf.persist_reasoner_reuse_state(
        state, project_id="PROBE-1", root=tmp_path
    )
    loaded = rf.load_reasoner_reuse_state("PROBE-1", tmp_path)
    assert loaded is not None
    assert loaded["sha256"] == "abc123"
    assert loaded["status"] == "ok"
    assert loaded["hypotheses"] == state["hypotheses"]
    assert loaded["schema"] == rf._STATE_SCHEMA


def test_reuse_state_rejects_corrupt_or_foreign_schema(tmp_path: Path) -> None:
    from ai_test_asset_center.enterprise_knowledge_center._utils import _paths

    path = _paths("PROBE-1", tmp_path)["reasoner_reuse"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert rf.load_reasoner_reuse_state("PROBE-1", tmp_path) is None

    path.write_text(
        '{"schema": "some.other.schema", "sha256": "x"}', encoding="utf-8"
    )
    assert rf.load_reasoner_reuse_state("PROBE-1", tmp_path) is None


def test_disable_env_bypasses_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(rf.DISABLE_ENV, "1")
    assert (
        rf.compute_reasoner_input_fingerprint("prd", "api", {}) is None
    )
    assert rf.load_reasoner_reuse_state("PROBE-1", tmp_path) is None
    assert not rf.persist_reasoner_reuse_state(
        {"sha256": "x"}, project_id="PROBE-1", root=tmp_path
    )
