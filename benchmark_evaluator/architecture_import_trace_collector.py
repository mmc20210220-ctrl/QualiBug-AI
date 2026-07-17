"""Evaluator-owned collection of real Python import and callable execution."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence


IMPORT_TRACE_SCHEMA = "qualibug.python-import-trace.v1"
INVENTORY_SCHEMA = "qualibug.architecture-inventory.v1"
PROCESS_TRACE_SCHEMA = "qualibug.python-import-trace-process.v1"
COLLECTOR_NAME = "qualibug.import-trace"
COLLECTOR_VERSION = "1"
_AUTHENTICATION_FIELDS = {"trace_fingerprint", "trace_authentication"}
_INJECTION_ENVIRONMENT = {
    "QUALIBUG_IMPORT_TRACE_PROCESS_DIR",
    "QUALIBUG_IMPORT_TRACE_SESSION_NONCE",
    "QUALIBUG_IMPORT_TRACE_TARGET_MODULE",
    "QUALIBUG_IMPORT_TRACE_TARGET_CALLABLE",
}
_SECRET_TERMS = (
    "AUTH",
    "COOKIE",
    "CREDENTIAL",
    "KEY",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)


class ArchitectureImportTraceCollectionError(ValueError):
    """Collection cannot produce truthful execution evidence."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArchitectureImportTraceCollectionError(
            f"{label}_invalid:{type(exc).__name__}"
        ) from exc
    if not isinstance(value, dict):
        raise ArchitectureImportTraceCollectionError(f"{label}_not_object")
    return value


def _safe_environment_fingerprint(
    environment: dict[str, str],
    *,
    workspace: Path,
) -> str:
    projected = {}
    for name, value in sorted(environment.items()):
        if name in _INJECTION_ENVIRONMENT:
            continue
        upper = name.upper()
        projected[name] = (
            "<sensitive-value-present>"
            if any(term in upper for term in _SECRET_TERMS)
            else value
        )
    return _fingerprint({
        "cwd": str(workspace),
        "platform": platform.platform(),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "environment": projected,
    })


def _outside_product_workspace(
    path: Path,
    *,
    product_workspace: Path,
    label: str,
) -> Path:
    resolved = path.resolve()
    if resolved == product_workspace or product_workspace in resolved.parents:
        raise ArchitectureImportTraceCollectionError(
            f"{label}_must_be_outside_product_workspace"
        )
    return resolved


def _root_descriptor(
    inventory: dict[str, Any],
    root_id: str,
) -> dict[str, str]:
    if inventory.get("schema_version") != INVENTORY_SCHEMA:
        raise ArchitectureImportTraceCollectionError("inventory_schema_invalid")
    roots = inventory.get("trace_roots")
    if not isinstance(roots, list):
        raise ArchitectureImportTraceCollectionError("inventory_trace_roots_invalid")
    matches = [
        row
        for row in roots
        if isinstance(row, dict) and str(row.get("root_id") or "") == root_id
    ]
    if len(matches) != 1:
        raise ArchitectureImportTraceCollectionError(
            f"inventory_trace_root_not_unique:{root_id}"
        )
    descriptor = {
        name: str(matches[0].get(name) or "").strip()
        for name in ("root_id", "module", "callable")
    }
    if not descriptor["module"]:
        raise ArchitectureImportTraceCollectionError("inventory_trace_root_invalid")
    return descriptor


def _read_process_observations(
    directory: Path,
    *,
    nonce: str,
) -> tuple[set[str], bool, bool, int]:
    modules: set[str] = set()
    target_module_observed = False
    target_callable_observed = False
    count = 0
    for path in sorted(directory.glob("process-*.json")):
        value = _load_object(path, label="process_observation")
        if (
            value.get("schema_version") != PROCESS_TRACE_SCHEMA
            or value.get("session_nonce") != nonce
            or not isinstance(value.get("modules"), list)
        ):
            raise ArchitectureImportTraceCollectionError(
                "process_observation_identity_invalid"
            )
        observed = value["modules"]
        if not all(
            isinstance(item, str)
            and item
            and all(part.isidentifier() for part in item.split("."))
            for item in observed
        ):
            raise ArchitectureImportTraceCollectionError(
                "process_observation_modules_invalid"
            )
        modules.update(observed)
        target_module_observed = target_module_observed or (
            value.get("target_module_observed") is True
        )
        target_callable_observed = target_callable_observed or (
            value.get("target_callable_observed") is True
        )
        count += 1
    if count == 0:
        raise ArchitectureImportTraceCollectionError(
            "python_process_observation_missing"
        )
    return modules, target_module_observed, target_callable_observed, count


