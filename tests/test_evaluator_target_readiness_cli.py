from __future__ import annotations

import json
from pathlib import Path

from tools.evaluator_target_readiness import main


def test_emit_persists_redacted_non_measurement_receipt(tmp_path: Path) -> None:
    code = main([
        "emit",
        "--receipts-root", str(tmp_path),
        "--sequence", "1",
        "--target-id", "benchmark-mall-131",
        "--target-role", "held_in_diagnostic",
        "--state", "STOPPED_CLEAN",
        "--previous-state", "RUNTIME_READY",
        "--environment-type", "sandbox",
        "--environment-ref", "benchmark-mall-local-sandbox",
        "--target-url", "http://127.0.0.1:8080",
        "--check", "target_stopped=passed",
        "--check", "ports_released=passed",
        "--fingerprint", f"source_sha256={'a' * 64}",
    ])
    assert code == 0
    path = tmp_path / "0001-benchmark-mall-131-stopped_clean.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["measurement_status"] == "NOT_MEASURED"
    assert payload["state"] == "STOPPED_CLEAN"


def test_admit_returns_two_when_another_target_is_active(tmp_path: Path) -> None:
    active = {
        "schema_version": "qualibug.evaluator-target-readiness.v1",
        "target_id": "benchmark-mall-131",
        "state": "RUNTIME_READY",
    }
    (tmp_path / "0001-benchmark-mall-131-runtime_ready.json").write_text(
        json.dumps(active), encoding="utf-8"
    )
    code = main([
        "admit",
        "--receipts-root", str(tmp_path),
        "--requested-target-id", "openproject-17.6.0",
    ])
    assert code == 2
