"""Governed lifecycle for project-scoped UI upload fixtures.

Upload fixtures are executable test authority because their bytes are sent to the
system under test.  This registry therefore keeps one fail-closed lifecycle:

``register``
    Copy one existing project-input file into an immutable candidate namespace.
``approve``
    Copy one active candidate into the runtime-only approved workspace namespace.
``list``
    Return metadata and binding identities only; raw file bytes and source paths are
    never embedded in responses.
``revoke``
    Disable authority while retaining immutable bytes and audit history.

Only active approved records can become ``runtime_contract.ui_file_bindings``.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .enterprise_knowledge_center._common import ROOT, _safe_project_id
from .enterprise_knowledge_center._utils import _now, _safe_slug

SCHEMA_VERSION = "qualibug.ui-upload-fixture-registry.v1"
INPUT_PREFIX = "ui_upload_fixtures"
APPROVED_PREFIX = "ui_upload_fixtures"
MAX_FIXTURE_BYTES = 25_000_000
MAX_FIXTURES_PER_BINDING_REQUEST = 20
_LOCK_TIMEOUT_SECONDS = 5.0
_STALE_LOCK_SECONDS = 120.0
_ALLOWED_ROLES = frozenset({
    "knowledge_admin",
    "project_owner",
    "qa_lead",
    "testops_admin",
    "security_owner",
    "admin",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _actor(actor: dict[str, Any] | None) -> dict[str, str]:
    row = _dict(actor)
    name = _text(
        row.get("name")
        or row.get("actor_ref")
        or row.get("subject")
        or row.get("sub")
        or row.get("id")
        or row.get("username")
        or "ui_fixture_operator",
        limit=160,
    )
    role = _text(row.get("role") or "", limit=64)
    if role not in _ALLOWED_ROLES:
        raise PermissionError(
            "UI upload fixture changes require knowledge_admin, project_owner, "
            "qa_lead, testops_admin, security_owner, or admin"
        )
    return {"name": name, "role": role}


def _actor_ref(actor: dict[str, str]) -> str:
    return f"{actor['name']}:{actor['role']}"


def _paths(project: str, root: Path) -> dict[str, Path]:
    workspace = Path(root) / "platform_workspace" / project
    return {
        "registry": workspace / "ui_upload_fixture_registry.json",
        "project_input": Path(root) / "platform_inputs" / project,
        "candidate": Path(root) / "platform_inputs" / project / INPUT_PREFIX,
        "approved": workspace / APPROVED_PREFIX,
    }


def _default_registry(project: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project,
        "fixtures": [],
        "audit_events": [],
        "updated_at_utc": "",
    }


def _load(project: str, root: Path) -> dict[str, Any]:
    path = _paths(project, root)["registry"]
    if not path.is_file():
        return _default_registry(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("ui_upload_fixture_registry_corrupt") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("project_id") not in {None, "", project}
        or not isinstance(payload.get("fixtures", []), list)
        or not isinstance(payload.get("audit_events", []), list)
    ):
        raise RuntimeError("ui_upload_fixture_registry_schema_invalid")
    payload["project_id"] = project
    payload.setdefault("fixtures", [])
    payload.setdefault("audit_events", [])
    return payload


def _save(project: str, root: Path, registry: dict[str, Any]) -> None:
    path = _paths(project, root)["registry"]
    path.parent.mkdir(parents=True, exist_ok=True)
    registry["schema_version"] = SCHEMA_VERSION
    registry["project_id"] = project
    registry["updated_at_utc"] = _now()
    serialized = json.dumps(registry, ensure_ascii=False, indent=2, default=str)
    fd, temporary = tempfile.mkstemp(
        prefix=".ui-upload-fixtures-",
        suffix=".json.tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _mutation_lock(project: str, root: Path) -> Iterator[None]:
    registry_path = _paths(project, root)["registry"]
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = registry_path.with_name(registry_path.name + ".lock")
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > _STALE_LOCK_SECONDS
            except OSError:
                stale = False
            if stale:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("ui_upload_fixture_registry_busy")
            time.sleep(0.05)
    try:
        os.write(
            descriptor,
            f"pid={os.getpid()} acquired={time.time():.6f}\n".encode("ascii"),
        )
        os.fsync(descriptor)
        yield
    finally:
        try:
            os.close(descriptor)
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _source_file(project: str, root: Path, file_path: str | Path) -> Path:
    paths = _paths(project, root)
    project_input_lexical = _lexical(paths["project_input"])
    raw = Path(file_path).expanduser()
    unresolved = raw if raw.is_absolute() else Path(root) / raw
    lexical = _lexical(unresolved)
    if not _within(lexical, project_input_lexical):
        raise PermissionError("ui_upload_fixture_source_outside_project_inputs")
    if _has_symlink_component(lexical, project_input_lexical):
        raise PermissionError("ui_upload_fixture_symlink_forbidden")
    source = lexical.resolve()
    project_input_resolved = project_input_lexical.resolve()
    if not _within(source, project_input_resolved):
        raise PermissionError("ui_upload_fixture_source_outside_project_inputs")
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError("ui_upload_fixture_source_file_not_found")
    size = int(source.stat().st_size)
    if not 1 <= size <= MAX_FIXTURE_BYTES:
        raise ValueError("ui_upload_fixture_size_invalid")
    return source


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_copy_verified(source: Path, destination: Path, expected_sha256: str) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or destination.is_symlink():
            raise RuntimeError("ui_upload_fixture_immutable_path_invalid")
        if _stream_sha256(destination) != expected_sha256:
            raise RuntimeError("ui_upload_fixture_immutable_path_conflict")
        return int(destination.stat().st_size)
    fd, temporary = tempfile.mkstemp(
        prefix=".ui-upload-fixture-",
        suffix=".tmp",
        dir=str(destination.parent),
    )
    temporary_path = Path(temporary)
    digest = hashlib.sha256()
    total = 0
    try:
        with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FIXTURE_BYTES:
                    raise ValueError("ui_upload_fixture_size_invalid")
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if digest.hexdigest() != expected_sha256:
            raise RuntimeError("ui_upload_fixture_source_changed_during_copy")
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return total


def _record_id(project: str, ref: str, digest: str, generation: int) -> str:
    raw = f"{project}|{ref}|{digest}|generation:{generation}".encode("utf-8")
    return "uif_" + hashlib.sha256(raw).hexdigest()[:20]


def _binding_ref(project: str, approved_ref: str, digest: str) -> str:
    raw = f"{project}|{approved_ref}|{digest}|runtime-binding".encode("utf-8")
    return "uifb_" + hashlib.sha256(raw).hexdigest()[:20]


def _public_record(row: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "fixture_id",
        "binding_ref",
        "fixture_name",
        "ref",
        "namespace",
        "status",
        "authority",
        "sha256",
        "size_bytes",
        "content_type",
        "file_suffix",
        "created_at_utc",
        "created_by",
        "approved_from_fixture_id",
        "revoked_at_utc",
        "revoked_by",
        "revocation_reason",
        "source_name_fingerprint",
        "raw_file_bytes_embedded_in_registry",
        "raw_source_path_embedded_in_registry",
    }
    return {key: value for key, value in row.items() if key in allowed}


def register_upload_fixture(
    project_id: str,
    *,
    file_path: str | Path,
    fixture_name: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor)
    source = _source_file(project, effective_root, file_path)
    before = source.stat()
    digest = _stream_sha256(source)
    after = source.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise RuntimeError("ui_upload_fixture_source_changed_during_hash")
    name = _safe_slug(_text(fixture_name, limit=180) or source.stem, 120)
    suffix = source.suffix.lower()[:20]
    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    with _mutation_lock(project, effective_root):
        registry = _load(project, effective_root)
        generation = len(registry["fixtures"]) + 1
        canonical_filename = f"{name}__{digest[:12]}{suffix}"
        canonical_ref = f"{INPUT_PREFIX}/{canonical_filename}"
        active_exact = [
            row
            for row in registry["fixtures"]
            if row.get("status") == "active"
            and row.get("authority") == "source_registered"
            and row.get("ref") == canonical_ref
            and row.get("sha256") == digest
        ]
        if len(active_exact) > 1:
            raise RuntimeError("ui_upload_fixture_active_identity_ambiguous")
        if active_exact:
            return {
                "ok": True,
                "status": "DUPLICATE_ACTIVE",
                "fixture": _public_record(active_exact[0]),
            }
        historical = any(row.get("ref") == canonical_ref for row in registry["fixtures"])
        if historical:
            canonical_filename = f"{name}__{digest[:12]}__v{generation}{suffix}"
            canonical_ref = f"{INPUT_PREFIX}/{canonical_filename}"
        destination = _paths(project, effective_root)["candidate"] / canonical_filename
        size = _atomic_copy_verified(source, destination, digest)
        now = _now()
        record = {
            "fixture_id": _record_id(project, canonical_ref, digest, generation),
            "binding_ref": "",
            "fixture_name": name,
            "ref": canonical_ref,
            "namespace": INPUT_PREFIX,
            "status": "active",
            "authority": "source_registered",
            "sha256": digest,
            "size_bytes": size,
            "content_type": content_type,
            "file_suffix": suffix,
            "created_at_utc": now,
            "created_by": _actor_ref(clean_actor),
            "source_name_fingerprint": hashlib.sha256(source.name.encode("utf-8")).hexdigest(),
            "raw_file_bytes_embedded_in_registry": False,
            "raw_source_path_embedded_in_registry": False,
        }
        registry["fixtures"].append(record)
        registry["audit_events"].append({
            "event": "register",
            "at_utc": now,
            "actor_ref": _actor_ref(clean_actor),
            "fixture_id": record["fixture_id"],
            "ref": canonical_ref,
            "sha256": digest,
        })
        _save(project, effective_root, registry)
        return {"ok": True, "status": "REGISTERED", "fixture": _public_record(record)}


def approve_upload_fixture(
    project_id: str,
    *,
    fixture_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor)
    with _mutation_lock(project, effective_root):
        registry = _load(project, effective_root)
        source = next(
            (
                row
                for row in registry["fixtures"]
                if row.get("fixture_id") == fixture_id
                and row.get("status") == "active"
                and row.get("authority") == "source_registered"
            ),
            None,
        )
        if not source:
            raise KeyError("active_source_upload_fixture_not_found")
        filename = Path(_text(source.get("ref"))).name
        source_path = _paths(project, effective_root)["candidate"] / filename
        if not source_path.is_file() or source_path.is_symlink():
            raise FileNotFoundError("registered_upload_fixture_bytes_missing")
        digest = _stream_sha256(source_path)
        if digest != source.get("sha256"):
            raise RuntimeError("registered_upload_fixture_hash_drift")
        approved_ref = f"{APPROVED_PREFIX}/{filename}"
        existing = [
            row
            for row in registry["fixtures"]
            if row.get("status") == "active"
            and row.get("authority") == "approved_copy"
            and row.get("approved_from_fixture_id") == fixture_id
            and row.get("sha256") == digest
        ]
        if len(existing) > 1:
            raise RuntimeError("ui_upload_fixture_approved_identity_ambiguous")
        if existing:
            return {
                "ok": True,
                "status": "DUPLICATE_ACTIVE",
                "fixture": _public_record(existing[0]),
            }
        destination = _paths(project, effective_root)["approved"] / filename
        size = _atomic_copy_verified(source_path, destination, digest)
        generation = len(registry["fixtures"]) + 1
        now = _now()
        record = {
            "fixture_id": _record_id(project, approved_ref, digest, generation),
            "binding_ref": _binding_ref(project, approved_ref, digest),
            "fixture_name": source.get("fixture_name"),
            "ref": approved_ref,
            "namespace": APPROVED_PREFIX,
            "status": "active",
            "authority": "approved_copy",
            "sha256": digest,
            "size_bytes": size,
            "content_type": source.get("content_type") or "application/octet-stream",
            "file_suffix": source.get("file_suffix") or "",
            "approved_from_fixture_id": fixture_id,
            "created_at_utc": now,
            "created_by": _actor_ref(clean_actor),
            "source_name_fingerprint": source.get("source_name_fingerprint") or "",
            "raw_file_bytes_embedded_in_registry": False,
            "raw_source_path_embedded_in_registry": False,
        }
        registry["fixtures"].append(record)
        registry["audit_events"].append({
            "event": "approve",
            "at_utc": now,
            "actor_ref": _actor_ref(clean_actor),
            "fixture_id": record["fixture_id"],
            "approved_from_fixture_id": fixture_id,
            "binding_ref": record["binding_ref"],
            "ref": approved_ref,
            "sha256": digest,
        })
        _save(project, effective_root, registry)
        return {"ok": True, "status": "APPROVED", "fixture": _public_record(record)}


def revoke_upload_fixture(
    project_id: str,
    *,
    fixture_id: str,
    reason: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor)
    explanation = _text(reason, limit=500)
    if not explanation:
        raise ValueError("ui_upload_fixture_revocation_reason_required")
    with _mutation_lock(project, effective_root):
        registry = _load(project, effective_root)
        record = next(
            (
                row
                for row in registry["fixtures"]
                if row.get("fixture_id") == fixture_id and row.get("status") == "active"
            ),
            None,
        )
        if not record:
            raise KeyError("active_upload_fixture_not_found")
        now = _now()
        revoked: list[dict[str, Any]] = []
        targets = [record]
        if record.get("authority") == "source_registered":
            targets.extend(
                row
                for row in registry["fixtures"]
                if row.get("status") == "active"
                and row.get("authority") == "approved_copy"
                and row.get("approved_from_fixture_id") == fixture_id
            )
        for target in targets:
            target["status"] = "revoked"
            target["revoked_at_utc"] = now
            target["revoked_by"] = _actor_ref(clean_actor)
            target["revocation_reason"] = explanation
            revoked.append(_public_record(target))
        registry["audit_events"].append({
            "event": "revoke",
            "at_utc": now,
            "actor_ref": _actor_ref(clean_actor),
            "fixture_id": fixture_id,
            "cascade_count": len(revoked) - 1,
            "reason": explanation,
            "bytes_retained_for_audit": True,
        })
        _save(project, effective_root, registry)
        return {
            "ok": True,
            "status": "REVOKED",
            "fixture": revoked[0],
            "revoked_records": revoked,
        }


def list_upload_fixtures(
    project_id: str,
    *,
    root: Path | None = None,
    include_revoked: bool = False,
) -> dict[str, Any]:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    registry = _load(project, effective_root)
    rows = [
        _public_record(row)
        for row in registry["fixtures"]
        if include_revoked or row.get("status") == "active"
    ]
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "project_id": project,
        "fixtures": rows,
        "summary": {
            "active_count": sum(1 for row in registry["fixtures"] if row.get("status") == "active"),
            "revoked_count": sum(1 for row in registry["fixtures"] if row.get("status") == "revoked"),
            "source_registered_count": sum(1 for row in rows if row.get("authority") == "source_registered"),
            "approved_copy_count": sum(1 for row in rows if row.get("authority") == "approved_copy"),
        },
        "raw_file_bytes_embedded": False,
        "raw_source_paths_embedded": False,
    }


def active_approved_upload_fixture(
    project_id: str,
    identity: str,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    registry = _load(project, effective_root)
    matches = [
        row
        for row in registry["fixtures"]
        if row.get("status") == "active"
        and row.get("authority") == "approved_copy"
        and identity in {row.get("fixture_id"), row.get("binding_ref")}
    ]
    if len(matches) != 1:
        return None
    return dict(matches[0])


def approved_upload_fixture_binding(
    project_id: str,
    identity: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    record = active_approved_upload_fixture(project, identity, root=effective_root)
    if not record:
        raise KeyError("active_approved_upload_fixture_not_found")
    filename = Path(_text(record.get("ref"))).name
    approved_root = _paths(project, effective_root)["approved"].resolve()
    path = (approved_root / filename).resolve()
    if not _within(path, approved_root) or not path.is_file() or path.is_symlink():
        raise RuntimeError("approved_upload_fixture_path_invalid")
    size = int(path.stat().st_size)
    if size != int(record.get("size_bytes") or -1):
        raise RuntimeError("approved_upload_fixture_size_drift")
    digest = _stream_sha256(path)
    if digest != record.get("sha256"):
        raise RuntimeError("approved_upload_fixture_hash_drift")
    relative = path.relative_to(effective_root.resolve())
    return {
        "approved": True,
        "status": "approved",
        "fixture_id": record["fixture_id"],
        "binding_ref": record["binding_ref"],
        "file_path": relative.as_posix(),
        "sha256": digest,
        "size_bytes": size,
        "content_type": record.get("content_type") or "application/octet-stream",
        "raw_file_content_included": False,
        "raw_source_path_included": False,
    }


def materialize_upload_fixture_bindings(
    project_id: str,
    identities: list[str],
    *,
    root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    normalized = list(dict.fromkeys(_text(value, limit=160) for value in identities if _text(value, limit=160)))
    if len(normalized) > MAX_FIXTURES_PER_BINDING_REQUEST:
        raise ValueError("ui_upload_fixture_binding_request_limit_exceeded")
    bindings: dict[str, dict[str, Any]] = {}
    for identity in normalized:
        binding = approved_upload_fixture_binding(project_id, identity, root=root)
        key = _text(binding.get("binding_ref"), limit=160)
        if not key or key in bindings:
            raise RuntimeError("ui_upload_fixture_binding_identity_ambiguous")
        bindings[key] = binding
    return bindings


def operate_upload_fixture_registry(
    project_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = _dict(payload)
    operation = _text(action).lower() or "list"
    if operation in {"list", "view"}:
        return list_upload_fixtures(
            project_id,
            root=root,
            include_revoked=bool(data.get("include_revoked")),
        )
    if operation == "register":
        return register_upload_fixture(
            project_id,
            file_path=_text(data.get("file_path"), limit=2000),
            fixture_name=_text(data.get("fixture_name"), limit=180),
            root=root,
            actor=actor,
        )
    if operation == "approve":
        return approve_upload_fixture(
            project_id,
            fixture_id=_text(data.get("fixture_id"), limit=160),
            root=root,
            actor=actor,
        )
    if operation == "revoke":
        return revoke_upload_fixture(
            project_id,
            fixture_id=_text(data.get("fixture_id"), limit=160),
            reason=_text(data.get("reason"), limit=500),
            root=root,
            actor=actor,
        )
    if operation in {"binding", "resolve_binding"}:
        binding = approved_upload_fixture_binding(
            project_id,
            _text(data.get("fixture_id") or data.get("binding_ref"), limit=160),
            root=root,
        )
        return {"ok": True, "status": "RESOLVED", "binding": binding}
    raise ValueError(
        "unsupported upload fixture action; use list, register, approve, revoke, or binding"
    )


__all__ = [
    "APPROVED_PREFIX",
    "INPUT_PREFIX",
    "MAX_FIXTURE_BYTES",
    "SCHEMA_VERSION",
    "active_approved_upload_fixture",
    "approve_upload_fixture",
    "approved_upload_fixture_binding",
    "list_upload_fixtures",
    "materialize_upload_fixture_bindings",
    "operate_upload_fixture_registry",
    "register_upload_fixture",
    "revoke_upload_fixture",
]
