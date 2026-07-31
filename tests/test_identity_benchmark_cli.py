from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center import identity_benchmark_cli as cli
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    identity_annotation_operator as operator,
    identity_annotation_tasks as tasks,
    identity_benchmark_workflow as workflow,
)


def _write(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _submission(name: str, role: str = "ANNOTATOR") -> dict:
    return {
        "schema": "qualibug.enterprise-identity-annotation-submission.v1",
        "task_package_id": "package:1",
        "manifest_id": "manifest:1",
        "annotation_scope": "CLOSED_WORLD_IDENTITY_MENTIONS",
        "generated_from_product_output": False,
        "annotator": {"name": name, "role": role},
        "annotations": [],
    }


def _workspace(*, blocked: bool) -> dict:
    return {
        "project_id": "customer-a",
        "manifest": {"manifest_id": "manifest:1", "mention_count": 2},
        "ground_truth_summary": {"present": True, "annotated_mention_count": 2},
        "benchmark": {
            "status": "MEASURED",
            "benchmark_id": "benchmark:1",
            "metrics": {"pairwise_precision": 1.0, "pairwise_recall": 1.0},
        },
        "regression": {"status": "PASS", "metric_deltas": {}},
        "identity_quality_gate": {
            "status": "BLOCKED_IDENTITY_QUALITY" if blocked else "PASS",
            "entry_allowed": not blocked,
            "enforced": blocked,
        },
        "history": {"snapshot_count": 1, "latest_snapshot": {"snapshot_id": "snapshot:1"}},
        "error_queue": {"active_errors": [], "resolved_errors": []},
    }


def test_submission_files_are_routed_by_role_not_selection_order(tmp_path: Path) -> None:
    adjudicator = _write(tmp_path / "review.json", _submission("reviewer", "ADJUDICATOR"))
    secondary = _write(tmp_path / "b.json", _submission("annotator-b"))
    primary = _write(tmp_path / "a.json", _submission("annotator-a"))

    payload = cli._submission_payload([adjudicator, secondary, primary])

    assert payload["primary_submission"]["annotator"]["role"] == "ANNOTATOR"
    assert payload["secondary_submission"]["annotator"]["role"] == "ANNOTATOR"
    assert payload["adjudication_submission"]["annotator"]["role"] == "ADJUDICATOR"


def test_export_writes_redacted_task_package(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    package = {
        "task_package_id": "package:1",
        "batch_layout_id": "layout:1",
        "manifest_id": "manifest:1",
        "task_count": 1,
        "batch_count": 1,
        "batch_size": 40,
        "source_context_is_redacted": True,
        "tasks": [
            {
                "mention_ref": "mention:1",
                "context": [{"quote": "Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"}],
            }
        ],
    }
    monkeypatch.setattr(
        operator,
        "get_identity_annotation_task_package",
        lambda project, root, batch_size=40: dict(package),
    )
    output = tmp_path / "tasks.json"

    code = cli.main(
        [
            "export",
            "--project",
            "customer-a",
            "--root",
            str(tmp_path),
            "--output",
            str(output),
        ]
    )

    assert code == cli.EXIT_SUCCESS
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in json.dumps(persisted)
    assert "REDACTED" in json.dumps(persisted)
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "EXPORTED"
    assert result["batch_layout_id"] == "layout:1"


def test_validate_returns_review_exit_without_importing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    a = _write(tmp_path / "a.json", _submission("annotator-a"))
    b = _write(tmp_path / "b.json", _submission("annotator-b"))
    monkeypatch.setattr(
        operator,
        "get_identity_annotation_task_package",
        lambda project, root: {"task_package_id": "package:1", "manifest_id": "manifest:1"},
    )
    monkeypatch.setattr(
        tasks,
        "compile_identity_annotation_submissions",
        lambda package, primary, secondary_submission=None, adjudication_submission=None: {
            "status": "REVIEW_REQUIRED",
            "task_package_id": "package:1",
            "manifest_id": "manifest:1",
            "review_status": "DOUBLE_BLIND_DISAGREED",
            "progress": {"status": "AWAITING_ADJUDICATION"},
            "disagreement_count": 1,
            "disagreements": [{"disagreement_id": "disagreement:1"}],
            "ground_truth_import_allowed": False,
        },
    )

    code = cli.main(
        [
            "validate",
            "--project",
            "customer-a",
            "--root",
            str(tmp_path),
            "--submission",
            a,
            "--submission",
            b,
        ]
    )

    assert code == cli.EXIT_REVIEW_REQUIRED
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["ground_truth_import_allowed"] is False


def test_import_passes_audited_actor_and_returns_quality_blocked_exit(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    submission = _write(tmp_path / "a.json", _submission("annotator-a"))
    captured: dict = {}

    def fake_import(project, payload, *, actor, root):
        captured.update({"project": project, "payload": payload, "actor": actor, "root": root})
        return {
            "status": "IMPORTED",
            "task_package_id": "package:1",
            "manifest_id": "manifest:1",
            "ground_truth_imported": True,
            "compilation": {"review_status": "SINGLE_ANNOTATOR", "disagreement_count": 0},
            "workspace": _workspace(blocked=True),
        }

    monkeypatch.setattr(operator, "compile_and_import_identity_annotations", fake_import)

    code = cli.main(
        [
            "import",
            "--project",
            "customer-a",
            "--root",
            str(tmp_path),
            "--actor-name",
            "qa-lead",
            "--actor-role",
            "qa_lead",
            "--tenant-id",
            "tenant-a",
            "--submission",
            submission,
        ]
    )

    assert code == cli.EXIT_QUALITY_BLOCKED
    assert captured["actor"] == {
        "name": "qa-lead",
        "role": "qa_lead",
        "tenant_id": "tenant-a",
    }
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "IMPORTED"
    assert result["workspace"]["quality_gate"]["entry_allowed"] is False


def test_invalid_manager_role_fails_before_ground_truth_import(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    submission = _write(tmp_path / "a.json", _submission("annotator-a"))

    def must_not_import(*args, **kwargs):
        raise AssertionError("ground truth import must not be reached")

    monkeypatch.setattr(operator, "compile_and_import_identity_annotations", must_not_import)

    code = cli.main(
        [
            "import",
            "--project",
            "customer-a",
            "--root",
            str(tmp_path),
            "--actor-name",
            "viewer",
            "--actor-role",
            "viewer",
            "--submission",
            submission,
        ]
    )

    assert code == cli.EXIT_ERROR
    error = json.loads(capsys.readouterr().err)
    assert error["status"] == "FAILED"
    assert error["error_type"] == "PermissionError"


def test_invalid_enforced_policy_is_blocked_by_entry_authority() -> None:
    workspace = _workspace(blocked=False)
    workspace["identity_quality_gate"] = {
        "status": "INVALID_IDENTITY_QUALITY_POLICY",
        "entry_allowed": False,
        "enforced": True,
    }

    assert cli._quality_blocked(workspace) is True


def test_status_can_fail_ci_on_blocked_gate(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        workflow,
        "get_identity_benchmark_workspace",
        lambda project, root: _workspace(blocked=True),
    )

    code = cli.main(
        [
            "status",
            "--project",
            "customer-a",
            "--root",
            str(tmp_path),
            "--fail-on-blocked",
        ]
    )

    assert code == cli.EXIT_QUALITY_BLOCKED
    assert json.loads(capsys.readouterr().out)["status"] == "QUALITY_BLOCKED"
