# -*- coding: utf-8 -*-
"""Unit tests for role-variant aggregation (distribution balance, task A).

Locks in: the concrete treatment actor class is EVIDENCE, not identity —
multi-role variants of the same defect surface (normalized operation +
assertion kind + violation shape) derive one canonical identity; role
evidence is preserved via proof.evidence_actor_classes and the archive keys
by the role-invariant aggregation identity with a one-time compatibility
migration for legacy per-role entries. Synthetic data only — no benchmark
material, no GT.
"""
from __future__ import annotations

import json

from ai_test_asset_center._canonical_defect_registry_mechanics import (
    _concrete_actor_classes,
    build_canonical_defect_identity,
    derive_canonical_identity_evidence,
)
from ai_test_asset_center.verified_discovery_archive import (
    apply_archive_to_run,
    finding_stable_identity,
    load_verified_discovery_archive,
    merge_run_deliveries,
    migrate_archive_for_aggregation,
    save_verified_discovery_archive,
)


def _deliverable_attempt(
    *,
    role: str,
    path: str = "/api/orders/batch-cancel",
    expected: dict | None = None,
    actual: dict | None = None,
) -> dict:
    request_semantics = "a" * 64
    request_body = "b" * 64
    actor_ref = f"actor:{role}"
    return {
        "terminal_status": "DELIVERABLE",
        "delivery_evidence_bundle": {
            "reproduction_receipt": {
                "receipt_id": f"repro-{role}",
                "source_refs": [{"kind": "api", "locator": f"POST {path}"}],
                "step_observations": [
                    {
                        "phase": "control",
                        "actor_ref": "actor:owner",
                        "adapter": "http",
                        "method": "POST",
                        "operation_ref": "op:create-item",
                        "path_template": path,
                        "request_semantics_fingerprint": request_semantics,
                        "request_body_fingerprint": request_body,
                        "mutation_class": "create",
                        "mutation_selector": "body",
                        "mutation_operator": "set",
                        "status_code": 200,
                    },
                    {
                        "phase": "treatment",
                        "actor_ref": actor_ref,
                        "adapter": "http",
                        "method": "POST",
                        "operation_ref": "op:create-item",
                        "path_template": path,
                        "request_semantics_fingerprint": request_semantics,
                        "request_body_fingerprint": request_body,
                        "mutation_class": "create",
                        "mutation_selector": "body",
                        "mutation_operator": "set",
                        "status_code": 200,
                    },
                ],
            },
            "oracle_receipt": {
                "receipt_id": f"oracle-{role}",
                "assertions": [{
                    "receipt_id": f"assertion-{role}",
                    "status": "VIOLATION",
                    "kind": "owner_tenant_visibility",
                    "expected": expected or {"viewer_can_access": False},
                    "actual": actual or {"viewer_can_access": True},
                    "observer_receipt_ids": [f"observer-{role}"],
                    "source_refs": [{"kind": "api", "locator": f"POST {path}"}],
                }],
            },
            "observer_receipts": [{
                "receipt_id": f"observer-{role}",
                "observer_id": "typed_assertion",
            }],
            "contract_evidence_receipts": [
                {
                    "receipt_id": f"actor-owner-{role}",
                    "kind": "actor",
                    "subject_id": "actor:owner",
                    "status": "OBSERVED",
                    "evidence": {"role": "seller"},
                },
                {
                    "receipt_id": f"actor-treatment-{role}",
                    "kind": "actor",
                    "subject_id": actor_ref,
                    "status": "OBSERVED",
                    "evidence": {"role": role},
                },
                {
                    "receipt_id": f"treatment-contract-{role}",
                    "kind": "treatment",
                    "status": "OBSERVED",
                    "evidence": {
                        "request_semantics_fingerprint": request_semantics,
                        "path_template": path,
                        "request_body_fingerprint": request_body,
                        "mutation_class": "create",
                        "mutation_selector": "body",
                        "mutation_operator": "set",
                    },
                },
            ],
        },
    }


def _canonical_id(*, role: str, path: str = "/api/orders/batch-cancel") -> dict:
    evidence = derive_canonical_identity_evidence(
        _deliverable_attempt(role=role, path=path)
    )
    return build_canonical_defect_identity(
        target_id="target-1",
        evidence=evidence,
    )


