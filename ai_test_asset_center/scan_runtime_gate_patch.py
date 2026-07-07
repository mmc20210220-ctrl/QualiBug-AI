from __future__ import annotations

"""Install a conservative runtime-scenario gate in front of the scan entrypoint.

The patch avoids a large rewrite of ``ai_test_asset_center.__main__``. It wraps
``scan`` so customer-provided runtime_scenario_contract payloads are validated
before V12 can receive a live base_url. For valid contracts, it also injects the
contract into ``SemanticScenarioGenerator.generate`` for the current scan call.
"""

import contextvars
import functools
from pathlib import Path
from typing import Any, Callable


_PATCHED = False
_ORIGINAL_SCAN: Callable[..., dict[str, Any]] | None = None
_ORIGINAL_GENERATE: Callable[..., list[Any]] | None = None
_RUNTIME_CONTRACT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("qualibug_runtime_scenario_contract", default={})


_READ_ONLY_METHODS = {"GET", "HEAD"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _runtime_block_contract(context: dict[str, Any], gaps: list[dict[str, str]], base_runtime_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = _as_dict(context.get("source_manifest"))
    source_manifest = {
        "source_id": str(manifest.get("source_id") or ""),
        "source_hash": str(manifest.get("source_hash") or ""),
        "source_version_id": str(manifest.get("source_version_id") or ""),
        "source_origin": str(manifest.get("source_origin") or ""),
    }
    codes = [str(item.get("code") or "") for item in gaps if str(item.get("code") or "")]
    runtime_contract = dict(base_runtime_contract or {})
    runtime_contract.update(
        {
            "status": "blocked",
            "reason": "runtime_scenario_contract_blocked",
            "source_manifest": runtime_contract.get("source_manifest") or source_manifest,
            "missing_requirements": sorted(set(codes)),
            "approved_base_url": "",
        }
    )
    return runtime_contract


def _blocked_scan(scan_module: Any, project: str, root: Path, started: float, gaps: list[dict[str, str]], context: dict[str, Any], save_report: bool, output_dir: Path | None) -> dict[str, Any]:
    runtime_contract = _runtime_block_contract(context, gaps)
    return scan_module._blocked_result(project, root, started, gaps, runtime_contract, context, save_report, output_dir)


def _contract_steps(raw_steps: Any, policy: str, actor_id: str, step_cls: Any) -> list[Any]:
    steps: list[Any] = []
    for index, row in enumerate(raw_steps if isinstance(raw_steps, list) else []):
        if not isinstance(row, dict):
            continue
        method = str(row.get("method") or row.get("api_method") or "").upper().strip()
        path = str(row.get("path") or row.get("api_path") or "").strip()
        if not method or not path.startswith("/"):
            continue
        if policy == "safe_read_only" and method not in _READ_ONLY_METHODS:
            continue
        if policy != "safe_read_only" and method not in _READ_ONLY_METHODS | _WRITE_METHODS:
            continue
        steps.append(
            step_cls(
                order=int(row.get("order") or index + 1),
                action=str(row.get("action") or f"{method} {path}"),
                api_method=method,
                api_path=path,
                body_template=row.get("body") if isinstance(row.get("body"), dict) else (row.get("body_template") if isinstance(row.get("body_template"), dict) else {}),
                extract_from_response=[str(item) for item in row.get("extract", row.get("extract_from_response", [])) if str(item)],
                expected_status=int(row.get("expected_status") or row.get("expected") or (200 if method in _READ_ONLY_METHODS else 201)),
                actor=str(row.get("actor") or actor_id),
            )
        )
    return steps


def _runtime_contract_scenarios(generator_module: Any, contract: dict[str, Any], discovery_round: int) -> list[Any]:
    if not isinstance(contract, dict) or not contract:
        return []
    policy = str(contract.get("execution_policy") or "safe_read_only").strip()
    actor = _as_dict(contract.get("actor"))
    actor_id = str(actor.get("id") or actor.get("name") or actor.get("actor") or "").strip()
    if not actor_id:
        return []
    step_cls = getattr(generator_module, "ScenarioStep")
    scenario_cls = getattr(generator_module, "ExecutableScenario")
    behavior_slice_id = getattr(generator_module, "behavior_slice_id")
    scenarios: list[Any] = []
    for index, row in enumerate(contract.get("scenarios") if isinstance(contract.get("scenarios"), list) else []):
        if not isinstance(row, dict):
            continue
        steps = _contract_steps(row.get("steps"), policy, actor_id, step_cls)
        if not steps:
            continue
        cleanup = _contract_steps(row.get("cleanup_steps") or row.get("cleanup"), "approved_sandbox_write", actor_id, step_cls)
        declared_slice_id = str(row.get("behavior_slice_id") or "").strip()
        slice_id = declared_slice_id or behavior_slice_id("runtime_contract", str(row.get("entity") or "runtime"), steps[0].api_method, steps[0].api_path)
        scenarios.append(
            scenario_cls(
                id=str(row.get("id") or f"SCN_RUNTIME_{index}"),
                title=str(row.get("title") or f"[运行合同] {steps[0].api_method} {steps[0].api_path}")[:160],
                description=str(row.get("description") or "Customer-approved runtime scenario contract."),
                category=str(row.get("category") or "runtime_contract"),
                severity=str(row.get("severity") or "P2"),
                entity=str(row.get("entity") or "runtime"),
                preconditions=[str(item) for item in row.get("preconditions", []) if str(item)] or ["runtime_scenario_contract_approved"],
                actors=[actor_id],
                steps=steps,
                expected_state=str(row.get("expected_state") or "runtime_observed"),
                oracle_rules=[str(item) for item in row.get("oracle_rules", []) if str(item)] or ["RuntimeContract.approved_step_executes"],
                cleanup_steps=cleanup,
                confidence=float(row.get("confidence") or 0.9),
                actor_token=str(actor.get("token") or ""),
                execution_policy=policy,
                evidence_gaps=[],
                source_refs=[{"source": "runtime_scenario_contract", "scenario_id": str(row.get("id") or index)}],
                behavior_slice_id=slice_id,
                behavior_slice_kind="runtime_contract",
                discovery_round=max(1, int(discovery_round or 1)),
            )
        )
    return scenarios


def _install_generator_patch() -> bool:
    global _ORIGINAL_GENERATE
    if _ORIGINAL_GENERATE is not None:
        return True
    try:
        from . import semantic_scenario_generator as generator_module
        generator_cls = generator_module.SemanticScenarioGenerator
    except Exception:
        return False
    original_generate = getattr(generator_cls, "generate", None)
    if not callable(original_generate):
        return False
    _ORIGINAL_GENERATE = original_generate

    @functools.wraps(original_generate)
    def patched_generate(self: Any, graphs: dict[str, Any], api_doc: str = "", active_slice_ids: set[str] | None = None, discovery_round: int = 1, *args: Any, **kwargs: Any) -> list[Any]:
        scenarios = list(original_generate(self, graphs, api_doc, active_slice_ids, discovery_round, *args, **kwargs))
        contract = _RUNTIME_CONTRACT.get({})
        runtime_scenarios = _runtime_contract_scenarios(generator_module, contract, discovery_round)
        if active_slice_ids is not None:
            runtime_scenarios = [item for item in runtime_scenarios if not getattr(item, "behavior_slice_id", "") or getattr(item, "behavior_slice_id", "") in active_slice_ids or getattr(item, "behavior_slice_kind", "") == "runtime_contract"]
        seen = {f"{getattr(item, 'behavior_slice_id', '')}|{getattr(item, 'id', '')}" for item in scenarios}
        for item in runtime_scenarios:
            key = f"{getattr(item, 'behavior_slice_id', '')}|{getattr(item, 'id', '')}"
            if key not in seen:
                scenarios.append(item)
                seen.add(key)
        return scenarios

    setattr(generator_cls, "generate", patched_generate)
    return True


def install_scan_runtime_gate() -> bool:
    global _PATCHED, _ORIGINAL_SCAN
    if _PATCHED:
        return True
    try:
        import time
        from .runtime_scenario_contract_gate import runtime_scenario_contract_gaps
        from . import __main__ as scan_module
    except Exception:
        return False
    original = getattr(scan_module, "scan", None)
    if not callable(original):
        return False
    _install_generator_patch()
    _ORIGINAL_SCAN = original

    @functools.wraps(original)
    def guarded_scan(
        project: str,
        root: Path | None = None,
        *,
        prd_text: str = "",
        api_doc_path: str = "",
        api_doc_text: str = "",
        base_url: str = "",
        ci_gate: bool = False,
        multi_layer: bool = True,
        output_dir: Path | None = None,
        save_report: bool = True,
        campaign_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(campaign_context or {})
        contract = _as_dict(context.get("runtime_scenario_contract"))
        if base_url and contract:
            gaps = runtime_scenario_contract_gaps(context)
            if gaps:
                resolved_root = Path(root or Path.cwd())
                safe_project = str(project or "").strip()
                if not safe_project:
                    return {"success": False, "error": "project is required"}
                return _blocked_scan(scan_module, safe_project, resolved_root, time.time(), gaps, context, save_report, output_dir)
        token = _RUNTIME_CONTRACT.set(contract)
        try:
            return original(
                project,
                root=root,
                prd_text=prd_text,
                api_doc_path=api_doc_path,
                api_doc_text=api_doc_text,
                base_url=base_url,
                ci_gate=ci_gate,
                multi_layer=multi_layer,
                output_dir=output_dir,
                save_report=save_report,
                campaign_context=context,
            )
        finally:
            _RUNTIME_CONTRACT.reset(token)

    setattr(scan_module, "scan", guarded_scan)
    _PATCHED = True
    return True


def restore_scan_runtime_gate() -> bool:
    global _PATCHED, _ORIGINAL_SCAN
    if not _PATCHED or _ORIGINAL_SCAN is None:
        return True
    try:
        from . import __main__ as scan_module
        setattr(scan_module, "scan", _ORIGINAL_SCAN)
    except Exception:
        return False
    _PATCHED = False
    _ORIGINAL_SCAN = None
    return True
