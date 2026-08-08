# -*- coding: utf-8 -*-
"""Unit tests for verified_discovery_archive — 已验证发现跨 run 单调保持。

Locks in: stable cross-run identity (canonical id / normalized title, UUIDs
stripped), archive merge (new append, known refresh, never drop), run output
= this-run deliveries ∪ held archive entries with archive_entry provenance,
retirement only through consecutive target-fix signals. Synthetic findings
only — no benchmark material, no GT.
"""
import json

import pytest

from ai_test_asset_center.verified_discovery_archive import (
    apply_archive_to_run,
    finding_stable_identity,
    load_verified_discovery_archive,
    merge_run_deliveries,
    record_target_fix_signals,
    save_verified_discovery_archive,
)


def _finding(
    finding_id: str, title: str, family: str = "authorization",
    canonical: str | None = None,
) -> dict:
    return {
        "finding_id": finding_id,
        "title": title,
        "risk_family": family,
        "category": "owner_tenant_visibility",
        "gate_passed": True,
        "customer_delivery_status": "defect",
        "bug_status": "reproduced",
        "canonical_defect_id": canonical,
        "evidence": {"obs": "before/after"},
        "reproduction_receipt": {"status": "OK"},
        "delivery_gate_receipt": {"adjudication": {"assertion": "VIOLATION"}},
    }


def test_identity_stable_across_runs_with_different_instances():
    # Same canonical defect (content-derived fingerprint) across runs.
    a = _finding("a1", "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel", canonical="cdef_same")
    b = _finding("a2", "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel", canonical="cdef_same")
    assert finding_stable_identity(a) == finding_stable_identity(b)


def test_identity_strips_runtime_uuid():
    # No canonical id → fallback over normalized title must strip UUIDs.
    a = _finding("u1", "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/e88ce5cd-8e45-4621-86b2-9f05e152d31e/cancel", canonical=None)
    b = _finding("u2", "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/9784aaba-3aa7-48c6-884f-4dd34bb08200/cancel", canonical=None)
    assert finding_stable_identity(a) == finding_stable_identity(b)


def test_identity_distinguishes_different_operations():
    a = _finding("p1", "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel")
    b = _finding("p2", "[ContractOracle] owner_tenant_visibility: buyer POST /api/products/admin")
    assert finding_stable_identity(a) != finding_stable_identity(b)


def test_merge_keeps_verified_finding_across_runs(tmp_path):
    project = "demo"
    run1 = _finding("f1", "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel")
    archive = merge_run_deliveries(
        {"entries": {}, "retired": {}},
        run_id="run-1",
        campaign_id="cmp-1",
        findings=[run1],
    )
    save_verified_discovery_archive(project, tmp_path, archive)

    # run-2 does NOT rediscover the bug (coverage fluctuation)
    archive2 = load_verified_discovery_archive(project, tmp_path)
    output, receipt = apply_archive_to_run(
        archive2, run_id="run-2", findings=[]
    )
    assert len(output) == 1
    assert output[0]["archive_entry"] is True
    assert output[0]["first_verified_run"] == "run-1"
    assert receipt["run_delivered"] == 0
    assert receipt["archive_held"] == 1


def test_run_delivery_wins_over_archive_on_same_identity(tmp_path):
    project = "demo"
    old = _finding("old", "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel")
    archive = merge_run_deliveries(
        {"entries": {}, "retired": {}},
        run_id="run-1",
        campaign_id="cmp-1",
        findings=[old],
    )
    fresh = _finding("fresh", "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel")
    fresh["evidence"] = {"obs": "newer-evidence"}
    archive = merge_run_deliveries(
        archive, run_id="run-2", campaign_id="cmp-2", findings=[fresh]
    )
    output, _ = apply_archive_to_run(archive, run_id="run-2", findings=[fresh])
    assert len(output) == 1
    assert output[0]["evidence"] == {"obs": "newer-evidence"}
    assert output[0].get("archive_entry") is None  # this-run delivery, not hold-over


def test_non_delivered_or_unreproduced_findings_never_enter_archive(tmp_path):
    candidate = _finding("c1", "candidate title")
    candidate["gate_passed"] = False
    not_repro = _finding("n1", "not reproduced")
    not_repro["bug_status"] = "not_reproduced"
    archive = merge_run_deliveries(
        {"entries": {}, "retired": {}},
        run_id="run-1",
        campaign_id="cmp-1",
        findings=[candidate, not_repro],
    )
    assert archive["entries"] == {}


def test_retire_only_after_consecutive_target_fix_signals(tmp_path):
    project = "demo"
    finding = _finding("f1", "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel")
    archive = merge_run_deliveries(
        {"entries": {}, "retired": {}},
        run_id="run-1",
        campaign_id="cmp-1",
        findings=[finding],
    )
    identity = finding_stable_identity(finding)
    # Coverage fluctuation (finding simply not regenerated) must NEVER count.
    output, _ = apply_archive_to_run(archive, run_id="run-2", findings=[])
    assert len(output) == 1
    # Real fix signal: operation exercised, no violation — needs 3 runs.
    for run in ("run-2", "run-3"):
        receipt = record_target_fix_signals(
            archive,
            run_id=run,
            fix_evidence={"identities": {identity: {}}, "evidence": {}},
        )
        assert receipt["retired_now"] == 0
    output, _ = apply_archive_to_run(archive, run_id="run-3", findings=[])
    assert len(output) == 1  # still held before threshold
    receipt = record_target_fix_signals(
        archive,
        run_id="run-4",
        fix_evidence={"identities": {identity: {}}, "evidence": {}},
    )
    assert receipt["retired_now"] == 1
    output, _ = apply_archive_to_run(archive, run_id="run-4", findings=[])
    assert len(output) == 0  # retired: not finding the bug is now expected
    assert identity in archive["retired"]


def test_archive_file_roundtrip(tmp_path):
    project = "demo"
    finding = _finding("f1", "[ContractOracle] owner_tenant_visibility: buyer POST /api/orders/batch-cancel")
    archive = merge_run_deliveries(
        {"entries": {}, "retired": {}},
        run_id="run-1",
        campaign_id="cmp-1",
        findings=[finding],
    )
    path = save_verified_discovery_archive(project, tmp_path, archive)
    assert path.exists()
    reloaded = load_verified_discovery_archive(project, tmp_path)
    assert reloaded["schema_version"] == "qualibug.verified-discovery-archive.v1"
    assert len(reloaded["entries"]) == 1
