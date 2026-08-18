"""Delivery validation performance caches: correctness, invalidation, safety.

The delivery phase re-validates the same sealed receipts and findings many
times within one run.  The caches are content-addressed: a content change
changes the key and forces recomputation; failures are never cached, so no
fail-closed gate is relaxed.  These tests verify:

* finding payload fingerprint cache (redact result) reuse + invalidation;
* gate receipt validation cache reuse + invalidation (fingerprint mismatch
  must re-raise after the finding content changes);
* gate bundle validation cache reuse + invalidation;
* ledger validation cache reuse + invalidation (mutated ledger is re-validated
  and a mutated ledger that no longer matches its fingerprint still fails);
* gate index cache reuse + invalidation;
* semantic invariance: cached result equals the uncached result;
* thread safety: concurrent validation yields identical results.
"""
from __future__ import annotations

import threading
from copy import deepcopy

import pytest

from ai_test_asset_center import _customer_delivery_gate_v2_mechanics as mechanics
from ai_test_asset_center import obligation_attempt_ledger as ledger_facade
from ai_test_asset_center._delivery_validation_cache import (
    clear_delivery_validation_caches,
)
from ai_test_asset_center.customer_delivery_gate import (
    LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
)
from ai_test_asset_center.customer_delivery_gate_v2 import (
    DeliveryGateV2Error,
    validate_customer_delivery_gate_bundle,
    validate_customer_delivery_gate_receipt_v2,
)
from ai_test_asset_center.discovery_mainline_contract import MainlineContractError
from ai_test_asset_center.formal_delivery_scope import (
    validated_deliverable_gate_index,
)


# ── Fixtures: real, self-consistent v2 gates ────────────────────────────────


def _identity(finding_id: str) -> dict:
    return {
        "run_id": "run:1",
        "campaign_id": "campaign:1",
        "target_id": "target:1",
        "environment_id": "environment:1",
        "mainline_contract_fingerprint": "m" * 64,
        "candidate_id": "candidate:1",
        "slice_id": "slice:1",
        "obligation_id": "obl:1",
        "experiment_id": "exp:1",
        "execution_id": "exec:1",
        "evidence_id": "evidence:1",
        "finding_id": finding_id,
    }


def _receipt_refs() -> dict:
    return {
        "execution": {"receipt_id": "execution:1", "fingerprint": "e" * 64},
        "actors": [],
        "fixtures": [],
        "controls": [],
        "treatments": [],
        "observers": [],
        "assertions": [],
        "oracle": {"receipt_id": "oracle:1", "fingerprint": "o" * 64},
        "reproduction": {"receipt_id": "reproduction:1", "fingerprint": "r" * 64},
        "cleanup": [],
        "lineage": {"receipt_id": "lineage:1", "fingerprint": "l" * 64},
    }


def _deliverable_finding(finding_id: str = "finding:1") -> dict:
    return {
        "finding_id": finding_id,
        "title": "Tenant A can read tenant B orders",
        "severity": "high",
        "evidence": {
            "method": "GET",
            "path": "/api/orders/1",
            "observed": {"status": 200, "body": {"customerId": "tenant-b"}},
        },
    }


def _deliverable_gate(finding: dict) -> dict:
    """Build a real, valid DELIVERABLE v2 gate bound to ``finding``."""
    identity = _identity(finding["finding_id"])
    payload_fingerprint = mechanics.finding_payload_fingerprint(finding)
    receipt_refs = _receipt_refs()
    payload = {
        "schema_version": "qualibug.customer-delivery-gate-receipt.v2",
        "status": "DELIVERABLE",
        "reason_code": "",
        "reason_codes": [],
        "identity": identity,
        "finding_payload_fingerprint": payload_fingerprint,
        "receipt_refs": receipt_refs,
        "adjudication": {
            "execution": "EXECUTED",
            "activation": "ACTIVE",
            "assertion": "VIOLATION",
            "oracle": "VIOLATION",
            "reproduction": "REPRODUCED",
            "cleanup": "COMPLETED",
            "lineage": "CONSISTENT",
        },
        "cost_coverage_status": "MEASURED",
        "input_fingerprint": mechanics._fingerprint({
            "identity": identity,
            "finding_payload_fingerprint": payload_fingerprint,
            "receipt_refs": receipt_refs,
        }),
    }
    return mechanics._seal(
        payload,
        prefix="gate_",
        id_field="gate_receipt_id",
        fingerprint_field="output_fingerprint",
    )


