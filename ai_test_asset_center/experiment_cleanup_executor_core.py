"""Cleanup compensation orchestration for experiment execution.

Extracted from ``experiment_executor.execute_one_experiment``. Runs governed
write cleanup and fixture compensation in reverse order, then always returns
so observers / oracle evaluation can proceed. Predicate helpers remain in
``experiment_cleanup``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .contract_oracles import build_contract_evidence_receipt
from .experiment_cleanup import (
    _cleanup_restores_governed_write,
    _governance_audit_receipt_id,
    _governed_write_attempts,
    _governed_write_changed_state,
    _governed_write_reached_transport,
    _rejected_writes_left_state_unchanged,
)
from .experiment_runtime_support import (
    _WRITE_METHODS,
    _declared_observation_path,
    _dict,
    _documented_routes,
    _inverse_delta_cleanup_body,
    _list,
    _resolve_token,
    _text,
    _unresolved_body_placeholders,
    _unresolved_path_placeholders,
)
from .real_id_resolver import (
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
)
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    materialize_path as _materialize_path,
    runtime_cleanup_paths as _runtime_cleanup_paths,
)
from .cleanup_execution_receipt import build_cleanup_execution_receipt
from .sandbox_write_executor import (
    _restore_payload,
    execute_governed_control_write,
    sandbox_write_allowed,
)


_LOGGER = logging.getLogger(__name__)


def _cleanup_actor_for_write_step(
    source_step: dict[str, Any],
    *,
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
) -> tuple[str, dict[str, Any], str]:
    """Use the write's own actor so actor-scoped collections restore correctly."""
    actor_ref = _text(source_step.get("actor_ref"))
    if not actor_ref or actor_ref not in actors:
        raise ValueError(
            f"cleanup_actor_identity_missing:{actor_ref or '<empty>'}"
        )
    actor = actors[actor_ref]
    token = _resolve_token(actor, tokens)
    return actor_ref, actor, token


def _primary_write_step_for_readback(
    steps_out: list[dict[str, Any]],
) -> dict[str, Any]:
    """Last accepted control/treatment write that carries an observation path."""
    for step in reversed(steps_out):
        if not isinstance(step, dict):
            continue
        if _text(step.get("phase")) not in {"control", "treatment"}:
            continue
        gov = _dict(step.get("governance_receipt"))
        if gov.get("accepted") is not True:
            continue
        path = _text(
            step.get("observation_path")
            or gov.get("observation_path")
            or step.get("path")
        )
        if path.startswith("/") and not path_has_placeholders(path):
            return step
    return {}


def _normalize_after_cleanup_obs(
    *,
    status_code: int,
    body: Any,
    path: str,
    source: str,
) -> dict[str, Any]:
    return {
        "status_code": int(status_code or 0),
        "status": int(status_code or 0),
        "body": body,
        "path": _text(path),
        "phase": "after_cleanup",
        "source": source,
    }


def _record_after_cleanup_seal_block(
    observations: dict[str, Any],
    *,
    reason_code: str,
    detail: str = "",
) -> dict[str, Any]:
    """Record why after-cleanup sealing did not produce an observation (fail visible)."""
    block = {
        "status": "BLOCKED",
        "reason_code": _text(reason_code) or "AFTER_CLEANUP_SEAL_BLOCKED",
        "detail": _text(detail),
    }
    observations["after_cleanup_observation_seal"] = block
    return block


