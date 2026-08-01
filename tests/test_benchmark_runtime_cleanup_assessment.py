from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ai_test_asset_center.benchmark_runtime_cleanup_assessment import (
    MEASUREMENT_NOT_MEASURED,
    VERDICT_CLEAN,
    VERDICT_INCOMPLETE,
    VERDICT_NOT_EXERCISED,
    VERDICT_NOT_MEASURED,
    assess_benchmark_runtime_cleanup,
    assess_runtime_cleanup_document,
)
from benchmark_evaluator.funnel_benchmark_prep import (
    prepare_funnel_benchmark_target,
)


_PROJECT = "benchmark_mall"


def _binding(*, bound: int = 1, unbound: int = 0, complete: bool = True) -> dict:
    return {
        "schema_version": "qualibug.declared-adapter-cleanup-runtime-binding.v1",
        "required": True,
        "declared_operation_refs": ["create_product"],
        "declared_operation_count": 1,
        "bound_count": bound,
        "unbound_count": unbound,
        "bound": [
            {
                "step_id": f"treatment_{index + 1}",
                "operation_ref": "create_product",
                "status": "BOUND",
            }
            for index in range(bound)
        ],
        "unbound": [
            {
                "step_id": "",
                "operation_ref": "create_product",
                "status": "UNBOUND",
                "reason_code": "ADAPTER_CLEANUP_RUNTIME_STEP_MISSING",
            }
            for _ in range(unbound)
        ],
        "complete": complete,
    }


def _cleaned_receipt(receipt_id: str = "cleanup_adapter_1") -> dict:
    return {
        "schema_version": "qualibug.cleanup-adapter-execution.v1",
        "receipt_id": receipt_id,
        "adapter": "db_sql",
        "table": "products",
        "identity_column": "sku",
        "status": "CLEANED",
        "reason_code": "",
        "rows_deleted": 1,
        "mode": "row_delete",
        "ownership_basis": "creation_receipt",
    }


def _result(
    *,
    binding: dict | None = None,
    receipts: list[dict] | None = None,
    restored: bool = False,
) -> dict:
    observations = {}
    if binding is not None:
        observations["declared_adapter_cleanup_runtime_binding"] = binding
    if receipts is not None:
        observations["adapter_cleanup_receipts"] = receipts
    if restored:
        observations["environment_restoration_receipt"] = {
            "schema_version": "qualibug.environment-restoration-receipt.v1",
            "receipt_id": "environment_restored_1",
            "environment_restored": True,
            "status": "RESTORED",
        }
    return {
        "experiment_id": "experiment_adapter_cleanup_1",
        "environment_restored": restored,
        "observations": observations,
    }


def _document(*rows: dict) -> dict:
    return {
        "v12": {
            "experiment_execution": {
                "results": list(rows),
            }
        }
    }


def test_missing_scan_result_remains_not_measured(tmp_path: Path) -> None:
    assessment = assess_benchmark_runtime_cleanup(
        root=tmp_path,
        project=_PROJECT,
    )

    assert assessment["measurement_status"] == MEASUREMENT_NOT_MEASURED
    assert assessment["verdict"] == VERDICT_NOT_MEASURED
    assert assessment["reason_codes"] == ["SCAN_RESULT_MISSING"]
    assert assessment["physical_residue_measurement_status"] == (
        MEASUREMENT_NOT_MEASURED
    )
    assert assessment["receipt_id"].startswith("brca_")


def test_no_adapter_binding_is_not_exercised() -> None:
    assessment = assess_runtime_cleanup_document(
        _document(_result(binding=None, receipts=None, restored=False)),
        project=_PROJECT,
    )

    assert assessment["verdict"] == VERDICT_NOT_EXERCISED
    assert assessment["adapter_binding_required_experiment_count"] == 0
    assert assessment["reason_codes"] == [
        "ADAPTER_CLEANUP_NOT_DECLARED_OR_REACHED"
    ]


def test_declared_but_unreached_adapter_is_not_exercised_and_visible() -> None:
    assessment = assess_runtime_cleanup_document(
        _document(
            _result(
                binding=_binding(bound=0, unbound=1, complete=False),
                receipts=[],
                restored=False,
            )
        ),
        project=_PROJECT,
    )

    assert assessment["verdict"] == VERDICT_NOT_EXERCISED
    assert assessment["adapter_binding_required_experiment_count"] == 1
    assert assessment["adapter_binding_bound_experiment_count"] == 0
    assert assessment["adapter_binding_unbound_experiment_count"] == 1
    assert assessment["reason_codes"] == [
        "ADAPTER_CLEANUP_RUNTIME_NOT_REACHED",
        "ADAPTER_BINDING_INCOMPLETE",
    ]