def _harness_gate(*, reason_detail: str = "") -> dict:
    """Build a real, valid HARNESS_FAILED v2 gate (no finding required)."""
    identity = _identity("")
    receipt_refs = _receipt_refs()
    payload = {
        "schema_version": "qualibug.customer-delivery-gate-receipt.v2",
        "status": "HARNESS_FAILED",
        "reason_code": "CONTRACT_ORACLE_HARNESS_FAILED",
        "reason_codes": ["CONTRACT_ORACLE_HARNESS_FAILED"],
        "identity": identity,
        "finding_payload_fingerprint": "",
        "receipt_refs": receipt_refs,
        "adjudication": {
            "execution": "EXECUTED",
            "activation": "HARNESS_FAILED",
            "assertion": "PASS",
            "oracle": "HARNESS_FAILED",
            "reproduction": "NOT_REPRODUCED",
            "cleanup": "FAILED",
            "lineage": "CONSISTENT",
        },
        "cost_coverage_status": "UNKNOWN",
        "input_fingerprint": mechanics._fingerprint({
            "identity": identity,
            "finding_payload_fingerprint": "",
            "receipt_refs": receipt_refs,
        }),
    }
    if reason_detail:
        payload["reason_detail"] = reason_detail
    return mechanics._seal(
        payload,
        prefix="gate_",
        id_field="gate_receipt_id",
        fingerprint_field="output_fingerprint",
    )


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_delivery_validation_caches()
    yield
    clear_delivery_validation_caches()


# ── finding_payload_fingerprint (redact result) cache ───────────────────────


def test_finding_fingerprint_cache_reuses_result(monkeypatch) -> None:
    from ai_test_asset_center import artifact_redactor

    finding = _deliverable_finding()
    calls = {"n": 0}
    original = artifact_redactor.redact_artifact

    def counting_redact(payload):
        calls["n"] += 1
        return original(payload)

    monkeypatch.setattr(artifact_redactor, "redact_artifact", counting_redact)

    first = mechanics.finding_payload_fingerprint(finding)
    second = mechanics.finding_payload_fingerprint(finding)
    assert first == second
    # Second call with identical content must not re-run the expensive redact.
    assert calls["n"] == 1


def test_finding_fingerprint_cache_invalidates_on_content_change() -> None:
    finding = _deliverable_finding()
    before = mechanics.finding_payload_fingerprint(finding)
    finding["title"] = "changed title"
    after = mechanics.finding_payload_fingerprint(finding)
    assert before != after


def test_finding_fingerprint_cache_respects_derived_fields() -> None:
    finding = _deliverable_finding()
    base = mechanics.finding_payload_fingerprint(finding)
    projected = dict(finding)
    projected["finding_class"] = "shadow"
    projected["delivery_gate_receipt_id"] = "gate:1"
    assert mechanics.finding_payload_fingerprint(projected) == base


# ── validate_customer_delivery_gate_receipt_v2 cache ────────────────────────


def test_gate_validation_cache_reuses_result() -> None:
    finding = _deliverable_finding()
    gate = _deliverable_gate(finding)
    first = validate_customer_delivery_gate_receipt_v2(gate, finding=finding)
    second = validate_customer_delivery_gate_receipt_v2(gate, finding=finding)
    assert first == second
    assert first == gate


def test_gate_validation_cache_invalidates_when_finding_changes() -> None:
    finding = _deliverable_finding()
    gate = _deliverable_gate(finding)
    assert validate_customer_delivery_gate_receipt_v2(gate, finding=finding) == gate
    finding["title"] = "tenant B can read tenant A orders"
    # The gate is bound to the original payload fingerprint; after the finding
    # content changes, validation MUST fail again (no stale success served).
    with pytest.raises(DeliveryGateV2Error, match="finding_payload_fingerprint_mismatch"):
        validate_customer_delivery_gate_receipt_v2(gate, finding=finding)


def test_gate_validation_cache_invalidates_when_gate_changes() -> None:
    finding = _deliverable_finding()
    gate = _deliverable_gate(finding)
    first = validate_customer_delivery_gate_receipt_v2(gate, finding=finding)
    mutated = deepcopy(gate)
    mutated["adjudication"]["cleanup"] = "NOT_REQUIRED"
    # Mutating the receipt breaks its own output fingerprint: validation must
    # re-run and raise, proving no stale cached success is served.
    with pytest.raises(DeliveryGateV2Error):
        validate_customer_delivery_gate_receipt_v2(mutated, finding=finding)
    assert first == gate


