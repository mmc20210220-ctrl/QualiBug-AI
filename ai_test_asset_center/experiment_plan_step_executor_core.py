"""Sequential control/treatment plan execution for experiments.

Extracted from ``experiment_executor.execute_one_experiment``. Executes
non-barrier plan steps after ``execute_barrier_plans``, preserving
control-body capture for treatment reuse and pre-transport binding blocks.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode

from .contract_oracles import build_contract_evidence_receipt
from .experiment_runtime_support import (
    _WRITE_METHODS,
    _declared_observation_path,
    _dict,
    _has_response_bound_create_observers,
    _list,
    _observation_state,
    _resolve_token,
    _response_bound_observation_path,
    _run_http_step,
    _runtime_entity_candidates,
    _sha256,
    _text,
    _unresolved_body_placeholders,
    _unresolved_path_placeholders,
    _missing_required_body_fields,
    _declared_required_field_removals,
    _foreign_key_violations,
    _unauthorized_actor_role,
    _resolve_body_credential_refs,
)
from .real_id_resolver import (
    infer_path_params,
)
from .real_id_resolver_base import normalize_path_placeholders
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    materialize_path as _materialize_path,
)
from .sandbox_write_executor import (
    _http_request,
    execute_governed_control_write,
    sandbox_write_allowed,
)
from .sandbox_write_executor_base import evaluator_request_trace


def _resolve_route_with_fallback(
    *,
    base_url: str,
    method: str,
    path: str,
    token: str,
) -> dict[str, Any]:
    """Execute one HTTP step against the single approved, campaign-bound base URL.

    This used to retry a 404 against arbitrary alternate hosts named by the
    operator-settable ``QUALIBUG_SERVICE_URLS`` environment variable, replaying the
    same actor token against every one of them. That let a single approved-target
    campaign spray a live actor credential across hosts nobody in the campaign
    contract approved -- an env-controlled bypass of the whole non-production
    target-policy gate. Multi-service discovery belongs to the declared connector /
    approved-target registry (``target_policy`` / ``enterprise_pilot_runtime``), not
    to a token replay loop reading process environment variables at request time.

    Returns the observation dict with a ``resolved_route`` field so downstream
    reporting keeps the same shape as before.
    """
    obs = _run_http_step(base_url=base_url, method=method, path=path, token=token)
    obs["resolved_route"] = {
        "strategy": "approved_target",
        "base_url": base_url,
        "path": path,
        "status_code": int(obs.get("status_code") or 0),
    }
    return obs


def execute_non_barrier_plans(
    *,
    control_plan: list[Any],
    treatment_plan: list[Any],
    consumed_barrier_steps: set[int],
    actors: dict[str, dict[str, Any]],
    ops: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    runtime_bindings: dict[str, Any],
    activation_requirements: dict[str, Any],
    observations: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    cleanup_failures: int = 0,
    cleanup_plan: list[Any] | None = None,
) -> dict[str, Any]:
    """Execute sequential control/treatment steps not consumed by barriers.

    Mutates ``observations`` in place. Returns steps and evidence bags for the
    caller to merge before cleanup compensation.
    """
    contract_evidence_receipts: list[dict[str, Any]] = []
    request_bodies_for_cleanup: dict[str, Any] = {}
    pre_transport_block_reasons: list[str] = []

    # ── V1.5.0 §21 / V1.6.2-R1: Per-Step Execution Ledger ──
    from .process_step_execution import ProcessStepLedger, attach_ledger_refs_to_observations
    _fixture_id = _text(_dict(observations.get("disposable_fixture_contract")).get("fixture_id"))
    _protocol_id = _text(
        observations.get("protocol_id")
        or (
            _dict(observations.get("protocol")).get("protocol_id")
            if isinstance(observations.get("protocol"), dict)
            else ""
        )
    )
    # Required step identities come from activation_requirements (compile/plan SSOT),
    # never forged from final HTTP responses.
    _required_step_ids: list[str] = []
    for _phase_key in ("control", "treatment"):
        for _sid in list(activation_requirements.get(_phase_key) or []):
            _text_sid = _text(_sid)
            if _text_sid and _text_sid not in _required_step_ids:
                _required_step_ids.append(_text_sid)
    process_ledger = ProcessStepLedger(
        experiment_id=eid,
        fixture_id=_fixture_id,
        campaign_id=_text(resolved_campaign_id or campaign_id),
        run_id=_text(resolved_execution_id),
        obligation_id=_text(oid),
        protocol_id=_protocol_id,
        required_step_ids=_required_step_ids,
    )
    attach_ledger_refs_to_observations(observations, process_ledger)

    source_observed_control_bodies: dict[str, Any] = {}
    source_body_control_blocked = False

    def _identity_block(
        *,
        phase: str,
        subject_id: str,
        step: dict[str, Any],
        reason_code: str,
        detail: str,
    ) -> dict[str, Any]:
        pre_transport_block_reasons.append(f"{reason_code}:{detail}")
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind=phase,
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=subject_id,
            status="BLOCKED",
            evidence={
                "request_reached_transport": False,
                "reason_code": reason_code,
                "detail": detail,
            },
        ))
        return {
            "phase": phase,
            "step_id": subject_id,
            "status": "blocked_request",
            "reason": reason_code,
            "detail": detail,
            "method": _text(step.get("method")).upper(),
            "path": _text(step.get("path") or step.get("path_template")),
            "status_code": 0,
            "actor_ref": _text(step.get("actor_ref")),
            "operation_ref": _text(step.get("operation_ref")),
        }

    def _exec_plan(plan: list[Any], *, phase: str) -> list[dict[str, Any]]:
        nonlocal cleanup_failures, source_body_control_blocked
        results = []
        planned_subjects = activation_requirements.get(phase) or []
        for index, step in enumerate(plan):
            if not isinstance(step, dict):
                continue
            actor_ref = _text(step.get("actor_ref"))
            op_ref = _text(step.get("operation_ref"))
            # Contract subject identity is step_id-authoritative, not positional.
            #
            # This fixes a live misalignment, not just a future N-step concern.
            # `planned_subjects` is activation_requirements[phase], built by
            # contract_oracles._plan_subjects over the UNFILTERED plan and then de-duplicated
            # with dict.fromkeys. But `plan` here is the barrier-FILTERED list built at the
            # two _exec_plan call sites, which drop every step already consumed as a barrier
            # participant. So any phase mixing barrier and non-barrier steps mislabels every
            # step after the first barrier step, filing its contract evidence receipt under
            # another step's identity.
            #
            # Provably identical for every existing plan: _plan_subjects derives each subject
            # as `step_id or id`, and every built-in protocol branch emits a literal
            # 'control_1' / 'treatment_1'. For a one-step phase planned_subjects is exactly
            # ['control_1'] or ['treatment_1'] and the step's own step_id is that same
            # literal, so the declared branch returns what planned_subjects[index] returned.
            # The positional and generated fallbacks are retained unchanged for a step that
            # declares no id.
            declared_step_id = _text(step.get("step_id"))
            subject_id = (
                declared_step_id
                if declared_step_id and declared_step_id in planned_subjects
                else (
                    planned_subjects[index]
                    if index < len(planned_subjects)
                    else declared_step_id
                    or f"{phase}:{op_ref or 'operation'}:{index + 1}"
                )
            )
            if phase == "treatment" and source_body_control_blocked:
                reason = "control_body_binding_blocked"
                pre_transport_block_reasons.append(reason)
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="BLOCKED",
                    evidence={
                        "write_reached_transport": False,
                        "reason_code": reason,
                    },
                ))
                results.append({
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BLOCKED_MISSING_BINDING",
                    "detail": reason,
                    "method": _text(step.get("method") or "POST").upper(),
                    "path": _text(step.get("path") or step.get("path_template")),
                    "status_code": 0,
                })
                continue
            if not actor_ref or actor_ref not in actors:
                results.append(_identity_block(
                    phase=phase,
                    subject_id=subject_id,
                    step=step,
                    reason_code="BLOCKED_MISSING_ACTOR",
                    detail=f"actor_identity_missing:{actor_ref or '<empty>'}",
                ))
                continue
            if not op_ref or op_ref not in ops:
                results.append(_identity_block(
                    phase=phase,
                    subject_id=subject_id,
                    step=step,
                    reason_code="BLOCKED_MISSING_OPERATION",
                    detail=f"operation_identity_missing:{op_ref or '<empty>'}",
                ))
                continue
            actor = actors[actor_ref]
            op = ops[op_ref]
            method = _text(op.get("method") or "GET").upper()
            path_template = _text(
                step.get("path") or op.get("path") or op.get("raw_path")
                or op.get("normalized_path") or op.get("path_template")
            )
            if not path_template:
                # SPEC §8.1: path missing → BLOCKED, no guessing from operation_id
                results.append(_identity_block(
                    phase=phase,
                    subject_id=subject_id,
                    step=step,
                    reason_code="BLOCKED_MISSING_OPERATION",
                    detail=f"source_declared_path_missing:{op_ref}",
                ))
                continue
            path = _materialize_path(
                path_template,
                runtime_bindings,
            )
            unresolved_path_tokens = _unresolved_path_placeholders(path)
            query_spec = _dict(step.get("query"))
            if query_spec and not unresolved_path_tokens:
                materialized_query: dict[str, str] = {}
                unresolved_query_tokens: list[str] = []
                for key, raw_value in query_spec.items():
                    name = _text(key)
                    if not name:
                        continue
                    value = raw_value
                    if isinstance(raw_value, str):
                        token_match = re.fullmatch(
                            r"\s*\{([A-Za-z_][A-Za-z0-9_]*)\}\s*",
                            raw_value,
                        )
                        if token_match:
                            token = _text(token_match.group(1))
                            bound = runtime_bindings.get(token)
                            if bound in (None, "", [], {}):
                                unresolved_query_tokens.append(token)
                                continue
                            value = bound
                    if value in (None, "", [], {}):
                        unresolved_query_tokens.append(name)
                        continue
                    materialized_query[name] = str(value)
                if unresolved_query_tokens:
                    unresolved_path_tokens = list(dict.fromkeys(
                        [*unresolved_path_tokens, *unresolved_query_tokens]
                    ))
                elif materialized_query:
                    separator = "&" if "?" in path else "?"
                    path = f"{path}{separator}{urlencode(materialized_query)}"
            if unresolved_path_tokens:
                # ── P0-PLACEHOLDER: BLOCK instead of force-strip ──
                # Unresolved path placeholders mean no real entity exists.
                # Executing with placeholder IDs (e.g. "1") guarantees 404/400
                # failures and wastes compute. Block the experiment until real
                # fixture data exists via Bootstrap.
                results.append({
                    "phase": phase,
                    "subject_id": subject_id,
                    "method": method,
                    "path": path,
                    "status_code": 0,
                    "skipped_reason": f"BLOCKED_UNRESOLVED_PATH_PLACEHOLDERS:{','.join(unresolved_path_tokens[:6])}",
                    "request": {},
                    "response": {"status_code": 0, "body": {}},
                })
                pre_transport_block_reasons.append(f"unresolved_path_placeholders:{','.join(unresolved_path_tokens[:6])}")
                continue
            request_body = (
                step.get("body")
                if "body" in step
                else op.get("request_example")
                if method in _WRITE_METHODS and op.get("request_example")
                else None
            )
            if method in _WRITE_METHODS:
                # V1.9c: The compiler may defer the write body to a runtime
                # projection of a source-observed entity row (the operation
                # declares a schema but no request example, or its documented
                # example carries foreign-key reference fields that only the
                # environment can satisfy). The projected body is real
                # environment data, never synthesized values; the required-
                # field gate below still fail-closes visibly when the observed
                # row cannot satisfy the schema. Observed values win over the
                # documentation example: reference fields (orderId) must name
                # a real row of the referenced entity, while the example's
                # scalar values (amount, reason) remain the documented body
                # and are kept wherever the observed projection does not
                # cover them.
                _observed_body = runtime_bindings.get("__observed_body")
                if isinstance(_observed_body, dict) and _observed_body:
                    if isinstance(request_body, dict) and request_body:
                        request_body = {**request_body, **_observed_body}
                    elif request_body in (None, {}, []):
                        request_body = deepcopy(_observed_body)
            request_body = _materialize_body_template(
                request_body,
                runtime_bindings,
            )
            request_body = _resolve_body_credential_refs(
                request_body,
                root=root,
                project=project,
            )
            # ── Runtime entity-state violation arm ──
            # Entity-state isolation rules (用户端不展示下架商品、草稿商品、内部
            # 商品) demand a treatment that references an entity the
            # environment really has in a non-public state. The compile-time
            # mutation descriptor names the status-carrying list read; here we
            # execute it with the step's own credentials and replace the
            # identity field with a non-public-state entity. Statuses are
            # classified by their own English meaning (ON_SALE/ACTIVE/ENABLED/
            # PUBLISHED/… are public; DRAFT/OFF_SALE/DISABLED/EXPIRED/DELETED
            # are not) — generic status vocabulary, never industry terms.
            _mutation_runtime = _dict(step.get("mutation"))
            if (
                _text(_mutation_runtime.get("class"))
                == "runtime_entity_state_violation"
                and isinstance(request_body, dict)
                and phase == "treatment"
            ):
                _identity_field = _text(_mutation_runtime.get("identity_field"))
                _status_field = _text(
                    _mutation_runtime.get("status_field") or "status"
                )
                _resolvers = _list(_mutation_runtime.get("resolver_operations"))
                if _identity_field and _resolvers:
                    _resolver = _dict(_resolvers[0])
                    _resolver_path = _text(_resolver.get("path"))
                    if _resolver_path:
                        _list_resp = _run_http_step(
                            base_url=base_url,
                            method="GET",
                            path=_resolver_path,
                            token=_resolve_token(actor, tokens),
                        )
                        if (
                            200
                            <= int(_list_resp.get("status_code") or 0)
                            < 300
                        ):
                            _PUBLIC_STATUSES = {
                                "on_sale", "active", "enabled", "published",
                                "available", "open", "normal", "listed",
                                "in_stock", "activated",
                            }
                            _violating = next(
                                (
                                    row
                                    for row in _runtime_entity_candidates(
                                        _list_resp.get("body")
                                    )
                                    if isinstance(row, dict)
                                    and str(
                                        row.get(_status_field) or ""
                                    ).lower()
                                    not in _PUBLIC_STATUSES
                                    and row.get(_identity_field) not in (None, "")
                                ),
                                None,
                            )
                            if _violating is not None:
                                _json_path = _text(
                                    _mutation_runtime.get("json_path") or ""
                                )
                                _nested_match = re.match(
                                    r"^\$\.([A-Za-z_]\w*)\[0\]\.([A-Za-z_]\w*)$",
                                    _json_path,
                                )
                                if _nested_match:
                                    _list_key, _elem_key = _nested_match.groups()
                                    _list_value = request_body.get(_list_key)
                                    if (
                                        isinstance(_list_value, list)
                                        and _list_value
                                        and isinstance(_list_value[0], dict)
                                    ):
                                        _first = dict(_list_value[0])
                                        _first[_elem_key] = _violating[
                                            _identity_field
                                        ]
                                        request_body = {
                                            **request_body,
                                            _list_key: [
                                                _first,
                                                *_list_value[1:],
                                            ],
                                        }
                                else:
                                    request_body = {
                                        **request_body,
                                        _identity_field: _violating[
                                            _identity_field
                                        ],
                                    }
            unresolved_body_tokens = _unresolved_body_placeholders(
                request_body,
                runtime_bindings,
            )
            if method in _WRITE_METHODS and unresolved_body_tokens:
                # ── P0-PLACEHOLDER: BLOCK instead of force-fill ──
                # Unresolved body placeholders mean fixture data is missing.
                # Force-filling with generated values creates fake data that
                # violates the Non-Production Execution Contract.
                results.append({
                    "phase": phase,
                    "subject_id": subject_id,
                    "method": method,
                    "path": path,
                    "status_code": 0,
                    "skipped_reason": f"BLOCKED_UNRESOLVED_BODY_PLACEHOLDERS:{','.join(unresolved_body_tokens[:6])}",
                    "request": {"body": request_body} if request_body else {},
                    "response": {"status_code": 0, "body": {}},
                })
                pre_transport_block_reasons.append(f"unresolved_body_placeholders:{','.join(unresolved_body_tokens[:6])}")
                continue
            if method in _WRITE_METHODS:
                # ── P1: fail fast on missing required body fields ──
                # The materialized body must satisfy the operation's declared
                # request schema. If a required field is absent/empty (e.g. the
                # target 500s on a not-null constraint like `sku`), block here
                # with a visible reason instead of letting the target error.
                missing_required = _missing_required_body_fields(request_body, op)
                if missing_required:
                    # Required-field-removal mutations deliberately omit a
                    # declared required field (single_constraint_mutation with
                    # constraint=required) to test whether the target rejects
                    # the malformed write. Blocking pre-transport would hide
                    # the very observation the protocol exists to make — a
                    # leaky target accepts the write (2xx) while a clean one
                    # rejects (4xx). Exempt the mutated field only.
                    _mutated_required = _declared_required_field_removals(step)
                    if _mutated_required:
                        missing_required = [
                            field
                            for field in missing_required
                            if field not in _mutated_required
                        ]
                if missing_required:
                    results.append({
                        "phase": phase,
                        "subject_id": subject_id,
                        "method": method,
                        "path": path,
                        "status_code": 0,
                        "skipped_reason": f"BLOCKED_MISSING_REQUIRED_BODY_FIELDS:{','.join(missing_required[:6])}",
                        "request": {"body": request_body} if request_body else {},
                        "response": {"status_code": 0, "body": {}},
                    })
                    pre_transport_block_reasons.append(
                        f"missing_required_body_fields:{','.join(missing_required[:6])}"
                    )
                    continue
            if method in _WRITE_METHODS:
                # ── P1: fail fast on fabricated/placeholder foreign keys ──
                # A bound user_id/order_id/coupon_code that resolves to a
                # placeholder, sentinel, or default (e.g. "1") cannot reference
                # a real entity and will 500/404 at the target. Block before
                # transport with a visible reason (§8.5.3: avoid fabricated IDs).
                fk_violations = _foreign_key_violations(request_body, op)
                if fk_violations:
                    results.append({
                        "phase": phase,
                        "subject_id": subject_id,
                        "method": method,
                        "path": path,
                        "status_code": 0,
                        "skipped_reason": f"BLOCKED_FABRICATED_FOREIGN_KEY:{','.join(fk_violations[:6])}",
                        "request": {"body": request_body} if request_body else {},
                        "response": {"status_code": 0, "body": {}},
                    })
                    pre_transport_block_reasons.append(
                        f"fabricated_foreign_key:{','.join(fk_violations[:6])}"
                    )
                    continue
            # ── P2: pre-transport actor permission check (§8.5.4) ──
            # 54 envelope-observed 403s (e.g. PATCH /api/users/admin/.../balance,
            # POST /api/products/admin) come from an actor whose role is not
            # permitted on the target operation. The contract declares the
            # required roles; block before transport instead of letting the
            # target return 403 (which the funnel would misread as a finding).
            # Exception: an authorization-comparison experiment executes its
            # treatment arm with a deliberately non-permitted actor and asserts
            # the rejection — blocking pre-transport would hide the very
            # observation (a missing role check returns 2xx) the experiment
            # exists to make.
            unauth_role = None
            if not observations.get("_experiment_expects_authorization_rejection"):
                unauth_role = _unauthorized_actor_role(op, actor)
            if unauth_role is not None:
                required_roles = [
                    str(r).lower()
                    for r in _list(op.get("required_roles") or op.get("allowed_roles"))
                ]
                results.append({
                    "phase": phase,
                    "subject_id": subject_id,
                    "method": method,
                    "path": path,
                    "status_code": 0,
                    "skipped_reason": f"BLOCKED_UNAUTHORIZED_ACTOR:{unauth_role}->{'|'.join(required_roles[:6])}",
                    "request": {"body": request_body} if (method in _WRITE_METHODS and request_body) else {},
                    "response": {"status_code": 0, "body": {}},
                })
                pre_transport_block_reasons.append(
                    f"unauthorized_actor:{unauth_role}->{'|'.join(required_roles[:6])}"
                )
                continue
            runtime_body_plan = deepcopy(_dict(step.get("runtime_body_plan")))
            if runtime_body_plan:
                identity_fields = infer_path_params(path_template)
                runtime_body_plan["identity_bindings"] = {
                    field: runtime_bindings[field]
                    for field in identity_fields
                    if field in runtime_bindings
                    and runtime_bindings[field] not in (None, "")
                }
                if phase == "treatment" and op_ref in source_observed_control_bodies:
                    request_body = deepcopy(source_observed_control_bodies[op_ref])
                    runtime_body_plan = {}
            mutation = _dict(step.get("mutation"))
            mutation_class = _text(
                mutation.get("class")
                or mutation.get("constraint")
                or mutation.get("operator")
                or step.get("protocol_step")
                or step.get("intent")
                or f"{phase}_request"
            )
            mutation_selector = _text(
                mutation.get("json_path")
                or mutation.get("field_selector")
                or mutation.get("field")
            )
            mutation_operator = _text(
                mutation.get("operator") or mutation.get("constraint")
            )
            request_body_fingerprint = _sha256(request_body)
            request_semantics_fingerprint = _sha256({
                "operation_ref": op_ref,
                "method": method,
                "path_template": path_template,
                "mutation_class": mutation_class,
                "mutation_selector": mutation_selector,
                "mutation_operator": mutation_operator,
                "request_body_fingerprint": request_body_fingerprint,
            })
            token = _resolve_token(actor, tokens)
            is_write = method in _WRITE_METHODS
            response_bound_observation: dict[str, Any] = {}
            runtime_body_blocked = False
            if is_write:
                allowed, reason = sandbox_write_allowed(
                    root=root,
                    project=project,
                    runtime_contract=runtime_contract,
                    actor_token=token,
                    actor_identity=_text(actor.get("role") or actor_ref),
                )
                if not allowed:
                    policy_reason = f"BLOCKED_TARGET_POLICY:{_text(reason)}"
                    pre_transport_block_reasons.append(policy_reason)
                    contract_evidence_receipts.append(
                        build_contract_evidence_receipt(
                            kind=phase,
                            experiment_id=eid,
                            obligation_id=oid,
                            campaign_id=resolved_campaign_id,
                            execution_id=resolved_execution_id,
                            subject_id=subject_id,
                            status="BLOCKED",
                            evidence={"reason_code": _text(reason)},
                        )
                    )
                    results.append({
                        "phase": phase,
                        "step_id": subject_id,
                        "status": "blocked_write",
                        "reason": reason,
                        "method": method,
                        "path": path,
                    })
                    continue
                observation_path = _declared_observation_path(
                    path_template,
                    ops,
                    runtime_bindings=runtime_bindings,
                    request_body=request_body,
                )
                # Collection creates often only declare identity GETs
                # (…/{id}). Those cannot materialize before the write; governance
                # may observe the create collection, and effect proof comes from
                # _response_bound_observation_path after a 2xx response.
                if not observation_path and _has_response_bound_create_observers(op, ops):
                    observation_path = normalize_path_placeholders(path_template)
                # ── Fallback: use compiled write_observers from the experiment ──
                # The compiler already resolved auto-observers (path-prefix matching
                # + readback resolver). When the runtime re-derivation above fails
                # (e.g. placeholder materialization gap), reuse the compiled result.
                if not observation_path:
                    for _wo in _list(observations.get("_compiled_write_observers")):
                        _wo_path = normalize_path_placeholders(
                            _text(_wo.get("path") or _wo.get("endpoint_template"))
                        )
                        if not _wo_path.startswith("/"):
                            continue
                        # Materialize placeholders from runtime bindings
                        _materialized_obs = _wo_path
                        for _bk, _bv in (runtime_bindings or {}).items():
                            if _bv in (None, ""):
                                continue
                            _materialized_obs = _materialized_obs.replace(
                                "{" + _bk + "}", str(_bv)
                            )
                        if (
                            _materialized_obs.startswith("/")
                            and not _unresolved_path_placeholders(_materialized_obs)
                        ):
                            observation_path = _materialized_obs
                            break
                # The observation path must be a source-declared read. Deriving
                # one from the write path assumes a URL convention the source
                # never stated, and reading back the write path itself makes the
                # write its own evidence.
                #
                # However, response-only experiments (authorization, validation)
                # assert on HTTP status codes and do NOT need effect observation.
                # Their write is expected to be rejected; no state change occurs.
                _EFFECT_OBSERVER_IDS_RT = {
                    "entity_state", "before_state", "after_state",
                    "final_state", "business_effect",
                }
                _compiled_observers_list = _list(observations.get("_compiled_observers"))
                _exp_observer_ids = {
                    _text(o.get("observer_id"))
                    for o in _compiled_observers_list
                    if isinstance(o, dict)
                }
                # Effect observers with resolver_operations or readback_contract_id
                # have their own readback mechanism (e.g. database observers) and
                # do not need an HTTP observation_path from the step executor.
                _effect_observers_with_resolvers = any(
                    isinstance(o, dict)
                    and _text(o.get("observer_id")) in _EFFECT_OBSERVER_IDS_RT
                    and (
                        bool(_list(o.get("resolver_operations")))
                        or bool(_text(o.get("readback_contract_id")))
                    )
                    for o in _compiled_observers_list
                )
                _needs_effect_observation = bool(
                    _exp_observer_ids & _EFFECT_OBSERVER_IDS_RT
                ) and not _effect_observers_with_resolvers
                if not observation_path and not _needs_effect_observation:
                    # Response-only experiment: send the write without before/after
                    # observation. The HTTP response IS the evidence.
                    observation_path = normalize_path_placeholders(path_template)
                if not observation_path:
                    reason_code = "BLOCKED_MISSING_OBSERVER"
                    pre_transport_block_reasons.append(reason_code)
                    contract_evidence_receipts.append(
                        build_contract_evidence_receipt(
                            kind=phase,
                            experiment_id=eid,
                            obligation_id=oid,
                            campaign_id=resolved_campaign_id,
                            execution_id=resolved_execution_id,
                            subject_id=subject_id,
                            status="BLOCKED",
                            evidence={
                                "write_reached_transport": False,
                                "reason_code": reason_code,
                            },
                        )
                    )
                    results.append({
                        "phase": phase,
                        "step_id": subject_id,
                        "status": "blocked_write",
                        "reason": reason_code,
                        "method": method,
                        "path": path,
                        "status_code": 0,
                    })
                    continue
                # A source-declared identity/state write whose cleanup restores
                # the exact before snapshot on the same operation is a
                # legitimate business operation under test: it cannot
                # permanently disable a runtime account, so the protected-
                # identity guard in the sandbox executor must not block it.
                _restorable_identity = any(
                    isinstance(_clean, dict)
                    and _text(_clean.get("mode")) == "restore_snapshot"
                    and _text(_clean.get("compensates_operation_ref"))
                    == _text(op_ref)
                    for _clean in _list(cleanup_plan)
                )
                governed = execute_governed_control_write(
                    root=root,
                    project=project,
                    base_url=base_url,
                    runtime_contract=runtime_contract,
                    campaign_id=campaign_id,
                    operation_phase=f"experiment_{phase}",
                    actor_identity=_text(actor.get("role") or actor_ref),
                    actor_token=token,
                    method=method,
                    path=path,
                    body=request_body,
                    observation_path=observation_path,
                    restorable_identity_mutation=_restorable_identity,
                    # Only pass runtime_body_plan when it is a real mutation plan.
                    # The compile freezer adds readback_contract/async_policy metadata
                    # to runtime_body_plan; the sandbox executor misinterprets any
                    # non-None plan as a mutation directive and blocks on missing
                    # schema_version. Separate the two concerns here.
                    runtime_body_plan=(
                        runtime_body_plan
                        if _text(runtime_body_plan.get("schema_version"))
                        == "qualibug.source-observed-mutation-plan.v1"
                        else None
                    ),
                )
                runtime_body_receipt = _dict(governed.get("runtime_body_receipt"))
                runtime_body_blocked = (
                    _text(runtime_body_receipt.get("status")).upper() == "BLOCKED"
                )
                if runtime_body_blocked:
                    reason_code = _text(
                        runtime_body_receipt.get("reason_code")
                        or governed.get("reason")
                    ) or "runtime_body_materialization_blocked"
                    pre_transport_block_reasons.append(reason_code)
                materialized_body = governed.get("materialized_request_body")
                if isinstance(materialized_body, dict) and materialized_body:
                    request_body = deepcopy(materialized_body)
                    request_body_fingerprint = _sha256(request_body)
                    request_semantics_fingerprint = _sha256({
                        "operation_ref": op_ref,
                        "method": method,
                        "path_template": path_template,
                        "mutation_class": mutation_class,
                        "mutation_selector": mutation_selector,
                        "mutation_operator": mutation_operator,
                        "request_body_fingerprint": request_body_fingerprint,
                    })
                    if phase == "control":
                        source_observed_control_bodies[op_ref] = deepcopy(request_body)
                request_bodies_for_cleanup[subject_id] = request_body
                write_receipt = _dict(governed.get("write"))
                # Zero-transport governance blocks after a real before-GET are
                # identity/mutation gaps, not connection failures. The write step
                # keeps status_code=0, which previously fell through finalize's
                # HARNESS_CONNECTION_FAILED fallback even when the observation
                # gateway had already counted the before request.
                _write_attempts = int(
                    governed.get("write_request_attempt_count") or 0
                )
                _before_obs = _dict(governed.get("before"))
                _before_status = int(_before_obs.get("status") or 0)
                _gov_reason = _text(
                    governed.get("reason") or write_receipt.get("error")
                )
                # Empty reason still means the write never left the harness after
                # a real before-GET — seal as pre-transport BLOCKED, not COHF.
                if (
                    _write_attempts == 0
                    and _before_status > 0
                    and "connection" not in _gov_reason.lower()
                ):
                    _block_reason = _gov_reason or "governed_write_not_attempted"
                    if _block_reason not in pre_transport_block_reasons:
                        pre_transport_block_reasons.append(_block_reason)
                if 200 <= int(write_receipt.get("status") or 0) < 300:
                    response_bound_path = _response_bound_observation_path(
                        op,
                        ops,
                        write_receipt.get("body"),
                    )
                    if response_bound_path:
                        response_bound_raw = _http_request(
                            _text(response_bound_path.get("method") or "GET"),
                            base_url.rstrip("/") + _text(response_bound_path.get("path")),
                            token=token,
                        )
                        response_bound_observation = {
                            "method": _text(response_bound_path.get("method") or "GET"),
                            "path": _text(response_bound_path.get("path")),
                            "path_template": _text(response_bound_path.get("path_template")),
                            "status_code": int(response_bound_raw.get("status") or 0),
                            "status": int(response_bound_raw.get("status") or 0),
                            "body": response_bound_raw.get("body"),
                            "headers": response_bound_raw.get("headers") or {},
                            "duration_ms": response_bound_raw.get("duration_ms"),
                            "phase": f"{phase}_response_bound_effect_observation",
                            "step_id": f"{subject_id}:response_bound_effect",
                            "actor_ref": actor_ref,
                            "operation_ref": _text(response_bound_path.get("operation_ref")),
                            "source_operation_ref": op_ref,
                        }
                        governed["response_bound_after"] = {
                            "method": _text(response_bound_path.get("method") or "GET"),
                            "url": base_url.rstrip("/") + _text(response_bound_path.get("path")),
                            "status": int(response_bound_raw.get("status") or 0),
                            "body": response_bound_raw.get("body"),
                            "headers": response_bound_raw.get("headers") or {},
                            "duration_ms": response_bound_raw.get("duration_ms"),
                        }
                        governed["response_bound_after_ref"] = (
                            "response_bound_after:"
                            f"{_text(response_bound_path.get('path'))}:"
                            f"{int(response_bound_raw.get('status') or 0)}"
                        )
                        governed["response_bound_observer_operation_ref"] = _text(
                            response_bound_path.get("operation_ref")
                        )
                obs = {
                    "method": method,
                    "path": path,
                    "status_code": int(write_receipt.get("status") or 0),
                    "body": write_receipt.get("body"),
                    "headers": write_receipt.get("headers") or {},
                    "duration_ms": write_receipt.get("duration_ms"),
                    "error": write_receipt.get("error") or governed.get("reason") or "",
                    "governance_receipt": governed,
                    "observation_path": observation_path,
                }
                if response_bound_observation:
                    obs["response_bound_observation"] = response_bound_observation
            else:
                obs = _resolve_route_with_fallback(base_url=base_url, method=method, path=path, token=token)
            obs["phase"] = phase
            obs["step_id"] = subject_id
            obs["actor_ref"] = actor_ref
            obs["operation_ref"] = op_ref
            obs["path_template"] = path_template
            obs["request_body_fingerprint"] = request_body_fingerprint
            obs["request_semantics_fingerprint"] = (
                request_semantics_fingerprint
            )
            obs["mutation_class"] = mutation_class
            obs["mutation_selector"] = mutation_selector
            obs["mutation_operator"] = mutation_operator
            if runtime_body_blocked:
                obs["status"] = "blocked_write"
                obs["reason"] = _text(
                    _dict(obs.get("governance_receipt")).get("reason")
                ) or "runtime_body_materialization_blocked"
            observed_status = int(obs.get("status_code") or 0)
            obs["response_observed"] = observed_status > 0
            if _text(step.get("protocol_step")) == "temporal_write":
                temporal_elapsed = int(obs.get("duration_ms") or 0)
                after_state = _observation_state(
                    _dict(obs.get("governance_receipt")).get("after")
                )
                observations.setdefault("temporal_timeline", []).extend([
                    {
                        "event": "trigger",
                        "phase": phase,
                        "step_id": subject_id,
                        "at_ms": 0,
                        "status_code": observed_status,
                    },
                    {
                        "event": "final_observed",
                        "phase": phase,
                        "step_id": subject_id,
                        "at_ms": temporal_elapsed,
                        "status_code": int(after_state.get("status") or 0),
                    },
                ])
            _gov_for_status = _dict(obs.get("governance_receipt"))
            _write_reached = (
                int(_gov_for_status.get("write_request_attempt_count") or 0) > 0
            )
            # Governed writes blocked before transport (status_code=0,
            # write_request_attempt_count=0) are plan/binding gaps — BLOCKED.
            # FAILED is reserved for transport-reached requests that still
            # produced no usable response (connection drop after send, etc.).
            # Stamping FAILED here previously activated CONTRACT_ORACLE_HARNESS_FAILED
            # for 8× authorization/validation arms on T140342Z.
            _zero_transport_governed_block = (
                observed_status <= 0
                and bool(_gov_for_status)
                and not _write_reached
            )
            contract_status = (
                "BLOCKED"
                if runtime_body_blocked or _zero_transport_governed_block
                else "OBSERVED"
                if observed_status > 0
                else "FAILED"
            )
            contract_evidence_receipts.append(build_contract_evidence_receipt(
                kind=phase,
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=subject_id,
                status=contract_status,
                evidence={
                    "method": method,
                    "path": path,
                    "status_code": observed_status,
                    "operation_ref": op_ref,
                    "path_template": path_template,
                    "request_body_fingerprint": request_body_fingerprint,
                    "request_semantics_fingerprint": (
                        request_semantics_fingerprint
                    ),
                    "mutation_class": mutation_class,
                    "mutation_selector": mutation_selector,
                    "mutation_operator": mutation_operator,
                    "response_observed": observed_status > 0,
                    "write_reached_transport": _write_reached,
                    "request_reached_transport": (
                        observed_status > 0 or _write_reached
                    ),
                    "control_succeeded": (
                        200 <= observed_status < 300
                        if phase == "control"
                        else None
                    ),
                },
            ))
            results.append(obs)
            # ── V1.5.0 §21: Record step in ProcessStepLedger ──
            _gov = _dict(obs.get("governance_receipt"))
            _transport_rid = _text(_gov.get("receipt_id") or request_body_fingerprint)
            # Response body fingerprint proves transport only. Independent
            # business observation must be supplied by a typed Observer receipt;
            # otherwise a response can self-prove semantic completion.
            _response_rid = (
                _sha256(obs.get("body")) if int(observed_status or 0) > 0 else ""
            )
            _step_obs_rids: list[str] = []
            _before = _dict(_gov.get("before"))
            _after = _dict(_gov.get("after"))
            process_ledger.record_step_execution(
                step_id=subject_id,
                phase=phase,
                operation_ref=op_ref,
                actor_ref=actor_ref,
                runtime_identity=runtime_bindings,
                request_receipt_id=request_body_fingerprint,
                response_receipt_id=_response_rid,
                transport_receipt_id=_transport_rid,
                before_state_receipt_id=_text(
                    _before.get("receipt_id") or _before.get("observation_receipt_id")
                ),
                after_state_receipt_id=_text(
                    _after.get("receipt_id") or _after.get("observation_receipt_id")
                ),
                observer_receipt_ids=_step_obs_rids,
                cleanup_contract_id=_text(step.get("cleanup_contract_id")),
                status_code=observed_status,
                final_status="EXECUTED" if observed_status > 0 else "BLOCKED",
                target_reached=observed_status > 0,
            )
            process_ledger.record_timeline_event(
                step_id=subject_id,
                phase=phase,
                event_type="STEP_COMPLETED" if observed_status > 0 else "STEP_FAILED",
                operation_ref=op_ref,
                actor_ref=actor_ref,
                receipt_id=_transport_rid,
            )
            if response_bound_observation:
                results.append(response_bound_observation)
            if phase == "control":
                observations["control_observation"] = obs
                observations["control_actor_ref"] = actor_ref
                if 200 <= int(obs.get("status_code") or 0) < 300:
                    observations["control_succeeded"] = True
                    observations["authorized_control"] = True
                # Single-arm experiments (response-side constraint rules like
                # 导出结果禁止包含 password) have no treatment step, so the
                # control response must fill the top-level evidence slots
                # that assertions read (body/status_code); otherwise every
                # assertion reports HTTP_*_EVIDENCE_MISSING.
                if not treatment_plan:
                    observations["status_code"] = obs.get("status_code")
                    observations["body"] = obs.get("body")
            if phase == "treatment":
                observations["treatment_observation"] = obs
                observations["treatment_result"] = obs
                observations["treatment_actor_ref"] = actor_ref
                observations["status_code"] = obs.get("status_code")
                observations["body"] = obs.get("body")

            # Per-step evidence channel, append-only and parallel to the single slots above.
            #
            # Every http-shaped observer and assertion reads control_observation /
            # treatment_observation, which this loop OVERWRITES on each step. So in a plan
            # with more than one step per phase, steps 1..N-1 leave no trace and the
            # experiment still reports a verdict from the last one. An additive channel
            # keyed by step_id is the only way to expose per-step evidence without changing
            # what any existing observer sees.
            #
            # Guarded on len(plan) > 1, so for every existing one-step-per-phase experiment
            # the observations dict is byte-identical and no observer's input changes.
            #
            # Deliberately NOT written into barrier_timeline: that observer returns
            # INDETERMINATE without a release-class event and two distinct participants, so
            # feeding ordinary sequential steps into it would manufacture a degraded
            # concurrency reading out of a process that has no concurrency in it. The shape
            # and placement copy the temporal_timeline writer above.
            if len(plan) > 1:
                observations.setdefault("process_timeline", []).append({
                    "event": "step_observed",
                    "phase": phase,
                    "step_id": subject_id,
                    "step_ordinal": index + 1,
                    "step_count": len(plan),
                    "operation_ref": op_ref,
                    "actor_ref": actor_ref,
                    "status_code": observed_status,
                    "after_state": _observation_state(
                        _dict(obs.get("governance_receipt")).get("after")
                    ),
                })
        return results

    steps: list[dict[str, Any]] = []
    steps.extend(_exec_plan(
        [
            step for step in control_plan
            if not isinstance(step, dict) or id(step) not in consumed_barrier_steps
        ],
        phase="control",
    ))
    steps.extend(_exec_plan(
        [
            step for step in treatment_plan
            if not isinstance(step, dict) or id(step) not in consumed_barrier_steps
        ],
        phase="treatment",
    ))
    # Refresh id/hash + step sets after real event rows are written.
    attach_ledger_refs_to_observations(observations, process_ledger)
    return {
        "steps": steps,
        "contract_evidence_receipts": contract_evidence_receipts,
        "request_bodies_for_cleanup": request_bodies_for_cleanup,
        "pre_transport_block_reasons": pre_transport_block_reasons,
        "cleanup_failures": cleanup_failures,
        "process_step_ledger": process_ledger,
        "process_step_ledger_id": process_ledger.ledger_id,
        "process_step_ledger_hash": process_ledger.compute_hash(),
        "process_timeline": process_ledger.build_timeline_receipt(),
        "required_step_ids": list(process_ledger.required_step_ids),
        # Planned == required (compile/plan authority) ONLY — never fall back
        # to executed steps, which would make the balance check tautological.
        "planned_step_ids": list(process_ledger.required_step_ids),
        "executed_step_ids": process_ledger.executed_step_ids(),
    }
