from __future__ import annotations

"""Conservative safety gate for customer-provided runtime scenario contracts."""

import math
from typing import Any

READ_ONLY_METHODS = {"GET", "HEAD"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
WRITE_POLICIES = {"approved_sandbox_write", "runtime_approved"}
ALLOWED_POLICIES = {"safe_read_only", "approved_sandbox_write", "runtime_approved"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _method(value: Any) -> str:
    return str(value or "").upper().strip()


def _path(value: Any) -> str:
    return str(value or "").strip()


def _steps(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _has_cleanup(row: dict[str, Any]) -> bool:
    cleanup = _steps(row.get("cleanup_steps") or row.get("cleanup"))
    return any(
        _method(item.get("method") or item.get("api_method")) in READ_ONLY_METHODS | WRITE_METHODS
        and _path(item.get("path") or item.get("api_path")).startswith("/")
        for item in cleanup
    )


def runtime_scenario_contract_gaps(context: dict[str, Any]) -> list[dict[str, str]]:
    contract = _as_dict(context.get("runtime_scenario_contract"))
    if not contract:
        return []
    policy = str(contract.get("execution_policy") or "safe_read_only").strip()
    actor = _as_dict(contract.get("actor"))
    scenarios = _steps(contract.get("scenarios"))
    gaps: list[dict[str, str]] = []

    if policy not in ALLOWED_POLICIES:
        gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_POLICY_INVALID", "detail": "runtime_scenario_contract.execution_policy is not allowed."})

    # ── Actor validation (P3-19: support per-step and multi-role actors) ──
    # The contract-level actor is required as a fallback, but individual steps
    # and scenarios may declare their own actors for cross-role comparison.
    has_contract_actor = bool(str(actor.get("id") or actor.get("name") or actor.get("actor") or "").strip())
    # A non-empty container is not actor evidence. At least one entry must carry
    # an explicit identity; otherwise compilation would create an anonymous
    # runtime principal.
    has_scenario_actors = any(
        any(
            (
                isinstance(item, str)
                and bool(item.strip())
            )
            or (
                isinstance(item, dict)
                and bool(
                    str(
                        item.get("id")
                        or item.get("name")
                        or item.get("actor")
                        or ""
                    ).strip()
                )
            )
            for item in row.get("actors", [])
        )
        for row in scenarios
        if isinstance(row, dict) and isinstance(row.get("actors"), list)
    ) if scenarios else False
    if not has_contract_actor and not has_scenario_actors:
        gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_ACTOR_MISSING", "detail": "runtime_scenario_contract requires an explicit customer-approved actor (at contract or scenario level)."})
    if not scenarios:
        gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_STEPS_MISSING", "detail": "runtime_scenario_contract requires at least one scenario with source-bound steps."})

    test_data = _as_dict(context.get("test_data_contract"))
    write_approved = test_data.get("write_approved") is True or contract.get("write_approved") is True
    compiled_scenario_ids: set[str] = set()
    for index, row in enumerate(scenarios):
        raw_scenario_actors = row.get("actors")
        scenario_actor_ids: list[str] = []
        if raw_scenario_actors is not None and not isinstance(raw_scenario_actors, list):
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "SCENARIO_ACTORS_INVALID", "detail": f"scenario[{index}].actors must be a list."})
        if isinstance(raw_scenario_actors, list):
            for actor_index, raw_actor in enumerate(raw_scenario_actors):
                actor_id = (
                    str(raw_actor).strip()
                    if isinstance(raw_actor, str)
                    else str(
                        _as_dict(raw_actor).get("id")
                        or _as_dict(raw_actor).get("name")
                        or _as_dict(raw_actor).get("actor")
                        or ""
                    ).strip()
                )
                if not actor_id:
                    gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "SCENARIO_ACTOR_ID_MISSING", "detail": f"scenario[{index}].actors[{actor_index}] has no id/name."})
                    continue
                if actor_id in scenario_actor_ids:
                    gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "SCENARIO_ACTOR_DUPLICATE", "detail": f"scenario[{index}] repeats actor {actor_id!r}."})
                    continue
                scenario_actor_ids.append(actor_id)
        if not has_contract_actor and not scenario_actor_ids:
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "SCENARIO_ACTOR_MISSING", "detail": f"scenario[{index}] has no explicit actor and no contract actor fallback."})

        confidence = row.get("confidence", 0.9)
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "SCENARIO_CONFIDENCE_INVALID", "detail": f"scenario[{index}].confidence must be a finite number between 0 and 1."})

        base_id = str(row.get("id") or f"SCN_RUNTIME_{index}").strip()
        identity_actors = scenario_actor_ids or (
            [_actor_id(actor)] if has_contract_actor else []
        )
        expanded_ids = [
            f"{base_id}_{actor_id}" if len(identity_actors) > 1 else base_id
            for actor_id in identity_actors
        ]
        if (
            not base_id
            or any(not scenario_id for scenario_id in expanded_ids)
            or any(scenario_id in compiled_scenario_ids for scenario_id in expanded_ids)
        ):
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "SCENARIO_ID_DUPLICATE", "detail": f"scenario[{index}] expands to a missing or duplicate scenario id."})
        compiled_scenario_ids.update(expanded_ids)

        steps = _steps(row.get("steps"))
        if not isinstance(row.get("steps"), list) or len(steps) != len(row.get("steps") or []):
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_STEPS_INVALID", "detail": f"scenario[{index}].steps must contain only objects."})
        if not steps:
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_STEPS_MISSING", "detail": f"scenario[{index}] has no executable steps."})
            continue
        methods = [_method(item.get("method") or item.get("api_method")) for item in steps]
        paths = [_path(item.get("path") or item.get("api_path")) for item in steps]
        if any(not method or method not in READ_ONLY_METHODS | WRITE_METHODS for method in methods):
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_METHOD_INVALID", "detail": f"scenario[{index}] contains an invalid HTTP method."})
        if any(not path.startswith("/") for path in paths):
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_PATH_INVALID", "detail": f"scenario[{index}] contains a non-source-bound path."})
        has_write = any(method in WRITE_METHODS for method in methods)
        if policy == "safe_read_only" and has_write:
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "WRITE_STEP_NOT_ALLOWED_IN_READ_ONLY_POLICY", "detail": f"scenario[{index}] contains a write step under safe_read_only policy."})
        if has_write and policy not in WRITE_POLICIES:
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "WRITE_POLICY_REQUIRED", "detail": f"scenario[{index}] contains a write step but execution_policy is not write-capable."})
        if has_write and not write_approved:
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "WRITE_APPROVAL_MISSING", "detail": "Write-capable runtime scenarios require test_data_contract.write_approved=true."})
        if has_write and not _has_cleanup(row):
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "CLEANUP_CONTRACT_MISSING", "detail": f"scenario[{index}] contains write steps but no cleanup_steps contract."})

        # ── Validate per-step actors (P3-19) ──
        observed_orders: set[int] = set()
        for step_idx, step in enumerate(steps):
            expected_status = step.get("expected_status", step.get("expected"))
            if expected_status is not None and (
                isinstance(expected_status, bool)
                or not isinstance(expected_status, int)
                or not 100 <= expected_status <= 599
            ):
                gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "STEP_EXPECTED_STATUS_INVALID", "detail": f"scenario[{index}].step[{step_idx}].expected_status must be an integer from 100 through 599."})
            if "order" in step:
                order = step.get("order")
                if (
                    isinstance(order, bool)
                    or not isinstance(order, int)
                    or order <= 0
                ):
                    gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "STEP_ORDER_INVALID", "detail": f"scenario[{index}].step[{step_idx}].order must be a positive integer."})
                elif order in observed_orders:
                    gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "STEP_ORDER_DUPLICATE", "detail": f"scenario[{index}] repeats step order {order}."})
                else:
                    observed_orders.add(order)
            if "actor" not in step:
                continue
            raw_step_actor = step.get("actor")
            step_actor_id = (
                str(raw_step_actor).strip()
                if isinstance(raw_step_actor, str)
                else str(
                    _as_dict(raw_step_actor).get("id")
                    or _as_dict(raw_step_actor).get("name")
                    or _as_dict(raw_step_actor).get("actor")
                    or ""
                ).strip()
            )
            if not step_actor_id:
                gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "STEP_ACTOR_ID_MISSING", "detail": f"scenario[{index}].step[{step_idx}] declares an actor without an id/name."})

    return gaps