class TestRoleVariantIdentity:
    def test_role_variants_share_one_canonical_identity(self):
        buyer = _canonical_id(role="buyer")
        auditor = _canonical_id(role="auditor")
        seller = _canonical_id(role="seller")
        assert buyer["canonical_defect_id"] == auditor["canonical_defect_id"]
        assert buyer["canonical_defect_id"] == seller["canonical_defect_id"]

    def test_identity_is_role_free(self):
        identity = _canonical_id(role="buyer")["identity"]
        assert identity["actor_relation"]["treatment_actor_class"] == "any_actor"
        assert identity["actor_relation"]["relation"] == "control_to_treatment"

    def test_different_interface_stays_distinct(self):
        batch = _canonical_id(role="buyer", path="/api/orders/batch-cancel")
        products = _canonical_id(role="buyer", path="/api/products/admin")
        assert batch["canonical_defect_id"] != products["canonical_defect_id"]

    def test_different_violation_shape_stays_distinct(self):
        # Same interface and role, different violation shape (the assertion's
        # expected/actual semantics differ) → different canonical identity.
        leak = _canonical_id(role="buyer")
        evidence = derive_canonical_identity_evidence(
            _deliverable_attempt(
                role="buyer",
                expected={"viewer_can_access": False, "owner_can_access": True},
                actual={"viewer_can_access": True, "owner_can_access": False},
            )
        )
        mutated = build_canonical_defect_identity(
            target_id="target-1",
            evidence=evidence,
        )
        assert leak["canonical_defect_id"] != mutated["canonical_defect_id"]


class TestRoleEvidencePreserved:
    def test_concrete_actor_classes_filter_identity_markers(self):
        assert _concrete_actor_classes(
            ["buyer", "buyer", "auditor", "any_actor", "not_identity_defining", ""]
        ) == ["auditor", "buyer"]

    def test_proof_carries_evidence_actor_classes(self):
        buyer = derive_canonical_identity_evidence(
            _deliverable_attempt(role="buyer")
        )
        assert buyer["proof"]["evidence_actor_classes"] == ["buyer"]
        auditor = derive_canonical_identity_evidence(
            _deliverable_attempt(role="auditor")
        )
        assert auditor["proof"]["evidence_actor_classes"] == ["auditor"]

    def test_actor_insensitive_proof_has_no_role_evidence(self):
        attempt = _deliverable_attempt(role="buyer")
        attempt["delivery_evidence_bundle"]["oracle_receipt"]["assertions"][0][
            "kind"
        ] = "input_validation"
        attempt["delivery_evidence_bundle"]["reproduction_receipt"][
            "step_observations"
        ] = [
            step
            for step in attempt["delivery_evidence_bundle"]["reproduction_receipt"][
                "step_observations"
            ]
            if step["phase"] != "control"
        ]
        evidence = derive_canonical_identity_evidence(attempt)
        assert evidence["proof"]["evidence_actor_classes"] == []


def _finding(
    finding_id: str,
    title: str,
    canonical: str | None = None,
    path: str = "/api/orders/batch-cancel",
) -> dict:
    return {
        "finding_id": finding_id,
        "title": title,
        "risk_family": "authorization",
        "category": "owner_tenant_visibility",
        "gate_passed": True,
        "customer_delivery_status": "defect",
        "bug_status": "reproduced",
        "canonical_defect_id": canonical,
        "reproduction": {"method": "POST", "path": path},
        "evidence": {"assertion": {
            "kind": "owner_tenant_visibility",
            "expected": {"viewer_can_access": False},
            "actual": {"viewer_can_access": True},
        }},
        "delivery_gate_receipt": {"adjudication": {"assertion": "VIOLATION"}},
    }


class TestArchiveAggregationIdentity:
    def test_role_variants_share_one_archive_identity(self):
        buyer = _finding(
            "f1",
            "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel",
        )
        auditor = _finding(
            "f2",
            "[ContractOracle] owner_tenant_visibility: auditor POST /api/orders/batch-cancel",
        )
        assert finding_stable_identity(buyer) == finding_stable_identity(auditor)

    def test_archive_identity_ignores_legacy_canonical_id(self):
        legacy = _finding(
            "f1",
            "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel",
            canonical="cdef_old_per_role_buyer",
        )
        new = _finding(
            "f9",
            "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel",
            canonical="cdef_new_aggregated",
        )
        assert finding_stable_identity(legacy) == finding_stable_identity(new)

    def test_different_interface_keeps_distinct_archive_identity(self):
        orders = _finding(
            "f1",
            "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel",
        )
        products = _finding(
            "f2",
            "[ContractOracle] owner_tenant_visibility: buyer POST /api/products/admin",
            path="/api/products/admin",
        )
        assert finding_stable_identity(orders) != finding_stable_identity(products)


