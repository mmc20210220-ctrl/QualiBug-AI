"""Machine-friendly CLI for enterprise identity annotation and benchmark baselines.

This is an operator surface over the existing annotation compiler and transactional
benchmark workflow. It never calculates a second identity result or persists Ground
Truth through an independent path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

CLI_RESULT_SCHEMA = "qualibug.enterprise-identity-benchmark-cli-result.v1"
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_REVIEW_REQUIRED = 2
EXIT_QUALITY_BLOCKED = 3


class _MachineArgumentParser(argparse.ArgumentParser):
    """Convert parser failures into the CLI's JSON error contract."""

    def error(self, message: str) -> None:
        raise ValueError(f"identity_benchmark_cli_argument_error:{message}")


def _root(value: str) -> Path:
    return Path(value).expanduser().resolve() if value else Path.cwd().resolve()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _read_json_object(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"identity_annotation_submission_not_found:{source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"identity_annotation_submission_json_invalid:{source}:{exc.lineno}:{exc.colno}"
        ) from exc
    except OSError as exc:
        raise ValueError(f"identity_annotation_submission_unreadable:{source}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"identity_annotation_submission_must_be_object:{source}")
    return payload


def _annotator_role(submission: dict[str, Any]) -> str:
    annotator = _as_dict(submission.get("annotator"))
    return str(annotator.get("role") or "ANNOTATOR").strip().upper()


def _submission_payload(paths: Sequence[str]) -> dict[str, Any]:
    if not 1 <= len(paths) <= 3:
        raise ValueError("identity_annotation_requires_one_to_three_submission_files")
    rows = [_read_json_object(path) for path in paths]
    adjudications = [row for row in rows if _annotator_role(row) == "ADJUDICATOR"]
    annotations = [row for row in rows if _annotator_role(row) != "ADJUDICATOR"]
    if not 1 <= len(annotations) <= 2:
        raise ValueError("identity_annotation_requires_one_or_two_annotator_submissions")
    if len(adjudications) > 1:
        raise ValueError("identity_annotation_allows_one_adjudication_submission")
    if adjudications and len(annotations) != 2:
        raise ValueError("identity_adjudication_requires_two_annotator_submissions")
    payload: dict[str, Any] = {"primary_submission": annotations[0]}
    if len(annotations) == 2:
        payload["secondary_submission"] = annotations[1]
    if adjudications:
        payload["adjudication_submission"] = adjudications[0]
    return payload


def _actor(args: argparse.Namespace) -> dict[str, str]:
    from .enterprise_knowledge_center._utils import _require_manage_actor

    name = str(getattr(args, "actor_name", "") or "").strip()
    if not name:
        raise ValueError("identity_benchmark_actor_name_required")
    role = str(getattr(args, "actor_role", "") or "knowledge_admin").strip()
    validated = _require_manage_actor({"name": name, "role": role})
    return {
        **validated,
        "tenant_id": str(getattr(args, "tenant_id", "") or "").strip(),
    }


def _write_json(path: str | Path, payload: Any) -> Path:
    from .artifact_redactor import write_json_redacted

    target = Path(path).expanduser().resolve()
    write_json_redacted(target, payload)
    return target


def _workspace_summary(workspace: dict[str, Any]) -> dict[str, Any]:
    manifest = _as_dict(workspace.get("manifest"))
    benchmark = _as_dict(workspace.get("benchmark"))
    regression = _as_dict(workspace.get("regression") or benchmark.get("regression"))
    gate = _as_dict(workspace.get("identity_quality_gate") or benchmark.get("quality_gate"))
    history = _as_dict(workspace.get("history"))
    latest = _as_dict(history.get("latest_snapshot"))
    errors = _as_dict(workspace.get("error_queue") or history.get("error_queue"))
    return {
        "project_id": workspace.get("project_id"),
        "manifest": {
            "manifest_id": manifest.get("manifest_id"),
            "mention_count": int(manifest.get("mention_count") or 0),
        },
        "ground_truth": _as_dict(workspace.get("ground_truth_summary")),
        "measurement": {
            "status": benchmark.get("status") or "NOT_MEASURED",
            "benchmark_id": benchmark.get("benchmark_id"),
            "metrics": _as_dict(benchmark.get("metrics")),
        },
        "regression": {
            "status": regression.get("status") or "NOT_COMPARABLE",
            "baseline_snapshot_id": regression.get("baseline_snapshot_id"),
            "metric_deltas": _as_dict(regression.get("metric_deltas")),
        },
        "quality_gate": gate,
        "history": {
            "snapshot_count": int(history.get("snapshot_count") or 0),
            "latest_snapshot_id": latest.get("snapshot_id"),
            "latest_recorded_at_utc": latest.get("recorded_at_utc"),
        },
        "errors": {
            "active_count": len(_as_list(errors.get("active_errors"))),
            "resolved_count": len(_as_list(errors.get("resolved_errors"))),
        },
    }


def _quality_blocked(workspace: dict[str, Any]) -> bool:
    benchmark = _as_dict(workspace.get("benchmark"))
    gate = _as_dict(workspace.get("identity_quality_gate") or benchmark.get("quality_gate"))
    if "entry_allowed" in gate:
        return gate.get("entry_allowed") is False
    if gate.get("enforced") is False:
        return False
    status = str(gate.get("status") or "").strip().upper()
    if status.startswith("BLOCKED") or status in {"FAIL", "FAILED", "REJECTED"}:
        return True
    return any(
        key in gate and gate.get(key) is False
        for key in ("admission_allowed", "allowed", "passed", "ready")
    )


def _envelope(command: str, status: str, **payload: Any) -> dict[str, Any]:
    return {"schema": CLI_RESULT_SCHEMA, "command": command, "status": status, **payload}


def _print(payload: Any, *, stream: Any = None) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        file=stream or sys.stdout,
    )


