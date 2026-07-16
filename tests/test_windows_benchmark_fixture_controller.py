from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmark_evaluator.windows_fixture_controller import (
    WINDOWS_BENCHMARK_FIXTURE_SCHEMA,
    WindowsBenchmarkFixtureController,
)


def test_windows_fixture_controller_emits_governed_prepare_and_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    target_root = tmp_path / "target"
    workspace.mkdir()
    target_root.mkdir()
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        "\ufeff"
        + json.dumps({
            "schema_version": WINDOWS_BENCHMARK_FIXTURE_SCHEMA,
            "project": "generic-benchmark",
            "base_url": "http://127.0.0.1:8080",
            "target_root": str(target_root),
        }),
        encoding="utf-8",
    )
    fingerprint = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    calls: list[str] = []

    def fake_prepare(**kwargs: object) -> dict[str, object]:
        calls.append("reset")
        assert kwargs["project"] == "generic-benchmark"
        return {
            "reset_receipt_path": str(tmp_path / "reset.json"),
            "reset_receipt": {
                "receipt_id": f"RESET-{len(calls)}",
                "status": "completed",
            },
        }

    def fake_clean(**_: object) -> dict[str, object]:
        calls.append("clean")
        return {
            "status": "clean_reset_receipt_verified",
            "archived_receipt": str(tmp_path / "archived-reset.json"),
        }

    monkeypatch.setattr(
        "benchmark_evaluator.windows_fixture_controller.prepare_funnel_benchmark_target",
        fake_prepare,
    )
    monkeypatch.setattr(
        "benchmark_evaluator.windows_fixture_controller.assert_benchmark_target_clean",
        fake_clean,
    )
    monkeypatch.setattr(
        "benchmark_evaluator.windows_fixture_controller._observe_target",
        lambda _: f"target-observation-{len(calls)}",
    )
    monkeypatch.setenv("QUALIBUG_DB_DSN", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("QUALIBUG_JWT_SECRET", "test-secret")
    runtime_view = {
        "target": {
            "target_id": "TARGET-1",
            "project_id": "generic-benchmark",
            "runtime": {
                "environment_ref": "http://127.0.0.1:8080",
                "environment_type": "test",
                "fixture_snapshot_ref": str(fixture_path),
            },
        },
    }
    controller = WindowsBenchmarkFixtureController(workspace_root=workspace)

    prepared = controller.prepare(
        runtime_view=runtime_view,
        campaign_id="CMP-1",
        policy_id="POLICY-1",
        evaluation_mode="replay",
        expected_fixture_fingerprint=fingerprint,
    )
    cleaned = controller.cleanup(
        runtime_view=runtime_view,
        campaign_id="CMP-1",
        policy_id="POLICY-1",
        evaluation_mode="replay",
        preparation_receipt=prepared,
        scan_output={},
    )

    assert calls == ["reset", "clean", "reset", "clean"]
    assert prepared["status"] == "READY"
    assert prepared["governed_sandbox_executor"] is True
    assert cleaned["status"] == "SUCCEEDED"
    assert cleaned["dirty_environment"] is False
    assert prepared["fixture_fingerprint"] == fingerprint
