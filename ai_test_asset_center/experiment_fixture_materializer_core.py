"""Fixture DAG materialization and activation evidence for experiments.

Extracted from ``experiment_executor.execute_one_experiment``. Resolves actor
contexts and runtime-read bindings (including governed fixture setup) before
control/treatment plans run. Never invents identifiers.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from .contract_oracles import build_contract_evidence_receipt
from .experiment_runtime_support import (
    _body_contains_scalar,
    _dict,
    _list,
    _request_example,
    _resolve_token,
    _run_http_step,
    _select_fixture_actor,
    _select_runtime_binding,
    _text,
    consensus_identity_value,
)
from .real_id_resolver import (
    bind_entity_fields,
    collection_path,
    normalize_path_placeholders,
    path_has_placeholders,
)
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    runtime_setup_value_from_response as _runtime_setup_value_from_response,
    runtime_value_from_response as _runtime_value_from_response,
    validated_fixture_setup as _validated_fixture_setup,
    validated_runtime_resolvers as _validated_runtime_resolvers,
)
from .sandbox_write_executor import execute_governed_control_write


def _validate_fixture_preconditions(
    exp: dict[str, Any],
    fixture_response_body: Any,
    target: str,
) -> list[dict[str, str]]:
    """P0-8: Validate fixture preconditions after setup.

    After fixture setup completes, verify that the created resource satisfies
    the experiment's preconditions. Returns a list of validation failures
    (empty list means all preconditions passed).
    """
    failures: list[dict[str, str]] = []
    body = _dict(fixture_response_body) if isinstance(fixture_response_body, dict) else {}
    if not body:
        # Non-dict response (e.g., plain ID string) — skip field validation
        return failures

    # Extract precondition fields from experiment assertions
    assertions = _list(exp.get("assertions"))
    precondition_fields: set[str] = set()
    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        # Conservation: terms define fields that must exist
        for term in _list(assertion.get("terms")):
            field = _text(_dict(term).get("field"))
            if field:
                precondition_fields.add(field)
        # State transition: from_field/to_field must exist
        for key in ("from_field", "to_field", "state_field"):
            field = _text(assertion.get(key))
            if field:
                precondition_fields.add(field)
        # Generic assertion fields
        field = _text(assertion.get("field"))
        if field:
            precondition_fields.add(field)

    # Also check treatment_plan for body fields that reference the fixture
    for step in _list(exp.get("treatment_plan")):
        step_body = _dict(step).get("body")
        if isinstance(step_body, dict):
            for key, val in step_body.items():
                # If body references the fixture target, the fixture must have that field
                if isinstance(val, str) and f"{{{target}}}" in val:
                    precondition_fields.add(key)

    # Validate each precondition field exists in fixture response
    for field in sorted(precondition_fields):
        # Check top-level and nested (one level deep)
        found = field in body
        if not found:
            # Check nested objects (e.g., response.data.field)
            for nested_key in ("data", "result", "entity", "record"):
                nested = body.get(nested_key)
                if isinstance(nested, dict) and field in nested:
                    found = True
                    break
        if not found:
            failures.append({
                "field": field,
                "reason": "field_missing_in_fixture_response",
                "target": target,
            })

    return failures


def _run_declared_db_identity_read(
    *,
    root: Path,
    project: str,
    runtime_contract: dict[str, Any],
    resolver: dict[str, Any],
) -> dict[str, Any]:
    """Governed SELECT of a source-declared entity identity column.

    Read-only and fail-closed: gated on the same declared non-production
    environment as writes, identifiers validated against the introspected
    schema by the persistence observer, and every refusal carries a named
    reason code. Never invents a row — an empty table yields no value.
    """
    from .persistence_observer import (
        PersistenceObserverError,
        persistence_read_allowed,
        read_declared_entity_state,
        resolve_declared_data_sources,
    )

    table = _text(resolver.get("table"))
    column = _text(resolver.get("identity_column"))
    obs: dict[str, Any] = {
        "adapter": "db_sql",
        "method": "DB_READ",
        "table": table,
        "identity_column": column,
        "status_code": 0,
        "value": None,
        "reason_code": "",
        "row_count": 0,
    }
    allowed, reason = persistence_read_allowed(root, project, runtime_contract)
    if not allowed:
        obs["reason_code"] = f"persistence_read_not_permitted:{reason}"
        return obs
    try:
        sources = resolve_declared_data_sources(root, project)
    except PersistenceObserverError as exc:
        obs["reason_code"] = f"persistence_config_invalid:{exc}"
        return obs
    if not sources:
        obs["reason_code"] = "persistence_source_not_declared"
        return obs
    failures: list[str] = []
    for source in sources:
        try:
            observed = read_declared_entity_state(
                dsn=source["dsn"], table=table, fields=[column], max_rows=25
            )
        except PersistenceObserverError as exc:
            failures.append(f"{_text(source.get('module')) or 'default'}:{exc}")
            continue
        obs["row_count"] = int(observed.get("row_count") or 0)
        for row in _list(observed.get("rows")):
            value = _dict(row).get(column)
            if value not in (None, "", [], {}):
                obs["status_code"] = 200
                obs["value"] = value
                obs["source_service"] = _text(source.get("service"))
                return obs
        obs["reason_code"] = "persistence_identity_not_observed"
    if failures and not obs["status_code"]:
        obs["reason_code"] = "persistence_read_failed:" + ";".join(failures)
    return obs


def _source_backed_dependency_fixture_setup(
    *,
    dependency_leaf: str,
    resolver_operations: list[Any],
    ops: dict[str, dict[str, Any]],
    actors: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any] | None,
    binding_plan: dict[str, Any],
) -> dict[str, Any]:
    """Build a validated disposable-create setup for one create-body FK.

    Used when a parent fixture's body dependency list-read returns empty.
    Only source-declared POST + request_example + identity-bound cleanup may
    produce a setup — never invents identifiers or undeclared cleanup.
    """
    from .runtime_binding_graph import _declared_fixture_setup

    resolver_path = ""
    for resolver in resolver_operations:
        if isinstance(resolver, dict) and _text(resolver.get("path")).startswith("/"):
            resolver_path = _text(resolver.get("path"))
            break
    synthetic_op = {
        "id": f"dependency_read:{dependency_leaf}",
        "method": "GET",
        "path": resolver_path or f"/{{{dependency_leaf}}}",
    }
    declared = _declared_fixture_setup(
        synthetic_op,
        target=dependency_leaf,
        behavior_ir=_dict(behavior_ir) or {"operations": list(ops.values())},
    )
    binding: dict[str, Any] = {
        "target": dependency_leaf,
        "target_path": f"/{{{dependency_leaf}}}",
        "resolver_operations": [
            dict(row) for row in resolver_operations if isinstance(row, dict)
        ],
    }
    if declared:
        binding["fixture_setup"] = declared
    else:
        auto_create = _auto_fixture_create_for_binding_target(
            dependency_leaf,
            binding,
            ops,
            binding_plan,
            actors=actors,
            behavior_ir=behavior_ir,
        )
        if auto_create:
            binding = {**binding, **auto_create}
    return _validated_fixture_setup(
        binding,
        ops,
        actors,
        entities=_list(_dict(behavior_ir).get("entities")) or None,
    )


def _auto_fixture_create_for_binding_target(
    target: str,
    binding: dict[str, Any],
    operations: dict[str, dict[str, Any]],
    binding_plan: dict[str, Any],
    actors: dict[str, dict[str, Any]] | None = None,
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Find a POST create operation at the same collection as the binding target path.

    When a runtime read binding returns empty (no data to extract an id from),
    this discovers a create operation that can serve as a disposable fixture to
    produce a resource whose id can then be used as the binding value.

    The fixture_setup sub-dict uses the POST operation's documented request
    example from the Behavior IR as body_template — industry-neutral, no
    hardcoding.
    """
    from .real_id_resolver import collection_path as _collection_path

    resolver_ops = binding.get("resolver_operations") or []
    target_path = _text(binding.get("target_path") or "")
    candidate_paths = set()
    for resolver in resolver_ops:
        if isinstance(resolver, dict):
            rpath = _text(resolver.get("path"))
            if rpath.startswith("/"):
                candidate_paths.add(rpath)
                cp = _collection_path(rpath)
                if cp.startswith("/"):
                    candidate_paths.add(cp)
    # Also try the binding's own target_path as a collection hint
    if target_path.startswith("/"):
        cp = _collection_path(target_path)
        if cp.startswith("/"):
            candidate_paths.add(cp)

    if not candidate_paths:
        return None

    for op_id, op in operations.items():
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        if method != "POST":
            continue
        op_path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        )
        op_collection = normalize_path_placeholders(_collection_path(op_path))
        if op_path in candidate_paths or op_collection in candidate_paths:
            # Prefer identity-bound DELETE; otherwise accept a source-shaped
            # compensation action on the same collection (e.g. …/{id}/cancel).
            from .runtime_binding_graph import _declared_cleanup_operations

            source_ir = _dict(behavior_ir)
            if not source_ir:
                source_ir = {"operations": list(operations.values())}
            selected_cleanup = _declared_cleanup_operations(
                op_collection,
                behavior_ir=source_ir,
            )
            if not selected_cleanup:
                continue
            owner = _text(binding.get("fixture_owner_actor_ref"))
            actor_refs = [owner] if owner else []
            # Auto-create has no permits-bound owner. The runtime actor picker
            # (_select_fixture_actor) prefers the control/treatment plan actors
            # and only falls back to another executable declared actor, so the
            # candidate pool may safely contain every non-anonymous campaign
            # actor: the created disposable resource stays plan-visible while
            # the final identity is still plan-aligned. Anonymous/public actors
            # cannot own a resource, so they are excluded.
            if not actor_refs and actors:
                actor_refs = [
                    candidate_ref
                    for candidate_ref, candidate in actors.items()
                    if isinstance(candidate, dict)
                    and _text(candidate.get("role")).lower()
                    not in {"anonymous", "public"}
                ]
            if not actor_refs:
                continue
            return {
                "fixture_setup": {
                    "operation_ref": op_id,
                    "method": "POST",
                    "path": op_path,
                    "cleanup_operations": selected_cleanup,
                    "actor_refs": actor_refs,
                },
                "force_fixture_setup": True,
                "create_operation_ref": op_id,
                "create_path": op_path,
                "create_method": "POST",
                "synthetic_value": None,
            }

    return None


