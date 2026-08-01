from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.feishu_tenant_acceptance import (
    run_feishu_tenant_acceptance,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"


def _connection(**overrides):
    return {
        "status": "AVAILABLE",
        "connector_type": "feishu",
        "auth_mode": "internal_app",
        "space_count": 2,
        "network_side_effect": "READ_ONLY",
        "credentials_persisted": False,
        "access_token_persisted": False,
        **overrides,
    }


def _run(
    *,
    cursor: str,
    materialized: int,
    unchanged: int,
    unsupported: int = 0,
    unknown_gap: int = 0,
    mutation: bool = False,
    status: str = "COMPLETE",
    export_avoided: int | None = None,
):
    discovered = materialized + unchanged + unsupported
    covered = materialized + unchanged
    return {
        "sync_epoch_id": f"sync-{cursor}-{materialized}-{unchanged}",
        "status": status,
        "discovered_resource_count": discovered,
        "covered_resource_count": covered,
        "materialized_resource_count": materialized,
        "unchanged_resource_count": unchanged,
        "unsupported_resource_count": unsupported,
        "unknown_gap_count": unknown_gap,
        "failure_count": 0 if status == "COMPLETE" else 1,
        "degraded_resource_count": 0,
        "export_avoided_count": unchanged if export_avoided is None else export_avoided,
        "knowledge_coverage_ratio": covered / discovered if discovered else 1.0,
        "knowledge_coverage_status": (
            "PARTIAL_UNSUPPORTED" if unsupported else "COMPLETE"
        ),
        "remote_discovery_complete": True,
        "supported_materialization_complete": status == "COMPLETE",
        "cursor_checkpoint_committed": status == "COMPLETE",
        "checkpoint_commit_protocol": "RECOVERABLE_TWO_STAGE",
        "customer_material_mutation_executed": mutation,
        "source_content_persisted_in_adapter_receipt": False,
        "next_cursor": cursor,
        "run_receipt_path": f"receipts/{cursor}.json",
        "content": "CUSTOMER-SOURCE-CONTENT-MUST-NOT-BE-PERSISTED",
    }


def _clock(*values: float):
    iterator = iter(values)
    return lambda: next(iterator)


def _execute(tmp_path: Path, sync_runs, *, profile="pilot", connection=None):
    return run_feishu_tenant_acceptance(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        profile=profile,
        connection_tester=lambda *args, **kwargs: connection or _connection(),
        sync_runner=lambda *args, **kwargs: next(sync_runs),
        clock=_clock(0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        sleeper=lambda _: None,
    )


def test_pilot_acceptance_passes_and_persists_bounded_report(tmp_path: Path) -> None:
    report = _execute(
        tmp_path,
        iter(
            [
                _run(cursor="raw-secret-cursor", materialized=20, unchanged=0),
                _run(cursor="raw-secret-cursor", materialized=0, unchanged=20),
            ]
        ),
    )

    assert report["verdict"] == "PASS"
    assert report["acceptance_ready"] is True
    assert report["summary"]["executed_run_count"] == 2
    assert report["summary"]["maximum_discovered_resource_count"] == 20
    assert report["governance"]["deletion_policy"] == "RETAIN"
    assert report["governance"]["customer_material_mutation_executed"] is False
    assert report["governance"]["source_content_persisted_in_sync_receipt"] is False

    checks = {row["check_id"]: row for row in report["checks"]}
    assert checks["RUN_2_STABLE_SNAPSHOT_NOT_REEXPORTED"]["status"] == "PASS"
    assert checks["CONNECTION_CREDENTIALS_NOT_PERSISTED"]["status"] == "PASS"
    assert checks["ACCESS_TOKEN_NOT_PERSISTED"]["status"] == "PASS"

    path = tmp_path / report["report_path"]
    persisted = path.read_text(encoding="utf-8")
    assert "raw-secret-cursor" not in persisted
    assert "CUSTOMER-SOURCE-CONTENT-MUST-NOT-BE-PERSISTED" not in persisted
    assert "next_cursor_fingerprint" in persisted

    payload = json.loads(persisted)
    assert payload["verdict"] == "PASS"
    assert payload["report_path"] == report["report_path"]
    assert payload["connection"]["credentials_persisted"] is False
    assert payload["connection"]["access_token_persisted"] is False


def test_unknown_gap_and_customer_mutation_are_blockers(tmp_path: Path) -> None:
    bad = _run(
        cursor="cursor-1",
        materialized=1,
        unchanged=0,
        unknown_gap=1,
        mutation=True,
    )
    report = _execute(tmp_path, iter([bad, dict(bad)]), profile="smoke")

    assert report["verdict"] == "FAIL"
    assert report["governance"]["customer_material_mutation_executed"] is True
    checks = {row["check_id"]: row for row in report["checks"]}
    assert checks["RUN_1_RESOURCE_ACCOUNTING_BALANCED"]["status"] == "FAIL"
    assert checks["RUN_1_NO_UNKNOWN_GAPS"]["status"] == "FAIL"
    assert checks["RUN_1_NO_CUSTOMER_MUTATION"]["status"] == "FAIL"


def test_missing_safety_evidence_fails_closed(tmp_path: Path) -> None:
    first = _run(cursor="missing", materialized=1, unchanged=0)
    second = _run(cursor="missing", materialized=0, unchanged=1)
    for row in (first, second):
        row.pop("customer_material_mutation_executed")
        row.pop("source_content_persisted_in_adapter_receipt")

    report = _execute(tmp_path, iter([first, second]), profile="smoke")

    assert report["verdict"] == "FAIL"
    assert report["governance"]["customer_material_mutation_executed"] is None
    assert report["governance"]["source_content_persisted_in_sync_receipt"] is None
    checks = {row["check_id"]: row for row in report["checks"]}
    assert checks["RUN_1_NO_CUSTOMER_MUTATION"]["observed"] is None
    assert checks["RUN_1_NO_CUSTOMER_MUTATION"]["status"] == "FAIL"
    assert checks["RUN_1_NO_SOURCE_CONTENT_IN_SYNC_RECEIPT"]["status"] == "FAIL"


def test_persisted_connection_credentials_are_blockers(tmp_path: Path) -> None:
    report = _execute(
        tmp_path,
        iter(
            [
                _run(cursor="cursor", materialized=1, unchanged=0),
                _run(cursor="cursor", materialized=0, unchanged=1),
            ]
        ),
        profile="smoke",
        connection=_connection(
            credentials_persisted=True,
            access_token_persisted=True,
        ),
    )

    assert report["verdict"] == "FAIL"
    checks = {row["check_id"]: row for row in report["checks"]}
    assert checks["CONNECTION_CREDENTIALS_NOT_PERSISTED"]["status"] == "FAIL"
    assert checks["ACCESS_TOKEN_NOT_PERSISTED"]["status"] == "FAIL"


def test_stable_snapshot_reexport_fails_acceptance(tmp_path: Path) -> None:
    report = _execute(
        tmp_path,
        iter(
            [
                _run(cursor="stable", materialized=20, unchanged=0),
                _run(
                    cursor="stable",
                    materialized=1,
                    unchanged=19,
                    export_avoided=19,
                ),
            ]
        ),
    )

    assert report["verdict"] == "FAIL"
    check = next(
        row
        for row in report["checks"]
        if row["check_id"] == "RUN_2_STABLE_SNAPSHOT_NOT_REEXPORTED"
    )
    assert check["status"] == "FAIL"


def test_remote_change_during_acceptance_is_explained_not_misclassified(tmp_path: Path) -> None:
    report = _execute(
        tmp_path,
        iter(
            [
                _run(cursor="revision-1", materialized=20, unchanged=0),
                _run(cursor="revision-2", materialized=1, unchanged=19),
            ]
        ),
    )

    assert report["verdict"] == "PASS"
    check = next(
        row
        for row in report["checks"]
        if row["check_id"] == "RUN_2_REMOTE_CHANGE_OBSERVED"
    )
    assert check["status"] == "PASS"
    assert check["severity"] == "INFO"


def test_partial_coverage_must_meet_profile_threshold(tmp_path: Path) -> None:
    report = _execute(
        tmp_path,
        iter(
            [
                _run(cursor="partial", materialized=18, unchanged=0, unsupported=2),
                _run(cursor="partial", materialized=0, unchanged=18, unsupported=2),
            ]
        ),
    )

    assert report["verdict"] == "FAIL"
    checks = {row["check_id"]: row for row in report["checks"]}
    assert checks["KNOWLEDGE_COVERAGE_MEETS_PROFILE"]["status"] == "FAIL"
    assert checks["UNSUPPORTED_RATIO_WITHIN_PROFILE"]["status"] == "FAIL"


def test_connection_must_prove_read_only_access(tmp_path: Path) -> None:
    report = _execute(
        tmp_path,
        iter(
            [
                _run(cursor="cursor", materialized=1, unchanged=0),
                _run(cursor="cursor", materialized=0, unchanged=1),
            ]
        ),
        profile="smoke",
        connection=_connection(network_side_effect="UNKNOWN"),
    )

    assert report["verdict"] == "FAIL"
    check = next(
        row
        for row in report["checks"]
        if row["check_id"] == "REMOTE_ACCESS_READ_ONLY"
    )
    assert check["status"] == "FAIL"