def _existing_trace(
    output: Path,
    *,
    inventory: dict[str, Any],
) -> dict[str, Any] | None:
    if not output.exists():
        return None
    value = _load_object(output, label="existing_import_trace")
    if value.get("schema_version") != IMPORT_TRACE_SCHEMA:
        raise ArchitectureImportTraceCollectionError(
            "existing_import_trace_schema_invalid"
        )
    if _AUTHENTICATION_FIELDS.intersection(value):
        raise ArchitectureImportTraceCollectionError(
            "authenticated_import_trace_is_immutable"
        )
    source = inventory.get("source_identity")
    if not isinstance(source, dict):
        raise ArchitectureImportTraceCollectionError("inventory_source_identity_invalid")
    expected = {
        "source_fingerprint": source.get("python_source_fingerprint"),
        "config_fingerprint": source.get("config_fingerprint"),
        "project_scripts_fingerprint": source.get("project_scripts_fingerprint"),
    }
    if any(value.get(name) != expected_value for name, expected_value in expected.items()):
        raise ArchitectureImportTraceCollectionError(
            "existing_import_trace_source_identity_mismatch"
        )
    collector = value.get("collector")
    if not isinstance(collector, dict) or (
        collector.get("name") != COLLECTOR_NAME
        or collector.get("version") != COLLECTOR_VERSION
        or not str(collector.get("session_id") or "").strip()
    ):
        raise ArchitectureImportTraceCollectionError(
            "existing_import_trace_collector_invalid"
        )
    modules = value.get("modules")
    sessions = value.get("root_sessions")
    if (
        value.get("coverage_status") not in {"COMPLETE", "PARTIAL"}
        or not isinstance(modules, list)
        or not all(
            isinstance(item, str)
            and item
            and all(part.isidentifier() for part in item.split("."))
            for item in modules
        )
        or not isinstance(sessions, list)
        or not all(isinstance(row, dict) for row in sessions)
    ):
        raise ArchitectureImportTraceCollectionError(
            "existing_import_trace_evidence_invalid"
        )
    required_ids = {
        str(row.get("root_id") or "")
        for row in inventory.get("trace_roots", [])
        if isinstance(row, dict)
    }
    session_ids = [str(row.get("root_id") or "") for row in sessions]
    if (
        len(set(session_ids)) != len(session_ids)
        or not set(session_ids).issubset(required_ids)
        or any(
            not root_id
            or row.get("status") != "COMPLETE"
            or not str(row.get("command_fingerprint") or "").strip()
            or not str(row.get("environment_fingerprint") or "").strip()
            for root_id, row in zip(session_ids, sessions)
        )
    ):
        raise ArchitectureImportTraceCollectionError(
            "existing_import_trace_sessions_invalid"
        )
    return value


