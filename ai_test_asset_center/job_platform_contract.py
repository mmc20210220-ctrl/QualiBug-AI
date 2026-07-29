"""Industry-neutral Job platform contract and source-backed Job asset normalization.

This module is deliberately limited to adapter protocol, normalization and mapping
into the existing enterprise-business operation schema. It does not own planning,
execution, Oracle evaluation, findings or receipts.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Protocol, runtime_checkable

JOB_PLATFORM_CONTRACT_SCHEMA = "qualibug.job-platform-contract.v1"
JOB_DEFINITION_SCHEMA = "qualibug.job-definition.v1"
JOB_ASSET_SCHEMA = "qualibug.job-asset.v1"
ASYNC_OPERATION_KIND = "ASYNC_JOB"

TRIGGER_TYPES = frozenset(
    {"CRON", "EVENT", "MESSAGE", "MANUAL", "API", "DEPENDENCY", "DELAYED", "UNKNOWN"}
)
SAFETY_LEVELS = frozenset(
    {
        "READ_ONLY",
        "REVERSIBLE_WRITE",
        "COMPENSATABLE",
        "RESETTABLE_SANDBOX_ONLY",
        "UNSAFE_FOR_AUTONOMOUS_EXECUTION",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique_text(values: Iterable[Any]) -> list[str]:
    return sorted({_text(value) for value in values if _text(value)})


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(
        json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
        if isinstance(part, (dict, list, tuple, set))
        else _text(part)
        for part in parts
    )
    return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _normalize_evidence(rows: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = {
            key: value
            for key, value in raw.items()
            if value not in (None, "", [], {})
        }
        key = (
            _text(row.get("source_id")),
            _text(row.get("source_locator") or row.get("locator")),
            _text(row.get("quote_hash")),
            _text(row.get("connector_id")),
            _text(row.get("external_ref")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _normalize_trigger(raw: dict[str, Any]) -> dict[str, Any]:
    trigger = _dict(raw.get("trigger"))
    raw_type = _text(
        trigger.get("type")
        or raw.get("trigger_type")
        or ("CRON" if trigger.get("cron") or raw.get("cron") else "")
    ).upper()
    aliases = {
        "SCHEDULE": "CRON",
        "SCHEDULED": "CRON",
        "TIMER": "CRON",
        "QUEUE": "MESSAGE",
        "MQ": "MESSAGE",
        "WEBHOOK": "EVENT",
        "HTTP": "API",
        "UPSTREAM": "DEPENDENCY",
    }
    trigger_type = aliases.get(raw_type, raw_type)
    if trigger_type not in TRIGGER_TYPES:
        trigger_type = "UNKNOWN"
    return {
        "type": trigger_type,
        "cron": _text(trigger.get("cron") or raw.get("cron")),
        "timezone": _text(trigger.get("timezone") or raw.get("timezone")),
        "event": _text(trigger.get("event") or raw.get("event")),
        "topic": _text(trigger.get("topic") or raw.get("topic")),
        "dependency_ref": _text(
            trigger.get("dependency_ref") or raw.get("dependency_ref")
        ),
        "manual_entry_ref": _text(
            trigger.get("manual_entry_ref")
            or raw.get("manual_entry_ref")
            or raw.get("trigger_ref")
        ),
    }


def _normalize_steps(values: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(values):
        if isinstance(raw, str):
            name = _text(raw)
            row: dict[str, Any] = {"name": name}
        elif isinstance(raw, dict):
            row = dict(raw)
            name = _text(
                row.get("name")
                or row.get("step_name")
                or row.get("operation")
                or row.get("handler")
            )
        else:
            continue
        if not name:
            continue
        step_id = _text(row.get("step_id")) or _stable_id(
            "job_step", index, name, row.get("operation_ref")
        )
        result.append(
            {
                "step_id": step_id,
                "ordinal": int(row.get("ordinal") or index + 1),
                "name": name,
                "operation_ref": _text(
                    row.get("operation_ref") or row.get("operation_id")
                ),
                "read_set": _unique_text(_list(row.get("read_set"))),
                "write_set": _unique_text(_list(row.get("write_set"))),
                "source_refs": _normalize_evidence(_list(row.get("source_refs"))),
            }
        )
    return result


def _normalize_policy(value: Any) -> dict[str, Any]:
    row = _dict(value)
    return {
        key: val
        for key, val in {
            "max_attempts": row.get("max_attempts") or row.get("retry_count"),
            "backoff_ms": row.get("backoff_ms"),
            "timeout_ms": row.get("timeout_ms"),
            "allow_concurrent": row.get("allow_concurrent"),
            "lock_type": _text(row.get("lock_type")),
            "shard_count": row.get("shard_count"),
            "misfire_policy": _text(row.get("misfire_policy")),
        }.items()
        if val not in (None, "")
    }


@runtime_checkable
class JobPlatformAdapter(Protocol):
    """Minimal adapter surface. Business semantics stay in existing QualiBug stages."""

    adapter_kind: str

    def list_jobs(self, connector: dict[str, Any]) -> Iterable[dict[str, Any]]:
        ...

    def get_job_definition(
        self, connector: dict[str, Any], platform_job_id: str
    ) -> dict[str, Any]:
        ...

    def trigger_job(
        self,
        connector: dict[str, Any],
        platform_job_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def get_job_run(
        self, connector: dict[str, Any], platform_job_id: str, job_run_id: str
    ) -> dict[str, Any]:
        ...

    def list_job_steps(
        self, connector: dict[str, Any], platform_job_id: str, job_run_id: str
    ) -> Iterable[dict[str, Any]]:
        ...

    def get_job_log(
        self, connector: dict[str, Any], platform_job_id: str, job_run_id: str
    ) -> dict[str, Any]:
        ...

    def cancel_job(
        self, connector: dict[str, Any], platform_job_id: str, job_run_id: str
    ) -> dict[str, Any]:
        ...


_ADAPTERS: dict[str, JobPlatformAdapter] = {}


def register_job_platform_adapter(kind: str, adapter: JobPlatformAdapter) -> None:
    normalized = _text(kind).lower()
    if not normalized:
        raise ValueError("job_adapter_kind_missing")
    if not isinstance(adapter, JobPlatformAdapter):
        raise TypeError("job_adapter_contract_invalid")
    _ADAPTERS[normalized] = adapter


def get_job_platform_adapter(kind: str) -> JobPlatformAdapter | None:
    return _ADAPTERS.get(_text(kind).lower())


def normalize_job_definition(
    raw: dict[str, Any],
    *,
    source_refs: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Normalize only source-declared Job facts; missing facts stay visible gaps."""

    if not isinstance(raw, dict):
        raise TypeError("job_definition_not_object")
    platform_job_id = _text(
        raw.get("platform_job_id") or raw.get("job_id") or raw.get("id")
    )
    if not platform_job_id:
        raise ValueError("job_definition_id_missing")

    platform_type = _text(
        raw.get("platform_type") or raw.get("platform") or raw.get("adapter_kind")
    ).lower() or "unknown"
    display_name = _text(raw.get("display_name") or raw.get("name")) or platform_job_id
    handler = _text(raw.get("handler") or raw.get("entrypoint"))
    service = _text(raw.get("service") or raw.get("system_name"))
    module = _text(raw.get("module") or raw.get("module_name"))
    trigger = _normalize_trigger(raw)

    runtime = _dict(raw.get("runtime"))
    trigger_ref = _text(
        runtime.get("trigger_ref")
        or raw.get("trigger_ref")
        or trigger.get("manual_entry_ref")
    )
    run_identity_ref = _text(
        runtime.get("run_identity_ref")
        or runtime.get("run_lookup_ref")
        or raw.get("run_identity_ref")
        or raw.get("run_lookup_ref")
    )
    status_query_ref = _text(
        runtime.get("status_query_ref") or raw.get("status_query_ref")
    )
    step_query_ref = _text(
        runtime.get("step_query_ref") or raw.get("step_query_ref")
    )
    log_query_ref = _text(runtime.get("log_query_ref") or raw.get("log_query_ref"))
    cancel_ref = _text(runtime.get("cancel_ref") or raw.get("cancel_ref"))
    terminal_states = _unique_text(
        _list(runtime.get("terminal_states")) or _list(raw.get("terminal_states"))
    )

    behavior = _dict(raw.get("behavior"))
    process_steps = _normalize_steps(
        _list(behavior.get("process_steps"))
        or _list(raw.get("process_steps"))
        or _list(raw.get("steps"))
    )
    selection_predicates = [
        dict(item) if isinstance(item, dict) else {"expression": _text(item)}
        for item in (
            _list(behavior.get("selection_predicates"))
            or _list(raw.get("selection_predicates"))
        )
        if isinstance(item, dict) or _text(item)
    ]
    read_set = _unique_text(
        [
            *_list(behavior.get("read_set")),
            *_list(raw.get("read_set")),
            *[
                field
                for step in process_steps
                for field in _list(step.get("read_set"))
            ],
        ]
    )
    write_set = _unique_text(
        [
            *_list(behavior.get("write_set")),
            *_list(raw.get("write_set")),
            *[
                field
                for step in process_steps
                for field in _list(step.get("write_set"))
            ],
        ]
    )
    object_refs = _unique_text(
        [
            *_list(behavior.get("object_refs")),
            *_list(raw.get("object_refs")),
            *_list(raw.get("entity_refs")),
        ]
    )
    expected_effects = [
        dict(item) if isinstance(item, dict) else {"expression": _text(item)}
        for item in (
            _list(behavior.get("expected_effects"))
            or _list(raw.get("expected_effects"))
        )
        if isinstance(item, dict) or _text(item)
    ]
    external_calls = _unique_text(
        _list(behavior.get("external_calls")) or _list(raw.get("external_calls"))
    )
    messages = _unique_text(
        _list(behavior.get("messages")) or _list(raw.get("messages"))
    )
    transaction_boundaries = _unique_text(
        _list(behavior.get("transaction_boundaries"))
        or _list(raw.get("transaction_boundaries"))
    )
    compensation_paths = _unique_text(
        _list(behavior.get("compensation_paths"))
        or _list(raw.get("compensation_paths"))
    )

    cleanup = _dict(raw.get("cleanup"))
    cleanup_ref = _text(
        cleanup.get("cleanup_ref")
        or cleanup.get("operation_ref")
        or raw.get("cleanup_ref")
    )
    cleanup_mode = _text(cleanup.get("mode") or raw.get("cleanup_mode")).upper()
    cleanup_verified_by = _text(
        cleanup.get("verification_ref") or raw.get("cleanup_verification_ref")
    )

    evidence = _normalize_evidence(
        [*list(source_refs), *_list(raw.get("source_refs")), *_list(raw.get("evidence"))]
    )
    if not evidence:
        raise ValueError("job_definition_source_evidence_missing")

    policy = _normalize_policy(
        {
            **_dict(raw.get("execution_policy")),
            **_dict(raw.get("retry_policy")),
            **_dict(raw.get("concurrency_policy")),
            **_dict(raw.get("timeout_policy")),
            **_dict(raw.get("sharding_policy")),
            **_dict(raw.get("misfire_policy")),
        }
    )

    if not write_set:
        safety_level = "READ_ONLY"
    elif cleanup_ref and cleanup_mode in {"DELETE", "RESTORE", "RESET", "COMPENSATE"}:
        safety_level = (
            "COMPENSATABLE" if cleanup_mode == "COMPENSATE" else "REVERSIBLE_WRITE"
        )
    elif _text(raw.get("sandbox_reset_ref")):
        safety_level = "RESETTABLE_SANDBOX_ONLY"
    else:
        safety_level = "UNSAFE_FOR_AUTONOMOUS_EXECUTION"

    trigger_ready = bool(trigger_ref or trigger["type"] in {"EVENT", "MESSAGE", "CRON"})
    identity_ready = bool(run_identity_ref)
    fixture_ready = bool(object_refs and selection_predicates)
    observer_ready = bool(status_query_ref and (read_set or terminal_states))
    oracle_ready = bool(expected_effects or raw.get("runtime_oracle_contract"))
    cleanup_ready = not write_set or bool(cleanup_ref or raw.get("sandbox_reset_ref"))
    autonomous_safe = safety_level != "UNSAFE_FOR_AUTONOMOUS_EXECUTION"
    execution_ready = all(
        (
            trigger_ready,
            identity_ready,
            fixture_ready,
            observer_ready,
            cleanup_ready,
            autonomous_safe,
        )
    )
    if execution_ready:
        execution_status = "EXECUTION_READY"
    elif safety_level == "UNSAFE_FOR_AUTONOMOUS_EXECUTION":
        execution_status = "UNSAFE"
    else:
        execution_status = "PARTIALLY_EXECUTABLE"

    job_asset_id = _stable_id(
        "job_asset", platform_type, platform_job_id, service, module, handler
    )
    return {
        "schema": JOB_ASSET_SCHEMA,
        "job_asset_id": job_asset_id,
        "definition_schema": JOB_DEFINITION_SCHEMA,
        "platform_type": platform_type,
        "platform_job_id": platform_job_id,
        "display_name": display_name,
        "identity": {
            "handler": handler,
            "service": service,
            "module": module,
            "version": _text(raw.get("version") or raw.get("job_version")),
        },
        "trigger": trigger,
        "runtime": {
            "trigger_ref": trigger_ref,
            "run_identity_ref": run_identity_ref,
            "status_query_ref": status_query_ref,
            "step_query_ref": step_query_ref,
            "log_query_ref": log_query_ref,
            "cancel_ref": cancel_ref,
            "terminal_states": terminal_states,
        },
        "execution_policy": policy,
        "behavior": {
            "selection_predicates": selection_predicates,
            "process_steps": process_steps,
            "object_refs": object_refs,
            "read_set": read_set,
            "write_set": write_set,
            "expected_effects": expected_effects,
            "external_calls": external_calls,
            "messages": messages,
            "transaction_boundaries": transaction_boundaries,
            "compensation_paths": compensation_paths,
        },
        "cleanup": {
            "mode": cleanup_mode,
            "cleanup_ref": cleanup_ref,
            "verification_ref": cleanup_verified_by,
            "sandbox_reset_ref": _text(raw.get("sandbox_reset_ref")),
        },
        "testability": {
            "discovery_ready": True,
            "trigger_ready": trigger_ready,
            "identity_ready": identity_ready,
            "fixture_ready": fixture_ready,
            "observer_ready": observer_ready,
            "oracle_ready": oracle_ready,
            "cleanup_ready": cleanup_ready,
            "safety_level": safety_level,
            "execution_status": execution_status,
        },
        "evidence": evidence,
        "fact_authority": {
            "implementation_behavior_only": True,
            "business_expectation_confirmed": bool(
                raw.get("business_expectation_confirmed")
            ),
            "formal_business_oracle_eligible": bool(
                raw.get("formal_business_oracle_eligible")
                and raw.get("business_expectation_confirmed")
            ),
        },
        "customer_effort": {
            "manual_job_creation_required": False,
            "manual_step_configuration_required": False,
            "manual_field_binding_required": False,
            "manual_oracle_authoring_required": False,
            "manual_cleanup_authoring_required": False,
            "long_text_input_required": False,
        },
    }


