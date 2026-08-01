from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_test_asset_center.benchmark_target_cleanliness import (
    assert_benchmark_target_clean,
)
from benchmark_evaluator.funnel_benchmark_prep import (
    prepare_funnel_benchmark_target,
    reset_benchmark_target_db,
    should_skip_target_db_reset,
)


def _reset_script(root: Path) -> Path:
    script = root / "scripts" / "init_db_windows.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# noop", encoding="utf-8")
    return script


def _successful_runner(cmd, **kwargs):
    return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")


def _dirty_audit(root: Path, project: str) -> Path:
    path = (
        root
        / "platform_workspace"
        / project
        / "defect_discovery"
        / "sandbox_write_audit.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-08-01T00:00:00Z",
                "method": "POST",
                "path": "/api/products",
                "operation_accepted": True,
                "cleanup_status": "not_applicable",
                "cleanup_strategy": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_reset_skip_requires_independent_dirty_target_acknowledgement() -> None:
    assert should_skip_target_db_reset(
        {"QUALIBUG_SKIP_TARGET_DB_RESET": "1"}
    ) is False
    assert should_skip_target_db_reset(
        {
            "QUALIBUG_SKIP_TARGET_DB_RESET": "1",
            "QUALIBUG_BENCHMARK_ACCEPT_DIRTY_TARGET": "1",
        }
    ) is True


def test_unacknowledged_skip_request_executes_real_reset(tmp_path: Path) -> None:
    script = _reset_script(tmp_path)
    calls: list[list[str]] = []

    def runner(cmd, **kwargs):
        calls.append([str(value) for value in cmd])
        return _successful_runner(cmd, **kwargs)

    result = reset_benchmark_target_db(
        target_root=tmp_path,
        env={"QUALIBUG_SKIP_TARGET_DB_RESET": "1"},
        runner=runner,
    )

    assert result["status"] == "ok"
    assert calls
    assert str(script) in calls[0]


def test_confirmed_skip_receipt_is_never_cleanliness_eligible(
    tmp_path: Path,
) -> None:
    result = prepare_funnel_benchmark_target(
        root=tmp_path,
        project="benchmark_mall",
        target_base_url="http://localhost:8080",
        env={
            "QUALIBUG_SKIP_TARGET_DB_RESET": "1",
            "QUALIBUG_BENCHMARK_ACCEPT_DIRTY_TARGET": "1",
        },
        runner=_successful_runner,
    )

    receipt = result["reset_receipt"]
    assert result["target_db_reset"]["status"] == "skipped"
    assert result["cleanliness_proof_eligible"] is False
    assert receipt["status"] == "skipped"
    assert receipt["cleanliness_proof_eligible"] is False


def test_cleanliness_guard_rejects_skipped_reset_receipt(
    tmp_path: Path,
) -> None:
    project = "benchmark_mall"
    _dirty_audit(tmp_path, project)
    result = prepare_funnel_benchmark_target(
        root=tmp_path,
        project=project,
        target_base_url="http://localhost:8080",
        env={
            "QUALIBUG_SKIP_TARGET_DB_RESET": "1",
            "QUALIBUG_BENCHMARK_ACCEPT_DIRTY_TARGET": "1",
        },
        runner=_successful_runner,
    )

    with pytest.raises(
        RuntimeError,
        match="benchmark_reset_receipt_invalid.*target_db_reset.status",
    ):
        assert_benchmark_target_clean(
            root=tmp_path,
            project=project,
            target_base_url="http://localhost:8080",
            reset_receipt_path=result["reset_receipt_path"],
        )