def _persist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def collect_architecture_import_trace(
    *,
    inventory_path: Path,
    root_id: str,
    output_path: Path,
    product_workspace: Path,
    command: Sequence[str],
) -> dict[str, Any]:
    """Execute one declared root and append its real process observation."""
    workspace = product_workspace.resolve()
    output = _outside_product_workspace(
        output_path,
        product_workspace=workspace,
        label="output",
    )
    command_values = [str(item) for item in command]
    if not command_values or any(not item for item in command_values):
        raise ArchitectureImportTraceCollectionError("command_missing")
    inventory = _load_object(inventory_path.resolve(), label="inventory")
    descriptor = _root_descriptor(inventory, root_id)
    source = inventory.get("source_identity")
    if not isinstance(source, dict) or any(
        not isinstance(source.get(name), str)
        or len(source[name]) != 64
        for name in (
            "python_source_fingerprint",
            "config_fingerprint",
            "project_scripts_fingerprint",
        )
    ):
        raise ArchitectureImportTraceCollectionError("inventory_source_identity_invalid")
    existing = _existing_trace(output, inventory=inventory)
    existing_sessions = list(existing.get("root_sessions", [])) if existing else []
    if any(
        isinstance(row, dict) and row.get("root_id") == root_id
        for row in existing_sessions
    ):
        raise ArchitectureImportTraceCollectionError(
            f"root_session_already_collected:{root_id}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    environment = dict(os.environ)
    bootstrap = Path(__file__).resolve().parent / "import_trace_bootstrap"
    current_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(bootstrap) + (
        os.pathsep + current_pythonpath if current_pythonpath else ""
    )
    environment.update({
        "QUALIBUG_IMPORT_TRACE_PROCESS_DIR": "",
        "QUALIBUG_IMPORT_TRACE_SESSION_NONCE": nonce,
        "QUALIBUG_IMPORT_TRACE_TARGET_MODULE": descriptor["module"],
        "QUALIBUG_IMPORT_TRACE_TARGET_CALLABLE": descriptor["callable"],
    })
    with tempfile.TemporaryDirectory(
        prefix="qualibug-import-trace-",
        dir=output.parent,
    ) as temporary_directory:
        process_directory = Path(temporary_directory)
        environment["QUALIBUG_IMPORT_TRACE_PROCESS_DIR"] = str(process_directory)
        stdout_path = process_directory / "stdout.bin"
        stderr_path = process_directory / "stderr.bin"
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                completed = subprocess.run(
                    command_values,
                    cwd=workspace,
                    env=environment,
                    check=False,
                    stdout=stdout,
                    stderr=stderr,
                )
        except OSError as exc:
            raise ArchitectureImportTraceCollectionError(
                f"trace_command_start_failed:{type(exc).__name__}"
            ) from exc
        modules, module_observed, callable_observed, process_count = (
            _read_process_observations(process_directory, nonce=nonce)
        )
        stdout_bytes = stdout_path.read_bytes()
        stderr_bytes = stderr_path.read_bytes()
    if completed.returncode != 0:
        raise ArchitectureImportTraceCollectionError(
            f"trace_command_failed:{completed.returncode}:"
            f"stdout_sha256={hashlib.sha256(stdout_bytes).hexdigest()}:"
            f"stderr_sha256={hashlib.sha256(stderr_bytes).hexdigest()}"
        )
    if not module_observed:
        raise ArchitectureImportTraceCollectionError(
            f"target_module_not_observed:{descriptor['module']}"
        )
    if descriptor["callable"] and not callable_observed:
        raise ArchitectureImportTraceCollectionError(
            "target_callable_not_observed:"
            f"{descriptor['module']}:{descriptor['callable']}"
        )

    session = {
        "root_id": root_id,
        "module": descriptor["module"],
        "callable": descriptor["callable"],
        "status": "COMPLETE",
        "command_fingerprint": _fingerprint(command_values),
        "environment_fingerprint": _safe_environment_fingerprint(
            environment,
            workspace=workspace,
        ),
    }
    sessions = sorted(
        [*existing_sessions, session],
        key=lambda row: str(row.get("root_id") or ""),
    )
    required_root_ids = {
        str(row.get("root_id") or "")
        for row in inventory.get("trace_roots", [])
        if isinstance(row, dict)
    }
    covered_root_ids = {
        str(row.get("root_id") or "")
        for row in sessions
        if isinstance(row, dict) and row.get("status") == "COMPLETE"
    }
    trace = {
        "schema_version": IMPORT_TRACE_SCHEMA,
        "coverage_status": (
            "COMPLETE" if covered_root_ids == required_root_ids else "PARTIAL"
        ),
        "source_fingerprint": source.get("python_source_fingerprint"),
        "config_fingerprint": source.get("config_fingerprint"),
        "project_scripts_fingerprint": source.get("project_scripts_fingerprint"),
        "collector": (
            existing["collector"]
            if existing
            else {
                "name": COLLECTOR_NAME,
                "version": COLLECTOR_VERSION,
                "session_id": uuid.uuid4().hex,
            }
        ),
        "modules": sorted(set(existing.get("modules", []) if existing else []).union(modules)),
        "root_sessions": sessions,
    }
    _persist(output, trace)
    return {
        "schema_version": IMPORT_TRACE_SCHEMA,
        "status": "OBSERVED",
        "coverage_status": trace["coverage_status"],
        "root_id": root_id,
        "target_module_observed": module_observed,
        "target_callable_observed": callable_observed,
        "observed_process_count": process_count,
        "observed_module_count": len(modules),
        "stdout_byte_count": len(stdout_bytes),
        "stderr_byte_count": len(stderr_bytes),
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "output": str(output),
    }
