"""Parameter fuzzer integration for v12 pipeline.
Extracted from v12_pipeline.py.
"""
from __future__ import annotations

import hashlib, json, re, os
from pathlib import Path
from typing import Any

from .pipeline_runtime import _dict


def _runtime_contract_allows_parameter_fuzzer_writes(runtime_contract: dict[str, Any]) -> bool:
    rc = _dict(runtime_contract)
    if str(rc.get("status") or "") != "approved":
        return False
    if not str(rc.get("approved_base_url") or "").strip():
        return False
    return str(rc.get("execution_mode") or "").strip() in {
        "approved_sandbox_write",
        "approved_test_write",
    }


def _prepare_parameter_fuzzer_catalog(
    catalog: list[dict[str, Any]],
    *,
    selected_paths: set[str],
    api_doc: str,
    runtime_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    writes_allowed = _runtime_contract_allows_parameter_fuzzer_writes(runtime_contract)
    prepared: list[dict[str, Any]] = []
    selected = {str(path or "") for path in selected_paths if str(path or "")}
    for route in catalog or []:
        if not isinstance(route, dict):
            continue
        path = str(route.get("path") or "")
        if selected and path not in selected:
            continue
        item = dict(route)
        method = str(item.get("method") or "GET").upper()
        if method in {"POST", "PUT", "PATCH", "DELETE"} and writes_allowed:
            template = item.get("request_template")
            provenance = str(item.get("body_template_provenance") or "")
            if not isinstance(template, dict) or not template:
                try:
                    from .auto_test_data_factory import build_source_grounded_request_body
                    built = build_source_grounded_request_body(api_doc, method, path)
                except Exception as exc:
                    raise RuntimeError(
                        f"parameter_fuzzer_body_materialization_failed:{method}:{path}:{type(exc).__name__}"
                    ) from exc
                template = built.get("body") if isinstance(built, dict) else {}
                provenance = str((built or {}).get("provenance") or "")
            if isinstance(template, dict) and template:
                item["request_template"] = dict(template)
                item["body_template_provenance"] = provenance or "source_grounded"
                if not isinstance(item.get("body_properties"), dict) or not item.get("body_properties"):
                    item["body_properties"] = {str(key): {} for key in template.keys() if str(key)}
                item["execution_policy"] = "disposable_sandbox_required"
                item["disposable_sandbox"] = {"approved": True}
        prepared.append(item)
    return prepared


def _parameter_fuzzer_trace_result(trace: dict[str, Any], method: str, path: str) -> tuple[int, Any]:
    for step in trace.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("method") or "").upper() != method:
            continue
        if str(step.get("path") or "") != path:
            continue
        response = _dict(step.get("response"))
        try:
            status = int(response.get("status_code") or response.get("status") or step.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        return status, response.get("body") if "body" in response else {}
    sandbox = _dict(trace.get("sandbox_write"))
    return 0, {"error": str(sandbox.get("reason") or trace.get("errors") or "governed_write_no_step")}


def _build_parameter_fuzzer_governed_write_executor(
    *,
    approved_base_url: str,
    root: Path,
    project: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    round_number: int,
    documented_routes: list[dict[str, Any]],
    safety_boundary: dict[str, Any] | None,
    selected_slice_by_path: dict[str, dict[str, Any]],
):
    from .real_id_resolver_base import normalize_path_placeholders

    def execute_governed_parameter_write(
        *, method: str, path: str, body: dict[str, Any],
        route: dict[str, Any], token: str,
    ) -> dict[str, Any]:
        from .sandbox_write_executor import execute_with_sandbox_write
        from .semantic_scenario_generator import ExecutableScenario, ScenarioStep

        route_source_refs = route.get("source_refs") or route.get("document_refs") or []
        slice_info = selected_slice_by_path.get(path) or selected_slice_by_path.get(normalize_path_placeholders(path)) or {}
        body_digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()[:12]
        scenario = ExecutableScenario(
            id=f"parameter_fuzzer:{method}:{normalize_path_placeholders(path)}:{body_digest}",
            title=f"Source-bound parameter mutation {method} {path}",
            description="Parameter fuzzer mutation routed through governed sandbox write executor.",
            category="input_validation", severity="P1",
            entity=str(route.get("entity") or ""),
            actors=[str(runtime_contract.get("actor_identity") or "")] if str(runtime_contract.get("actor_identity") or "") else [],
            steps=[ScenarioStep(order=1, action="parameter_fuzzer_mutation", api_method=method, api_path=path,
                body_template=dict(body), expected_status=0,
                body_provenance=str(route.get("body_template_provenance") or ""))],
            oracle_rules=["HttpStatusOracle.server_error_is_defect"],
            actor_token=str(token or ""), execution_policy="approved_sandbox_write",
            source_refs=list(route_source_refs) if isinstance(route_source_refs, list) else [],
            behavior_slice_id=str(slice_info.get("slice_id") or ""),
            behavior_slice_kind=str(slice_info.get("kind") or ""),
            discovery_round=int(round_number or 1), selection_origin="parameter_fuzzer",
            runtime_hints={"parameter_fuzzer": True},
        )
        trace = execute_with_sandbox_write(
            scenario, approved_base_url, root=root, project=project,
            runtime_contract=runtime_contract, campaign_id=campaign_id,
            safety_boundary=safety_boundary, observer_token=str(token or ""),
            documented_routes=documented_routes,
            execute_fn=lambda sc, bu, safety_boundary=None, write_observer=None: _execute_scenario(
                sc, bu, max_retries=0, safety_boundary=safety_boundary, write_observer=write_observer),
        )
        status, response = _parameter_fuzzer_trace_result(trace, method, path)
        sandbox = _dict(trace.get("sandbox_write"))
        return {"status": status, "response": response, "duration_ms": trace.get("duration_ms") or 0,
                "audit_path": str(sandbox.get("audit_path") or ""), "trace": trace}

    # _execute_scenario is imported from the enclosing module
    from . import v12_pipeline as _v12
    _execute_scenario = _v12._execute_scenario
    return execute_governed_parameter_write