def _predicate_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("expression", "statement", "raw", "predicate"):
            if _text(value.get(key)):
                return _text(value.get(key))
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _text(value)


def _effect_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("expression", "statement", "effect", "raw"):
            if _text(value.get(key)):
                return _text(value.get(key))
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _text(value)


def to_async_operation(job_asset: dict[str, Any]) -> dict[str, Any]:
    """Project a Job asset into the existing enterprise business operation schema."""

    if _text(job_asset.get("schema")) != JOB_ASSET_SCHEMA:
        raise ValueError("job_asset_schema_invalid")
    behavior = _dict(job_asset.get("behavior"))
    trigger = _dict(job_asset.get("trigger"))
    runtime = _dict(job_asset.get("runtime"))
    testability = _dict(job_asset.get("testability"))
    operation_id = _stable_id("business_operation", ASYNC_OPERATION_KIND, job_asset.get("job_asset_id"))
    temporal_constraints = _unique_text(
        [
            trigger.get("cron"),
            trigger.get("timezone"),
            trigger.get("event"),
            trigger.get("topic"),
        ]
    )
    return {
        "schema": "qualibug.enterprise-business-operation.v1",
        "operation_id": operation_id,
        "name": _text(job_asset.get("display_name")) or _text(job_asset.get("platform_job_id")),
        "raw_action_names": _unique_text(
            [
                job_asset.get("display_name"),
                job_asset.get("platform_job_id"),
                _dict(job_asset.get("identity")).get("handler"),
            ]
        ),
        "operation_kind": ASYNC_OPERATION_KIND,
        "actor_refs": [],
        "object_refs": _unique_text(_list(behavior.get("object_refs"))),
        "preconditions": _unique_text(
            _predicate_text(item) for item in _list(behavior.get("selection_predicates"))
        ),
        "effects": _unique_text(
            _effect_text(item) for item in _list(behavior.get("expected_effects"))
        ),
        "exceptions": [],
        "temporal_constraints": temporal_constraints,
        "scopes": [],
        "modality_contracts": [],
        "fact_refs": [],
        "evidence": _normalize_evidence(_list(job_asset.get("evidence"))),
        "status": (
            "UNDERSTOOD"
            if _text(testability.get("execution_status")) == "EXECUTION_READY"
            else "PARTIAL"
        ),
        "async_contract": {
            "schema": JOB_PLATFORM_CONTRACT_SCHEMA,
            "job_asset_ref": job_asset.get("job_asset_id"),
            "platform_type": job_asset.get("platform_type"),
            "platform_job_id": job_asset.get("platform_job_id"),
            "trigger": trigger,
            "runtime": runtime,
            "execution_policy": _dict(job_asset.get("execution_policy")),
            "process_steps": _list(behavior.get("process_steps")),
            "read_set": _list(behavior.get("read_set")),
            "write_set": _list(behavior.get("write_set")),
            "cleanup": _dict(job_asset.get("cleanup")),
            "testability": testability,
            "fact_authority": _dict(job_asset.get("fact_authority")),
        },
    }


__all__ = [
    "JOB_PLATFORM_CONTRACT_SCHEMA",
    "JOB_DEFINITION_SCHEMA",
    "JOB_ASSET_SCHEMA",
    "ASYNC_OPERATION_KIND",
    "JobPlatformAdapter",
    "register_job_platform_adapter",
    "get_job_platform_adapter",
    "normalize_job_definition",
    "to_async_operation",
]
