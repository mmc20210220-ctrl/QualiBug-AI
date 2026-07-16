from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from benchmark_evaluator.windows_fixture_controller import (
    WINDOWS_BENCHMARK_FIXTURE_SCHEMA,
)
from tools import bootstrap_evaluator_trust_root
from tools import build_observed_diagnostic_manifest


def test_bootstrap_evaluator_trust_root_requires_external_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap_evaluator_trust_root.py",
            "--root",
            str(workspace / "trust-root"),
            "--product-workspace",
            str(workspace),
        ],
    )

    with pytest.raises(RuntimeError, match="outside product workspace"):
        bootstrap_evaluator_trust_root.main()


def test_bootstrap_evaluator_trust_root_creates_observation_output_and_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    trust_root = tmp_path / "evaluator-trust"
    workspace.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap_evaluator_trust_root.py",
            "--root",
            str(trust_root),
            "--product-workspace",
            str(workspace),
        ],
    )

    assert bootstrap_evaluator_trust_root.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert Path(summary["observation_root"]).is_dir()
    assert Path(summary["output_root"]).is_dir()
    assert Path(summary["hmac_key_file"]).is_file()
    assert Path(summary["hmac_key_file"]).stat().st_size >= 32
    assert summary["key_created"] is True


def test_build_observed_diagnostic_manifest_writes_windows_fixture_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "diagnostic"
    target_root = tmp_path / "target"
    workspace.mkdir()
    target_root.mkdir()
    api_doc = tmp_path / "api.md"
    prd = tmp_path / "prd.md"
    ground_truth = tmp_path / "bugs.json"
    api_doc.write_text("GET /api/items", encoding="utf-8")
    prd.write_text("Items must remain owner-scoped.", encoding="utf-8")
    ground_truth.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_observed_diagnostic_manifest.py",
            "--output-dir",
            str(output_dir),
            "--product-workspace",
            str(workspace),
            "--dataset-id",
            "dataset-1",
            "--dataset-version",
            "v1",
            "--target-id",
            "TARGET-1",
            "--project-id",
            "generic-project",
            "--industry",
            "retail",
            "--base-url",
            "http://127.0.0.1:8080/",
            "--environment-type",
            "test",
            "--api-doc",
            str(api_doc),
            "--prd",
            str(prd),
            "--ground-truth",
            str(ground_truth),
            "--target-root",
            str(target_root),
        ],
    )

    assert build_observed_diagnostic_manifest.main() == 0

    manifest = json.loads(
        (output_dir / "evaluation_manifest.json").read_text(encoding="utf-8")
    )
    fixture = json.loads(
        (output_dir / "fixture.json").read_text(encoding="utf-8")
    )
    target = manifest["targets"][0]
    assert target["target_id"] == "TARGET-1"
    assert target["runtime"]["environment_ref"] == "http://127.0.0.1:8080"
    assert fixture["schema_version"] == WINDOWS_BENCHMARK_FIXTURE_SCHEMA
    assert fixture["target_root"] == str(target_root.resolve())


def test_build_observed_diagnostic_manifest_requires_environment_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_observed_diagnostic_manifest.py",
            "--output-dir",
            "diagnostic",
            "--product-workspace",
            "workspace",
            "--dataset-id",
            "dataset-1",
            "--dataset-version",
            "v1",
            "--target-id",
            "TARGET-1",
            "--project-id",
            "generic-project",
            "--industry",
            "retail",
            "--base-url",
            "http://127.0.0.1:8080/",
            "--api-doc",
            "api.md",
            "--prd",
            "prd.md",
            "--ground-truth",
            "bugs.json",
            "--target-root",
            "target",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        build_observed_diagnostic_manifest.main()