def test_gate_validation_cache_never_caches_failures() -> None:
    finding = _deliverable_finding()
    bad_gate = dict(_deliverable_gate(finding))
    bad_gate.pop("output_fingerprint")
    with pytest.raises(DeliveryGateV2Error):
        validate_customer_delivery_gate_receipt_v2(bad_gate, finding=finding)
    # Same invalid input must raise again (not served from cache).
    with pytest.raises(DeliveryGateV2Error):
        validate_customer_delivery_gate_receipt_v2(bad_gate, finding=finding)


def test_gate_validation_cache_cached_equals_uncached() -> None:
    finding = _deliverable_finding()
    gate = _deliverable_gate(finding)
    clear_delivery_validation_caches()
    uncached = validate_customer_delivery_gate_receipt_v2(gate, finding=finding)
    clear_delivery_validation_caches()
    cached_first = validate_customer_delivery_gate_receipt_v2(gate, finding=finding)
    cached_second = validate_customer_delivery_gate_receipt_v2(gate, finding=finding)
    assert cached_first == uncached
    assert cached_second == uncached


# ── validate_customer_delivery_gate_bundle cache ────────────────────────────


def test_bundle_validation_cache_reuses_and_invalidates(monkeypatch) -> None:
    from ai_test_asset_center import customer_delivery_gate_v2 as gate_v2

    # The bundle validator rebuilds the gate from receipts; stub the rebuild
    # with a counting wrapper (same pattern as the existing cleanup test) so
    # the cache hit is observable as a skipped rebuild.  The stub echoes the
    # gate content being rebuilt so validated == rebuilt always holds.
    harness = _harness_gate()
    other_harness = _harness_gate(reason_detail="diagnostic")
    rebuild_calls = {"n": 0}

    def counting_build(**kwargs):
        rebuild_calls["n"] += 1
        if rebuild_calls["n"] == 1:
            return deepcopy(harness)
        return deepcopy(other_harness)

    monkeypatch.setattr(
        gate_v2, "build_customer_delivery_gate_receipt_v2", counting_build
    )
    bundle_kwargs = dict(
        gate_receipt=harness,
        finding=None,
        execution_receipt={},
        contract_evidence_receipts=[],
        observer_receipts=[],
        oracle_receipt={},
        reproduction_receipt={},
    )
    first = validate_customer_delivery_gate_bundle(**bundle_kwargs)
    second = validate_customer_delivery_gate_bundle(**bundle_kwargs)
    assert first == second == harness
    # Second identical bundle must not re-run the full rebuild.
    assert rebuild_calls["n"] == 1
    # Content change invalidates: a different gate content forces a rebuild.
    other_kwargs = dict(bundle_kwargs, gate_receipt=other_harness)
    third = validate_customer_delivery_gate_bundle(**other_kwargs)
    assert third == other_harness
    assert rebuild_calls["n"] == 2


# ── validate_obligation_attempt_ledger cache ────────────────────────────────


def _minimal_ledger() -> dict:
    """A structurally minimal ledger the facade validator accepts."""
    root = {
        "schema_version": "qualibug.obligation-attempt-ledger.v1",
        "run_id": "run:1",
        "campaign_id": "campaign:1",
        "mainline_contract_fingerprint": "m" * 64,
        "selected_count": 0,
        "terminal_count": 0,
        "accounted_count": 0,
        "complete": True,
        "terminal_status_counts": {},
        "selection_status_counts": {},
        "attempts": [],
    }
    root["ledger_fingerprint"] = mechanics._fingerprint({
        key: value for key, value in root.items() if key != "ledger_fingerprint"
    })
    return root


def test_ledger_validation_cache_reuses_result(monkeypatch) -> None:
    ledger = _minimal_ledger()
    calls = {"n": 0}
    original = ledger_facade._original_validate_obligation_attempt_ledger

    def counting_validate(value):
        calls["n"] += 1
        return original(value)

    monkeypatch.setattr(
        ledger_facade,
        "_original_validate_obligation_attempt_ledger",
        counting_validate,
    )
    first = ledger_facade.validate_obligation_attempt_ledger(ledger)
    second = ledger_facade.validate_obligation_attempt_ledger(ledger)
    assert first == second
    assert calls["n"] == 1


def test_ledger_validation_cache_invalidates_on_content_change(monkeypatch) -> None:
    ledger = _minimal_ledger()
    calls = {"n": 0}
    original = ledger_facade._original_validate_obligation_attempt_ledger

    def counting_validate(value):
        calls["n"] += 1
        return original(value)

    monkeypatch.setattr(
        ledger_facade,
        "_original_validate_obligation_attempt_ledger",
        counting_validate,
    )
    ledger_facade.validate_obligation_attempt_ledger(ledger)
    # Real content change: a new attempt set and a fresh sealed fingerprint.
    changed_ledger = _sealed_ledger(attempts=[_valid_legacy_attempt()])
    ledger_facade.validate_obligation_attempt_ledger(changed_ledger)
    assert calls["n"] == 2


