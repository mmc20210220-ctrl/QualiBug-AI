from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.benchmark_target_cleanliness import assert_benchmark_target_clean


def _audit_path(root: Path) -> Path:
    return root / "platform_workspace" / "benchmark" / "defect_discovery" / "sandbox_write_audit.jsonl"


def test_cleanliness_guard_allows_first_run_without_prior_audit(tmp_path: Path) -> None:
    result = assert_benchmark_target_clean(
        root=tmp_path,
        project="benchmark",
        target_base_url="http://127.0.0.1:8080",
    )
    assert result["status"] == "clean_no_prior_write_audit"


def test_cleanliness_guard_blocks_incomplete_cleanup_without_reset_receipt(tmp_path: Path) -> None:
    path = _audit_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"timestamp": "2026-07-10T01:00:00Z", "cleanup_status": "failed"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="benchmark_target_dirty_reset_receipt_required"):
        assert_benchmark_target_clean(
            root=tmp_path,
            project="benchmark",
            target_base_url="http://127.0.0.1:8080",
        )


def test_cleanliness_guard_archives_fully_clean_prior_audit(tmp_path: Path) -> None:
    path = _audit_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"timestamp": "2026-07-10T01:00:00Z", "cleanup_status": "completed"}) + "\n",
        encoding="utf-8",
    )

    result = assert_benchmark_target_clean(
        root=tmp_path,
        project="benchmark",
        target_base_url="http://127.0.0.1:8080",
    )

    assert result["status"] == "clean_all_prior_writes_cleaned"
    assert Path(result["archived_audit"]).is_file()


def test_cleanliness_guard_verifies_and_archives_reset_receipt(tmp_path: Path) -> None:
    path = _audit_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-07-10T01:00:00Z", "cleanup_status": "completed"}),
                json.dumps({"timestamp": "2026-07-10T01:01:00Z", "cleanup_status": "failed"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    receipt_path = tmp_path / "target-reset.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": "benchmark_target_reset.v1",
                "receipt_id": "reset-1",
                "project": "benchmark",
                "target_base_url": "http://127.0.0.1:8080",
                "status": "completed",
                "reset_at_utc": "2026-07-10T01:02:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = assert_benchmark_target_clean(
        root=tmp_path,
        project="benchmark",
        target_base_url="http://127.0.0.1:8080",
        reset_receipt_path=str(receipt_path),
    )

    assert result["status"] == "clean_reset_receipt_verified"
    assert result["incomplete_cleanup_count"] == 1
    assert Path(result["archived_audit"]).is_file()
    assert Path(result["archived_receipt"]).is_file()