def seal_after_cleanup_observation(
    *,
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    base_url: str,
    root: Path,
    project: str,
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    """Materialize post-cleanup business-state readback for equivalence.

    Prefer an already-present cleanup-phase governance ``after`` snapshot.
    Otherwise re-read the write's declared observation_path with the write actor.
    Never invent a path and never treat the cleanup write response body as state.
    Blocking reasons are written to ``after_cleanup_observation_seal``.
    """
    existing = _dict(observations.get("after_cleanup_observation"))
    if existing and (
        existing.get("body") is not None
        or int(existing.get("status_code") or existing.get("status") or 0) > 0
    ):
        observations.pop("after_cleanup_observation_seal", None)
        return existing

    # Prefer real cleanup-step governance after snapshots already captured.
    for step in reversed(steps_out):
        if not isinstance(step, dict) or _text(step.get("phase")) != "cleanup":
            continue
        gov = _dict(step.get("governance_receipt"))
        after = _dict(gov.get("after"))
        if after and int(after.get("status") or 0) > 0:
            sealed = _normalize_after_cleanup_obs(
                status_code=int(after.get("status") or 0),
                body=after.get("body"),
                path=_text(
                    gov.get("observation_path")
                    or step.get("observation_path")
                    or step.get("path")
                ),
                source="cleanup_step_governance_after",
            )
            observations["after_cleanup_observation"] = sealed
            observations.pop("after_cleanup_observation_seal", None)
            return sealed

    write_step = _primary_write_step_for_readback(steps_out)
    if not write_step:
        _record_after_cleanup_seal_block(
            observations,
            reason_code="AFTER_CLEANUP_WRITE_STEP_MISSING",
            detail="no_accepted_control_or_treatment_write_with_observation_path",
        )
        return {}
    gov = _dict(write_step.get("governance_receipt"))
    observation_path = _text(
        write_step.get("observation_path")
        or gov.get("observation_path")
        or write_step.get("path")
    )
    if not observation_path.startswith("/") or path_has_placeholders(observation_path):
        _record_after_cleanup_seal_block(
            observations,
            reason_code="AFTER_CLEANUP_OBSERVATION_PATH_UNRESOLVED",
            detail=observation_path or "empty_path",
        )
        return {}
    try:
        _actor_ref, _actor, token = _cleanup_actor_for_write_step(
            write_step, actors=actors, tokens=tokens
        )
    except ValueError as exc:
        _record_after_cleanup_seal_block(
            observations,
            reason_code="AFTER_CLEANUP_ACTOR_UNRESOLVED",
            detail=str(exc),
        )
        return {}
    allowed, deny_reason = sandbox_write_allowed(
        root=root,
        project=project,
        runtime_contract=runtime_contract,
        actor_token=token,
        actor_identity=_text(_actor.get("role") or _actor_ref),
    )
    # Readback is a GET; still require the actor to be sandbox-allowed for the
    # campaign target so we never probe undeclared environments.
    if not allowed:
        _record_after_cleanup_seal_block(
            observations,
            reason_code="AFTER_CLEANUP_SANDBOX_DENIED",
            detail=_text(deny_reason),
        )
        return {}
    base = _text(base_url).rstrip("/")
    if not base:
        _record_after_cleanup_seal_block(
            observations,
            reason_code="AFTER_CLEANUP_BASE_URL_MISSING",
            detail="base_url_empty",
        )
        return {}
    # Use the sandbox HTTP transport (same authority as governed writes' reads).
    from .sandbox_write_executor_base import _http_request as _governed_http_get

    raw = _governed_http_get("GET", base + observation_path, token=token)
    sealed = _normalize_after_cleanup_obs(
        status_code=int(raw.get("status") or 0),
        body=raw.get("body"),
        path=observation_path,
        source="post_cleanup_readback",
    )
    # HTTP transport errors yield status 0 — do not seal empty/indeterminate rows.
    if int(sealed.get("status_code") or 0) <= 0 and sealed.get("body") is None:
        _record_after_cleanup_seal_block(
            observations,
            reason_code="AFTER_CLEANUP_READBACK_TRANSPORT_FAILED",
            detail=_text(raw.get("error") or raw.get("detail") or "status_0_empty_body"),
        )
        return {}
    observations["after_cleanup_observation"] = sealed
    observations.pop("after_cleanup_observation_seal", None)
    # This GET crosses the evaluator observation gateway under the same
    # correlation headers as the experiment. Emit an explicit cleanup-phase
    # step so operational receipts count it; otherwise attestation sees
    # trusted_observation_target_request_count_mismatch.
    status_code = int(sealed.get("status_code") or 0)
    steps_out.append(
        {
            "phase": "cleanup",
            "method": "GET",
            "path": observation_path,
            "observation_path": observation_path,
            "status_code": status_code,
            "body": sealed.get("body"),
            "governance_receipt": {
                # Observation-only: must not count as an accepted cleanup write.
                "accepted": False,
                "status": "executed",
                "reason": "post_cleanup_readback",
                "method": "GET",
                "path": observation_path,
                "observation_path": observation_path,
                "before": {},
                "write": {},
                "after": {
                    "status": status_code,
                    "body": sealed.get("body"),
                },
                "http_attempt_count": 1,
                "write_request_attempt_count": 0,
                "production_http_requests": 0,
            },
        }
    )
    return sealed


def _append_adapter_cleanup_runtime_step(
    *,
    steps_out: list[dict[str, Any]],
    cleanup_subject_id: str,
    adapter_receipt: dict[str, Any],
    after_cleanup_obs: dict[str, Any],
) -> None:
    """Record adapter cleanup as a real cleanup-phase runtime step.

    Without this row, cleanup_execution_receipt sees zero cleanup steps and
    equivalence stays permanently INDETERMINATE even when DB delete succeeded.
    """
    cleaned = _text(adapter_receipt.get("status")).upper() == "CLEANED"
    after_payload = {}
    if after_cleanup_obs:
        after_payload = {
            "status": int(
                after_cleanup_obs.get("status_code")
                or after_cleanup_obs.get("status")
                or 0
            ),
            "body": after_cleanup_obs.get("body"),
        }
    steps_out.append(
        {
            "phase": "cleanup",
            "cleanup_subject_id": cleanup_subject_id,
            "method": "ADAPTER_DB_SQL",
            "path": _text(after_cleanup_obs.get("path")),
            "observation_path": _text(after_cleanup_obs.get("path")),
            "status_code": 200 if cleaned else 0,
            "body": adapter_receipt,
            "adapter_cleanup_receipt": dict(adapter_receipt),
            "governance_receipt": {
                "accepted": cleaned,
                "status": "executed" if cleaned else "failed",
                "reason": _text(adapter_receipt.get("reason_code")) or (
                    "adapter_cleanup_cleaned" if cleaned else "adapter_cleanup_failed"
                ),
                "method": "ADAPTER_DB_SQL",
                "path": _text(after_cleanup_obs.get("path")),
                "observation_path": _text(after_cleanup_obs.get("path")),
                "before": {},
                "write": {
                    "status": 200 if cleaned else 0,
                    "body": {
                        "rows_deleted": int(adapter_receipt.get("rows_deleted") or 0),
                        "rows_updated": int(adapter_receipt.get("rows_updated") or 0),
                        "table": _text(adapter_receipt.get("table")),
                        "status": _text(adapter_receipt.get("status")),
                        "mode": _text(adapter_receipt.get("mode")),
                    },
                },
                "after": after_payload,
                # Adapter DB cleanup is not HTTP transport. Post-cleanup HTTP
                # readback is recorded as its own step by
                # seal_after_cleanup_observation when that GET is issued.
                "http_attempt_count": 0,
                "write_request_attempt_count": 0,
                "production_http_requests": 0,
            },
        }
    )


def _write_step_for_cleanup_path(
    *,
    path_template: str,
    cleanup_path: str,
    steps_out: list[dict[str, Any]],
    compensates_operation_ref: str = "",
) -> dict[str, Any]:
    """Map a materialized cleanup path back to the write that created it."""
    for step in reversed(steps_out):
        if _text(step.get("phase")) not in {"control", "treatment"}:
            continue
        if compensates_operation_ref and _text(step.get("operation_ref")) != compensates_operation_ref:
            continue
        targets, missing = _runtime_cleanup_paths(path_template, [step])
        if missing or not targets:
            continue
        for candidate_path, _bindings in targets:
            if candidate_path == cleanup_path:
                return step
    return {}


def _project_database_dsn(root: Path, project: str) -> tuple[str, str]:
    """The DSN the operator declared for this project.

    Returns ``(dsn, error_code)``. ``error_code`` is empty both when a DSN was
    resolved and when none was declared at all -- the caller's own
    ``CLEANUP_DB_CONNECTION_NOT_CONFIGURED`` already covers "not declared" and
    must stay distinguishable from a real failure. ``error_code`` is set only
    when credentials WERE declared but could not be used, e.g.
    ``CREDENTIAL_DECRYPT_FAILED``. Collapsing that into an empty string made a
    decrypt failure indistinguishable from "no database configured" -- two
    different problems with two different fixes -- so the caller must branch
    on this before ever reaching the "not configured" reason code.

    Resolution order (never invents secrets):
    1. ``QUALIBUG_DB_DSN`` operator override
    2. Declared ``multi_service_config.json`` db block, decrypting ``enc$`` with
       the same local/env key the credential-save path uses
    3. Per-field env overrides (``QUALIBUG_DB_*`` / ``QUALIBUG_SVC_<NAME>_DB_*``)
       when the on-disk password cannot be decrypted
    """
    import json as _json
    import os as _os
    import re as _re

    env_dsn = _text(_os.environ.get("QUALIBUG_DB_DSN"))
    if env_dsn:
        return env_dsn, ""

    # Same key file the credentials handler provisions when saving secrets.
    # Without this, adapter cleanup saw enc$ blobs while HTTP writes still
    # succeeded via test_accounts tokens that never need decrypt.
    # Only *load* an existing key here -- never provision a new one from a
    # cleanup DSN lookup against an arbitrary root (tests use /nonexistent).
    key_path = (
        Path(root) / "platform_workspace" / ".secrets" / "credential_encryption.key"
    )
    if key_path.is_file():
        try:
            from .private_pilot_credentials_patch import (
                ensure_local_credential_encryption_key,
            )

            ensure_local_credential_encryption_key(Path(root))
        except Exception as exc:
            # Decrypt below still fails closed; record the earlier key-loading
            # failure so operators can distinguish its root cause.
            _LOGGER.warning(
                "cleanup_credential_key_load_failed project=%s error=%s",
                project,
                type(exc).__name__,
            )

    path = Path(root) / "platform_workspace" / str(project) / "multi_service_config.json"
    if not path.exists():
        payload = {}
    else:
        try:
            payload = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return "", f"CLEANUP_DB_CONFIG_INVALID:{type(exc).__name__}"

    def _env_db_override(service_name: str = "") -> tuple[str, str, str, str, str]:
        """Return (host, port, name, user, password) from env when declared."""
        safe = _re.sub(r"[^A-Za-z0-9]+", "_", service_name.upper()).strip("_")
        prefixes: list[str] = []
        if safe:
            # Matches enterprise_credential_manager: QUALIBUG_SVC_<SERVICE>_DB_PASS
            prefixes.append(f"QUALIBUG_SVC_{safe}_DB_")
        prefixes.append("QUALIBUG_DB_")
        host = port = name = user = password = ""
        for prefix in prefixes:
            host = host or _text(_os.environ.get(f"{prefix}HOST"))
            port = port or _text(_os.environ.get(f"{prefix}PORT"))
            name = name or _text(_os.environ.get(f"{prefix}NAME"))
            user = user or _text(_os.environ.get(f"{prefix}USER"))
            password = password or _text(_os.environ.get(f"{prefix}PASS"))
        return host, port, name, user, password

    decrypt_error = ""
    for service in _list(_dict(payload).get("services")):
        svc = _dict(service)
        db = _dict(svc.get("db"))
        host = _text(db.get("host"))
        name = _text(db.get("name"))
        if not host or not name:
            continue
        user = _text(db.get("user"))
        password = _text(db.get("password"))
        port = _text(db.get("port")) or "5432"
        if password.startswith("enc$"):
            try:
                from .credential_crypto import decrypt as _decrypt

                password = _decrypt(password)
            except Exception as exc:
                decrypt_error = f"CREDENTIAL_DECRYPT_FAILED:{type(exc).__name__}"
                # Operator may still supply a plaintext DB password via env
                # without rewriting the encrypted on-disk blob.
                env_host, env_port, env_name, env_user, env_password = _env_db_override(
                    _text(svc.get("name"))
                )
                if env_password:
                    host = env_host or host
                    port = env_port or port
                    name = env_name or name
                    user = env_user or user
                    password = env_password
                    decrypt_error = ""
                else:
                    continue
        return f"postgresql://{user}:{password}@{host}:{port}/{name}", ""

    # No declared db block, but operator may still point cleanup at a DSN via env.
    env_host, env_port, env_name, env_user, env_password = _env_db_override("")
    if env_host and env_name and env_user and env_password:
        return (
            f"postgresql://{env_user}:{env_password}@{env_host}:{env_port or '5432'}/{env_name}",
            "",
        )
    if decrypt_error:
        return "", decrypt_error
    return "", ""


def _scoped_accepted_write_count_for_cleanup(
    cleanup: dict[str, Any] | None,
    accepted_governed_writes: list[dict[str, Any]],
) -> int:
    """Count accepted writes this cleanup subject must cover for the delivery gate.

    The gate sums ``evidence.accepted_write_count`` across every cleanup contract
    receipt. Stamping the full accepted-write set on each control/treatment
    subject double-counts and yields false ``CLEANUP_WRITE_COVERAGE_MISMATCH``.
    Prefer ``source_step_id`` / ``compensates_operation_ref`` scope; unscoped
    legacy plans keep whole-plan cardinality.
    """
    cleanup_row = _dict(cleanup)
    source_step_id = _text(cleanup_row.get("source_step_id"))
    compensates_op = _text(cleanup_row.get("compensates_operation_ref"))
    writes = [row for row in accepted_governed_writes if isinstance(row, dict)]
    if not source_step_id and not compensates_op:
        return len(writes)
    count = 0
    for attempt in writes:
        attempt_step = _text(attempt.get("step_id"))
        attempt_op = _text(attempt.get("operation_ref"))
        if source_step_id:
            if not attempt_step or attempt_step != source_step_id:
                continue
        elif compensates_op:
            if not attempt_op or attempt_op != compensates_op:
                continue
        count += 1
    return count


def _creation_receipts_from_accepted_writes(
    *,
    cleanup_plan: list[Any],
    accepted_governed_writes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build ownership creation receipts for declared db_sql cleanup plans.

    Uses the same declared-column + response-envelope identity rules as adapter
    cleanup. Multi-write plans stamp ``source_step_id``; each accepted write
    binds to its compensating template so control/treatment rows stay distinct.
    """
    from .cleanup_adapter_ladder import identity_value_from_body

    receipts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    templates = [
        _dict(row)
        for row in _list(cleanup_plan)
        if _text(_dict(row).get("adapter")) == "db_sql"
    ]
    if not templates:
        return receipts
    for attempt in accepted_governed_writes:
        attempt_step = _text(attempt.get("step_id"))
        attempt_op = _text(attempt.get("operation_ref"))
        tmpl: dict[str, Any] = {}
        for candidate in templates:
            source_step = _text(candidate.get("source_step_id"))
            compensates_op = _text(candidate.get("compensates_operation_ref"))
            if source_step and attempt_step and source_step != attempt_step:
                continue
            if compensates_op and attempt_op and compensates_op != attempt_op:
                continue
            tmpl = candidate
            break
        if not tmpl:
            tmpl = templates[0]
        write_row = _dict(attempt.get("write"))
        write_status = int(write_row.get("status") or 0)
        if not (200 <= write_status < 300):
            continue
        identity_column = _text(tmpl.get("identity_column")) or "id"
        table = _text(tmpl.get("table"))
        created_id = ""
        for body in (
            _dict(attempt.get("response_bound_after")).get("body"),
            write_row.get("body"),
            _dict(attempt.get("after")).get("body"),
            attempt.get("body"),
        ):
            if isinstance(body, dict):
                created_id = identity_value_from_body(body, identity_column)
                if created_id:
                    break
        if not created_id:
            continue
        key = (table.lower(), created_id)
        if key in seen:
            continue
        seen.add(key)
        receipts.append(
            {
                "status": "created",
                "identity_value": created_id,
                "resource_id": created_id,
                "table": table,
                "source_step_id": attempt_step
                or _text(tmpl.get("source_step_id")),
                "operation_ref": attempt_op
                or _text(tmpl.get("compensates_operation_ref")),
            }
        )
    return receipts


def _adapter_cleanup_identity(
    cleanup: dict[str, Any],
    *,
    runtime_bindings: dict[str, Any],
    steps_out: list[dict[str, Any]],
) -> str:
    """The concrete row identity for an adapter cleanup, from what the run observed.

    Restricts to control/treatment governance bodies for the write being
    compensated — never fixture/cleanup step bodies. When compile stamped
    ``source_step_id``, only that write may supply the identity (control and
    treatment often share one operation_ref but create distinct rows).
    Resolves only via the declared identity_column (plus generic primary-key
    aliases when the column itself is id/uuid/key). Returns "" when unbound
    rather than guessing.
    """
    from .cleanup_adapter_ladder import identity_body_keys, identity_value_from_body

    cleanup_row = _dict(cleanup)
    column = _text(cleanup_row.get("identity_column")) or "id"
    source_step_id = _text(cleanup_row.get("source_step_id"))
    compensates_op = _text(cleanup_row.get("compensates_operation_ref"))
    for step in reversed(_list(steps_out)):
        if _text(step.get("phase")) not in {"control", "treatment"}:
            continue
        if source_step_id and _text(step.get("step_id")) != source_step_id:
            continue
        if compensates_op and _text(step.get("operation_ref")) not in {
            "",
            compensates_op,
        }:
            continue
        gov = _dict(step.get("governance_receipt"))
        if not gov or gov.get("accepted") is not True:
            continue
        for body in (
            _dict(_dict(gov.get("response_bound_after")).get("body")),
            _dict(_dict(gov.get("write")).get("body")),
            _dict(_dict(gov.get("after")).get("body")),
            _dict(_dict(gov.get("before")).get("body")),
            _dict(step.get("body")) if isinstance(step.get("body"), dict) else {},
        ):
            value = identity_value_from_body(body, column)
            if value:
                return value
    if source_step_id:
        # A step-scoped cleanup must not fall back to a shared binding map —
        # that would reintroduce cross-write identity collapse.
        return ""
    bindings = _dict(runtime_bindings)
    for key in identity_body_keys(column):
        value = _text(bindings.get(key))
        if value:
            return value
    return ""


def _mutation_restore_plan_from_steps(
    steps_out: list[dict[str, Any]],
    *,
    identity_value: str = "",
    identity_column: str = "id",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(restore_fields, attestation)`` from one governed write step.

    Field restore and its attestation must bind the same control/treatment
    step. Selecting restore diffs from one arm and attestation snapshots from
    another produces false ``attestation_restore_value_mismatch`` refusals.

    An empty ``identity_value`` yields empty maps: unscoped scalar diffs must
    not enter field-restore (that previously dead-ended as
    ``CLEANUP_MUTATION_NOT_ATTESTED`` and blocked the owned-row DELETE path).
    """
    from .cleanup_adapter_ladder import identity_value_from_body
    from .cleanup_equivalence import SERVER_MANAGED_FIELDS

    identity = _text(identity_value)
    column = _text(identity_column) or "id"
    if not identity:
        return {}, {}
    for step in reversed(_list(steps_out)):
        if _text(step.get("phase")) not in {"control", "treatment"}:
            continue
        gov = _dict(step.get("governance_receipt"))
        if not gov or gov.get("accepted") is not True:
            continue
        before_body = _dict(_dict(gov.get("before")).get("body"))
        after_body = _dict(_dict(gov.get("after")).get("body"))
        if not before_body or not after_body:
            continue
        before_id = identity_value_from_body(before_body, column)
        after_id = identity_value_from_body(after_body, column)
        if before_id != identity or after_id != identity:
            continue
        restore: dict[str, Any] = {}
        for key, before_val in before_body.items():
            field = _text(key)
            if not field or field.lower() in SERVER_MANAGED_FIELDS:
                continue
            if isinstance(before_val, (dict, list)):
                continue
            after_val = after_body.get(key)
            if isinstance(after_val, (dict, list)):
                continue
            if before_val != after_val:
                restore[field] = before_val
        if not restore:
            continue
        write_receipt_ref = _text(
            gov.get("audit_path") or gov.get("before_ref") or gov.get("after_ref")
        )
        # Restore map and attestation always share this step. Missing write
        # receipt ref yields restore-without-attestation so the executor can
        # refuse field-restore *and* refuse falling through to customer-row
        # DELETE (see ``_execute_adapter_cleanup_step``).
        attestation = {
            "identity_value": identity,
            "identity_column": column,
            "accepted_write": True,
            "before_body": before_body,
            "after_body": after_body,
            "restore_fields": dict(restore),
            "write_receipt_ref": write_receipt_ref,
        } if write_receipt_ref else {}
        return restore, attestation
    return {}, {}


def _mutation_restore_fields_from_steps(
    steps_out: list[dict[str, Any]],
    *,
    identity_value: str = "",
    identity_column: str = "id",
) -> dict[str, Any]:
    """Scalar fields a governed write changed, restored from before-state."""
    restore_fields, _attestation = _mutation_restore_plan_from_steps(
        steps_out,
        identity_value=identity_value,
        identity_column=identity_column,
    )
    return restore_fields


def _mutation_attestation_from_steps(
    steps_out: list[dict[str, Any]],
    *,
    identity_value: str,
    identity_column: str,
    restore_fields: dict[str, Any],
) -> dict[str, Any]:
    """Build the mutation attestation field_restore requires before any UPDATE.

    When ``restore_fields`` is supplied, only a same-step plan whose restore
    map matches exactly may attest — never a sibling control/treatment arm.
    """
    restore_plan, attestation = _mutation_restore_plan_from_steps(
        steps_out,
        identity_value=identity_value,
        identity_column=identity_column,
    )
    if not attestation:
        return {}
    expected = _dict(restore_fields)
    if expected and restore_plan != expected:
        return {}
    return attestation


def _execute_adapter_cleanup_step(
    cleanup: dict[str, Any],
    *,
    root: Path,
    project: str,
    runtime_bindings: dict[str, Any],
    steps_out: list[dict[str, Any]],
    runtime_contract: dict[str, Any] | None = None,
    behavior_ir: dict[str, Any] | None = None,
    creation_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one declared-adapter cleanup step and return its receipt.

    Every refusal is a receipt too. A cleanup that did not happen must be as visible as
    one that did, or residue accumulates in a customer system unnoticed.

    Prefer field restore when governed before/after prove this write mutated an
    existing row. Row delete remains for run-created identities only.
    """
    from .cleanup_adapter_ladder import (
        EXECUTION_SCHEMA,
        build_ordered_delete_plan,
        execute_declared_adapter_cleanup,
        execute_declared_adapter_field_restore,
    )

    identity = _adapter_cleanup_identity(
        cleanup, runtime_bindings=runtime_bindings, steps_out=steps_out
    )
    dsn, dsn_error_code = _project_database_dsn(root, project)
    step = _dict(cleanup)

    # A declared-but-unusable credential (decrypt failure) is a different fault than
    # "no database configured" and must never be silently collapsed into the latter --
    # continuing with dsn="" would relabel a real credential failure as
    # CLEANUP_DB_CONNECTION_NOT_CONFIGURED, hiding the actual root cause.
    if dsn_error_code:
        return {
            "schema_version": EXECUTION_SCHEMA,
            "adapter": _text(step.get("adapter")),
            "table": _text(step.get("table")),
            "identity_column": _text(step.get("identity_column")),
            "identity_value": identity,
            "status": "REFUSED",
            "reason_code": dsn_error_code,
            "rows_deleted": 0,
            "dependent_receipts": [],
        }

    identity_column = _text(step.get("identity_column")) or "id"
    restore_fields, attestation = _mutation_restore_plan_from_steps(
        steps_out,
        identity_value=identity,
        identity_column=identity_column,
    )
    if restore_fields:
        if not attestation:
            # Observed scalar diffs without a sealed mutation attestation must
            # never fall through to UPDATE or to a customer-row DELETE.
            return {
                "schema_version": EXECUTION_SCHEMA,
                "adapter": _text(step.get("adapter")),
                "table": _text(step.get("table")),
                "identity_column": identity_column,
                "identity_value": identity,
                "status": "REFUSED",
                "reason_code": "CLEANUP_MUTATION_NOT_ATTESTED",
                "rows_deleted": 0,
                "rows_updated": 0,
                "mode": "field_restore",
                "dependent_receipts": [],
                "ownership_basis": "mutation_attestation_missing",
            }
        receipt = execute_declared_adapter_field_restore(
            step,
            identity_value=identity,
            restore_fields=restore_fields,
            dsn=dsn,
            root=root,
            project=project,
            runtime_contract=runtime_contract,
            mutation_attestation=attestation,
            entities=_list(_dict(behavior_ir).get("entities")),
        )
        summary = dict(receipt)
        summary["dependent_receipts"] = []
        # Keep mode-specific counters — never overload rows_deleted with updates.
        summary["rows_deleted"] = int(receipt.get("rows_deleted") or 0)
        summary["rows_updated"] = int(receipt.get("rows_updated") or 0)
        return summary

    # Dependents first, owner last. A single-table delete raised ForeignKeyViolation on
    # every run-created product against the live target, because inventory, cart_items,
    # inventory_locks and order_items reference them.
    ordered = build_ordered_delete_plan(
        table=_text(step.get("table")),
        identity_column=_text(step.get("identity_column")) or "id",
        identity_value=identity,
        entities=_list(_dict(behavior_ir).get("entities")),
    )

    _entity_rows = _list(_dict(behavior_ir).get("entities"))
    receipts = [
        execute_declared_adapter_cleanup(
            sub_step,
            identity_value=identity,
            dsn=dsn,
            creation_receipts=creation_receipts or [],
            root=root,
            project=project,
            runtime_contract=runtime_contract,
            entities=_entity_rows,
        )
        for sub_step in ordered
    ]
    owner_receipt = receipts[-1] if receipts else {}
    # The owner row is the one that had to go. A dependent that was already absent is
    # not a failure, but an owner that survived is.
    summary = dict(owner_receipt)
    summary["dependent_receipts"] = receipts[:-1]
    summary["rows_deleted"] = sum(int(r.get("rows_deleted") or 0) for r in receipts)
    failed = [r for r in receipts if _text(r.get("status")) == "FAILED"]
    if failed and _text(summary.get("status")) != "FAILED":
        summary["status"] = "FAILED"
        summary["reason_code"] = _text(failed[0].get("reason_code"))
    return summary


def execute_experiment_cleanup_compensation(
    *,
    exp: dict[str, Any],
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    contract_evidence_receipts: list[dict[str, Any]],
    activation_requirements: dict[str, Any],
    pre_transport_block_reasons: list[str],
    request_bodies_for_cleanup: dict[str, Any],
    runtime_bindings: dict[str, Any],
    pending_fixture_cleanups: list[dict[str, Any]],
    cleanup_failures: int,
    ops: dict[str, dict[str, Any]],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run governed cleanup + fixture compensation; always continue to observers.

    Mutates ``steps_out``, ``observations``, ``contract_evidence_receipts``, and
    pending fixture receipt status in place. Returns the updated cleanup_failures
    counter and the same mutable containers for the caller.

    ``behavior_ir`` is the campaign-level IR. Experiments rarely embed a full
    entity graph; adapter cleanup needs those source table aliases to rebind
    logical names (payment) onto catalog-present storage tables (payments).
    """
    # Cleanup compensation in reverse order for write experiments.
    safety = _dict(exp.get("safety_contract"))
    _cleanup_behavior_ir = _dict(behavior_ir) or _dict(exp.get("behavior_ir"))

    # ── Structured process trace: trigger → decision → failure → result ──
    # Stable correlation identity (campaign/slice/attempt) lets one cleanup
    # failure be reconstructed from logs alone, without ad-hoc scripts.
    _cleanup_slice_id = _text(
        exp.get("slice_id")
        or exp.get("behavior_slice_id")
        or resolved_execution_id
    )
    _cleanup_trace_identity = (
        f"campaign={resolved_campaign_id or '-'} "
        f"slice={_cleanup_slice_id or '-'} "
        f"obligation={oid or '-'} experiment={eid or '-'}"
    )

    def _log_cleanup(event: str, **fields: Any) -> None:
        extras = " ".join(
            f"{key}={value}"
            for key, value in fields.items()
            if value not in (None, "")
        )
        _LOGGER.info(
            "[cleanup-trace] event=%s %s%s",
            event,
            _cleanup_trace_identity,
            f" {extras}" if extras else "",
        )

    def _fail_cleanup(stage: str, reason: str) -> None:
        nonlocal cleanup_failures
        cleanup_failures += 1
        _LOGGER.warning(
            "[cleanup-trace] event=failure %s stage=%s reason=%s",
            _cleanup_trace_identity,
            stage,
            reason,
        )
    # fixture_cleanup:* subjects are sealed only by pending_fixture_cleanups.
    # Bulk NOT_REQUIRED/BLOCKED stamps that also cover those subjects produced
    # CONTRACT_EVIDENCE_IDENTITY_DUPLICATE when the real fixture cleanup later
    # emitted COMPLETED for the same subject_id (1× on T140342Z).
    plan_cleanup_subjects = [
        _text(subject)
        for subject in _list(activation_requirements.get("cleanup"))
        if _text(subject) and not _text(subject).startswith("fixture_cleanup:")
    ]
    governed_write_attempts = _governed_write_attempts(steps_out)
    accepted_governed_writes = [
        attempt
        for attempt in governed_write_attempts
        if attempt.get("accepted") is True
    ]
    delete_cleanup_templates = [
        normalize_path_placeholders(
            _text(
                _dict(item).get("path")
                or _dict(ops.get(_text(_dict(item).get("operation_ref")))).get("path")
                or _dict(ops.get(_text(_dict(item).get("operation_ref")))).get("raw_path")
            )
        )
        for item in _list(exp.get("cleanup_plan"))
        if _text(
            _dict(item).get("method")
            or _dict(ops.get(_text(_dict(item).get("operation_ref")))).get("method")
        ).upper()
        == "DELETE"
    ]
    _log_cleanup(
        "trigger",
        governed_writes=len(governed_write_attempts),
        accepted_writes=len(accepted_governed_writes),
        cleanup_plan_items=len(_list(exp.get("cleanup_plan"))),
        cleanup_subjects=len(plan_cleanup_subjects),
        pending_fixtures=len(pending_fixture_cleanups),
        governed_write=bool(safety.get("governed_write")),
    )

    def _accepted_write_needs_cleanup(attempt: dict[str, Any]) -> bool:
        if _governed_write_changed_state(attempt):
            return True
        # Identity-bound DELETE cleanup: an accepted create may only expose the
        # new id on the write response while collection snapshots stay empty.
        if not delete_cleanup_templates:
            return False
        projected_write_step = {
            "phase": "treatment",
            "operation_ref": _text(attempt.get("operation_ref")),
            "governance_receipt": attempt,
            "body": _dict(attempt.get("write")).get("body"),
            "status_code": int(_dict(attempt.get("write")).get("status") or 0),
        }
        for path_template in delete_cleanup_templates:
            targets, missing = _runtime_cleanup_paths(
                path_template,
                [projected_write_step],
            )
            if targets and not missing:
                return True
        return False

    def _write_step_requires_cleanup(
        step: dict[str, Any],
        *,
        cleanup_method: str,
        cleanup_path_template: str,
    ) -> bool:
        """Mirror execution-time source_step selection for one cleanup contract.

        Aggregation must use the real write step (path/bindings/body), not a
        projection that drops runtime identity — otherwise identity-bound
        DELETE cleanup that already returned 2xx is sealed FAILED with
        cleanup_required_write_count=0 (T113119Z treatment CLEANUP_RECEIPT_FAILED).
        """
        receipt = _dict(step.get("governance_receipt"))
        if not receipt or receipt.get("accepted") is not True:
            return False
        if _governed_write_changed_state(receipt):
            return True
        if cleanup_method == "DELETE" and cleanup_path_template:
            targets, missing = _runtime_cleanup_paths(
                cleanup_path_template,
                [step],
            )
            return bool(targets) and not missing
        return False

    accepted_write_steps = [
        step
        for step in steps_out
        if _text(_dict(step).get("phase")) in {"control", "treatment"}
        and _dict(step.get("governance_receipt")).get("accepted") is True
    ]
    # Start from governed attempts (facade may stamp adapter-binding markers onto
    # these copies). Then add identity-bound DELETE needs that only bind against
    # the real write step — receipt-only projections lose effectful_write_receipt
    # and would skip cleanup, later sealing false CLEANUP_RECEIPT_FAILED.
    accepted_governed_writes_requiring_cleanup = [
        attempt
        for attempt in accepted_governed_writes
        if _accepted_write_needs_cleanup(attempt)
    ]
    requiring_audit_ids = {
        _governance_audit_receipt_id(attempt)
        for attempt in accepted_governed_writes_requiring_cleanup
        if _governance_audit_receipt_id(attempt)
    }
    for step in accepted_write_steps:
        receipt = _dict(step.get("governance_receipt"))
        receipt_audit = _governance_audit_receipt_id(receipt)
        if receipt_audit and receipt_audit in requiring_audit_ids:
            continue
        if not any(
            _write_step_requires_cleanup(
                step,
                cleanup_method="DELETE",
                cleanup_path_template=path_template,
            )
            for path_template in delete_cleanup_templates
        ):
            continue
        matched = next(
            (
                attempt
                for attempt in accepted_governed_writes
                if _governance_audit_receipt_id(attempt) == receipt_audit
            ),
            receipt,
        )
        accepted_governed_writes_requiring_cleanup.append(matched)
        if receipt_audit:
            requiring_audit_ids.add(receipt_audit)
    _log_cleanup(
        "decision",
        boundary="cleanup_required_writes",
        required=len(accepted_governed_writes_requiring_cleanup),
        audit_ids=len(requiring_audit_ids),
    )
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and not accepted_governed_writes
    ):
        pre_transport_blocks = [
            step
            for step in steps_out
            if _text(_dict(step).get("phase")) in {"control", "treatment"}
            and _text(_dict(step).get("status")) == "blocked_write"
            and not isinstance(_dict(step).get("governance_receipt"), dict)
        ]
        runtime_body_blocks = [
            step
            for step in steps_out
            if _text(_dict(step).get("phase")) in {"control", "treatment"}
            and _text(_dict(
                _dict(
                    _dict(step).get("governance_receipt")
                ).get("runtime_body_receipt")
            ).get("status")).upper() == "BLOCKED"
        ]
        if (pre_transport_blocks or runtime_body_blocks) and not accepted_governed_writes:
            _log_cleanup(
                "decision",
                boundary="blocked_before_transport",
                pre_transport_blocks=len(pre_transport_blocks),
                runtime_body_blocks=len(runtime_body_blocks),
            )
            block_reasons = sorted(set(
                [
                    _text(_dict(step).get("reason"))
                    for step in pre_transport_blocks
                    if _text(_dict(step).get("reason"))
                ]
                + pre_transport_block_reasons
            ))
            for cleanup_subject in plan_cleanup_subjects:
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind="cleanup",
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=cleanup_subject,
                    status="BLOCKED",
                    evidence={
                        "accepted_write_count": 0,
                        "cleanup_write_count": 0,
                        "write_reached_transport": False,
                        "state_unchanged": None,
                        "audit_receipt_ids": [],
                        "reason_code": "NO_WRITE_REACHED_TRANSPORT",
                        "write_block_reasons": block_reasons,
                    },
                ))
            observations["cleanup_status"] = "blocked"
            observations["cleanup_reason"] = "write_blocked_before_transport"
        elif not governed_write_attempts or not any(
            _governed_write_reached_transport(attempt)
            for attempt in governed_write_attempts
        ):
            # Read-only / denied-before-governance paths, and governance
            # receipts that only cover before-GET / identity / mutation blocks
            # (write_request_attempt_count=0), never mutated the target.
            # Those must be NOT_REQUIRED cleanup, not a transport failure.
            # Empty attempts previously fell through to
            # REJECTED_WRITE_STATE_NOT_PROVEN_UNCHANGED because
            # `_rejected_writes_left_state_unchanged([])` is False (26× on
            # T113119Z). Zero-transport receipts with before!=after({}) still
            # tripped the same false HF (16× on T120110Z/T123504Z).
            no_transport_audit_ids = sorted({
                receipt_id
                for receipt_id in (
                    _governance_audit_receipt_id(attempt)
                    for attempt in governed_write_attempts
                )
                if receipt_id
            })
            for cleanup_subject in plan_cleanup_subjects:
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind="cleanup",
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=cleanup_subject,
                    status="NOT_REQUIRED",
                    evidence={
                        "accepted_write_count": 0,
                        "cleanup_write_count": 0,
                        "write_reached_transport": False,
                        "state_unchanged": True,
                        "audit_receipt_ids": no_transport_audit_ids,
                        "reason_code": "NO_WRITE_REACHED_TRANSPORT",
                    },
                ))
            observations["cleanup_status"] = "not_required"
            observations["cleanup_reason"] = "no_write_reached_transport"
        else:
            rejected_state_unchanged = _rejected_writes_left_state_unchanged(
                governed_write_attempts
            )
            rejected_audit_ids = sorted({
                receipt_id
                for receipt_id in (
                    _governance_audit_receipt_id(attempt)
                    for attempt in governed_write_attempts
                )
                if receipt_id
            })
            for cleanup_subject in plan_cleanup_subjects:
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind="cleanup",
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=cleanup_subject,
                    status="NOT_REQUIRED" if rejected_state_unchanged else "FAILED",
                    evidence={
                        "accepted_write_count": 0,
                        "cleanup_write_count": 0,
                        "write_reached_transport": True,
                        "state_unchanged": rejected_state_unchanged,
                        "audit_receipt_ids": rejected_audit_ids,
                        "reason_code": (
                            "NO_ACCEPTED_WRITE"
                            if rejected_state_unchanged
                            else "REJECTED_WRITE_STATE_NOT_PROVEN_UNCHANGED"
                        ),
                    },
                ))
            observations["cleanup_status"] = (
                "not_required" if rejected_state_unchanged else "failed"
            )
            _log_cleanup(
                "decision",
                boundary="rejected_write_attribution",
                state_unchanged=rejected_state_unchanged,
            )
            if not rejected_state_unchanged:
                _fail_cleanup(
                    "rejected_write_attribution",
                    "rejected_write_state_not_proven_unchanged",
                )
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and accepted_governed_writes
        and not accepted_governed_writes_requiring_cleanup
    ):
        accepted_audit_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(attempt)
                for attempt in accepted_governed_writes
            )
            if receipt_id
        })
        # Gate coverage sums accepted_write_count across every cleanup subject.
        # Attribute the unchanged accepted writes once — repeating the full
        # cardinality on control + treatment falsely trips
        # CLEANUP_WRITE_COVERAGE_MISMATCH (covered 4 vs accepted 2).
        unchanged_write_count = len(accepted_governed_writes)
        for index, cleanup_subject in enumerate(plan_cleanup_subjects):
            contract_evidence_receipts.append(build_contract_evidence_receipt(
                kind="cleanup",
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=cleanup_subject,
                status="NOT_REQUIRED",
                evidence={
                    "accepted_write_count": (
                        unchanged_write_count if index == 0 else 0
                    ),
                    "cleanup_required_write_count": 0,
                    "cleanup_write_count": 0,
                    "state_unchanged": True,
                    "audit_receipt_ids": accepted_audit_ids,
                    "reason_code": "ACCEPTED_WRITE_STATE_UNCHANGED",
                },
            ))
        observations["cleanup_status"] = "not_required"
        observations["cleanup_reason"] = "accepted_write_state_unchanged"
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and accepted_governed_writes_requiring_cleanup
    ):
        cleanup_plan = _list(exp.get("cleanup_plan"))
        cleanup_subjects = activation_requirements.get("cleanup") or []
        documented_routes = _documented_routes(ops)
        adapter_cleanup_receipts: list[dict[str, Any]] = []
        # Build creation receipts from accepted governed writes so the DB
        # adapter can prove row ownership. Each accepted 2xx write response
        # body may contain the created resource identity.
        _creation_receipts_for_cleanup = _creation_receipts_from_accepted_writes(
            cleanup_plan=cleanup_plan,
            accepted_governed_writes=accepted_governed_writes,
        )
        # The compiler emits cleanup plans in compensation order (reverse write
        # order: later writes are compensated first). Iterating them in that
        # emitted order preserves reverse-order compensation semantics for
        # per-step plans; the per-target reversal inside the DELETE branch still
        # handles the legacy single-plan full-step case.
        for cleanup_index in range(len(cleanup_plan)):
            cleanup = cleanup_plan[cleanup_index]
            cleanup_subject_id = (
                cleanup_subjects[cleanup_index]
                if cleanup_index < len(cleanup_subjects)
                else f"cleanup:operation:{cleanup_index + 1}"
            )
            # ── Declared-adapter cleanup, before any HTTP handling ──
            # A db_sql step carries no path or method, so the HTTP branch below would
            # record cleanup_compensation_unresolved and leave the row behind. That is
            # exactly what happened: 204 CLEANUP_RECEIPT_FAILED and 15 qb_auto rows left
            # in the target. Authorising a write whose cleanup cannot run is worse than
            # blocking the write.
            if _text(_dict(cleanup).get("adapter")) == "db_sql":
                _scoped_n = _scoped_accepted_write_count_for_cleanup(
                    cleanup, accepted_governed_writes
                )
                _source_step_id = _text(_dict(cleanup).get("source_step_id"))
                # Mirror the HTTP cleanup arm: a db_sql plan whose scoped
                # accepted-write set is empty must not call the adapter and
                # FAIL as CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE.
                #
                # Two live shapes (T120110Z, 16× CLEANUP_RECEIPT_FAILED):
                # 1) source_step_id=treatment_1 and that arm never accepted
                #    (type-mutation 404/500) — covered when source_step_id set.
                # 2) compensates_operation_ref matches no accepted write and
                #    source_step_id is absent — scoped count is 0 but the old
                #    guard required source_step_id, so the adapter still ran
                #    with an empty identity. Scope emptiness alone is enough.
                if _scoped_n == 0:
                    contract_evidence_receipts.append(
                        build_contract_evidence_receipt(
                            kind="cleanup",
                            experiment_id=eid,
                            obligation_id=oid,
                            campaign_id=resolved_campaign_id,
                            execution_id=resolved_execution_id,
                            subject_id=cleanup_subject_id,
                            status="NOT_REQUIRED",
                            evidence={
                                "accepted_write_count": 0,
                                "cleanup_required_write_count": 0,
                                "cleanup_write_count": 0,
                                "state_unchanged": True,
                                "restoration_verified": True,
                                "audit_receipt_ids": [],
                                "reason_code": "NO_ACCEPTED_WRITE",
                                "cleanup_adapter": "db_sql",
                                "cleanup_table": _text(_dict(cleanup).get("table")),
                                "ownership_basis": "",
                                "cleanup_mode": _text(_dict(cleanup).get("mode"))
                                or "row_delete",
                                "source_step_id": _source_step_id,
                                "compensates_operation_ref": _text(
                                    _dict(cleanup).get("compensates_operation_ref")
                                ),
                            },
                        )
                    )
                    if observations.get("cleanup_status") not in {
                        "cleaned",
                        "failed",
                    }:
                        observations["cleanup_status"] = "not_required"
                        observations["cleanup_reason"] = "no_accepted_write"
                    continue
                _adapter_receipt = _execute_adapter_cleanup_step(
                    cleanup,
                    root=root,
                    project=project,
                    runtime_bindings=runtime_bindings,
                    steps_out=steps_out,
                    runtime_contract=runtime_contract,
                    behavior_ir=_cleanup_behavior_ir,
                    creation_receipts=_creation_receipts_for_cleanup,
                )
                # contract_evidence_receipts has its own strict schema and the delivery
                # gate validates every entry; a cleanup receipt is a different artifact
                # and belongs on the observations, where the run records what it did.
                adapter_cleanup_receipts.append(_adapter_receipt)
                observations.setdefault("adapter_cleanup_receipts", []).append(
                    _adapter_receipt
                )
                _adapter_cleaned = _text(_adapter_receipt.get("status")) == "CLEANED"
                if _adapter_cleaned:
                    observations["cleanup_status"] = "cleaned"
                    _log_cleanup(
                        "recovery",
                        mode=_text(_adapter_receipt.get("mode")) or "row_delete",
                        adapter=_text(_dict(cleanup).get("adapter")),
                        table=_text(_dict(cleanup).get("table")),
                        subject=cleanup_subject_id,
                        rows_deleted=int(_adapter_receipt.get("rows_deleted") or 0),
                        rows_updated=int(_adapter_receipt.get("rows_updated") or 0),
                    )
                else:
                    _fail_cleanup(
                        "adapter_cleanup_receipt",
                        _text(_adapter_receipt.get("reason_code"))
                        or "adapter_receipt_status_not_cleaned",
                    )
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = _text(
                        _adapter_receipt.get("reason_code")
                    ) or "adapter_cleanup_failed"

                # Activation requires a cleanup CONTRACT EVIDENCE receipt, which is a
                # different artifact from the adapter's own execution receipt. Recording
                # only the latter is why every db_sql-plan experiment failed activation
                # with CLEANUP_RECEIPT_FAILED: 99 of them, exactly the set carrying a
                # db_sql plan. The HTTP path emits one; this branch did not.
                #
                # After successful field_restore/delete, state_unchanged means the
                # environment matches pre-write (restored), and restoration_verified
                # must be true with real audit ids — never inverted "write changed
                # state" semantics that fail the delivery gate.
                _adapter_audit_ids: list[str] = []
                for attempt in accepted_governed_writes:
                    aid = _governance_audit_receipt_id(attempt)
                    if aid:
                        _adapter_audit_ids.append(aid)
                _adapter_rid = _text(_adapter_receipt.get("receipt_id"))
                if _adapter_rid:
                    _adapter_audit_ids.append(_adapter_rid)
                _adapter_audit_ids = sorted(set(_adapter_audit_ids))
                _adapter_cleanup_writes = (
                    int(_adapter_receipt.get("rows_updated") or 0)
                    + int(_adapter_receipt.get("rows_deleted") or 0)
                )
                _scoped_accepted_writes = _scoped_accepted_write_count_for_cleanup(
                    cleanup,
                    accepted_governed_writes,
                )
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind="cleanup",
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=cleanup_subject_id,
                    # Contract-evidence statuses are COMPLETED/FAILED/NOT_REQUIRED/...
                    # EXECUTED is an experiment-lifecycle term and raises
                    # contract_evidence_status_invalid — which is why V9 only
                    # survived while every adapter cleanup FAILED.
                    status="COMPLETED" if _adapter_cleaned else "FAILED",
                    evidence={
                        "accepted_write_count": _scoped_accepted_writes,
                        "cleanup_write_count": (
                            _adapter_cleanup_writes if _adapter_cleaned else 0
                        ),
                        "state_unchanged": bool(_adapter_cleaned),
                        "restoration_verified": bool(_adapter_cleaned),
                        "audit_receipt_ids": _adapter_audit_ids,
                        "reason_code": _text(_adapter_receipt.get("reason_code")),
                        "cleanup_adapter": _text(_adapter_receipt.get("adapter")),
                        "cleanup_table": _text(_adapter_receipt.get("table")),
                        "ownership_basis": _text(_adapter_receipt.get("ownership_basis")),
                        "cleanup_mode": _text(_adapter_receipt.get("mode")),
                    },
                ))
                # Seal post-cleanup readback + emit a cleanup-phase runtime step so
                # cleanup_execution_receipt / equivalence see real cleanup evidence.
                _after_obs = seal_after_cleanup_observation(
                    steps_out=steps_out,
                    observations=observations,
                    actors=actors,
                    tokens=tokens,
                    base_url=base_url,
                    root=root,
                    project=project,
                    runtime_contract=runtime_contract,
                )
                _append_adapter_cleanup_runtime_step(
                    steps_out=steps_out,
                    cleanup_subject_id=cleanup_subject_id,
                    adapter_receipt=_adapter_receipt,
                    after_cleanup_obs=_after_obs,
                )
                continue

            # Compensation is declared; without a concrete reverse operation we
            # record an honest cleanup failure rather than inventing success.
            op_ref = _text(_dict(cleanup).get("operation_ref"))
            op = ops.get(op_ref) or {}
            path_template = _text(_dict(cleanup).get("path") or op.get("path") or op.get("raw_path"))
            method = _text(
                _dict(cleanup).get("method") or op.get("method") or ""
            ).upper()
            cleanup_action = _text(_dict(cleanup).get("action"))
            if cleanup_action == "source_declared_compensation":
                source_operation_ref = _text(
                    _dict(cleanup).get("compensates_operation_ref")
                )
                source_step_id = _text(_dict(cleanup).get("source_step_id"))
                source_steps = []
                for step in steps_out:
                    if _text(_dict(step).get("phase")) not in {"control", "treatment"}:
                        continue
                    if source_step_id and _text(_dict(step).get("step_id")) != source_step_id:
                        continue
                    if _text(_dict(step).get("operation_ref")) != source_operation_ref:
                        continue
                    receipt = _dict(step.get("governance_receipt"))
                    if not receipt:
                        continue
                    if _governed_write_changed_state(receipt):
                        source_steps.append(step)
                        continue
                    # Identity-bound DELETE: accepted creates may only expose the
                    # new id on the write response while collection snapshots stay
                    # empty — still require a concrete cleanup path binding.
                    if method == "DELETE" and receipt.get("accepted") is True:
                        bound_targets, bound_missing = _runtime_cleanup_paths(
                            path_template,
                            [step],
                        )
                        if bound_targets and not bound_missing:
                            source_steps.append(step)
                if not source_steps:
                    # Sibling arms of a dual-write plan may have no accepted
                    # write that needs cleanup while another arm does. That is
                    # NOT_REQUIRED for this subject — not a harness cleanup
                    # failure for the whole experiment.
                    _sibling_accepted = [
                        step
                        for step in steps_out
                        if _text(_dict(step).get("phase")) in {"control", "treatment"}
                        and (
                            not source_step_id
                            or _text(_dict(step).get("step_id")) == source_step_id
                        )
                        and _text(_dict(step).get("operation_ref"))
                        == source_operation_ref
                        and _dict(step.get("governance_receipt")).get("accepted")
                        is True
                    ]
                    contract_evidence_receipts.append(
                        build_contract_evidence_receipt(
                            kind="cleanup",
                            experiment_id=eid,
                            obligation_id=oid,
                            campaign_id=resolved_campaign_id,
                            execution_id=resolved_execution_id,
                            subject_id=cleanup_subject_id,
                            status="NOT_REQUIRED",
                            evidence={
                                "accepted_write_count": len(_sibling_accepted),
                                "cleanup_required_write_count": 0,
                                "cleanup_write_count": 0,
                                "state_unchanged": True,
                                "restoration_verified": True,
                                "audit_receipt_ids": sorted({
                                    receipt_id
                                    for receipt_id in (
                                        _governance_audit_receipt_id(
                                            _dict(step.get("governance_receipt"))
                                        )
                                        for step in _sibling_accepted
                                    )
                                    if receipt_id
                                }),
                                "reason_code": (
                                    "ACCEPTED_WRITE_STATE_UNCHANGED"
                                    if _sibling_accepted
                                    else "NO_ACCEPTED_WRITE"
                                ),
                            },
                        )
                    )
                    continue
                for source_step in reversed(source_steps):
                    actor_ref, actor, token = _cleanup_actor_for_write_step(
                        source_step,
                        actors=actors,
                        tokens=tokens,
                    )
                    allowed, reason = sandbox_write_allowed(
                        root=root,
                        project=project,
                        runtime_contract=runtime_contract,
                        actor_token=token,
                        actor_identity=_text(actor.get("role") or actor_ref),
                    )
                    if not allowed:
                        _fail_cleanup("cleanup_authorization", reason)
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = reason
                        continue
                    cleanup_targets, missing_bindings = _runtime_cleanup_paths(
                        path_template,
                        [source_step],
                    )
                    if missing_bindings or len(cleanup_targets) != 1:
                        _fail_cleanup(
                            "identity_bound_delete_binding",
                            f"cleanup_binding_unresolved:{','.join(missing_bindings)}"
                            if missing_bindings
                            else "cleanup_compensation_target_ambiguous",
                        )
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = (
                            f"cleanup_binding_unresolved:{','.join(missing_bindings)}"
                            if missing_bindings
                            else "cleanup_compensation_target_ambiguous"
                        )
                        continue
                    path, target_bindings = cleanup_targets[0]
                    # ── Fallback: derive cleanup path/method from source step ──
                    if not path.startswith("/"):
                        _src_path = _text(_dict(source_step).get("path"))
                        _src_method = _text(_dict(source_step).get("method")).upper()
                        if _src_path.startswith("/"):
                            if _src_method == "POST":
                                # POST create → DELETE cleanup
                                _write_resp = source_step.get("body")
                                _res_id = ""
                                if isinstance(_write_resp, dict):
                                    _res_id = (
                                        _text(_write_resp.get("id"))
                                        or _text(_write_resp.get("_id"))
                                        or _text(_write_resp.get("itemId"))
                                        or _text(_write_resp.get("cartItemId"))
                                        or _text(_write_resp.get("productId"))
                                        or _text(_write_resp.get("orderId"))
                                    )
                                if _res_id:
                                    path = _src_path.rstrip("/") + "/" + _res_id
                                else:
                                    path = _src_path
                                method = "DELETE"
                            elif _src_method in {"PUT", "PATCH"}:
                                path = _src_path
                                method = _src_method
                            elif _src_method == "DELETE":
                                path = _src_path
                                method = "POST"
                    cleanup_bindings = {**runtime_bindings, **target_bindings}
                    cleanup_body = None
                    if method in {"POST", "PUT", "PATCH"}:
                        original_body = request_bodies_for_cleanup.get(
                            _text(source_step.get("step_id"))
                        )
                        if original_body is None:
                            _fail_cleanup(
                                "identity_bound_delete_body",
                                "cleanup_original_request_missing",
                            )
                            observations["cleanup_status"] = "failed"
                            observations["cleanup_reason"] = (
                                "cleanup_original_request_missing"
                            )
                            continue
                        cleanup_body = _materialize_body_template(
                            original_body,
                            cleanup_bindings,
                        )
                        unresolved_cleanup_tokens = _unresolved_body_placeholders(
                            cleanup_body,
                            cleanup_bindings,
                        )
                        if unresolved_cleanup_tokens:
                            _fail_cleanup(
                                "identity_bound_delete_tokens",
                                "cleanup_body_placeholder_unresolved:"
                                + ",".join(unresolved_cleanup_tokens),
                            )
                            observations["cleanup_status"] = "failed"
                            observations["cleanup_reason"] = (
                                "cleanup_body_placeholder_unresolved:"
                                + ",".join(unresolved_cleanup_tokens)
                            )
                            continue
                    observation_path = _text(
                        _dict(source_step).get("observation_path")
                    ) or _declared_observation_path(
                        path_template,
                        ops,
                        runtime_bindings=cleanup_bindings,
                        request_body=cleanup_body,
                    )
                    if (
                        not path.startswith("/")
                        or path_has_placeholders(path)
                        or method not in {"POST", "PUT", "PATCH", "DELETE"}
                        or not observation_path
                    ):
                        # Structured diagnostic (replaces ad-hoc stderr print):
                        # which condition made the cleanup path unresolvable.
                        _log_cleanup(
                            "failure_diagnostic",
                            stage="identity_bound_delete_transport_path",
                            path=path,
                            method=method,
                            obs_path=observation_path,
                            starts_slash=path.startswith("/"),
                            has_placeholders=path_has_placeholders(path),
                            bindings=",".join(list(cleanup_bindings.keys())[:8]),
                        )
                        _fail_cleanup(
                            "identity_bound_delete_transport_path",
                            "cleanup_compensation_unresolved",
                        )
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = "cleanup_compensation_unresolved"
                        continue
                    governed_cleanup = execute_governed_control_write(
                        root=root,
                        project=project,
                        base_url=base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=campaign_id,
                        operation_phase="experiment_cleanup",
                        actor_identity=_text(actor.get("role") or actor_ref),
                        actor_token=token,
                        method=method,
                        path=path,
                        body=cleanup_body if method in {"POST", "PUT", "PATCH"} else None,
                        observation_path=observation_path,
                        # Cleanup restores/compensates the experiment's own
                        # writes; it is by definition restorable, so the
                        # protected-identity guard (which exists to stop
                        # permanent mutation of runtime accounts) does not
                        # apply to the compensation itself.
                        restorable_identity_mutation=True,
                    )
                    cleanup_write = _dict(governed_cleanup.get("write"))
                    cleanup_observation = {
                        "method": method,
                        "path": path,
                        "status_code": int(cleanup_write.get("status") or 0),
                        "body": cleanup_write.get("body"),
                        "headers": cleanup_write.get("headers") or {},
                        "duration_ms": cleanup_write.get("duration_ms"),
                        "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                        "governance_receipt": governed_cleanup,
                        "phase": "cleanup",
                        "operation_ref": op_ref,
                        "cleanup_subject_id": cleanup_subject_id,
                        "compensates_step_id": _text(source_step.get("step_id")),
                        "actor_ref": actor_ref,
                    }
                    steps_out.append(cleanup_observation)
                    if not (200 <= int(cleanup_observation.get("status_code") or 0) < 300):
                        _fail_cleanup(
                            "identity_bound_delete_request",
                            f"non_2xx_status={int(cleanup_observation.get('status_code') or 0)}",
                        )
                        observations["cleanup_status"] = "failed"
                    elif not cleanup_failures:
                        observations["cleanup_status"] = "completed"
                continue
            if cleanup_action == "best_effort_delete":
                _fail_cleanup(
                    "best_effort_delete",
                    "cleanup_authority_not_source_declared",
                )
                observations["cleanup_status"] = "failed"
                observations["cleanup_reason"] = (
                    "cleanup_authority_not_source_declared"
                )
                steps_out.append({
                    "phase": "cleanup",
                    "cleanup_subject_id": cleanup_subject_id,
                    "method": "",
                    "path": "",
                    "status_code": 0,
                    "operation_ref": op_ref,
                    "error": "cleanup_authority_not_source_declared",
                })
                contract_evidence_receipts.append(
                    build_contract_evidence_receipt(
                        kind="cleanup",
                        experiment_id=eid,
                        obligation_id=oid,
                        campaign_id=resolved_campaign_id,
                        execution_id=resolved_execution_id,
                        subject_id=cleanup_subject_id,
                        status="FAILED",
                        evidence={
                            "accepted_write_count": len(
                                accepted_governed_writes
                            ),
                            "cleanup_write_count": 0,
                            "state_unchanged": False,
                            "restoration_verified": False,
                            "audit_receipt_ids": [],
                            "reason_code": (
                                "cleanup_authority_not_source_declared"
                            ),
                        },
                    )
                )
                continue
            if cleanup_action in {"restore_before_snapshot", "inverse_delta_compensation"}:
                # A per-step cleanup plan (source_step_id scoped) compensates
                # only the write it was compiled for. Without the filter every
                # plan item restored every accepted control+treatment write,
                # so a two-step plan compensated each write twice — the
                # inverse-delta writes over-compensated and restoration never
                # verified (EXECUTED_BUT_NOT_RESTORED → HARNESS_FAILED).
                _source_step_id = _text(_dict(cleanup).get("source_step_id"))
                restore_steps = [
                    step for step in steps_out
                    if _text(_dict(step).get("phase")) in {"control", "treatment"}
                    and _text(_dict(step).get("operation_ref")) == op_ref
                    and _text(_dict(step).get("method")).upper() == method
                    and 200 <= int(_dict(step).get("status_code") or 0) < 300
                    and isinstance(_dict(step).get("governance_receipt"), dict)
                    and _governed_write_changed_state(
                        _dict(step.get("governance_receipt"))
                    )
                    and (
                        not _source_step_id
                        or _text(_dict(step).get("step_id")) == _source_step_id
                    )
                ]
                if not restore_steps:
                    # A scoped source write that never changed target state
                    # (rejected mutation, idempotent no-op) needs no restore.
                    # This mirrors the generic compensation arm, which seals
                    # NOT_REQUIRED with ACCEPTED_WRITE_STATE_UNCHANGED instead
                    # of failing a compensation that has nothing to undo.
                    _source_write_seen_unchanged = False
                    if _source_step_id:
                        _source_write_seen_unchanged = any(
                            _text(_dict(step).get("phase")) in {"control", "treatment"}
                            and _text(_dict(step).get("step_id")) == _source_step_id
                            for step in steps_out
                        )
                    if _source_write_seen_unchanged:
                        observations["cleanup_status"] = "not_required"
                        observations["cleanup_reason"] = (
                            "ACCEPTED_WRITE_STATE_UNCHANGED"
                        )
                        continue
                    _fail_cleanup(
                        "mutation_restore_plan",
                        "cleanup_accepted_write_missing",
                    )
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_accepted_write_missing"
                    continue
                for step in reversed(restore_steps):
                    actor_ref, actor, token = _cleanup_actor_for_write_step(
                        step,
                        actors=actors,
                        tokens=tokens,
                    )
                    allowed, reason = sandbox_write_allowed(
                        root=root,
                        project=project,
                        runtime_contract=runtime_contract,
                        actor_token=token,
                        actor_identity=_text(actor.get("role") or actor_ref),
                    )
                    if not allowed:
                        _fail_cleanup("mutation_restore_authorization", reason)
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = reason
                        continue
                    path = _text(_dict(step).get("path"))
                    if not path.startswith("/") or path_has_placeholders(path) or method not in {"POST", "PUT", "PATCH"}:
                        _fail_cleanup(
                            "mutation_restore_path",
                            "cleanup_restore_target_unresolved",
                        )
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = "cleanup_restore_target_unresolved"
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": "cleanup_restore_target_unresolved",
                        })
                        continue
                    original = _dict(step.get("governance_receipt"))
                    if cleanup_action == "inverse_delta_compensation":
                        restore_body, restore_projection = _inverse_delta_cleanup_body(
                            request_bodies_for_cleanup.get(_text(step.get("step_id")))
                            or _dict(cleanup).get("body"),
                            delta_field=_text(_dict(cleanup).get("delta_field")),
                        )
                    else:
                        original_request_body = (
                            request_bodies_for_cleanup.get(_text(step.get("step_id")))
                            or _dict(cleanup).get("body")
                            or {}
                        )
                        restore_body, restore_projection = _restore_payload(
                            method=method,
                            path=path,
                            before_body=_dict(original.get("before")).get("body"),
                            request_body=original_request_body,
                            write_body=_dict(original.get("write")).get("body"),
                            documented_routes=documented_routes,
                        )
                    if not restore_body:
                        _fail_cleanup(
                            "mutation_restore_body",
                            f"cleanup_restore_unresolved:{restore_projection}",
                        )
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = f"cleanup_restore_unresolved:{restore_projection}"
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": f"cleanup_restore_unresolved:{restore_projection}",
                        })
                        continue
                    observation_path = _text(_dict(step).get("observation_path")) or _declared_observation_path(
                        path_template,
                        ops,
                        runtime_bindings=runtime_bindings,
                    )
                    if not observation_path:
                        _fail_cleanup(
                            "mutation_restore_observation_path",
                            "cleanup_observer_unresolved",
                        )
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = "cleanup_observer_unresolved"
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": "cleanup_observer_unresolved",
                        })
                        continue
                    governed_cleanup = execute_governed_control_write(
                        root=root,
                        project=project,
                        base_url=base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=campaign_id,
                        operation_phase="experiment_cleanup",
                        actor_identity=_text(actor.get("role") or actor_ref),
                        actor_token=token,
                        method=method,
                        path=path,
                        body=restore_body,
                        observation_path=observation_path,
                        # restore_before_snapshot / inverse_delta_compensation
                        # restore the exact before state; restorable by
                        # definition, so the protected-identity guard does not
                        # apply to the restoration write.
                        restorable_identity_mutation=True,
                    )
                    cleanup_write = _dict(governed_cleanup.get("write"))
                    cobs = {
                        "method": method,
                        "path": path,
                        "status_code": int(cleanup_write.get("status") or 0),
                        "body": cleanup_write.get("body"),
                        "headers": cleanup_write.get("headers") or {},
                        "duration_ms": cleanup_write.get("duration_ms"),
                        "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                        "governance_receipt": governed_cleanup,
                        "restore_projection": restore_projection,
                    }
                    steps_out.append({
                        **cobs,
                        "phase": "cleanup",
                        "operation_ref": op_ref,
                        "cleanup_subject_id": cleanup_subject_id,
                    })
                    if not (200 <= int(cobs.get("status_code") or 0) < 300):
                        _fail_cleanup(
                            "mutation_restore_request",
                            f"non_2xx_status={int(cobs.get('status_code') or 0)}",
                        )
                        observations["cleanup_status"] = "failed"
                    elif not cleanup_failures:
                        observations["cleanup_status"] = "completed"
                continue
            # Per-step cleanup plans (source_step_id scoped) must compensate only
            # the write they were compiled for. Using every step here made each
            # plan delete every accepted write, so a control+treatment experiment
            # with one cleanup plan per step removed each resource twice. Fall
            # back to the full step list only for legacy unscoped cleanup plans.
            _cleanup_scoped_steps = steps_out
            _source_step_id = _text(_dict(cleanup).get("source_step_id"))
            if _source_step_id:
                _scoped_steps = [
                    step
                    for step in steps_out
                    if _text(_dict(step).get("step_id")) == _source_step_id
                ]
                if _scoped_steps:
                    _cleanup_scoped_steps = _scoped_steps
            cleanup_targets, missing_bindings = _runtime_cleanup_paths(
                path_template,
                _cleanup_scoped_steps,
            )
            if missing_bindings or not cleanup_targets:
                _fail_cleanup(
                    "cleanup_plan_binding",
                    f"cleanup_binding_unresolved:{','.join(missing_bindings)}"
                    if missing_bindings
                    else "cleanup_accepted_write_missing",
                )
                observations["cleanup_status"] = "failed"
                observations["cleanup_reason"] = (
                    f"cleanup_binding_unresolved:{','.join(missing_bindings)}"
                    if missing_bindings
                    else "cleanup_accepted_write_missing"
                )
                continue
            cleanup_method = method
            compensates_ref = _text(_dict(cleanup).get("compensates_operation_ref"))
            for path, target_bindings in reversed(cleanup_targets):
                if not path.startswith("/") or path_has_placeholders(path) or method not in {"DELETE", "POST", "PUT", "PATCH"}:
                    _fail_cleanup(
                        "cleanup_plan_path",
                        "cleanup_compensation_unresolved",
                    )
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_compensation_unresolved"
                    continue
                source_step = _write_step_for_cleanup_path(
                    path_template=path_template,
                    cleanup_path=path,
                    steps_out=_cleanup_scoped_steps,
                    compensates_operation_ref=compensates_ref,
                )
                # A source write that never changed target state (e.g. a
                # treatment arm re-deleting an id the control already removed,
                # or a rejected write) needs no compensation: executing the
                # compensating write anyway creates residue, and sealing it as
                # NOT_REQUIRED with cleanup_write_count=0 is the honest proof.
                # This mirrors the restore_before_snapshot branch, which only
                # restores steps whose governed write changed state.
                if source_step and not _governed_write_changed_state(
                    _dict(_dict(source_step).get("governance_receipt"))
                ):
                    observations["cleanup_status"] = "not_required"
                    observations["cleanup_reason"] = (
                        "ACCEPTED_WRITE_STATE_UNCHANGED"
                    )
                    continue
                actor_ref, actor, token = _cleanup_actor_for_write_step(
                    source_step,
                    actors=actors,
                    tokens=tokens,
                )
                allowed, reason = sandbox_write_allowed(
                    root=root,
                    project=project,
                    runtime_contract=runtime_contract,
                    actor_token=token,
                    actor_identity=_text(actor.get("role") or actor_ref),
                )
                if not allowed:
                    _fail_cleanup("cleanup_plan_authorization", reason)
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = reason
                    continue
                cleanup_bindings = {**runtime_bindings, **target_bindings}
                observation_path = _text(
                    source_step.get("observation_path")
                ) or _declared_observation_path(
                    path_template,
                    ops,
                    runtime_bindings=cleanup_bindings,
                    request_body=_dict(cleanup).get("body"),
                )
                if not observation_path:
                    _fail_cleanup(
                        "cleanup_plan_observation_path",
                        "cleanup_observer_unresolved",
                    )
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_observer_unresolved"
                    continue
                cleanup_body = _materialize_body_template(
                    _dict(cleanup).get("body"),
                    cleanup_bindings,
                )
                if cleanup_method in {"POST", "PUT", "PATCH"}:
                    unresolved_cleanup_tokens = _unresolved_body_placeholders(
                        cleanup_body,
                        cleanup_bindings,
                    )
                    if unresolved_cleanup_tokens:
                        _fail_cleanup(
                            "cleanup_plan_tokens",
                            "cleanup_body_placeholder_unresolved:"
                            + ",".join(unresolved_cleanup_tokens),
                        )
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = (
                            "cleanup_body_placeholder_unresolved:"
                            + ",".join(unresolved_cleanup_tokens)
                        )
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": cleanup_method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": observations["cleanup_reason"],
                        })
                        continue
                governed_cleanup = execute_governed_control_write(
                    root=root,
                    project=project,
                    base_url=base_url,
                    runtime_contract=runtime_contract,
                    campaign_id=campaign_id,
                    operation_phase="experiment_cleanup",
                    actor_identity=_text(actor.get("role") or actor_ref),
                    actor_token=token,
                    method=cleanup_method,
                    path=path,
                    body=cleanup_body if cleanup_method in {"POST", "PUT", "PATCH"} else None,
                    observation_path=observation_path,
                    restorable_identity_mutation=True,
                )
                cleanup_write = _dict(governed_cleanup.get("write"))
                cobs = {
                    "method": cleanup_method,
                    "path": path,
                    "status_code": int(cleanup_write.get("status") or 0),
                    "body": cleanup_write.get("body"),
                    "headers": cleanup_write.get("headers") or {},
                    "duration_ms": cleanup_write.get("duration_ms"),
                    "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                    "governance_receipt": governed_cleanup,
                }
                steps_out.append({
                    **cobs,
                    "phase": "cleanup",
                    "operation_ref": op_ref,
                    "cleanup_subject_id": cleanup_subject_id,
                    "actor_ref": actor_ref,
                    "compensates_step_id": _text(source_step.get("step_id")),
                })
                if not (200 <= int(cobs.get("status_code") or 0) < 300):
                    _fail_cleanup(
                        "cleanup_plan_request",
                        f"non_2xx_status={int(cobs.get('status_code') or 0)}",
                    )
                    observations["cleanup_status"] = "failed"
                elif not cleanup_failures:
                    observations["cleanup_status"] = "completed"

    # Fixture setup precedes experiment writes, so its compensation must run
    # after every experiment-write compensation to preserve global reverse
    # order.  Complete it before aggregating cleanup subjects so the Oracle
    # sees one authoritative fixture-cleanup receipt rather than a synthetic
    # missing receipt followed by the real one.
    for pending in reversed(pending_fixture_cleanups):
        cleanup = _dict(pending.get("cleanup"))
        cleanup_bindings = dict(runtime_bindings)
        cleanup_placeholders = infer_path_params(_text(cleanup.get("path")))
        if len(cleanup_placeholders) == 1:
            cleanup_bindings.setdefault(cleanup_placeholders[0], pending.get("value"))
        cleanup_path = _materialize_path(_text(cleanup.get("path")), cleanup_bindings)
        governed_cleanup = execute_governed_control_write(
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            operation_phase="experiment_fixture_cleanup",
            actor_identity=_text(pending.get("actor_identity")),
            actor_token=_text(pending.get("actor_token")),
            method=_text(cleanup.get("method")).upper(),
            path=cleanup_path,
            body=None,
            observation_path=_text(pending.get("observation_path")),
        )
        cleanup_write = _dict(governed_cleanup.get("write"))
        cleanup_status = int(cleanup_write.get("status") or 0)
        governed_setup = _dict(pending.get("governed_setup"))
        restoration_verified = _cleanup_restores_governed_write(
            governed_setup,
            governed_cleanup,
        )
        audit_receipt_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(governed_setup),
                _governance_audit_receipt_id(governed_cleanup),
            )
            if receipt_id
        })
        completed = bool(
            200 <= cleanup_status < 300
            and restoration_verified
            and audit_receipt_ids
        )
        _dict(pending.get("receipt"))["fixture_cleanup_status"] = (
            "completed" if completed else "failed"
        )
        fixture_subject = f"fixture_cleanup:{_text(pending.get('target'))}"
        # Replace any prior bulk stamp for this subject — the governed fixture
        # cleanup just executed and is the authority for this identity.
        contract_evidence_receipts[:] = [
            receipt
            for receipt in contract_evidence_receipts
            if not (
                _text(receipt.get("kind")) == "cleanup"
                and _text(receipt.get("subject_id")) == fixture_subject
            )
        ]
        if not completed:
            _fail_cleanup(
                "fixture_cleanup_seal",
                f"fixture_cleanup_receipt_not_completed subject={fixture_subject}",
            )
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="cleanup",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=fixture_subject,
            status="COMPLETED" if completed else "FAILED",
            evidence={
                "method": _text(cleanup.get("method")).upper(),
                "path": cleanup_path,
                "status_code": cleanup_status,
                "operation_ref": _text(cleanup.get("operation_ref")),
                "accepted_write_count": 1,
                "cleanup_write_count": 1 if governed_cleanup.get("accepted") is True else 0,
                "restoration_verified": restoration_verified,
                "state_unchanged": restoration_verified,
                "audit_receipt_ids": audit_receipt_ids,
            },
        ))
        steps_out.append({
            "phase": "fixture_cleanup",
            "cleanup_subject_id": fixture_subject,
            "method": _text(cleanup.get("method")).upper(),
            "path": cleanup_path,
            "status_code": cleanup_status,
            "operation_ref": _text(cleanup.get("operation_ref")),
            "governance_receipt": governed_cleanup,
        })
    if pending_fixture_cleanups:
        observations["cleanup_status"] = "failed" if cleanup_failures else "completed"

    recorded_cleanup_subjects = {
        _text(receipt.get("subject_id"))
        for receipt in contract_evidence_receipts
        if _text(receipt.get("kind")) == "cleanup"
    }
    cleanup_plan = _list(exp.get("cleanup_plan"))
    for cleanup_index, cleanup_subject in enumerate(
        activation_requirements["cleanup"]
    ):
        if cleanup_subject in recorded_cleanup_subjects:
            continue
        cleanup_contract = (
            _dict(cleanup_plan[cleanup_index])
            if cleanup_index < len(cleanup_plan)
            else {}
        )
        source_step_id = _text(cleanup_contract.get("source_step_id"))
        cleanup_op_ref = _text(cleanup_contract.get("operation_ref"))
        cleanup_op = ops.get(cleanup_op_ref) or {}
        cleanup_method = _text(
            cleanup_contract.get("method") or cleanup_op.get("method") or ""
        ).upper()
        cleanup_path_template = normalize_path_placeholders(
            _text(
                cleanup_contract.get("path")
                or cleanup_op.get("path")
                or cleanup_op.get("raw_path")
            )
        )
        if source_step_id:
            scoped_write_steps = [
                step
                for step in steps_out
                if _text(_dict(step).get("phase")) in {"control", "treatment"}
                and _text(_dict(step).get("step_id")) == source_step_id
                and _dict(step.get("governance_receipt")).get("accepted") is True
            ]
            scoped_accepted_writes = [
                _dict(step.get("governance_receipt"))
                for step in scoped_write_steps
            ]
            scoped_writes_requiring_cleanup = [
                _dict(step.get("governance_receipt"))
                for step in scoped_write_steps
                if _write_step_requires_cleanup(
                    step,
                    cleanup_method=cleanup_method,
                    cleanup_path_template=cleanup_path_template,
                )
            ]
        else:
            # Legacy cleanup plans without source_step_id have no safe per-step
            # identity. Preserve their historical whole-plan scope explicitly;
            # source-scoped plans must never use this fallback.
            scoped_accepted_writes = accepted_governed_writes
            scoped_writes_requiring_cleanup = (
                accepted_governed_writes_requiring_cleanup
            )
        matching_steps = [
            step for step in steps_out
            if _text(_dict(step).get("cleanup_subject_id")) == cleanup_subject
        ]
        if source_step_id:
            source_scoped_matching_steps = [
                step
                for step in matching_steps
                if not _text(_dict(step).get("compensates_step_id"))
                or _text(_dict(step).get("compensates_step_id")) == source_step_id
            ]
            if source_scoped_matching_steps:
                matching_steps = source_scoped_matching_steps
        cleanup_governance_receipts = [
            _dict(step.get("governance_receipt"))
            for step in matching_steps
            if isinstance(step.get("governance_receipt"), dict)
        ]
        if not scoped_writes_requiring_cleanup:
            # No source write for this subject needs cleanup. Do not seal FAILED
            # merely because a cleanup HTTP step exists — that double-failed
            # identity-bound treatment arms after a successful 2xx DELETE.
            not_required_reason = (
                "ACCEPTED_WRITE_STATE_UNCHANGED"
                if scoped_accepted_writes
                else "NO_ACCEPTED_WRITE"
            )
            contract_evidence_receipts.append(build_contract_evidence_receipt(
                kind="cleanup",
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=cleanup_subject,
                status="NOT_REQUIRED",
                evidence={
                    "step_count": len(matching_steps),
                    "status_codes": [
                        int(_dict(step).get("status_code") or 0)
                        for step in matching_steps
                    ],
                    "accepted_write_count": len(scoped_accepted_writes),
                    "cleanup_required_write_count": 0,
                    "cleanup_write_count": sum(
                        1
                        for receipt in cleanup_governance_receipts
                        if receipt.get("accepted") is True
                    ),
                    "restoration_verified": True,
                    "state_unchanged": True,
                    "audit_receipt_ids": sorted({
                        receipt_id
                        for receipt_id in (
                            _governance_audit_receipt_id(governed)
                            for governed in [
                                *scoped_accepted_writes,
                                *cleanup_governance_receipts,
                            ]
                        )
                        if receipt_id
                    }),
                    "reason_code": not_required_reason,
                },
            ))
            continue
        restoration_verified = all(
            any(
                _cleanup_restores_governed_write(original, cleanup)
                for cleanup in cleanup_governance_receipts
            )
            for original in scoped_writes_requiring_cleanup
        )
        audit_receipt_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(governed)
                for governed in [
                    *scoped_accepted_writes,
                    *cleanup_governance_receipts,
                ]
            )
            if receipt_id
        })
        _log_cleanup(
            "decision",
            boundary="cleanup_proof_aggregation",
            subject=cleanup_subject,
            requires_cleanup=len(scoped_writes_requiring_cleanup),
            cleanup_receipts=len(cleanup_governance_receipts),
            restoration_verified=restoration_verified,
            audit_ids=len(audit_receipt_ids),
        )
        cleanup_statuses_succeeded = bool(matching_steps) and all(
            200 <= int(_dict(step).get("status_code") or 0) < 300
            for step in matching_steps
        )
        completed = (
            cleanup_statuses_succeeded
            and restoration_verified
            and bool(audit_receipt_ids)
        )
        if cleanup_statuses_succeeded and not completed:
            _fail_cleanup(
                "cleanup_completion_verification",
                "cleanup_statuses_succeeded_but_restoration_or_audit_incomplete",
            )
            observations["cleanup_status"] = "failed"
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="cleanup",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=cleanup_subject,
            status="COMPLETED" if completed else "FAILED",
            evidence={
                "step_count": len(matching_steps),
                "status_codes": [
                    int(_dict(step).get("status_code") or 0)
                    for step in matching_steps
                ],
                "accepted_write_count": len(scoped_writes_requiring_cleanup),
                "cleanup_required_write_count": len(
                    scoped_writes_requiring_cleanup
                ),
                "cleanup_write_count": sum(
                    1
                    for receipt in cleanup_governance_receipts
                    if receipt.get("accepted") is True
                ),
                "restoration_verified": restoration_verified,
                "state_unchanged": restoration_verified,
                "audit_receipt_ids": audit_receipt_ids,
            },
        ))

    # ── SPEC v1.1.1 §6: Emit explicit Cleanup Execution Receipt ──
    safety = _dict(exp.get("safety_contract"))
    if safety.get("governed_write"):
        # Seal after-cleanup observation for HTTP and adapter paths alike.
        seal_after_cleanup_observation(
            steps_out=steps_out,
            observations=observations,
            actors=actors,
            tokens=tokens,
            base_url=base_url,
            root=root,
            project=project,
            runtime_contract=runtime_contract,
        )
        proof = _dict(exp.get("write_reversibility_proof"))
        cleanup_exec_receipt = build_cleanup_execution_receipt(
            experiment_id=eid,
            proof_id=_text(proof.get("proof_id")),
            cleanup_plan=_list(exp.get("cleanup_plan")),
            steps_out=steps_out,
            cleanup_failures=cleanup_failures,
            cleanup_status=_text(observations.get("cleanup_status")),
            proof=proof,
            adapter_cleanup_receipts=_list(observations.get("adapter_cleanup_receipts")),
        )
        observations["cleanup_execution_receipt"] = cleanup_exec_receipt
        observations["cleanup_execution_receipts"] = [cleanup_exec_receipt]
        _cleanup_rid = _text(cleanup_exec_receipt.get("receipt_id"))
        if _cleanup_rid:
            observations.setdefault("cleanup_execution_receipt_ids", [])
            if _cleanup_rid not in observations["cleanup_execution_receipt_ids"]:
                observations["cleanup_execution_receipt_ids"].append(_cleanup_rid)

    # ── V1.3.0-A: Database Cleanup Receipt + Environment Restoration Receipt ──
    # Wire the structured DB receipts into the main execution chain.
    if safety.get("governed_write"):
        from .cleanup_execution_receipt import (
            build_database_cleanup_receipt as _build_db_receipt,
            build_environment_restoration_receipt as _build_env_receipt,
            build_fixture_row_lineage as _build_row_lineage,
            verify_cleanup_completion as _verify_cleanup,
        )
        _db_contract = _dict(exp.get("database_cleanup_contract"))
        _db_receipts: list[dict] = []
        _table = _text(
            _dict(_dict(_list(_db_contract.get("target_entities"))[0] if _list(_db_contract.get("target_entities")) else {}).get("table"))
        ) or "unknown"
        _pk_fp = _text(_db_contract.get("contract_id"))
        _strategy = _text(_dict(_db_contract.get("cleanup_strategy")).get("strategy_type"))
        _authority = _text(_dict(_db_contract.get("cleanup_strategy")).get("authority_source"))

        # Only emit DB cleanup receipts for real adapter DB operations. Never
        # fabricate DB restoration after HTTP-only cleanup or CER NOT_REQUIRED.
        _real_adapter_receipts = [
            row
            for row in _list(observations.get("adapter_cleanup_receipts"))
            if isinstance(row, dict)
            and _text(row.get("status")).upper() == "CLEANED"
            and (
                int(row.get("rows_deleted") or 0) > 0
                or int(row.get("rows_updated") or 0) > 0
            )
        ]
        for _ar in _real_adapter_receipts:
            _db_r = _build_db_receipt(
                experiment_id=eid,
                fixture_id="",
                step_id=_text(_ar.get("identity_value")),
                datastore_id=_text(_db_contract.get("datastore_id")) or "primary",
                table=_text(_ar.get("table")) or _table,
                primary_key_fingerprint=_pk_fp
                or _text(_ar.get("identity_value")),
                cleanup_strategy=_strategy
                or _text(_ar.get("mode"))
                or "declared_adapter_cleanup",
                authority_source=_authority
                or _text(_ar.get("ownership_basis"))
                or "adapter_cleanup",
                cleanup_execution={
                    "attempted": True,
                    "affected_rows": int(_ar.get("rows_deleted") or 0)
                    + int(_ar.get("rows_updated") or 0),
                    "error": "",
                },
                verification={
                    "passed": True,
                },
            )
            _db_receipts.append(_db_r)

        # Safely collect API cleanup receipt IDs from all evidence receipts
        _api_cleanup_ids = sorted({
            _text(r.get("receipt_id"))
            for r in contract_evidence_receipts
            if _text(r.get("receipt_id"))
        })

        # Environment restoration receipt
        _env_receipt = _build_env_receipt(
            experiment_id=eid,
            campaign_id=resolved_campaign_id,
            database_cleanup_receipt_ids=[
                _text(r.get("receipt_id")) for r in _db_receipts
            ],
            api_cleanup_receipt_ids=_api_cleanup_ids,
            fixture_receipt_ids=[
                _text(r.get("receipt_id")) for r in _list(observations.get("fixture_receipts"))
            ],
            created_rows_remaining=0 if cleanup_failures == 0 else cleanup_failures,
            cleanup_failures=[
                {"reason": "cleanup_failure", "count": cleanup_failures}
            ] if cleanup_failures else [],
            baseline_comparison={
                "relevant_tables_match": cleanup_failures == 0,
                "relevant_fields_match": cleanup_failures == 0,
            },
        )
        observations["database_cleanup_receipts"] = _db_receipts
        observations["environment_restoration_receipt"] = _env_receipt
        observations["environment_restored"] = bool(_env_receipt.get("environment_restored"))

        # Cleanup completion verification
        _verification = _verify_cleanup(
            cleanup_receipts=_db_receipts,
            dependency_graph=_dict(_db_contract.get("dependency_graph")),
        )
        # This is a diagnostic summary, not a formal step verification. The
        # cleanup-equivalence authority publishes canonical aggregate and exact
        # step receipts later; dual publication causes alias multiplication.
        observations["cleanup_verification"] = _verification
        _ver_rid = _text(_verification.get("receipt_id") or _verification.get("verification_id"))
        if _ver_rid:
            observations.setdefault("cleanup_verification_receipt_ids", [])
            if _ver_rid not in observations["cleanup_verification_receipt_ids"]:
                observations["cleanup_verification_receipt_ids"].append(_ver_rid)

        # Fixture row lineage for created test objects
        _lineage_receipts: list[dict] = []
        for _fr in _list(observations.get("fixture_receipts")):
            _fr_d = _dict(_fr)
            if _fr_d.get("table") or _fr_d.get("entity"):
                _lineage_receipts.append(_build_row_lineage(
                    campaign_id=resolved_campaign_id,
                    experiment_id=eid,
                    fixture_id=_text(_fr_d.get("fixture_id") or _fr_d.get("receipt_id")),
                    step_id=_text(_fr_d.get("step_id")),
                    table=_text(_fr_d.get("table") or _fr_d.get("entity")),
                    primary_key=_text(_fr_d.get("primary_key") or _fr_d.get("row_id")),
                ))
        if _lineage_receipts:
            observations["fixture_row_lineage_receipts"] = _lineage_receipts

    _log_cleanup(
        "result",
        cleanup_status=_text(observations.get("cleanup_status")),
        cleanup_failures=cleanup_failures,
        environment_restored=observations.get("environment_restored"),
        adapter_receipts=len(_list(observations.get("adapter_cleanup_receipts"))),
    )
    return {
        "steps_out": steps_out,
        "observations": observations,
        "contract_evidence_receipts": contract_evidence_receipts,
        "cleanup_failures": cleanup_failures,
        "accepted_governed_writes": accepted_governed_writes,
    }