def _actor_row(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        return {"id": value.strip()}
    return dict(fallback)


def _actor_id(value: dict[str, Any]) -> str:
    return str(
        value.get("id") or value.get("name") or value.get("actor") or ""
    ).strip()


def _compile_steps(
    raw_steps: Any,
    *,
    fallback_actor: dict[str, Any],
) -> list[Any]:
    from .semantic_scenario_generator import ScenarioStep

    compiled: list[ScenarioStep] = []
    for index, row in enumerate(_steps(raw_steps)):
        method = _method(row.get("method") or row.get("api_method"))
        path = _path(row.get("path") or row.get("api_path"))
        actor = _actor_row(row.get("actor"), fallback_actor)
        expected_raw = row.get("expected_status", row.get("expected"))
        expected_status = int(
            expected_raw
            if expected_raw is not None
            else (200 if method in READ_ONLY_METHODS else 201)
        )
        body = row.get("body")
        if not isinstance(body, dict):
            body = row.get("body_template")
        compiled.append(
            ScenarioStep(
                order=int(row.get("order") or index + 1),
                action=str(row.get("action") or f"{method} {path}"),
                api_method=method,
                api_path=path,
                body_template=dict(body) if isinstance(body, dict) else {},
                extract_from_response=[
                    str(item)
                    for item in row.get(
                        "extract",
                        row.get("extract_from_response", []),
                    )
                    if str(item)
                ],
                extract_where=dict(row.get("extract_where") or {})
                if isinstance(row.get("extract_where"), dict)
                else {},
                expected_status=expected_status,
                actor=_actor_id(actor),
                body_provenance="runtime_scenario_contract",
            )
        )
    return compiled


def compile_runtime_scenarios(
    context: dict[str, Any],
    *,
    discovery_round: int = 1,
) -> list[Any]:
    """Compile an approved runtime contract without mutating global classes.

    The same validator used at the scan boundary runs here so direct V12 callers
    cannot bypass policy, actor, source-bound path, write-approval, or cleanup
    requirements. Invalid contracts fail explicitly instead of being filtered.
    """

    gaps = runtime_scenario_contract_gaps(context)
    if gaps:
        codes = ",".join(sorted({str(item.get("code") or "") for item in gaps}))
        raise ValueError(f"runtime_scenario_contract_invalid:{codes}")

    contract = _as_dict(context.get("runtime_scenario_contract"))
    if not contract:
        return []

    from .business_state_graph import behavior_slice_id
    from .semantic_scenario_generator import ExecutableScenario

    policy = str(contract.get("execution_policy") or "safe_read_only").strip()
    contract_actor = _actor_row(contract.get("actor"), {})
    scenarios: list[ExecutableScenario] = []
    for index, row in enumerate(_steps(contract.get("scenarios"))):
        raw_actors = row.get("actors")
        actors = (
            [_actor_row(item, contract_actor) for item in raw_actors]
            if isinstance(raw_actors, list) and raw_actors
            else [contract_actor]
        )
        for actor in actors:
            actor_id = _actor_id(actor)
            steps = _compile_steps(row.get("steps"), fallback_actor=actor)
            cleanup_steps = _compile_steps(
                row.get("cleanup_steps") or row.get("cleanup"),
                fallback_actor=actor,
            )
            entity = str(row.get("entity") or "runtime").strip() or "runtime"
            declared_slice_id = str(row.get("behavior_slice_id") or "").strip()
            slice_id = declared_slice_id or behavior_slice_id(
                "runtime_contract",
                entity,
                steps[0].api_method,
                steps[0].api_path,
            )
            base_id = str(row.get("id") or f"SCN_RUNTIME_{index}").strip()
            scenario_id = f"{base_id}_{actor_id}" if len(actors) > 1 else base_id
            scenarios.append(
                ExecutableScenario(
                    id=scenario_id,
                    title=str(
                        row.get("title")
                        or f"Runtime contract: {steps[0].api_method} {steps[0].api_path}"
                    )[:160],
                    description=str(
                        row.get("description")
                        or "Customer-approved runtime scenario contract."
                    ),
                    category=str(row.get("category") or "runtime_contract"),
                    severity=str(row.get("severity") or "P2"),
                    entity=entity,
                    preconditions=[
                        str(item)
                        for item in row.get("preconditions", [])
                        if str(item)
                    ]
                    or ["runtime_scenario_contract_approved"],
                    actors=[actor_id],
                    steps=steps,
                    expected_state=str(
                        row.get("expected_state") or "runtime_observed"
                    ),
                    oracle_rules=[
                        str(item)
                        for item in row.get("oracle_rules", [])
                        if str(item)
                    ]
                    or ["RuntimeContract.approved_step_executes"],
                    cleanup_steps=cleanup_steps,
                    confidence=float(row.get("confidence") or 0.9),
                    actor_token=str(actor.get("token") or ""),
                    execution_policy=policy,
                    evidence_gaps=[],
                    source_refs=[
                        {
                            "source": "runtime_scenario_contract",
                            "scenario_id": base_id,
                            "actor": actor_id,
                        }
                    ],
                    behavior_slice_id=slice_id,
                    behavior_slice_kind="runtime_contract",
                    discovery_round=max(1, int(discovery_round or 1)),
                    selection_origin="customer_runtime_contract",
                )
            )
    scenario_ids = [str(item.id or "").strip() for item in scenarios]
    if not all(scenario_ids) or len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("runtime_scenario_contract_invalid:SCENARIO_ID_DUPLICATE")
    return scenarios