def cmd_export(args: argparse.Namespace) -> int:
    from .enterprise_knowledge_center.enterprise_understanding.identity_annotation_operator import (
        get_identity_annotation_task_package,
    )

    package = get_identity_annotation_task_package(
        args.project,
        _root(args.root),
        batch_size=args.batch_size,
    )
    target = _write_json(
        args.output or f"{args.project}-identity-annotation-tasks.json",
        package,
    )
    _print(
        _envelope(
            "export",
            "EXPORTED",
            project_id=args.project,
            output=str(target),
            task_package_id=package.get("task_package_id"),
            batch_layout_id=package.get("batch_layout_id"),
            manifest_id=package.get("manifest_id"),
            task_count=int(package.get("task_count") or 0),
            batch_count=int(package.get("batch_count") or 0),
            batch_size=int(package.get("batch_size") or 0),
            source_context_is_redacted=bool(package.get("source_context_is_redacted")),
        )
    )
    return EXIT_SUCCESS


def cmd_validate(args: argparse.Namespace) -> int:
    from .enterprise_knowledge_center.enterprise_understanding.identity_annotation_operator import (
        get_identity_annotation_task_package,
    )
    from .enterprise_knowledge_center.enterprise_understanding.identity_annotation_tasks import (
        compile_identity_annotation_submissions,
    )

    package = get_identity_annotation_task_package(args.project, _root(args.root))
    submissions = _submission_payload(args.submission)
    compilation = compile_identity_annotation_submissions(
        package,
        _as_dict(submissions.get("primary_submission")),
        secondary_submission=_as_dict(submissions.get("secondary_submission")) or None,
        adjudication_submission=_as_dict(submissions.get("adjudication_submission")) or None,
    )
    output = str(_write_json(args.output, compilation)) if args.output else ""
    status = str(compilation.get("status") or "VALIDATED")
    _print(
        _envelope(
            "validate",
            status,
            project_id=args.project,
            output=output,
            task_package_id=compilation.get("task_package_id"),
            manifest_id=compilation.get("manifest_id"),
            review_status=compilation.get("review_status"),
            progress=compilation.get("progress"),
            disagreement_count=int(compilation.get("disagreement_count") or 0),
            disagreements=_as_list(compilation.get("disagreements")),
            ground_truth_import_allowed=bool(compilation.get("ground_truth_import_allowed")),
        )
    )
    return EXIT_REVIEW_REQUIRED if status == "REVIEW_REQUIRED" else EXIT_SUCCESS


def cmd_import(args: argparse.Namespace) -> int:
    from .enterprise_knowledge_center.enterprise_understanding.identity_annotation_operator import (
        compile_and_import_identity_annotations,
    )

    result = compile_and_import_identity_annotations(
        args.project,
        _submission_payload(args.submission),
        actor=_actor(args),
        root=_root(args.root),
    )
    output = str(_write_json(args.output, result)) if args.output else ""
    status = str(result.get("status") or "UNKNOWN")
    compilation = _as_dict(result.get("compilation"))
    workspace = _as_dict(result.get("workspace"))
    _print(
        _envelope(
            "import",
            status,
            project_id=args.project,
            output=output,
            task_package_id=result.get("task_package_id"),
            manifest_id=result.get("manifest_id"),
            ground_truth_imported=bool(result.get("ground_truth_imported")),
            review_status=compilation.get("review_status"),
            disagreement_count=int(compilation.get("disagreement_count") or 0),
            disagreements=_as_list(compilation.get("disagreements")),
            workspace=_workspace_summary(workspace) if workspace else {},
        )
    )
    if status == "REVIEW_REQUIRED":
        return EXIT_REVIEW_REQUIRED
    return EXIT_QUALITY_BLOCKED if workspace and _quality_blocked(workspace) else EXIT_SUCCESS


