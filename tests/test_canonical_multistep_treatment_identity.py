"""Recall regression for causal treatment scope in canonical defect identity."""
from __future__ import annotations

from copy import deepcopy

import pytest

import ai_test_asset_center.canonical_defect_registry as registry


def _step(step_id: str, operation_ref: str, path: str) -> dict:
    return {
        "phase": "treatment",
        "step_id": step_id,
        "operation_ref": operation_ref,
        "method": "POST",
        "path_template": path,
        "status_code": 200,
        "request_body_fingerprint": ("a" if step_id.endswith("first") else "b") * 64,
        "request_semantics_fingerprint": ("c" if step_id.endswith("first") else "d") * 64,
        "mutation_class": "boundary",
        "mutation_selector": "value",
        "mutation_operator": "above_max",
    }


def _observer(receipt_id: str, step_id: str) -> dict:
    return {
        "receipt_id": receipt_id,
        "observer_id": "http_response",
        "status": "OBSERVED",
        "evidence": {
            "step_id": step_id,
            "phase": "treatment",
        },
    }


def _attempt(observer_ids: list[str] | None = None) -> dict:
    return {
        "terminal_status": "DELIVERABLE",
        "delivery_evidence_bundle": {
            "reproduction_receipt": {
                "receipt_id": "repro_multistep",
                "step_observations": [
                    _step("treatment_first", "op.first", "/first"),
                    _step("treatment_final", "op.final", "/final"),
                ],
            },
            "oracle_receipt": {
                "receipt_id": "oracle_multistep",
                "assertions": [
                    {
                        "assertion_id": "assert_multistep",
                        "kind": "http_status_class",
                        "status": "VIOLATION",
                        "expected": 400,
                        "actual": 200,
                        "observer_receipt_ids": list(
                            observer_ids
                            if observer_ids is not None
                            else ["obs_first", "obs_final"]
                        ),
                    }
                ],
            },
            "observer_receipts": [
                _observer("obs_first", "treatment_first"),
                _observer("obs_final", "treatment_final"),
            ],
        },
    }


def _minimal_evidence() -> dict:
    return {
        "schema_version": registry.CANONICAL_IDENTITY_EVIDENCE_SCHEMA,
        "operation": {
            "adapter": "http",
            "verb": "POST",
            "operation_ref": "placeholder",
            "source_locator": "/placeholder",
        },
        "property": {
            "assertion_kind": "http_status_class",
            "expected_signature": 400,
        },
        "actor_relation": {
            "control_actor_class": "not_identity_defining",
            "treatment_actor_class": "not_identity_defining",
            "relation": "actor_insensitive_property",
        },
        "resource_identity_class": {
            "source_locators": ["/placeholder"],
        },
        "mutation": {
            "class": "boundary",
            "selector": "value",
            "operator": "above_max",
        },
        "observed_outcome": {
            "assertion_kind": "http_status_class",
            "expected_signature": 400,
            "actual_signature": 200,
            "control_observation_class": "not_observed",
            "treatment_observation_class": "http:2xx",
        },
        "proof": {
            "assertion_receipt_id": "assert_multistep",
            "oracle_receipt_id": "oracle_multistep",
            "reproduction_receipt_id": "repro_multistep",
            "request_body_fingerprint": "b" * 64,
            "request_semantics_fingerprint": "d" * 64,
            "evidence_actor_classes": [],
        },
    }


def test_multistep_identity_projects_the_exact_final_treatment_step() -> None:
    projected, causal_step_id = registry._causal_identity_attempt(_attempt())

    treatment_ids = [
        row["step_id"]
        for row in projected["delivery_evidence_bundle"][
            "reproduction_receipt"
        ]["step_observations"]
        if row.get("phase") == "treatment"
    ]
    assert causal_step_id == "treatment_final"
    assert treatment_ids == ["treatment_final"]


def test_multistep_identity_fails_closed_without_final_step_receipt_proof() -> None:
    with pytest.raises(
        registry.CanonicalDefectRegistryError,
        match="CANONICAL_IDENTITY_INCOMPLETE:assertion.causal_treatment_step",
    ):
        registry._causal_identity_attempt(_attempt(["obs_first"]))


def test_multistep_identity_rejects_duplicate_treatment_step_identity() -> None:
    attempt = _attempt()
    reproduction = attempt["delivery_evidence_bundle"]["reproduction_receipt"]
    reproduction["step_observations"][1]["step_id"] = "treatment_first"

    with pytest.raises(
        registry.CanonicalDefectRegistryError,
        match="CANONICAL_IDENTITY_AMBIGUOUS:reproduction.treatment_step_id",
    ):
        registry._causal_identity_attempt(attempt)


def test_single_treatment_identity_keeps_legacy_path_unchanged() -> None:
    attempt = _attempt()
    reproduction = attempt["delivery_evidence_bundle"]["reproduction_receipt"]
    reproduction["step_observations"] = [reproduction["step_observations"][-1]]

    projected, causal_step_id = registry._causal_identity_attempt(attempt)

    assert projected is attempt
    assert causal_step_id == ""


def test_public_identity_derivation_uses_causal_projection(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _capture(projected_attempt: dict) -> dict:
        captured["attempt"] = deepcopy(projected_attempt)
        return _minimal_evidence()

    monkeypatch.setattr(
        registry,
        "_original_derive_canonical_identity_evidence",
        _capture,
    )

    evidence = registry.derive_canonical_identity_evidence(_attempt())
    projected = captured["attempt"]
    treatment_ids = [
        row["step_id"]
        for row in projected["delivery_evidence_bundle"][
            "reproduction_receipt"
        ]["step_observations"]
        if row.get("phase") == "treatment"
    ]

    assert treatment_ids == ["treatment_final"]
    assert evidence["proof"]["causal_treatment_step_id"] == "treatment_final"
