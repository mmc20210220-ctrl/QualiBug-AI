"""Governed source-bound UI upload scenarios.

A scenario is executable authority, not a free-form browser macro.  Registration
accepts only explicit source identities and deterministic fields, builds one formal
UI request, validates every referenced upload fixture, and records the exact source
version used.  Approval creates a new immutable runtime identity; revocation removes
authority while retaining audit history.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from .enterprise_knowledge_center._common import ROOT, _safe_project_id
from .enterprise_knowledge_center._utils import _now, _safe_slug
from .enterprise_source_registry import list_source_assets
from .professional_ui_interaction_privacy_guard import EVIDENCE_POLICY
from .professional_ui_persistent_cleanup_probe import EQUIVALENCE_SCOPE
from .ui_upload_fixture_registry import approved_upload_fixture_binding

SCHEMA_VERSION = "qualibug.ui-upload-scenario-registry.v1"
MAX_SCENARIOS_PER_RUN = 20
MAX_FIXTURES_PER_SCENARIO = 10
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
        or "ui_scenario_operator",
        limit=160,
    )
    role = _text(row.get("role"), limit=64)
    if role not in _ALLOWED_ROLES:
        raise PermissionError(
            "UI upload scenario changes require knowledge_admin, project_owner, "
            "qa_lead, testops_admin, security_owner, or admin"
        )
    return {"name": name, "role": role}


def _actor_ref(actor: dict[str, str]) -> str:
    return f"{actor['name']}:{actor['role']}"


def _paths(project: str, root: Path) -> dict[str, Path]:
    workspace = Path(root) / "platform_workspace" / project
    return {"registry": workspace / "ui_upload_scenario_registry.json"}


def _default_registry(project: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project,
        "scenarios": [],
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
        raise RuntimeError("ui_upload_scenario_registry_corrupt") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("project_id") not in {None, "", project}
        or not isinstance(payload.get("scenarios"), list)
        or not isinstance(payload.get("audit_events"), list)
    ):
        raise RuntimeError("ui_upload_scenario_registry_schema_invalid")
    payload["project_id"] = project
    return payload


def _save(project: str, root: Path, registry: dict[str, Any]) -> None:
    path = _paths(project, root)["registry"]
    path.parent.mkdir(parents=True, exist_ok=True)
    registry["schema_version"] = SCHEMA_VERSION
    registry["project_id"] = project
    registry["updated_at_utc"] = _now()
    serialized = json.dumps(registry, ensure_ascii=False, indent=2, default=str)
    fd, temporary = tempfile.mkstemp(
        prefix=".ui-upload-scenarios-",
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
                str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > _STALE_LOCK_SECONDS
            except OSError:
                stale = False
            if stale:
                lock_path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("ui_upload_scenario_registry_busy")
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
            lock_path.unlink(missing_ok=True)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _source_identity(project: str, root: Path, source_id: str) -> dict[str, str]:
    identity = _text(source_id, limit=160)
    matches = [
        row
        for row in list_source_assets(project, root=root)
        if _text(row.get("source_id"), limit=160) == identity
    ]
    if len(matches) != 1:
        raise KeyError("active_enterprise_source_not_found")
    row = matches[0]
    source_hash = _text(row.get("latest_source_hash"), limit=64).lower()
    version_id = _text(row.get("latest_version_id"), limit=160)
    if not source_hash or not version_id:
        raise RuntimeError("enterprise_source_identity_incomplete")
    return {
        "source_id": identity,
        "source_hash": source_hash,
        "source_version_id": version_id,
        "source_type": _text(row.get("source_type"), limit=80),
    }


def _pure_origin(value: str) -> str:
    normalized = _text(value, limit=2000).rstrip("/")
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ui_upload_scenario_frame_origin_invalid")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _required(payload: dict[str, Any], key: str, *, limit: int = 1000) -> str:
    value = _text(payload.get(key), limit=limit)
    if not value:
        raise ValueError(f"ui_upload_scenario_{key}_required")
    return value


def _fixture_refs(project: str, root: Path, raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("ui_upload_scenario_fixture_binding_refs_not_list")
    refs: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            raise ValueError(
                f"ui_upload_scenario_fixture_binding_ref_not_string:{index}"
            )
        ref = _text(value, limit=160)
        if not ref or ref in refs:
            raise ValueError("ui_upload_scenario_fixture_binding_ref_invalid")
        binding = approved_upload_fixture_binding(project, ref, root=root)
        canonical_ref = _text(binding.get("binding_ref"), limit=160)
        if canonical_ref != ref:
            raise ValueError("ui_upload_scenario_fixture_binding_ref_not_canonical")
        refs.append(ref)
    if not 1 <= len(refs) <= MAX_FIXTURES_PER_SCENARIO:
        raise ValueError("ui_upload_scenario_fixture_count_invalid")
    return refs


def _frame_fields(payload: dict[str, Any]) -> dict[str, str]:
    selector = _text(payload.get("frame_selector"), limit=500)
    origin = _text(payload.get("frame_origin"), limit=2000)
    if bool(selector) != bool(origin):
        raise ValueError("ui_upload_scenario_frame_selector_origin_required_together")
    if not selector:
        return {}
    return {"frame_selector": selector, "frame_origin": _pure_origin(origin)}


def build_upload_scenario_contract(
    project_id: str,
    payload: dict[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    data = _dict(payload)
    source = _source_identity(
        project, effective_root, _required(data, "source_id", limit=160)
    )
    fixture_refs = _fixture_refs(
        project, effective_root, data.get("fixture_binding_refs")
    )
    title = _required(data, "title", limit=180)
    operation_ref = _required(data, "operation_ref", limit=240)
    actor_ref = _required(data, "actor_ref", limit=240)
    source_locator = _required(data, "source_locator", limit=500)
    start_url = _required(data, "start_url", limit=2000)
    upload_selector = _required(data, "upload_selector", limit=500)
    assertion_selector = _required(data, "assertion_selector", limit=500)
    assertion_text = _required(data, "assertion_text", limit=4000)
    rendered_probe_selector = _required(
        data, "rendered_probe_selector", limit=500
    )
    persistent_probe_url = _required(data, "persistent_probe_url", limit=2000)
    persistent_json_pointer = _required(
        data, "persistent_json_pointer", limit=500
    )
    if not persistent_json_pointer.startswith("/"):
        raise ValueError("ui_upload_scenario_persistent_json_pointer_invalid")
    frame = _frame_fields(data)
    request_seed = {
        "project": project,
        "source": source,
        "title": title,
        "operation_ref": operation_ref,
        "actor_ref": actor_ref,
        "source_locator": source_locator,
        "start_url": start_url,
        "upload_selector": upload_selector,
        "assertion_selector": assertion_selector,
        "assertion_text": assertion_text,
        "rendered_probe_selector": rendered_probe_selector,
        "persistent_probe_url": persistent_probe_url,
        "persistent_json_pointer": persistent_json_pointer,
        "fixture_refs": fixture_refs,
        "frame": frame,
    }
    request_id = "ui_upload_" + _digest(request_seed)[:20]
    source_ref = {
        "source_id": source["source_id"],
        "version": source["source_version_id"],
        "locator": source_locator,
        "kind": "formal_ui_upload_scenario",
        "quote_hash": "",
    }
    request = {
        "request_id": request_id,
        "title": title,
        "provider": "playwright_browser_plan",
        "start_url": start_url,
        "execution_mode": "approved_sandbox_write",
        "operation_ref": operation_ref,
        "actor_ref": actor_ref,
        "source_refs": [source_ref],
        "success_criteria": {"action": "expect_text"},
        "metadata": {
            "source_declared": True,
            "source_id": source["source_id"],
            "source_version_id": source["source_version_id"],
            "source_locator": source_locator,
            "ui_upload_scenario_registry": True,
            "auto_generated": False,
        },
        "browser_plan": {
            "execution_mode": "approved_sandbox_write",
            "write_approved": True,
            "interaction_contract": {
                "cleanup_strategy": "browser_compensation",
                "equivalence": "source_declared_state_probes",
                "equivalence_scope": EQUIVALENCE_SCOPE,
                "target_scope": "approved_nonproduction_target",
                "evidence_policy": EVIDENCE_POLICY,
            },
            "state_probes": [
                {
                    "probe_id": "upload_rendered_state",
                    "property": "text",
                    "selector": rendered_probe_selector,
                    **frame,
                },
                {
                    "probe_id": "upload_persistent_state",
                    "property": "http_json_pointer",
                    "method": "GET",
                    "url": persistent_probe_url,
                    "json_pointer": persistent_json_pointer,
                    "expected_status_class": 2,
                    "max_response_bytes": 1_000_000,
                },
            ],
            "steps": [
                {
                    "phase": "setup",
                    "action": "goto",
                    "url": start_url,
                    "wait_until": "networkidle",
                },
                {
                    "phase": "treatment",
                    "action": "set_input_files",
                    "selector": upload_selector,
                    "file_refs": fixture_refs,
                    **frame,
                },
                {
                    "phase": "assertion",
                    "action": "expect_text",
                    "selector": assertion_selector,
                    "text": assertion_text,
                    "match": "equals",
                    **frame,
                },
                {
                    "phase": "cleanup",
                    "action": "set_input_files",
                    "selector": upload_selector,
                    "file_refs": [],
                    **frame,
                },
            ],
        },
    }
    return {
        "schema_version": "qualibug.ui-formal-contract.v2",
        "contract_id": request_id,
        "title": title,
        "operation_ref": operation_ref,
        "actor_ref": actor_ref,
        "ui_request": request,
        "source_refs": [source_ref],
        "source_id": source["source_id"],
        "source_locator": source_locator,
        "fixture_binding_refs": fixture_refs,
        "source_identity": source,
        "status": "accepted",
        "derivation": "explicit",
        "confidence": 1.0,
    }


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(record.get(key))
        for key in (
            "scenario_id",
            "scenario_ref",
            "title",
            "status",
            "authority",
            "source_id",
            "source_version_id",
            "source_hash",
            "source_locator",
            "contract_id",
            "contract_sha256",
            "fixture_binding_refs",
            "created_at_utc",
            "created_by",
            "approved_at_utc",
            "approved_by",
            "approved_from_scenario_id",
            "revoked_at_utc",
            "revoked_by",
            "revocation_reason",
        )
        if record.get(key) not in (None, "")
    }


def register_upload_scenario(
    project_id: str,
    payload: dict[str, Any],
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor)
    contract = build_upload_scenario_contract(project, payload, root=effective_root)
    contract_hash = _digest(contract)
    with _mutation_lock(project, effective_root):
        registry = _load(project, effective_root)
        active_exact = [
            row
            for row in registry["scenarios"]
            if row.get("status") == "active"
            and row.get("authority") == "source_declared_candidate"
            and row.get("contract_sha256") == contract_hash
        ]
        if len(active_exact) > 1:
            raise RuntimeError("ui_upload_scenario_active_identity_ambiguous")
        if active_exact:
            return {
                "ok": True,
                "status": "DUPLICATE_ACTIVE",
                "scenario": _public_record(active_exact[0]),
            }
        generation = len(registry["scenarios"]) + 1
        scenario_id = "uisc_" + _digest(
            [project, contract_hash, generation, secrets.token_hex(8)]
        )[:20]
        source = _dict(contract.get("source_identity"))
        now = _now()
        record = {
            "scenario_id": scenario_id,
            "scenario_ref": "",
            "title": _text(contract.get("title"), limit=180),
            "status": "active",
            "authority": "source_declared_candidate",
            "source_id": _text(source.get("source_id"), limit=160),
            "source_version_id": _text(source.get("source_version_id"), limit=160),
            "source_hash": _text(source.get("source_hash"), limit=64),
            "source_locator": _text(contract.get("source_locator"), limit=500),
            "contract_id": _text(contract.get("contract_id"), limit=160),
            "contract_sha256": contract_hash,
            "fixture_binding_refs": list(contract.get("fixture_binding_refs") or []),
            "contract": contract,
            "created_at_utc": now,
            "created_by": _actor_ref(clean_actor),
        }
        registry["scenarios"].append(record)
        registry["audit_events"].append({
            "event": "register",
            "at_utc": now,
            "actor_ref": _actor_ref(clean_actor),
            "scenario_id": scenario_id,
            "contract_sha256": contract_hash,
            "source_id": record["source_id"],
            "source_version_id": record["source_version_id"],
        })
        _save(project, effective_root, registry)
        return {"ok": True, "status": "REGISTERED", "scenario": _public_record(record)}


def _verify_candidate(project: str, root: Path, record: dict[str, Any]) -> dict[str, Any]:
    source = _source_identity(project, root, _text(record.get("source_id"), limit=160))
    if (
        source["source_hash"] != _text(record.get("source_hash"), limit=64)
        or source["source_version_id"]
        != _text(record.get("source_version_id"), limit=160)
    ):
        raise RuntimeError("ui_upload_scenario_source_version_changed")
    contract = copy.deepcopy(_dict(record.get("contract")))
    if not contract or _digest(contract) != record.get("contract_sha256"):
        raise RuntimeError("ui_upload_scenario_contract_hash_drift")
    for ref in _list(record.get("fixture_binding_refs")):
        binding = approved_upload_fixture_binding(project, _text(ref, limit=160), root=root)
        if _text(binding.get("binding_ref"), limit=160) != _text(ref, limit=160):
            raise RuntimeError("ui_upload_scenario_fixture_binding_drift")
    return contract


def approve_upload_scenario(
    project_id: str,
    *,
    scenario_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor)
    with _mutation_lock(project, effective_root):
        registry = _load(project, effective_root)
        candidate = next(
            (
                row
                for row in registry["scenarios"]
                if row.get("scenario_id") == scenario_id
                and row.get("status") == "active"
                and row.get("authority") == "source_declared_candidate"
            ),
            None,
        )
        if not candidate:
            raise KeyError("active_source_upload_scenario_not_found")
        active_approved = [
            row
            for row in registry["scenarios"]
            if row.get("status") == "active"
            and row.get("authority") == "approved_copy"
            and row.get("approved_from_scenario_id") == scenario_id
        ]
        if len(active_approved) > 1:
            raise RuntimeError("ui_upload_scenario_active_approval_ambiguous")
        if active_approved:
            return {
                "ok": True,
                "status": "DUPLICATE_ACTIVE",
                "scenario": _public_record(active_approved[0]),
            }
        contract = _verify_candidate(project, effective_root, candidate)
        generation = len(registry["scenarios"]) + 1
        scenario_ref = "uisr_" + _digest(
            [project, scenario_id, candidate["contract_sha256"], generation, secrets.token_hex(16)]
        )[:20]
        now = _now()
        approved = {
            **copy.deepcopy(candidate),
            "scenario_id": "uisa_" + _digest([scenario_ref, generation])[:20],
            "scenario_ref": scenario_ref,
            "authority": "approved_copy",
            "approved_from_scenario_id": scenario_id,
            "approved_at_utc": now,
            "approved_by": _actor_ref(clean_actor),
            "contract": contract,
        }
        registry["scenarios"].append(approved)
        registry["audit_events"].append({
            "event": "approve",
            "at_utc": now,
            "actor_ref": _actor_ref(clean_actor),
            "scenario_id": approved["scenario_id"],
            "scenario_ref": scenario_ref,
            "approved_from_scenario_id": scenario_id,
            "contract_sha256": approved["contract_sha256"],
        })
        _save(project, effective_root, registry)
        return {"ok": True, "status": "APPROVED", "scenario": _public_record(approved)}


def revoke_upload_scenario(
    project_id: str,
    *,
    scenario_id: str,
    reason: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor)
    explanation = _text(reason, limit=500)
    if not explanation:
        raise ValueError("ui_upload_scenario_revocation_reason_required")
    with _mutation_lock(project, effective_root):
        registry = _load(project, effective_root)
        record = next(
            (
                row
                for row in registry["scenarios"]
                if row.get("scenario_id") == scenario_id
                and row.get("status") == "active"
            ),
            None,
        )
        if not record:
            raise KeyError("active_upload_scenario_not_found")
        targets = [record]
        if record.get("authority") == "source_declared_candidate":
            targets.extend(
                row
                for row in registry["scenarios"]
                if row.get("status") == "active"
                and row.get("authority") == "approved_copy"
                and row.get("approved_from_scenario_id") == scenario_id
            )
        now = _now()
        revoked: list[dict[str, Any]] = []
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
            "scenario_id": scenario_id,
            "cascade_count": len(revoked) - 1,
            "reason": explanation,
        })
        _save(project, effective_root, registry)
        return {
            "ok": True,
            "status": "REVOKED",
            "scenario": revoked[0],
            "revoked_records": revoked,
        }


def list_upload_scenarios(
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
        for row in registry["scenarios"]
        if include_revoked or row.get("status") == "active"
    ]
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "project_id": project,
        "scenarios": rows,
        "summary": {
            "active_count": sum(
                1 for row in registry["scenarios"] if row.get("status") == "active"
            ),
            "revoked_count": sum(
                1 for row in registry["scenarios"] if row.get("status") == "revoked"
            ),
            "candidate_count": sum(
                1
                for row in rows
                if row.get("authority") == "source_declared_candidate"
            ),
            "approved_count": sum(
                1 for row in rows if row.get("authority") == "approved_copy"
            ),
        },
        "raw_fixture_paths_embedded": False,
        "raw_fixture_content_embedded": False,
    }


def approved_upload_scenario(
    project_id: str,
    identity: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    effective_root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    registry = _load(project, effective_root)
    matches = [
        row
        for row in registry["scenarios"]
        if row.get("status") == "active"
        and row.get("authority") == "approved_copy"
        and identity in {row.get("scenario_id"), row.get("scenario_ref")}
    ]
    if len(matches) != 1:
        raise KeyError("active_approved_upload_scenario_not_found")
    record = matches[0]
    contract = _verify_candidate(project, effective_root, record)
    request = copy.deepcopy(_dict(contract.get("ui_request")))
    if not request:
        raise RuntimeError("ui_upload_scenario_request_missing")
    return {
        "scenario_id": record["scenario_id"],
        "scenario_ref": record["scenario_ref"],
        "contract_id": record["contract_id"],
        "contract_sha256": record["contract_sha256"],
        "source_id": record["source_id"],
        "source_version_id": record["source_version_id"],
        "source_hash": record["source_hash"],
        "fixture_binding_refs": list(record.get("fixture_binding_refs") or []),
        "ui_execution_request": request,
        "registry_derived": True,
        "raw_fixture_paths_embedded": False,
        "raw_fixture_content_embedded": False,
    }


def materialize_upload_scenarios(
    project_id: str,
    identities: list[str],
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    normalized = list(
        dict.fromkeys(
            _text(value, limit=160)
            for value in identities
            if _text(value, limit=160)
        )
    )
    if len(normalized) > MAX_SCENARIOS_PER_RUN:
        raise ValueError("ui_upload_scenario_run_limit_exceeded")
    return [
        approved_upload_scenario(project_id, identity, root=root)
        for identity in normalized
    ]


def operate_upload_scenario_registry(
    project_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = _dict(payload)
    operation = _text(action, limit=40).lower() or "list"
    if operation in {"list", "view"}:
        return list_upload_scenarios(
            project_id,
            root=root,
            include_revoked=bool(data.get("include_revoked")),
        )
    if operation == "register":
        return register_upload_scenario(
            project_id, data, root=root, actor=actor
        )
    if operation == "approve":
        return approve_upload_scenario(
            project_id,
            scenario_id=_text(data.get("scenario_id"), limit=160),
            root=root,
            actor=actor,
        )
    if operation == "revoke":
        return revoke_upload_scenario(
            project_id,
            scenario_id=_text(data.get("scenario_id"), limit=160),
            reason=_text(data.get("reason"), limit=500),
            root=root,
            actor=actor,
        )
    raise ValueError("ui_upload_scenario_action_unsupported")


__all__ = [
    "MAX_SCENARIOS_PER_RUN",
    "SCHEMA_VERSION",
    "approved_upload_scenario",
    "approve_upload_scenario",
    "build_upload_scenario_contract",
    "list_upload_scenarios",
    "materialize_upload_scenarios",
    "operate_upload_scenario_registry",
    "register_upload_scenario",
    "revoke_upload_scenario",
]