def cmd_status(args: argparse.Namespace) -> int:
    from .enterprise_knowledge_center.enterprise_understanding.identity_benchmark_workflow import (
        get_identity_benchmark_workspace,
    )

    workspace = get_identity_benchmark_workspace(args.project, _root(args.root))
    payload = workspace if args.full else _workspace_summary(workspace)
    output = str(_write_json(args.output, payload)) if args.output else ""
    blocked = _quality_blocked(workspace)
    _print(
        _envelope(
            "status",
            "QUALITY_BLOCKED" if blocked else "OK",
            project_id=args.project,
            output=output,
            workspace=payload,
        )
    )
    return EXIT_QUALITY_BLOCKED if blocked and args.fail_on_blocked else EXIT_SUCCESS


def cmd_remeasure(args: argparse.Namespace) -> int:
    from .enterprise_knowledge_center.enterprise_understanding.identity_benchmark_workflow import (
        run_identity_benchmark,
    )

    workspace = run_identity_benchmark(
        args.project,
        actor=_actor(args),
        root=_root(args.root),
    )
    output = str(_write_json(args.output, workspace)) if args.output else ""
    blocked = _quality_blocked(workspace)
    _print(
        _envelope(
            "remeasure",
            "QUALITY_BLOCKED" if blocked else "MEASURED",
            project_id=args.project,
            output=output,
            workspace=_workspace_summary(workspace),
        )
    )
    return EXIT_QUALITY_BLOCKED if blocked else EXIT_SUCCESS


def _project_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument(
        "--root",
        default="",
        help="Repository/runtime root; defaults to the current working directory",
    )


def _actor_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor-name", required=True, help="Audited operator name")
    parser.add_argument(
        "--actor-role",
        default="knowledge_admin",
        help="Audited operator role; default=knowledge_admin",
    )
    parser.add_argument("--tenant-id", default="", help="Optional tenant identifier")


def build_parser() -> argparse.ArgumentParser:
    parser = _MachineArgumentParser(
        prog="qualibug-identity-benchmark",
        description=(
            "Export blind identity annotation tasks, validate/import human labels, "
            "and manage the versioned identity benchmark baseline."
        ),
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_MachineArgumentParser,
    )

    export = commands.add_parser("export", help="Export the current blind annotation task package")
    _project_args(export)
    export.add_argument("--output", default="", help="Output JSON path")
    export.add_argument("--batch-size", type=int, default=40, help="Tasks per presentation batch")
    export.set_defaults(func=cmd_export)

    validate = commands.add_parser("validate", help="Validate submissions without importing Ground Truth")
    _project_args(validate)
    validate.add_argument("--submission", action="append", required=True, help="Completed annotation JSON; repeat up to three times")
    validate.add_argument("--output", default="", help="Optional compilation/review JSON path")
    validate.set_defaults(func=cmd_validate)

    import_cmd = commands.add_parser("import", help="Compile and transactionally import resolved Ground Truth")
    _project_args(import_cmd)
    _actor_args(import_cmd)
    import_cmd.add_argument("--submission", action="append", required=True, help="Completed annotation JSON; repeat up to three times")
    import_cmd.add_argument("--output", default="", help="Optional full import result JSON path")
    import_cmd.set_defaults(func=cmd_import)

    status = commands.add_parser("status", help="Show benchmark, Gate, history and error status")
    _project_args(status)
    status.add_argument("--full", action="store_true", help="Return the complete workspace")
    status.add_argument("--output", default="", help="Optional JSON output path")
    status.add_argument("--fail-on-blocked", action="store_true", help="Exit 3 when entry_allowed is false")
    status.set_defaults(func=cmd_status)

    remeasure = commands.add_parser("remeasure", help="Rebuild through the canonical root and record a snapshot")
    _project_args(remeasure)
    _actor_args(remeasure)
    remeasure.add_argument("--output", default="", help="Optional full workspace JSON path")
    remeasure.set_defaults(func=cmd_remeasure)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(list(argv) if argv is not None else None)
        return int(args.func(args))
    except SystemExit as exc:
        if int(exc.code or 0) == 0:
            raise
        _print(
            _envelope(
                "error",
                "FAILED",
                error_type="ArgumentError",
                error=f"identity_benchmark_cli_argument_exit:{exc.code}",
            ),
            stream=sys.stderr,
        )
        return EXIT_ERROR
    except Exception as exc:
        _print(
            _envelope(
                "error",
                "FAILED",
                error_type=type(exc).__name__,
                error=str(exc),
            ),
            stream=sys.stderr,
        )
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CLI_RESULT_SCHEMA",
    "EXIT_ERROR",
    "EXIT_QUALITY_BLOCKED",
    "EXIT_REVIEW_REQUIRED",
    "EXIT_SUCCESS",
    "build_parser",
    "main",
]