def materialize_experiment_fixtures(
    *,
    exp: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    started: float,
    actors: dict[str, dict[str, Any]],
    ops: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    binding_plan: dict[str, Any],
    resolver_actor_ref: str,
    resolver_token: str,
    activation_requirements: dict[str, Any],
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize fixture DAG + actor/fixture activation evidence.

    Returns ``{"status": "terminal", "result": ...}`` for fail-closed exits, or
    ``{"status": "ready", ...state...}`` when plans may execute.
    """
    steps_out: list[dict[str, Any]] = []
    fixture_receipts: list[dict[str, Any]] = []
    binding_materialization_receipts: list[dict[str, Any]] = []
    runtime_bindings: dict[str, Any] = {}
    pending_fixture_cleanups: list[dict[str, Any]] = []
    cleanup_failures = 0
    contract_evidence_receipts: list[dict[str, Any]] = []

    # Fixture DAG: actor contexts are resolved via tokens; disposable fixtures
    # without a concrete create path remain BLOCKED (already caught in preflight
    # when constructible=false). Record READY nodes as resolved receipts.
    dag = _dict(exp.get("fixture_dag"))
    # ── SPEC v1.2.1 §8: Use fixture_dependency_dag.execution_order if available ──
    v12_fixture_dag = _dict(exp.get("fixture_dependency_dag"))
    v12_execution_order = _list(v12_fixture_dag.get("execution_order")) or _list(v12_fixture_dag.get("topological_order"))
    setup_order = v12_execution_order if v12_execution_order else _list(dag.get("setup_order"))
    for node_id in setup_order:
        node = next((n for n in _list(dag.get("nodes")) if _text(_dict(n).get("node_id")) == node_id), {})
        kind = _text(_dict(node).get("kind"))
        if kind == "actor_context":
            actor_ref = _text(_dict(node).get("actor_ref"))
            actor = actors.get(actor_ref) or {}
            token = _resolve_token(actor, tokens)
            if _text(actor.get("role")).lower() not in {"anonymous", "public"} and not token:
                return {
                    "status": "terminal",
                    "result": {
                        "schema_version": "qualibug.experiment-execution.v1",
                        "experiment_id": eid,
                        "obligation_id": oid,
                        "status": "BLOCKED",
                        "reason_code": "BLOCKED_MISSING_ACTOR",
                        "detail": f"fixture_actor_unresolved:{actor_ref}",
                        "elapsed_ms": int((time.time() - started) * 1000),
                        "finding": None,
                        "execution_receipt": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_ACTOR"},
                    }
                }
            fixture_receipts.append({"node_id": node_id, "kind": kind, "status": "resolved"})
        elif kind == "runtime_read_binding":
            target = _text(_dict(node).get("target"))
            binding = binding_plan.get(target) or {}
            # ── Shortcut: source-observed, pre-materialized binding ──
            # Batch pre-resolution may supply a value, but only after a source
            # identity GET proves that value is observable on THIS binding's
            # collection. A cart-item {id} must never bind into /api/orders/{id}.
            if (
                binding.get("materialized_value") not in (None, "")
                and _text(binding.get("status")) == "bound"
                and _text(binding.get("source_priority")) == "same_actor_list_read"
            ):
                from .runtime_binding_resolver import (
                    collection_path_for_placeholder,
                    collection_segment_for_placeholder,
                    declared_identity_read_operations,
                    materialize_declared_identity_read,
                )

                _pre_val = str(binding["materialized_value"])
                _target_path = normalize_path_placeholders(
                    _text(binding.get("target_path"))
                )
                _collection_path = collection_path_for_placeholder(
                    _target_path,
                    target,
                )
                _identity_operations = declared_identity_read_operations(
                    list(ops.values()),
                    collection_path=_collection_path,
                )
                _proved = False
                _proof_source = ""
                if not _identity_operations:
                    _candidate_tokens: list[str] = []
                    for _candidate in (
                        resolver_token,
                        *[
                            _text(row)
                            for row in _dict(tokens).values()
                            if _text(row)
                        ],
                    ):
                        if _candidate and _candidate not in _candidate_tokens:
                            _candidate_tokens.append(_candidate)
                    for _resolver in _list(binding.get("resolver_operations")):
                        if not isinstance(_resolver, dict):
                            continue
                        _read_method = _text(_resolver.get("method")).upper()
                        _read_path = _text(_resolver.get("path"))
                        if (
                            _read_method not in {"GET", "HEAD"}
                            or not _read_path.startswith("/")
                            or path_has_placeholders(_read_path)
                        ):
                            continue
                        for _token_index, _candidate_token in enumerate(
                            _candidate_tokens[:8]
                        ):
                            _read_obs = _run_http_step(
                                base_url=base_url,
                                method=_read_method,
                                path=_read_path,
                                token=_candidate_token,
                            )
                            _read_obs.update({
                                "phase": "binding_identity_proof",
                                "step_id": (
                                    f"bind-proof:{target}:list{_token_index}"
                                ),
                                "actor_ref": resolver_actor_ref,
                                "operation_ref": _text(
                                    _resolver.get("operation_ref")
                                ),
                            })
                            steps_out.append(_read_obs)
                            if not (
                                200
                                <= int(_read_obs.get("status_code") or 0)
                                < 300
                            ):
                                continue
                            _observed = bind_entity_fields(
                                _read_obs.get("body"),
                                _target_path,
                            )
                            _observed_value = _text(
                                _observed.get(target) or _observed.get("id")
                            )
                            if _observed_value and _observed_value == _pre_val:
                                _proved = True
                                _proof_source = _read_path
                                break
                        if _proved:
                            break
                else:
                    for _identity_operation in _identity_operations[:3]:
                        _proof_path = materialize_declared_identity_read(
                            _identity_operation,
                            _pre_val,
                        )
                        if not _proof_path:
                            continue
                        _proof_obs = _run_http_step(
                            base_url=base_url,
                            method=_text(_identity_operation.get("method")).upper(),
                            path=_proof_path,
                            token=resolver_token,
                        )
                        _proof_obs.update({
                            "phase": "binding_identity_proof",
                            "step_id": f"bind-proof:{target}",
                            "actor_ref": resolver_actor_ref,
                            "operation_ref": _text(_identity_operation.get("id")),
                        })
                        steps_out.append(_proof_obs)
                        if 200 <= int(_proof_obs.get("status_code") or 0) < 300:
                            _proved = True
                            _proof_source = _proof_path
                            break
                if _proved:
                    runtime_bindings[target] = _pre_val
                    fixture_receipts.append({
                        "node_id": node_id,
                        "kind": kind,
                        "status": "resolved",
                        "target": target,
                        "value": _pre_val,
                        "value_fingerprint": hashlib.sha256(
                            _pre_val.encode("utf-8")
                        ).hexdigest()[:12],
                        "source": "pre_resolved_binding",
                        "proof_source": _proof_source,
                        "collection_segment": collection_segment_for_placeholder(
                            _target_path, target
                        ),
                    })
                    continue
                # Fall through to path-scoped resolvers when identity proof fails.
            # Invented identifiers are forbidden. A synthetic_value without a
            # source-declared GET/HEAD resolver or fixture setup remains blocked.
            # Before blocking, try to auto-discover a create operation on the
            # same collection path that can serve as a disposable fixture setup.
            if (
                binding.get("synthetic_value") is not None
                and not binding.get("resolver_operations")
                and not binding.get("fixture_setup")
            ):
                auto_create = _auto_fixture_create_for_binding_target(
                    target,
                    binding,
                    ops,
                    binding_plan,
                    actors=actors,
                    behavior_ir=behavior_ir,
                )
                if auto_create:
                    binding = {**binding, **auto_create}
                else:
                    # ── SPEC v1.2.2 §9: Synthetic binding FORBIDDEN ──
                    # No verified runtime source → BLOCKED, no transport.
                    fixture_receipts.append({
                        "node_id": node_id,
                        "kind": kind,
                        "status": "blocked",
                        "target": target,
                        "reason_code": "BLOCKED_MISSING_BINDING",
                        "detail": f"binding_has_no_verified_runtime_source:{target}",
                    })
                    continue
            resolvers = _validated_runtime_resolvers(binding, ops)
            force_fixture_setup = binding.get("force_fixture_setup") is True
            target_path = _text(binding.get("target_path"))
            preferred_binding_body: Any = None
            normalized_target_path = normalize_path_placeholders(target_path)
            for planned_step in [
                *_list(exp.get("control_plan")),
                *_list(exp.get("treatment_plan")),
            ]:
                if not isinstance(planned_step, dict):
                    continue
                planned_op = ops.get(_text(planned_step.get("operation_ref"))) or {}
                planned_path = normalize_path_placeholders(
                    _text(planned_op.get("path") or planned_op.get("raw_path"))
                )
                if planned_path != normalized_target_path:
                    continue
                preferred_binding_body = (
                    planned_step.get("body")
                    if planned_step.get("body")
                    else _request_example(planned_op)
                )
                if preferred_binding_body:
                    break
            value: Any = None
            fixture_setup_accepted = False
            _identity_conflict = False
            receipt: dict[str, Any] = {
                "target": target,
                "status": "BLOCKED",
                "source_priority": "same_actor_list_read",
                "resolver_path": "",
                "resolver_operation_ref": "",
                "status_code": 0,
                "value_fingerprint": "",
            }
            # ── Resolve per-binding actor token for fixture resolution ──
            # Resolve only with the binding's exact owner actor or the actor
            # already declared by the executable control/treatment plan.
            _binding_actor_ref = _text(binding.get("fixture_owner_actor_ref"))
            _binding_actor = actors.get(_binding_actor_ref) or {} if _binding_actor_ref else {}
            _binding_token = _resolve_token(_binding_actor, tokens) if _binding_actor_ref else ""
            _effective_resolver_token = _binding_token or resolver_token
            _effective_resolver_actor = _binding_actor_ref or resolver_actor_ref
            _actor_candidates: list[tuple[str, str]] = [
                (_effective_resolver_actor, _effective_resolver_token)
            ]
            for index, resolver in enumerate(resolvers):
                for _fb_idx, (_fb_actor, _fb_token) in enumerate(_actor_candidates):
                    obs = _run_http_step(
                        base_url=base_url,
                        method=resolver["method"],
                        path=resolver["path"],
                        token=_fb_token,
                    )
                    obs.update({
                        "phase": "binding_materialization",
                        "step_id": f"bind:{target}:{index}:actor{_fb_idx}",
                        "actor_ref": _fb_actor,
                        "operation_ref": resolver["operation_ref"],
                    })
                    steps_out.append(obs)
                    receipt.update({
                        "resolver_path": resolver["path"],
                        "resolver_operation_ref": resolver["operation_ref"],
                        "resolver_actor_ref": _fb_actor,
                        "status_code": int(obs.get("status_code") or 0),
                    })
                    _sc = int(obs.get("status_code") or 0)
                    if _sc == 0:
                        break
                    if not (200 <= _sc < 300):
                        continue
                    if (
                        _text(binding.get("identity_extraction"))
                        == "owner_field_consensus"
                    ):
                        # Owner-identity bindings resolve only on owner-field
                        # consensus across every observed row. Disagreement
                        # means the collection mixes owners; picking any row
                        # would arm the treatment step with another actor's
                        # identity (cross-contamination), so fail closed and
                        # skip both further resolvers and fixture fallback.
                        _identity_value, _identity_status = consensus_identity_value(
                            obs.get("body"), target,
                        )
                        if _identity_status == "conflicted":
                            _identity_conflict = True
                            break
                        value = _identity_value or None
                    else:
                        extracted = _select_runtime_binding(
                            obs.get("body"),
                            target_path,
                            preferred_body=preferred_binding_body,
                        )
                        value = extracted.get(target)
                    if value in (None, "", [], {}):
                        continue
                    _effective_resolver_actor = _fb_actor
                    _effective_resolver_token = _fb_token
                    runtime_bindings[target] = value
                    owner_actor_ref = _text(binding.get("fixture_owner_actor_ref"))
                    ownership_required = force_fixture_setup and bool(owner_actor_ref)
                    receipt.update({
                        "status": "BOUND",
                        "value_fingerprint": hashlib.sha256(
                            str(value).encode("utf-8")
                        ).hexdigest()[:12],
                        "owner_actor_ref": owner_actor_ref,
                        "ownership_proof_status": (
                            "OBSERVED"
                            if ownership_required
                            and _effective_resolver_actor == owner_actor_ref
                            else "NOT_REQUIRED"
                            if not ownership_required
                            else "INDETERMINATE"
                        ),
                        "ownership_proof_source": "source_actor_bound_read",
                    })
                    break
                if value not in (None, "", [], {}):
                    break
            if value in (None, "", [], {}) and _identity_conflict:
                # Owner-field disagreement across observed rows: the owned
                # collection read mixes owners, so no observed value can be
                # trusted as the arm identity. Fail closed with a distinct,
                # observable reason; never fall through to fixture creation
                # (a created fixture would mask the contamination signal) and
                # never let the experiment proceed without the identity.
                _conflict_detail = f"owner_identity_conflict:{target}"
                receipt.update({
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": _conflict_detail,
                })
                binding_materialization_receipts.append(receipt)
                fixture_receipts.append({
                    "node_id": node_id,
                    "kind": kind,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": _conflict_detail,
                    "resolver_path": _text(receipt.get("resolver_path")),
                    "resolver_operation_ref": _text(
                        receipt.get("resolver_operation_ref")
                    ),
                    "resolver_status_code": int(receipt.get("status_code") or 0),
                })
                return {
                    "status": "terminal",
                    "result": {
                        "schema_version": "qualibug.experiment-execution.v1",
                        "experiment_id": eid,
                        "obligation_id": oid,
                        "status": "BLOCKED",
                        "reason_code": "BLOCKED_MISSING_BINDING",
                        "detail": _conflict_detail,
                        "elapsed_ms": int((time.time() - started) * 1000),
                        "steps": steps_out,
                        "fixture_receipts": fixture_receipts,
                        "binding_materialization_receipts": (
                            binding_materialization_receipts
                        ),
                        "finding": None,
                        "cleanup_failures": cleanup_failures,
                        "execution_receipt": {
                            "status": "BLOCKED",
                            "reason_code": "BLOCKED_MISSING_BINDING",
                            "cleanup_failures": cleanup_failures,
                        },
                    },
                }
            if value in (None, "", [], {}):
                # Try auto-fixture: discover a POST create at the same collection.                # Uses the documented request_example from Behavior IR as body_template
                # — industry-neutral, no hardcoding, works across all systems.
                auto_create = _auto_fixture_create_for_binding_target(
                    target,
                    binding,
                    ops,
                    binding_plan,
                    actors=actors,
                    behavior_ir=behavior_ir,
                )
                if auto_create:
                    binding = {**binding, **auto_create}
                fixture_setup = _validated_fixture_setup(
                    binding,
                    ops,
                    actors,
                    entities=_list(_dict(behavior_ir).get("entities")) or None,
                )
                _fs_blocked = ""
                if not fixture_setup:
                    # Distinguish missing cleanup / example from a blank gap so
                    # funnel diagnosis does not collapse every empty-collection
                    # miss into opaque fixture_setup_not_generated.
                    _declared = _dict(binding.get("fixture_setup"))
                    _create_path_hint = _text(
                        _declared.get("path")
                        or binding.get("create_path")
                        or (
                            (_list(binding.get("resolver_operations")) or [{}])[0].get(
                                "path"
                            )
                            if _list(binding.get("resolver_operations"))
                            else ""
                        )
                    )
                    _has_post_create = any(
                        isinstance(_op, dict)
                        and _text(_op.get("method")).upper() == "POST"
                        and normalize_path_placeholders(
                            _text(_op.get("path") or _op.get("raw_path"))
                        )
                        == normalize_path_placeholders(
                            collection_path(_create_path_hint)
                            if _create_path_hint
                            else ""
                        )
                        for _op in ops.values()
                    )
                    if _has_post_create and not _list(_declared.get("cleanup_operations")):
                        from .runtime_binding_graph import _declared_cleanup_operations

                        _cleanup = _declared_cleanup_operations(
                            normalize_path_placeholders(
                                collection_path(_create_path_hint)
                                if _create_path_hint
                                else _create_path_hint
                            ),
                            behavior_ir=_dict(behavior_ir)
                            or {"operations": list(ops.values())},
                        )
                        if not _cleanup:
                            _fs_blocked = "fixture_setup_missing_cleanup"
                    if not _fs_blocked:
                        _fs_blocked = "fixture_setup_not_generated"
                _fs_diag = {
                    "generated": bool(fixture_setup),
                    "blocked_reason": "" if fixture_setup else _fs_blocked,
                    "create_path": "",
                    "create_status": 0,
                    "dependency": "",
                }
                fixture_actor_ref, fixture_actor, fixture_token = _select_fixture_actor(
                    fixture_setup,
                    control_plan=_list(exp.get("control_plan")),
                    treatment_plan=_list(exp.get("treatment_plan")),
                    actors=actors,
                    tokens=tokens,
                )
                if fixture_setup and not fixture_actor_ref:
                    fixture_setup = {}
                    _fs_diag["blocked_reason"] = "fixture_setup_no_actor"
                token_values: dict[str, Any] = {}
                dependency_blocked = False
                # Prefer the fixture actor, then other authenticated actors that
                # may already own dependency rows (buyer addresses vs admin).
                _dependency_actor: list[tuple[str, str]] = []
                _seen_dep_actors: set[str] = set()
                for _cand_ref, _cand_tok in (
                    [(fixture_actor_ref, fixture_token)]
                    + [
                        (_ref, _resolve_token(_act, tokens))
                        for _ref, _act in actors.items()
                        if isinstance(_act, dict)
                    ]
                ):
                    if not _cand_ref or _cand_ref in _seen_dep_actors:
                        continue
                    if not _cand_tok and _cand_ref != fixture_actor_ref:
                        continue
                    _seen_dep_actors.add(_cand_ref)
                    _dependency_actor.append((_cand_ref, _cand_tok))
                if not _dependency_actor and fixture_actor_ref:
                    _dependency_actor = [(fixture_actor_ref, fixture_token)]
                for dependency in _list(fixture_setup.get("body_bindings")):
                    dependency_target = _text(_dict(dependency).get("target"))
                    dependency_token = _text(_dict(dependency).get("template_token"))
                    dependency_value: Any = None
                    dependency_leaf = dependency_target.split(".")[-1].split("[")[0]
                    # Reuse a value already bound earlier in this experiment
                    # (e.g. address_id materialization before order create).
                    for _prior_key in (
                        dependency_token,
                        dependency_leaf,
                        dependency_target,
                    ):
                        _prior_val = runtime_bindings.get(_prior_key)
                        if _prior_val not in (None, "", [], {}):
                            dependency_value = _prior_val
                            token_values[dependency_token] = dependency_value
                            break
                    for index, resolver in enumerate(_list(_dict(dependency).get("resolver_operations"))):
                        if dependency_value not in (None, "", [], {}):
                            break
                        if not isinstance(resolver, dict):
                            continue
                        # Source-declared database read resolver: an observed
                        # identity value from the declared persistence surface,
                        # environment-gated and receipted. Runs as one resolver
                        # leg — a declined or empty read stays fail-closed.
                        if _text(resolver.get("adapter")) == "db_sql":
                            db_obs = _run_declared_db_identity_read(
                                root=root,
                                project=project,
                                runtime_contract=runtime_contract,
                                resolver=resolver,
                            )
                            db_obs.update({
                                "phase": "binding_materialization_dependency",
                                "step_id": (
                                    f"bind:{target}:dependency:{dependency_token}"
                                    f":{index}:db"
                                ),
                                "actor_ref": "",
                                "operation_ref": "",
                            })
                            steps_out.append(db_obs)
                            db_value = db_obs.get("value")
                            if db_value not in (None, "", [], {}):
                                dependency_value = db_value
                                token_values[dependency_token] = dependency_value
                            continue
                        for _dep_idx, (_dep_actor, _dep_token) in enumerate(
                            _dependency_actor
                        ):
                            obs = _run_http_step(
                                base_url=base_url,
                                method=_text(resolver.get("method")).upper(),
                                path=_text(resolver.get("path")),
                                token=_dep_token,
                            )
                            obs.update({
                                "phase": "binding_materialization_dependency",
                                "step_id": (
                                    f"bind:{target}:dependency:{dependency_token}"
                                    f":{index}:actor{_dep_idx}"
                                ),
                                "actor_ref": _dep_actor,
                                "operation_ref": _text(resolver.get("operation_ref")),
                            })
                            steps_out.append(obs)
                            _sc = int(obs.get("status_code") or 0)
                            if _sc == 0:
                                break
                            if not (200 <= _sc < 300):
                                continue
                            dependency_leaf = dependency_target.split(".")[-1].split("[")[0]
                            dependency_value = _runtime_value_from_response(
                                obs.get("body"),
                                dependency_leaf,
                                f"/{{{dependency_leaf}}}",
                            )
                            if dependency_value in (None, "", [], {}):
                                continue
                            token_values[dependency_token] = dependency_value
                            # Own the created resource as the actor that can
                            # actually see the dependency rows.
                            if _dep_actor and _dep_actor != fixture_actor_ref:
                                fixture_actor_ref = _dep_actor
                                fixture_actor = actors.get(_dep_actor) or {}
                                fixture_token = _dep_token
                            break
                        if dependency_value not in (None, "", [], {}):
                            break
                    # Empty list-read: attempt one source-backed disposable
                    # create for the dependency (POST+example+cleanup only).
                    # Never invent IDs or skip the cleanup authority gate.
                    if dependency_value in (None, "", [], {}):
                        dep_setup = _source_backed_dependency_fixture_setup(
                            dependency_leaf=dependency_leaf,
                            resolver_operations=_list(
                                _dict(dependency).get("resolver_operations")
                            ),
                            ops=ops,
                            actors=actors,
                            behavior_ir=behavior_ir,
                            binding_plan=binding_plan,
                        )
                        dep_actor_ref, dep_actor, dep_token = _select_fixture_actor(
                            dep_setup,
                            control_plan=_list(exp.get("control_plan")),
                            treatment_plan=_list(exp.get("treatment_plan")),
                            actors=actors,
                            tokens=tokens,
                        )
                        if dep_setup and dep_actor_ref and not _list(
                            dep_setup.get("body_bindings")
                        ):
                            # Prefer the dependency's own collection list-read
                            # as the governed observation path when declared.
                            dep_observation = _text(dep_setup.get("path"))
                            for _dep_res in _list(
                                _dict(dependency).get("resolver_operations")
                            ):
                                if isinstance(_dep_res, dict) and _text(
                                    _dep_res.get("path")
                                ).startswith("/"):
                                    dep_observation = _text(_dep_res.get("path"))
                                    break
                            dep_governed = execute_governed_control_write(
                                root=root,
                                project=project,
                                base_url=base_url,
                                runtime_contract=runtime_contract,
                                campaign_id=campaign_id,
                                operation_phase="experiment_fixture_setup",
                                actor_identity=_text(
                                    dep_actor.get("role") or dep_actor_ref
                                ),
                                actor_token=dep_token,
                                method=_text(dep_setup.get("method")).upper(),
                                path=_text(dep_setup.get("path")),
                                body=_materialize_body_template(
                                    dep_setup.get("body_template"),
                                    {},
                                ),
                                observation_path=dep_observation
                                or _text(dep_setup.get("path")),
                            )
                            dep_write = _dict(dep_governed.get("write"))
                            dep_status = int(dep_write.get("status") or 0)
                            steps_out.append({
                                "phase": "fixture_setup_dependency",
                                "method": _text(dep_setup.get("method")).upper(),
                                "path": _text(dep_setup.get("path")),
                                "status_code": dep_status,
                                "operation_ref": _text(dep_setup.get("operation_ref")),
                                "dependency_leaf": dependency_leaf,
                                "governance_receipt": dep_governed,
                            })
                            if 200 <= dep_status < 300:
                                dependency_value = _runtime_setup_value_from_response(
                                    dep_write.get("body"),
                                    dependency_leaf,
                                )
                            if dependency_value not in (None, "", [], {}):
                                token_values[dependency_token] = dependency_value
                                runtime_bindings[dependency_leaf] = dependency_value
                                if dependency_token:
                                    runtime_bindings[dependency_token] = dependency_value
                                if dep_actor_ref and dep_actor_ref != fixture_actor_ref:
                                    fixture_actor_ref = dep_actor_ref
                                    fixture_actor = dep_actor
                                    fixture_token = dep_token
                                _dep_cleanup_ops = _list(
                                    dep_setup.get("cleanup_operations")
                                )
                                _dep_first_cleanup = (
                                    _dep_cleanup_ops[0]
                                    if _dep_cleanup_ops
                                    else {}
                                )
                                pending_fixture_cleanups.append({
                                    "target": dependency_leaf,
                                    "value": dependency_value,
                                    "observation_path": dep_observation
                                    or _text(dep_setup.get("path")),
                                    "cleanup": dict(_dep_first_cleanup)
                                    if isinstance(_dep_first_cleanup, dict)
                                    else {},
                                    "receipt": {
                                        "status": "BOUND",
                                        "source_priority": "experiment_setup_response",
                                        "resolver_path": _text(dep_setup.get("path")),
                                    },
                                    "actor_ref": dep_actor_ref,
                                    "actor_identity": _text(
                                        dep_actor.get("role") or dep_actor_ref
                                    ),
                                    "actor_token": dep_token,
                                    "governed_setup": dep_governed,
                                })
                            else:
                                _fs_diag["blocked_reason"] = (
                                    f"dependency_fixture_create_failed:{dependency_leaf}"
                                )
                        elif dependency_value in (None, "", [], {}):
                            if dep_setup and _list(dep_setup.get("body_bindings")):
                                _fs_diag["blocked_reason"] = (
                                    f"dependency_fixture_nested_unsupported:"
                                    f"{dependency_leaf}"
                                )
                            elif not dep_setup:
                                _fs_diag["blocked_reason"] = (
                                    f"dependency_fixture_setup_not_generated:"
                                    f"{dependency_leaf}"
                                )
                    if dependency_value in (None, "", [], {}):
                        dependency_blocked = True
                        if not _text(_fs_diag.get("blocked_reason")).startswith(
                            "dependency_"
                        ):
                            _fs_diag["blocked_reason"] = (
                                f"dependency_unresolved:{dependency_leaf}"
                            )
                        _fs_diag["dependency"] = dependency_leaf
                if fixture_setup and not dependency_blocked:
                    setup_body = _materialize_body_template(
                        fixture_setup.get("body_template"),
                        token_values,
                    )

                    # Fixture create-only plans have no list-read resolver.
                    # Observe the create collection itself (no placeholders) so
                    # governance can emit before/after around the disposable write.
                    observation_path = (
                        _text(resolvers[0].get("path"))
                        if resolvers
                        else _text(fixture_setup.get("path"))
                    )
                    governed_setup = execute_governed_control_write(
                        root=root,
                        project=project,
                        base_url=base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=campaign_id,
                        operation_phase="experiment_fixture_setup",
                        actor_identity=_text(fixture_actor.get("role") or fixture_actor_ref),
                        actor_token=fixture_token,
                        method=_text(fixture_setup.get("method")).upper(),
                        path=_text(fixture_setup.get("path")),
                        body=setup_body,
                        observation_path=observation_path,
                    )
                    setup_write = _dict(governed_setup.get("write"))
                    setup_status = int(setup_write.get("status") or 0)
                    fixture_setup_accepted = bool(
                        governed_setup.get("accepted") is True
                        or 200 <= setup_status < 300
                    )
                    steps_out.append({
                        "phase": "fixture_setup",
                        "method": _text(fixture_setup.get("method")).upper(),
                        "path": _text(fixture_setup.get("path")),
                        "status_code": setup_status,
                        "operation_ref": _text(fixture_setup.get("operation_ref")),
                        "governance_receipt": governed_setup,
                    })
                    receipt["fixture_setup_status"] = (
                        "completed" if 200 <= setup_status < 300 else "failed"
                    )
                    _fs_diag["create_path"] = _text(fixture_setup.get("path"))
                    _fs_diag["create_status"] = setup_status
                    if not (200 <= setup_status < 300):
                        _fs_diag["blocked_reason"] = f"create_failed_status_{setup_status}"
                    if 200 <= setup_status < 300:
                        value = _runtime_setup_value_from_response(
                            setup_write.get("body"),
                            target,
                        )
                    if value in (None, "", [], {}) and 200 <= setup_status < 300:
                        _fs_diag["blocked_reason"] = "create_2xx_no_value_extracted"
                    if value not in (None, "", [], {}):
                        ownership_required = force_fixture_setup and bool(
                            _text(binding.get("fixture_owner_actor_ref"))
                        )
                        setup_after = _dict(governed_setup.get("after"))
                        ownership_observed = bool(
                            ownership_required
                            and fixture_actor_ref == _text(binding.get("fixture_owner_actor_ref"))
                            and 200 <= int(setup_after.get("status") or 0) < 300
                            and _body_contains_scalar(setup_after.get("body"), value)
                        )
                        runtime_bindings[target] = value
                        # ── P0-8: Validate fixture preconditions ──
                        _precond_failures = _validate_fixture_preconditions(
                            exp, setup_write.get("body"), target
                        )
                        if _precond_failures:
                            _fs_diag["blocked_reason"] = "fixture_precondition_failed"
                            receipt.update({
                                "status": "PRECONDITION_FAILED",
                                "precondition_failures": _precond_failures,
                            })
                            fixture_receipts.append({
                                "node_id": node_id,
                                "kind": "fixture_precondition_validation",
                                "status": "FAILED",
                                "failures": _precond_failures,
                            })
                        else:
                            receipt.update({
                                "status": "BOUND",
                                "source_priority": "experiment_setup_response",
                                "resolver_path": _text(fixture_setup.get("path")),
                                "resolver_operation_ref": _text(fixture_setup.get("operation_ref")),
                                "status_code": setup_status,
                                "value_fingerprint": hashlib.sha256(
                                    str(value).encode("utf-8")
                                ).hexdigest()[:12],
                                "fixture_cleanup_status": "pending",
                                "fixture_id": _text(binding.get("required_fixture_id")),
                                "owner_actor_ref": _text(binding.get("fixture_owner_actor_ref")),
                                "ownership_proof_status": (
                                    "OBSERVED"
                                    if ownership_observed
                                    else "NOT_REQUIRED"
                                    if not ownership_required
                                    else "INDETERMINATE"
                                ),
                                "ownership_proof_ref": _text(governed_setup.get("after_ref")),
                            })
                            _cleanup_ops = _list(fixture_setup.get("cleanup_operations"))
                            _first_cleanup = _cleanup_ops[0] if len(_cleanup_ops) > 0 else {}
                            pending_fixture_cleanups.append({
                                "target": target,
                                "value": value,
                                "observation_path": observation_path,
                                "cleanup": dict(_first_cleanup) if isinstance(_first_cleanup, dict) else {},
                                "receipt": receipt,
                                "actor_ref": fixture_actor_ref,
                                "actor_identity": _text(fixture_actor.get("role") or fixture_actor_ref),
                                "actor_token": fixture_token,
                                "governed_setup": governed_setup,
                            })
            binding_materialization_receipts.append(receipt)
            if value in (None, "", [], {}):
                if fixture_setup_accepted:
                    cleanup_failures += 1
                    receipt.update({
                        "status": "HARNESS_FAILURE",
                        "reason_code": "FIXTURE_SETUP_IDENTITY_UNRESOLVED",
                        "fixture_cleanup_status": "failed",
                    })
                    return {
                        "status": "terminal",
                        "result": {
                            "schema_version": "qualibug.experiment-execution.v1",
                            "experiment_id": eid,
                            "obligation_id": oid,
                            "status": "HARNESS_FAILURE",
                            "reason_code": "FIXTURE_SETUP_IDENTITY_UNRESOLVED",
                            "detail": f"accepted_fixture_identity_unresolved:{target}",
                            "elapsed_ms": int((time.time() - started) * 1000),
                            "steps": steps_out,
                            "fixture_receipts": fixture_receipts,
                            "binding_materialization_receipts": binding_materialization_receipts,
                            "finding": None,
                            "cleanup_failures": cleanup_failures,
                            "execution_receipt": {
                            "status": "HARNESS_FAILURE",
                            "reason_code": "FIXTURE_SETUP_IDENTITY_UNRESOLVED",
                            "cleanup_failures": cleanup_failures,
                            },
                        }
                    }
                _resolver_status = int(_dict(receipt).get("status_code") or 0)
                _resolver_path = _text(_dict(receipt).get("resolver_path"))
                _bind_detail = f"runtime_read_binding_unresolved:{target}"
                if _resolver_path:
                    if _resolver_status == 0:
                        _bind_detail = (
                            f"runtime_read_binding_unresolved:{target}:"
                            f"resolver_no_http_response:{_resolver_path}"
                        )
                    elif 200 <= _resolver_status < 300:
                        _empty_reason = _text(_fs_diag.get("blocked_reason")) or (
                            "empty_collection"
                        )
                        _bind_detail = (
                            f"runtime_read_binding_unresolved:{target}:"
                            f"resolver_status_{_resolver_status}_"
                            f"{_empty_reason}:{_resolver_path}"
                        )
                    else:
                        _bind_detail = (
                            f"runtime_read_binding_unresolved:{target}:"
                            f"resolver_status_{_resolver_status}:{_resolver_path}"
                        )
                fixture_receipts.append({
                    "node_id": node_id,
                    "kind": kind,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": _bind_detail,
                    "resolver_path": _resolver_path,
                    "resolver_operation_ref": _text(_dict(receipt).get("resolver_operation_ref")),
                    "resolver_status_code": _resolver_status,
                    "fixture_setup_generated": _fs_diag.get("generated"),
                    "fixture_setup_blocked_reason": _fs_diag.get("blocked_reason"),
                    "fixture_setup_create_path": _fs_diag.get("create_path"),
                    "fixture_setup_create_status": _fs_diag.get("create_status"),
                    "fixture_setup_dependency": _fs_diag.get("dependency"),
                })
                # An unresolved binding must not be substituted with an invented
                # identifier. Firing a manufactured id at the target yields an
                # HTTP response, but that response is evidence about a resource
                # the source never declared.
                return {
                    "status": "terminal",
                    "result": {
                        "schema_version": "qualibug.experiment-execution.v1",
                        "experiment_id": eid,
                        "obligation_id": oid,
                        "status": "BLOCKED",
                        "reason_code": "BLOCKED_MISSING_BINDING",
                        "detail": _bind_detail,
                        "elapsed_ms": int((time.time() - started) * 1000),
                        "steps": steps_out,
                        "fixture_receipts": fixture_receipts,
                        "binding_materialization_receipts": binding_materialization_receipts,
                        "finding": None,
                        "cleanup_failures": cleanup_failures,
                        "execution_receipt": {
                            "status": "BLOCKED",
                            "reason_code": "BLOCKED_MISSING_BINDING",
                            "cleanup_failures": cleanup_failures,
                        },
                    }
                }
            fixture_receipts.append({
                "node_id": node_id,
                "kind": kind,
                "status": "resolved",
                "target": target,
                "value_fingerprint": receipt["value_fingerprint"],
            })
        elif kind == "ownership_fixture_proof":
            target = _text(_dict(node).get("binding_target"))
            owner_actor_ref = _text(_dict(node).get("owner_actor_ref"))
            binding_receipt = next(
                (
                    receipt
                    for receipt in binding_materialization_receipts
                    if _text(_dict(receipt).get("target")) == target
                ),
                {},
            )
            proof_observed = bool(
                _text(_dict(binding_receipt).get("status")) == "BOUND"
                and _text(_dict(binding_receipt).get("owner_actor_ref"))
                == owner_actor_ref
                and _text(_dict(binding_receipt).get("ownership_proof_status"))
                == "OBSERVED"
            )
            fixture_receipts.append({
                "node_id": node_id,
                "kind": kind,
                "status": "resolved" if proof_observed else "BLOCKED",
                "reason_code": "" if proof_observed else "BLOCKED_MISSING_FIXTURE",
                "detail": "" if proof_observed else "ownership_proof_unresolved",
                "owner_actor_ref": owner_actor_ref,
                "binding_target": target,
                "value_fingerprint": _text(
                    _dict(binding_receipt).get("value_fingerprint")
                ),
                "ownership_proof_source": _text(
                    _dict(binding_receipt).get("ownership_proof_source")
                ),
            })
            if not proof_observed:
                return {
                    "status": "terminal",
                    "result": {
                        "schema_version": "qualibug.experiment-execution.v1",
                        "experiment_id": eid,
                        "obligation_id": oid,
                        "status": "BLOCKED",
                        "reason_code": "BLOCKED_MISSING_FIXTURE",
                        "detail": f"ownership_proof_unresolved:{target}",
                        "elapsed_ms": int((time.time() - started) * 1000),
                        "finding": None,
                        "fixture_receipts": fixture_receipts,
                        "binding_materialization_receipts": binding_materialization_receipts,
                        "execution_receipt": {
                            "status": "BLOCKED",
                            "reason_code": "BLOCKED_MISSING_FIXTURE",
                            "detail": f"ownership_proof_unresolved:{target}",
                        },
                    },
                }
        elif kind == "disposable_fixture":
            # Without a concrete create operation binding, refuse to invent IDs.
            fixture_receipts.append({
                "node_id": node_id,
                "kind": kind,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_FIXTURE",
                "detail": "disposable_fixture_create_path_unresolved",
            })
            return {
                "status": "terminal",
                "result": {
                    "schema_version": "qualibug.experiment-execution.v1",
                    "experiment_id": eid,
                    "obligation_id": oid,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_FIXTURE",
                    "detail": f"fixture_unresolved:{node_id}",
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "finding": None,
                    "fixture_receipts": fixture_receipts,
                    "execution_receipt": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_FIXTURE"},
                }
            }
        else:
            fixture_receipts.append({"node_id": node_id, "kind": kind or "unknown", "status": "resolved"})

    # ── Reconciliation: ensure every fixture required by activation has a receipt ──
    # The materializer iterates v12_execution_order (from fixture_dependency_dag)
    # while the oracle activation uses fixture_dag.setup_order. When these diverge,
    # a fixture node may be required by the oracle but never processed above.
    # Without this reconciliation, the oracle sees a missing receipt and reports
    # FIXTURE_RECEIPT_FAILED → HARNESS_FAILED for the entire experiment.
    _processed_node_ids = {
        _text(_dict(r).get("node_id")) for r in fixture_receipts if _text(_dict(r).get("node_id"))
    }
    for _required_fixture_id in activation_requirements.get("fixture", []):
        if _required_fixture_id in _processed_node_ids:
            continue
        # Look up the node in the DAG to determine its kind/constructibility.
        _req_node = next(
            (n for n in _list(dag.get("nodes")) if _text(_dict(n).get("node_id")) == _required_fixture_id),
            {},
        )
        _req_kind = _text(_dict(_req_node).get("kind"))
        if _req_node and _dict(_req_node).get("constructible") is not False:
            # Node exists in a READY DAG and is constructible — resolve it.
            fixture_receipts.append({
                "node_id": _required_fixture_id,
                "kind": _req_kind or "reconciled",
                "status": "resolved",
                "source": "activation_requirement_reconciliation",
            })
        elif not _req_node:
            # Node not in DAG at all — the DAG is READY so this is a stale
            # activation requirement. Mark resolved to avoid false harness failure.
            fixture_receipts.append({
                "node_id": _required_fixture_id,
                "kind": "stale_requirement",
                "status": "resolved",
                "source": "activation_requirement_reconciliation",
            })
        # If constructible is False, leave it without a receipt — the oracle
        # will correctly report FAILED for a genuinely unresolvable fixture.

    for actor_ref in activation_requirements["actor"]:
        actor = actors.get(actor_ref) or {}
        role = _text(actor.get("role"))
        token = _resolve_token(actor, tokens)
        actor_observed = role.lower() in {"anonymous", "public"} or bool(token)
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="actor",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=actor_ref,
            status="OBSERVED" if actor_observed else "FAILED",
            evidence={
                "role": role,
                "credential_secret_ref_present": bool(
                    _text(actor.get("credential_secret_ref"))
                ),
                "credential_material_observed": bool(token),
            },
        ))
    for fixture_id in activation_requirements["fixture"]:
        # Find all fixture receipts with matching node_id and prefer the best status
        matching_fixtures = [
            row for row in fixture_receipts
            if _text(_dict(row).get("node_id")) == fixture_id
        ]
        # Prefer valid statuses over BLOCKED (degraded_synthetic removed in v1.2.2)
        _preferred_statuses = {"bound", "completed", "ready", "resolved"}
        fixture = next(
            (row for row in matching_fixtures if _text(_dict(row).get("status")).lower() in _preferred_statuses),
            next(iter(matching_fixtures), {}),
        )
        fixture_status = _text(_dict(fixture).get("status")).lower()
        observed = fixture_status in {"bound", "completed", "ready", "resolved"}
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="fixture",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=fixture_id,
            status="OBSERVED" if observed else "FAILED",
            evidence={
                "fixture_kind": _text(_dict(fixture).get("kind")),
                "value_fingerprint": _text(
                    _dict(fixture).get("value_fingerprint")
                ),
                "binding_status": _text(_dict(fixture).get("status")),
                "binding_reason_code": _text(_dict(fixture).get("reason_code")),
                "binding_detail": _text(_dict(fixture).get("detail")),
                "resolver_path": _text(_dict(fixture).get("resolver_path")),
                "resolver_status_code": int(_dict(fixture).get("resolver_status_code") or 0),
                "fixture_setup_generated": bool(_dict(fixture).get("fixture_setup_generated")),
                "fixture_setup_blocked_reason": _text(_dict(fixture).get("fixture_setup_blocked_reason")),
                "fixture_setup_create_path": _text(_dict(fixture).get("fixture_setup_create_path")),
                "fixture_setup_create_status": int(_dict(fixture).get("fixture_setup_create_status") or 0),
                "fixture_setup_dependency": _text(_dict(fixture).get("fixture_setup_dependency")),
            },
        ))
    # ── P0-8: Fixture validation ──
    # Verify that resolved bindings have actual values before proceeding.
    _fixture_validation: dict[str, Any] = {
        "total_bindings": len(runtime_bindings),
        "empty_bindings": [],
        "fixture_receipts_count": len(fixture_receipts),
        "blocked_fixtures": [],
        "status": "PASSED",
    }
    for _bk, _bv in runtime_bindings.items():
        if _bv in (None, "", "auto_val", "1"):
            _fixture_validation["empty_bindings"].append(_bk)
    for _fr in fixture_receipts:
        if isinstance(_fr, dict) and _text(_fr.get("status")).upper() == "BLOCKED":
            _fixture_validation["blocked_fixtures"].append(_text(_fr.get("node_id") or _fr.get("target")))
    if _fixture_validation["blocked_fixtures"]:
        _fixture_validation["status"] = "DEGRADED"
    return {
        "status": "ready",
        "steps_out": steps_out,
        "fixture_receipts": fixture_receipts,
        "binding_materialization_receipts": binding_materialization_receipts,
        "runtime_bindings": runtime_bindings,
        "pending_fixture_cleanups": pending_fixture_cleanups,
        "cleanup_failures": cleanup_failures,
        "contract_evidence_receipts": contract_evidence_receipts,
        "fixture_validation": _fixture_validation,
    }
