"""Tests for binding-experience learning (write + read sides).

Covers:
- extraction of verified (BOUND) and failed resolver mappings from a v12
  scan result, and the guarantee that resolved business values never enter
  the knowledge base (fingerprint-only / no-inference contract);
- persistence of ``binding_resolver`` entries and non-reinforcement decay;
- planning-time resolver reorder (verified-first stable sort, no-history
  plans untouched, no sources added, receipts visible).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import learning_knowledge_db as _db_mod
from ai_test_asset_center import learning_pattern_bridge as _bridge_mod
from ai_test_asset_center.binding_experience_learning import (
    apply_binding_experience_reorder,
    build_binding_experience_context,
    build_binding_experience_index,
    extract_binding_experience,
)
from ai_test_asset_center.learning_knowledge_db import LearningKnowledgeDB

PROJECT = "binding_experience_test"


@pytest.fixture()
def isolated_kb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every knowledge-store path at tmp_path so nothing real is touched."""
    monkeypatch.setattr(_db_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(_bridge_mod, "_REPO_ROOT", tmp_path)
    return tmp_path


def _bound_receipt(
    *, target: str = "sku", operation_ref: str = "bir_products_list",
    path: str = "/api/products", status_code: int = 200,
) -> dict:
    return {
        "target": target,
        "status": "BOUND",
        "source_priority": "same_actor_list_read",
        "resolver_path": path,
        "resolver_operation_ref": operation_ref,
        "status_code": status_code,
        "resolver_actor_ref": "bir_actor_1",
        "value_fingerprint": "abc123def",
        "owner_actor_ref": "",
    }


def _blocked_receipt(*, operation_ref: str = "bir_products_list") -> dict:
    return {
        "target": "sku",
        "status": "BLOCKED",
        "source_priority": "same_actor_list_read",
        "resolver_path": "/api/products",
        "resolver_operation_ref": operation_ref,
        "status_code": 404,
        "resolver_actor_ref": "bir_actor_1",
    }


def _scan_result(*, execution_results: list[dict]) -> dict:
    return {
        "v12": {"experiment_execution": {"results": execution_results}},
    }


def _execution_result(binding_receipts: list[dict]) -> dict:
    return {
        "schema_version": "qualibug.experiment-execution.v1",
        "experiment_id": "exp_1",
        "obligation_id": "obl_1",
        "status": "DELIVERABLE",
        "binding_materialization_receipts": binding_receipts,
    }


# ── Write side: extraction ──


def test_extract_binding_experience_verified_and_failed() -> None:
    result = _scan_result(execution_results=[
        _execution_result([_bound_receipt()]),
        _execution_result([_blocked_receipt()]),
    ])
    extracted = extract_binding_experience(result)
    assert extracted["results_seen"] == 2
    assert len(extracted["verified"]) == 1
    assert len(extracted["failed"]) == 1
    verified = extracted["verified"][0]
    assert verified["operation_ref"] == "bir_products_list"
    assert verified["target"] == "sku"
    assert verified["path"] == "/api/products"
    failed = extracted["failed"][0]
    assert failed["operation_ref"] == "bir_products_list"
    assert failed["status_code"] == 404


def test_extract_never_carries_resolved_values() -> None:
    """The resolved business value (fingerprint) must never be extracted."""
    result = _scan_result(execution_results=[
        _execution_result([_bound_receipt()]),
    ])
    extracted = extract_binding_experience(result)
    assert extracted["verified"][0].get("value") is None
    assert "value_fingerprint" not in extracted["verified"][0]
    # No synthetic identifiers either — only source-declared identities.
    serialized = str(extracted["verified"][0])
    assert "abc123def" not in serialized


def test_extract_skips_receipts_without_resolver_identity() -> None:
    receipt = {"target": "sku", "status": "BOUND", "resolver_path": "/api/x"}
    result = _scan_result(execution_results=[_execution_result([receipt])])
    extracted = extract_binding_experience(result)
    assert extracted["verified"] == []
    assert extracted["failed"] == []


# ── Write side: persistence into the knowledge base ──


def test_write_persists_binding_resolver_entries(isolated_kb: Path) -> None:
    root = isolated_kb
    result = _scan_result(execution_results=[
        _execution_result([_bound_receipt()]),
    ])
    receipt = build_binding_experience_context(PROJECT, root, result)
    assert receipt["status"] == "OK"
    assert receipt["verified_count"] == 1
    assert receipt["stored_count"] == 1

    db = LearningKnowledgeDB(project=PROJECT)
    entries = db.get_effective_patterns("binding_resolver", min_usage=0)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.key == "bir_products_list:sku"
    assert entry.confidence == pytest.approx(0.95)
    content = entry.content
    # Only source-declared identities — never the resolved business value.
    assert content["operation_ref"] == "bir_products_list"
    assert content["target"] == "sku"
    assert content["path"] == "/api/products"
    assert "value" not in content and "value_fingerprint" not in content


def test_write_decays_failed_resolver_mappings(isolated_kb: Path) -> None:
    root = isolated_kb
    # Round 1: BOUND -> stored at 0.95
    result_ok = _scan_result(execution_results=[
        _execution_result([_bound_receipt()]),
    ])
    build_binding_experience_context(PROJECT, root, result_ok)
    db = LearningKnowledgeDB(project=PROJECT)
    assert db.get_effective_patterns("binding_resolver", min_usage=0)[0].confidence == pytest.approx(0.95)

    # Round 2: same resolver mapping tried but BLOCKED -> decayed, not deleted.
    result_fail = _scan_result(execution_results=[
        _execution_result([_blocked_receipt()]),
    ])
    receipt = build_binding_experience_context(PROJECT, root, result_fail)
    assert receipt["status"] == "DECAY_ONLY"
    assert receipt["decayed_count"] == 1
    entries = db.get_effective_patterns("binding_resolver", min_usage=0)
    assert len(entries) == 1  # never deleted
    assert entries[0].confidence < 0.95
    assert entries[0].confidence >= 0.05  # floor keeps it testable


def test_write_failure_stays_visible(isolated_kb: Path, monkeypatch) -> None:
    # A knowledge-base write failure must surface as FAILED, never crash
    # silently or fake an empty success.
    class _BrokenBridge:
        def __init__(self, project: str):
            self.kb = _BrokenKB()

    class _BrokenKB:
        def store(self, **kwargs):
            raise RuntimeError("disk_full")

        def adjust_confidence(self, *args, **kwargs):
            return 0

    monkeypatch.setattr(
        "ai_test_asset_center.learning_pattern_bridge.LearningPatternBridge",
        _BrokenBridge,
    )
    result = _scan_result(execution_results=[
        _execution_result([_bound_receipt()]),
    ])
    receipt = build_binding_experience_context(PROJECT, isolated_kb, result)
    assert receipt["status"] == "FAILED"
    assert "disk_full" in receipt["failure"]


def test_write_no_records_is_idempotent(isolated_kb: Path) -> None:
    # A scan with no binding materialization receipts is a no-op write.
    receipt = build_binding_experience_context(
        PROJECT, isolated_kb, _scan_result(execution_results=[])
    )
    assert receipt["status"] == "NO_RECORDS"
    assert receipt["verified_count"] == 0


# ── Read side: resolver reorder ──


def _runtime_resolvable_binding(resolvers: list[dict]) -> dict:
    return {
        "target": "sku",
        "status": "runtime_resolvable",
        "source_priority": "same_actor_list_read",
        "resolver_operations": resolvers,
    }


def _experiment_with_binding(binding: dict) -> dict:
    return {"obligation_id": "obl_1", "binding_plan": [binding]}


def test_reorder_prefers_verified_resolver() -> None:
    learned = {
        "binding_resolvers": [
            {
                "key": "bir_products_list:sku",
                "operation_ref": "bir_products_list",
                "target": "sku",
                "confidence": 0.95,
                "success_count": 4,
            },
        ]
    }
    experiment = _experiment_with_binding(_runtime_resolvable_binding([
        {"operation_ref": "bir_alt_1", "method": "GET", "path": "/api/x"},
        {"operation_ref": "bir_products_list", "method": "GET", "path": "/api/products"},
    ]))
    receipt = apply_binding_experience_reorder({"obl_1": experiment}, learned)
    assert receipt["status"] == "CONSUMED"
    assert receipt["reordered_count"] == 1
    order = [r["operation_ref"] for r in experiment["binding_plan"][0]["resolver_operations"]]
    assert order[0] == "bir_products_list"  # verified resolver moved first
    assert set(order) == {"bir_alt_1", "bir_products_list"}  # no sources added


def test_reorder_is_stable_without_history() -> None:
    learned = {"binding_resolvers": []}
    experiment = _experiment_with_binding(_runtime_resolvable_binding([
        {"operation_ref": "bir_a", "method": "GET", "path": "/api/a"},
        {"operation_ref": "bir_b", "method": "GET", "path": "/api/b"},
    ]))
    receipt = apply_binding_experience_reorder({"obl_1": experiment}, learned)
    assert receipt["status"] == "NO_PATTERNS"
    assert receipt["reordered_count"] == 0
    order = [r["operation_ref"] for r in experiment["binding_plan"][0]["resolver_operations"]]
    assert order == ["bir_a", "bir_b"]  # untouched


def test_reorder_ignores_bound_and_blocked_bindings() -> None:
    learned = {
        "binding_resolvers": [
            {"operation_ref": "bir_x", "target": "sku", "confidence": 0.95, "success_count": 2},
        ]
    }
    experiment = {
        "obligation_id": "obl_1",
        "binding_plan": [
            {"target": "sku", "status": "bound", "source_priority": "observed", "value_fingerprint": "f"},
            {"target": "other", "status": "blocked", "source_priority": "path_placeholder_unresolvable"},
        ],
    }
    receipt = apply_binding_experience_reorder({"obl_1": experiment}, learned)
    assert receipt["status"] == "CONSUMED"
    assert receipt["reordered_count"] == 0
    assert experiment["binding_plan"][0]["status"] == "bound"  # untouched


def test_reorder_single_resolver_list_is_untouched() -> None:
    learned = {
        "binding_resolvers": [
            {"operation_ref": "bir_x", "target": "sku", "confidence": 0.95, "success_count": 2},
        ]
    }
    experiment = _experiment_with_binding(_runtime_resolvable_binding([
        {"operation_ref": "bir_x", "method": "GET", "path": "/api/x"},
    ]))
    receipt = apply_binding_experience_reorder({"obl_1": experiment}, learned)
    assert receipt["reordered_count"] == 0  # nothing to reorder


def test_reorder_load_failure_is_visible() -> None:
    learned = {"load_failure": "sqlite_corrupt", "binding_resolvers": []}
    experiment = _experiment_with_binding(_runtime_resolvable_binding([
        {"operation_ref": "bir_a", "method": "GET", "path": "/api/a"},
    ]))
    receipt = apply_binding_experience_reorder({"obl_1": experiment}, learned)
    assert receipt["status"] == "LOAD_FAILED"
    assert receipt["load_failure"] == "sqlite_corrupt"
    assert receipt["reordered_count"] == 0


def test_build_binding_experience_index_contract() -> None:
    index = build_binding_experience_index({
        "binding_resolvers": [
            {"key": "bir_a:sku", "operation_ref": "bir_a", "target": "sku", "confidence": 0.9, "success_count": 3},
        ]
    })
    assert index["status"] == "CONSUMED"
    assert index["resolver_count"] == 1
    assert index["entries"][0]["success_count"] == 3

    empty = build_binding_experience_index({})
    assert empty["status"] == "NO_PATTERNS"

    broken = build_binding_experience_index({"binding_resolvers": [{"operation_ref": ""}]})
    assert broken["status"] == "NO_PATTERNS"