def test_ledger_validation_cache_mutation_without_reseal_fails_closed(
    monkeypatch,
) -> None:
    ledger = _minimal_ledger()
    calls = {"n": 0}
    original = ledger_facade._original_validate_obligation_attempt_ledger

    def counting_validate(value):
        calls["n"] += 1
        return original(value)

    monkeypatch.setattr(
        ledger_facade,
        "_original_validate_obligation_attempt_ledger",
        counting_validate,
    )
    ledger_facade.validate_obligation_attempt_ledger(ledger)
    # Mutate content WITHOUT resealing: the next validation must re-run and
    # fail closed on the fingerprint mismatch (never served from cache).
    ledger["selected_count"] = 999
    with pytest.raises(Exception):
        ledger_facade.validate_obligation_attempt_ledger(ledger)
    assert calls["n"] == 2


# ── validated_deliverable_gate_index cache ──────────────────────────────────


def _valid_legacy_attempt(finding_id: str = "finding:legacy") -> dict:
    """A legacy DELIVERABLE attempt accepted by the ledger mechanics."""
    attempt = {
        "terminal_status": "DELIVERABLE",
        "finding_id": finding_id,
        "risk_family": "validation",
        "obligation_id": "obl:1",
        "executed_obligation_id": "obl:1",
        "experiment_id": "exp:1",
        "execution_id": "exec:1",
        "selection_status": "SELECTED",
        "terminal_stage": "gate",
        "stages": [{"stage": "gate"}],
        "gate_receipt": {
            "schema_version": LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
            "status": "DELIVERABLE",
            "finding_id": finding_id,
            "receipt_id": "legacy:1",
            "gate_receipt_id": "gate:legacy",
            "output_fingerprint": "g" * 64,
            "finding_payload_fingerprint": "p" * 64,
        },
        "delivery_evidence_bundle": {"finding": {"finding_id": finding_id}},
    }
    attempt["attempt_fingerprint"] = mechanics._fingerprint({
        key: value for key, value in attempt.items() if key != "attempt_fingerprint"
    })
    return attempt


def _sealed_ledger(*, attempts: list[dict], run_id: str = "run:1") -> dict:
    root = {
        "schema_version": "qualibug.obligation-attempt-ledger.v1",
        "run_id": run_id,
        "campaign_id": "campaign:1",
        "mainline_contract_fingerprint": "m" * 64,
        "selected_count": len(attempts),
        "terminal_count": len(attempts),
        "accounted_count": len(attempts),
        "complete": True,
        "terminal_status_counts": dict(
            sorted(
                {
                    status: sum(
                        1
                        for attempt in attempts
                        if attempt.get("terminal_status") == status
                    )
                    for status in {
                        attempt.get("terminal_status") for attempt in attempts
                    }
                }.items()
            )
        ),
        "selection_status_counts": dict(
            sorted(
                {
                    status: sum(
                        1
                        for attempt in attempts
                        if (attempt.get("selection_status") or "SELECTED") == status
                    )
                    for status in {
                        attempt.get("selection_status") or "SELECTED"
                        for attempt in attempts
                    }
                }.items()
            )
        ),
        "attempts": attempts,
    }
    root["ledger_fingerprint"] = mechanics._fingerprint({
        key: value for key, value in root.items() if key != "ledger_fingerprint"
    })
    return root


def _ledger_with_legacy_occurrence() -> dict:
    """Ledger with one legacy DELIVERABLE attempt the index accepts."""
    return _sealed_ledger(attempts=[_valid_legacy_attempt()])


def test_gate_index_cache_reuses_result(monkeypatch) -> None:
    ledger = _ledger_with_legacy_occurrence()
    calls = {"n": 0}
    original = ledger_facade._original_validate_obligation_attempt_ledger

    def counting_validate(value):
        calls["n"] += 1
        return original(value)

    monkeypatch.setattr(
        ledger_facade,
        "_original_validate_obligation_attempt_ledger",
        counting_validate,
    )
    first = validated_deliverable_gate_index(ledger)
    second = validated_deliverable_gate_index(ledger)
    assert first == second
    assert first == {"finding:legacy": ledger["attempts"][0]["gate_receipt"]}
    # Second identical ledger must not re-validate / re-derive the index.
    assert calls["n"] == 1