class TestArchiveMigration:
    def _legacy_archive(self) -> dict:
        buyer = _finding(
            "f1",
            "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel",
            canonical="cdef_old_buyer",
        )
        auditor = _finding(
            "f2",
            "[ContractOracle] owner_tenant_visibility: auditor POST /api/orders/batch-cancel",
            canonical="cdef_old_auditor",
        )
        warehouse = _finding(
            "f3",
            "[ContractOracle] owner_tenant_visibility: warehouse POST /api/orders/batch-cancel",
            canonical="cdef_old_warehouse",
        )
        products = _finding(
            "f4",
            "[ContractOracle] owner_tenant_visibility: buyer POST /api/products/admin",
            canonical="cdef_old_products",
            path="/api/products/admin",
        )
        return {
            "schema_version": "qualibug.verified-discovery-archive.v1",
            "entries": {
                "cdef_old_buyer": {
                    "identity": "cdef_old_buyer",
                    "first_verified_run": "run-1",
                    "last_verified_run": "run-1",
                    "finding": buyer,
                },
                "cdef_old_auditor": {
                    "identity": "cdef_old_auditor",
                    "first_verified_run": "run-2",
                    "last_verified_run": "run-2",
                    "finding": auditor,
                },
                "cdef_old_warehouse": {
                    "identity": "cdef_old_warehouse",
                    "first_verified_run": "run-3",
                    "last_verified_run": "run-3",
                    "finding": warehouse,
                },
                "cdef_old_products": {
                    "identity": "cdef_old_products",
                    "first_verified_run": "run-1",
                    "last_verified_run": "run-1",
                    "finding": products,
                },
            },
            "retired": {},
        }

    def test_legacy_role_entries_collapse_onto_one_aggregation_identity(self):
        archive = self._legacy_archive()
        migrated, receipt = migrate_archive_for_aggregation(archive)
        assert len(migrated["entries"]) == 2  # orders family + products family
        assert receipt["collapsed_entries"] == 2
        assert receipt["migrated_entries"] == 4
        # Earliest first_verified provenance preserved across the collapse.
        orders_entry = [
            entry
            for entry in migrated["entries"].values()
            if entry["finding"]["reproduction"]["path"] == "/api/orders/batch-cancel"
        ][0]
        assert orders_entry["first_verified_run"] == "run-1"

    def test_migration_is_idempotent(self):
        migrated, _ = migrate_archive_for_aggregation(self._legacy_archive())
        again, receipt = migrate_archive_for_aggregation(migrated)
        assert again["entries"] == migrated["entries"]
        assert receipt["migrated_entries"] == 0
        assert receipt["collapsed_entries"] == 0

    def test_new_aggregated_delivery_aligns_with_migrated_entry(self):
        migrated, _ = migrate_archive_for_aggregation(self._legacy_archive())
        new_delivery = _finding(
            "f9",
            "[ContractOracle] owner_tenant_visibility: auditor POST /api/orders/batch-cancel",
            canonical="cdef_new_aggregated",
        )
        orders_entry = [
            entry
            for entry in migrated["entries"].values()
            if entry["finding"]["reproduction"]["path"] == "/api/orders/batch-cancel"
        ][0]
        assert finding_stable_identity(new_delivery) == orders_entry["identity"]

    def test_load_migrates_and_merge_does_not_duplicate_role_variants(self, tmp_path):
        project = "demo"
        archive = self._legacy_archive()
        save_verified_discovery_archive(project, tmp_path, archive)
        loaded = load_verified_discovery_archive(project, tmp_path)
        assert len(loaded["entries"]) == 2
        # A new run delivers ONE aggregated finding for the orders surface.
        new_delivery = _finding(
            "f9",
            "[ContractOracle] owner_tenant_visibility: auditor POST /api/orders/batch-cancel",
            canonical="cdef_new_aggregated",
        )
        merged = merge_run_deliveries(
            loaded,
            run_id="run-9",
            campaign_id="cmp-9",
            findings=[new_delivery],
        )
        assert len(merged["entries"]) == 2  # still no duplicate for orders surface
        output, receipt = apply_archive_to_run(
            merged, run_id="run-9", findings=[new_delivery]
        )
        assert receipt["archive_held"] == 1  # only the products surface is held
        assert len(output) == 2
        json.dumps(merged)  # serializable