def test_bound_adapter_without_execution_receipt_is_incomplete() -> None:
    assessment = assess_runtime_cleanup_document(
        _document(
            _result(
                binding=_binding(),
                receipts=[],
                restored=False,
            )
        ),
        project=_PROJECT,
    )

    assert assessment["verdict"] == VERDICT_INCOMPLETE
    assert "ADAPTER_CLEANUP_RECEIPT_MISSING" in assessment["reason_codes"]
    assert "ADAPTER_CLEANUP_BINDING_RECEIPT_IMBALANCE" in assessment[
        "reason_codes"
    ]
    assert "ENVIRONMENT_RESTORATION_NOT_PROVEN" in assessment[
        "reason_codes"
    ]


def test_clean_requires_exact_receipt_balance_and_restoration() -> None:
    assessment = assess_runtime_cleanup_document(
        _document(
            _result(
                binding=_binding(),
                receipts=[_cleaned_receipt()],
                restored=True,
            )
        ),
        project=_PROJECT,
        source_path="scan_result.json",
        source_sha256="source-hash",
    )

    assert assessment["verdict"] == VERDICT_CLEAN
    assert assessment["reason_codes"] == []
    assert assessment["adapter_binding_bound_count"] == 1
    assert assessment["adapter_cleanup_receipt_count"] == 1
    assert assessment["adapter_cleanup_cleaned_count"] == 1
    assert assessment["environment_restoration_proven_experiment_count"] == 1
    assert assessment["physical_residue_measurement_status"] == (
        MEASUREMENT_NOT_MEASURED
    )
    assert assessment["target_reset_excluded_from_runtime_cleanup_proof"] is True


def test_cleaned_label_without_execution_shape_is_invalid() -> None:
    receipt = _cleaned_receipt()
    receipt.pop("rows_deleted")
    assessment = assess_runtime_cleanup_document(
        _document(
            _result(
                binding=_binding(),
                receipts=[receipt],
                restored=True,
            )
        ),
        project=_PROJECT,
    )

    assert assessment["verdict"] == VERDICT_INCOMPLETE
    assert assessment["adapter_cleanup_invalid_count"] == 1
    assert "ADAPTER_CLEANUP_RECEIPT_INVALID" in assessment["reason_codes"]


def test_prep_persists_assessment_before_database_reset(
    tmp_path: Path,
) -> None:
    project = _PROJECT
    scan_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    scan_path.parent.mkdir(parents=True, exist_ok=True)
    scan_path.write_text(
        json.dumps(
            _document(
                _result(
                    binding=_binding(),
                    receipts=[_cleaned_receipt()],
                    restored=True,
                )
            )
        ),
        encoding="utf-8",
    )
    reset_script = tmp_path / "scripts" / "init_db_windows.ps1"
    reset_script.parent.mkdir(parents=True, exist_ok=True)
    reset_script.write_text("# noop", encoding="utf-8")
    observations: list[dict] = []

    def runner(cmd, **kwargs):
        joined = " ".join(str(value) for value in cmd)
        if "init_db_windows.ps1" in joined:
            assessment_files = list(
                (
                    tmp_path
                    / "_funnel_runs"
                    / "runtime_cleanup_assessments"
                ).glob("*.json")
            )
            assert len(assessment_files) == 1
            observations.append(
                json.loads(assessment_files[0].read_text(encoding="utf-8"))
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    prep = prepare_funnel_benchmark_target(
        root=tmp_path,
        env={
            "QUALIBUG_BENCHMARK_TARGET_ROOT": str(tmp_path),
            "QUALIBUG_SKIP_TARGET_DB_RESET": "0",
        },
        runner=runner,
        project=project,
    )

    assert observations[0]["verdict"] == VERDICT_CLEAN
    assert prep["pre_reset_runtime_cleanup_assessment"]["verdict"] == (
        VERDICT_CLEAN
    )
    assert Path(prep["pre_reset_runtime_cleanup_assessment_path"]).is_file()
    assert prep["reset_receipt"][
        "pre_reset_runtime_cleanup_assessment_excluded_from_reset_proof"
    ] is True