def test_gate_index_cache_invalidates_on_content_change(monkeypatch) -> None:
    ledger = _ledger_with_legacy_occurrence()
    calls = {"n": 0}
    original = ledger_facade._original_validate_obligation_attempt_ledger

    def counting_validate(value):
        calls["n"] += 1
        return original(value)

    monkeypatch.setattr(
        ledger_facade,
        "_original_validate_obligation_attempt_ledger",
        counting_validate,
    )
    first = validated_deliverable_gate_index(ledger)
    # Reseal the ledger with a different occurrence: content change must force
    # re-validation and re-derivation.
    changed_attempt = _valid_legacy_attempt(finding_id="finding:changed")
    changed_ledger = _sealed_ledger(attempts=[changed_attempt])
    second = validated_deliverable_gate_index(changed_ledger)
    assert first != second
    assert second == {"finding:changed": changed_attempt["gate_receipt"]}
    assert calls["n"] == 2


def test_gate_index_cache_failures_never_cached() -> None:
    ledger = {"run_id": "run:1", "campaign_id": "campaign:1", "attempts": []}
    with pytest.raises(MainlineContractError):
        validated_deliverable_gate_index(ledger)
    # Same invalid ledger must raise again (fail-closed preserved).
    with pytest.raises(MainlineContractError):
        validated_deliverable_gate_index(ledger)


# ── concurrency ─────────────────────────────────────────────────────────────


def test_caches_are_thread_safe() -> None:
    findings = [_deliverable_finding(f"finding:{i}") for i in range(8)]
    gates = [_deliverable_gate(f) for f in findings]
    results: list[list[dict]] = [[] for _ in findings]
    errors: list[Exception] = []
    barrier = threading.Barrier(8)

    def worker(index: int) -> None:
        try:
            barrier.wait()
            for _ in range(20):
                validated = validate_customer_delivery_gate_receipt_v2(
                    gates[index], finding=findings[index]
                )
                results[index].append(validated)
                mechanics.finding_payload_fingerprint(findings[index])
        except Exception as exc:  # pragma: no cover - failure surface
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(i,)) for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    for index in range(8):
        assert results[index]
        assert all(row == results[index][0] for row in results[index])


# ── formal_customer_deliverable_findings rebuild (scan-persist recovery) ────


def test_missing_envelope_finding_is_rebuilt_from_ledger_bundle() -> None:
    """A DELIVERABLE attempt whose finding lives in the delivery_evidence_bundle
    must be rebuilt when the envelope's occurrence list omits it, instead of
    failing the whole scan persist with formal_deliverable_finding_missing.

    Scenario: a verified archive hold-over — the ledger records the attempt
    (with gate + bundled finding) but the run itself delivered nothing, so the
    envelope's occurrence list is empty. The ledger is the authoritative
    execution record; the finding is recovered from it and the already
    validated gate is attached.
    """
    from ai_test_asset_center.formal_delivery_scope import (
        formal_customer_deliverable_findings,
    )

    ledger = _sealed_ledger(attempts=[_valid_legacy_attempt("finding:archived")])

    output = formal_customer_deliverable_findings(
        [],
        obligation_attempt_ledger=ledger,
    )

    assert len(output) == 1
    rebuilt = output[0]
    assert rebuilt["finding_id"] == "finding:archived"
    assert rebuilt["delivery_gate_receipt"]["gate_receipt_id"] == "gate:legacy"
    assert rebuilt["delivery_gate_receipt"]["schema_version"] == (
        LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
    )


def test_ledger_that_cannot_produce_finding_still_fails_closed() -> None:
    """An index-accepted finding that the ledger itself cannot rebuild stays
    fail-closed: the recovery path must never fabricate a finding."""
    from ai_test_asset_center.formal_delivery_scope import (
        formal_customer_deliverable_findings,
    )

    # DELIVERABLE attempt with gate but NO bundled finding. The attempt
    # fingerprint must be recomputed after the bundle is emptied so the
    # ledger stays self-consistent (the recovery path must see a valid ledger
    # that simply cannot produce the finding).
    from ai_test_asset_center import _customer_delivery_gate_v2_mechanics as _mech

    attempt = _valid_legacy_attempt("finding:orphan")
    attempt["delivery_evidence_bundle"] = {}
    attempt["attempt_fingerprint"] = _mech._fingerprint({
        key: value
        for key, value in attempt.items()
        if key != "attempt_fingerprint"
    })
    ledger = _sealed_ledger(attempts=[attempt])

    with pytest.raises(
        MainlineContractError, match="formal_deliverable_finding_missing"
    ):
        formal_customer_deliverable_findings([], obligation_attempt_ledger=ledger)
