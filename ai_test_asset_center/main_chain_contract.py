from __future__ import annotations

"""Main enterprise-to-evidence chain contract.

This module answers one commercial question for a project run:

    Did the core chain actually connect?

The chain is intentionally narrow and product-critical:
enterprise inputs -> parsing/knowledge -> test plan -> execution -> bug discovery -> evidence bundle.
It reads the artifacts already produced by the existing runtime and produces a
single diagnostic contract instead of adding another execution engine.
"""

import json
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _safe_project_id, _write_json, config_paths

CHAIN_STAGES = (
    "enterprise_inputs",
    "knowledge_parse",
    "test_plan",
    "execution",
    "bug_discovery",
    "evidence_chain",
)


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8") or "null")
    except Exception:
        return fallback


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _stage(name: str, status: str, *, count: int = 0, message: str = "", blockers: list[str] | None = None, next_action: str = "") -> dict[str, Any]:
    return {
        "stage": name,
        "status": status,
        "ok": status == "passed",
        "count": int(count or 0),
        "message": message,
        "blockers": blockers or [],
        "next_action": next_action,
    }


def _input_stage(input_dir: Path) -> dict[str, Any]:
    config_exists = (input_dir / "real_project_config.json").exists()
    prd_exists = (input_dir / "prd.md").exists() and bool((input_dir / "prd.md").read_text(encoding="utf-8", errors="replace").strip())
    openapi_exists = (input_dir / "openapi.json").exists() or (input_dir / "openapi_raw.txt").exists()
    account_exists = (input_dir / "test_accounts.json").exists()
    count = sum(1 for value in (config_exists, prd_exists, openapi_exists, account_exists) if value)
    blockers: list[str] = []
    if not config_exists:
        blockers.append("missing_real_project_config")
    if not openapi_exists:
        blockers.append("missing_openapi_contract")
    if not prd_exists:
        blockers.append("missing_prd_or_business_docs")
    if not account_exists:
        blockers.append("missing_test_accounts")
    status = "passed" if config_exists and openapi_exists else "partial" if count else "missing"
    return _stage(
        "enterprise_inputs",
        status,
        count=count,
        message="企业输入资料已具备主配置和接口契约" if status == "passed" else "企业输入资料不完整，后续只能降级生成计划或线索",
        blockers=blockers,
        next_action="补齐 real_project_config.json、OpenAPI、PRD/业务资料和测试账号。",
    )


def _knowledge_stage(workspace_dir: Path, root: Path, project: str) -> dict[str, Any]:
    normalized_openapi = _read_json(workspace_dir / "normalized_openapi.json", {})
    path_count = len((normalized_openapi or {}).get("paths") or {}) if isinstance(normalized_openapi, dict) else 0
    knowledge_candidates = [
        root / "platform_outputs" / project / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
        root / "platform_workspace" / project / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
    ]
    knowledge_asset = next((_read_json(path, {}) for path in knowledge_candidates if path.exists()), {})
    summary = knowledge_asset.get("summary") if isinstance(knowledge_asset, dict) else {}
    oracle_count = int((summary or {}).get("oracle_count") or (summary or {}).get("rule_count") or 0) if isinstance(summary, dict) else 0
    source_count = int((summary or {}).get("active_source_count") or 0) if isinstance(summary, dict) else 0
    status = "passed" if path_count > 0 and (source_count > 0 or oracle_count > 0) else "partial" if path_count > 0 else "missing"
    blockers: list[str] = []
    if path_count <= 0:
        blockers.append("openapi_not_normalized")
    if source_count <= 0 and oracle_count <= 0:
        blockers.append("business_knowledge_not_materialized")
    return _stage(
        "knowledge_parse",
        status,
        count=path_count + source_count + oracle_count,
        message="接口契约和业务知识已进入可用结构化资产" if status == "passed" else "解析资产不完整，计划生成会缺少业务语义",
        blockers=blockers,
        next_action="运行 onboarding check 和企业知识中心构建，确保 normalized_openapi 与 knowledge asset 存在。",
    )


def _plan_stage(workspace_dir: Path) -> dict[str, Any]:
    probes = _items(_read_json(workspace_dir / "defect_probes.json", {}))
    executable = [probe for probe in probes if str(probe.get("execution_policy") or "execute") != "candidate_only"]
    status = "passed" if executable else "partial" if probes else "missing"
    blockers = [] if probes else ["no_defect_probes"]
    if probes and not executable:
        blockers.append("all_probes_candidate_only")
    return _stage(
        "test_plan",
        status,
        count=len(probes),
        message="已生成可执行测试计划" if status == "passed" else "已有候选计划但缺少可执行探针" if status == "partial" else "未生成测试计划",
        blockers=blockers,
        next_action="检查风险计划、OpenAPI 路径、账号和安全门禁，确保至少有可执行探针。",
    )


def _execution_stage(workspace_dir: Path, discovery_data: dict[str, Any]) -> dict[str, Any]:
    executions = _items(_read_json(workspace_dir / "probe_execution_result.json", {}))
    executed = [item for item in executions if item.get("response_status") is not None or item.get("duration_seconds") is not None]
    blocked = [item for item in executions if item.get("error")]
    network_requests = int(discovery_data.get("network_requests") or discovery_data.get("http_request_count") or len(executed) or 0)
    status = "passed" if network_requests > 0 or executed else "partial" if executions else "missing"
    blockers: list[str] = []
    if not executions:
        blockers.append("no_probe_execution_result")
    if executions and not executed and network_requests <= 0:
        blockers.append("no_real_execution_observed")
    if blocked:
        blockers.append("execution_errors_present")
    return _stage(
        "execution",
        status,
        count=len(executions),
        message="探针已有运行时回执" if status == "passed" else "执行记录存在但没有真实请求/响应回执" if status == "partial" else "没有执行记录",
        blockers=blockers,
        next_action="优先排查 base_url、服务映射、测试账号、环境安全门禁和执行策略。",
    )


def _bug_stage(output_dir: Path, discovery_data: dict[str, Any]) -> dict[str, Any]:
    discovered = _read_json(output_dir / "discovered_issues.json", {})
    issues = _items(discovered)
    validated = int(discovery_data.get("validated_bug_count") or ((discovery_data.get("summary") or {}).get("validated_bug_count") if isinstance(discovery_data.get("summary"), dict) else 0) or 0)
    candidate = int(discovery_data.get("candidate_issue_count") or 0)
    pending = int(discovery_data.get("pending_finding_count") or 0)
    status = "passed" if validated > 0 else "partial" if issues or candidate or pending else "missing"
    blockers: list[str] = []
    if not issues:
        blockers.append("no_discovered_issues")
    if issues and validated <= 0:
        blockers.append("no_validated_bug")
    return _stage(
        "bug_discovery",
        status,
        count=len(issues),
        message="已形成严格验证缺陷" if status == "passed" else "已有候选/线索，但尚未形成严格验证缺陷" if status == "partial" else "未发现问题或未产出缺陷文件",
        blockers=blockers,
        next_action="不要把候选线索当客户缺陷；继续补执行、复现、断言和证据。",
    )


def _has_raw_request(item: dict[str, Any]) -> bool:
    return isinstance(item.get("request"), dict) or isinstance(item.get("request_raw"), dict) or isinstance(item.get("raw_request"), dict)


def _has_raw_response(item: dict[str, Any]) -> bool:
    return isinstance(item.get("response"), dict) or isinstance(item.get("response_raw"), dict) or isinstance(item.get("raw_response"), dict)


def _has_expected_actual(item: dict[str, Any]) -> bool:
    return item.get("expected") is not None and item.get("actual") is not None


def _is_synthetic_evidence(item: dict[str, Any]) -> bool:
    if item.get("is_synthetic") is True or item.get("synthetic") is True:
        return True
    reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
    replay = item.get("replay") if isinstance(item.get("replay"), dict) else {}
    proof = item.get("proof") if isinstance(item.get("proof"), dict) else {}
    return bool(reproduction.get("is_synthetic") is True or replay.get("is_synthetic") is True or proof.get("is_synthetic") is True)


def _has_replay_or_reproduction(item: dict[str, Any]) -> bool:
    reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
    replay = item.get("replay") if isinstance(item.get("replay"), dict) else {}
    proof = item.get("proof") if isinstance(item.get("proof"), dict) else {}
    return any((
        bool(reproduction) and reproduction.get("is_synthetic") is not True,
        bool(replay) and replay.get("is_synthetic") is not True,
        proof.get("can_reproduce") is True,
        proof.get("repro_rate") not in (None, 0, "0", "0%"),
        item.get("can_reproduce") is True,
    ))


def _has_execution_receipt(item: dict[str, Any]) -> bool:
    return any((
        item.get("execution_id"),
        item.get("probe_id"),
        item.get("trace_id"),
        item.get("timestamp"),
        item.get("captured_at"),
        item.get("duration_seconds") is not None,
        item.get("response_status") is not None,
    ))


def _strict_evidence_items(evidence_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item for item in evidence_items
        if item.get("issue_id")
        and _has_raw_request(item)
        and _has_raw_response(item)
        and _has_expected_actual(item)
        and _has_replay_or_reproduction(item)
        and _has_execution_receipt(item)
        and not _is_synthetic_evidence(item)
    ]


def _evidence_stage(output_dir: Path) -> dict[str, Any]:
    evidence_bundle = _read_json(output_dir / "evidence_bundle.json", {})
    evidence_items = _items(evidence_bundle)
    issue_ids = {str(item.get("issue_id") or "") for item in _items(_read_json(output_dir / "discovered_issues.json", {})) if item.get("issue_id")}
    evidence_issue_ids = {str(item.get("issue_id") or "") for item in evidence_items if item.get("issue_id")}
    strict_items = _strict_evidence_items(evidence_items)
    strict_issue_ids = {str(item.get("issue_id") or "") for item in strict_items if item.get("issue_id")}
    blockers: list[str] = []
    if not evidence_items:
        blockers.append("no_evidence_bundle_items")
    if not evidence_issue_ids:
        blockers.append("missing_stable_issue_link")
    if issue_ids and len(issue_ids & evidence_issue_ids) < len(issue_ids):
        blockers.append("evidence_not_linked_to_all_issues")
    if evidence_items and not any(_has_raw_request(item) for item in evidence_items):
        blockers.append("missing_raw_request")
    if evidence_items and not any(_has_raw_response(item) for item in evidence_items):
        blockers.append("missing_raw_response")
    if evidence_items and not any(_has_expected_actual(item) for item in evidence_items):
        blockers.append("missing_expected_actual_pair")
    if evidence_items and not any(_has_replay_or_reproduction(item) for item in evidence_items):
        blockers.append("missing_replay_or_reproduction")
    if evidence_items and not any(_has_execution_receipt(item) for item in evidence_items):
        blockers.append("missing_execution_receipt")
    if evidence_items and any(_is_synthetic_evidence(item) for item in evidence_items):
        blockers.append("synthetic_evidence_present")
    if issue_ids and len(issue_ids & strict_issue_ids) < len(issue_ids):
        blockers.append("strict_evidence_not_linked_to_all_issues")
    status = "passed" if evidence_items and issue_ids and len(issue_ids & strict_issue_ids) == len(issue_ids) and not blockers else "partial" if evidence_items else "missing"
    return _stage(
        "evidence_chain",
        status,
        count=len(evidence_items),
        message="证据包已和缺陷稳定关联，具备原始请求/响应、期望/实际、复现回放和真实执行回执" if status == "passed" else "证据包存在但严格证据链字段不完整" if status == "partial" else "未产出证据包",
        blockers=blockers,
        next_action="为每个 issue_id 绑定原始 request/response、expected、actual、reproduction/replay、非 synthetic 标记和真实执行回执。",
    )


def evaluate_main_chain_contract(project_id: str = "real_project_demo", root: Path | None = None, *, persist: bool = True) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    output_dir = paths["output_dir"]
    workspace_dir = paths["workspace_dir"]
    discovery_data = _read_json(output_dir / "real_project_defect_data.json", {})
    discovery_data = discovery_data if isinstance(discovery_data, dict) else {}

    stages = [
        _input_stage(paths["input_dir"]),
        _knowledge_stage(workspace_dir, root, project),
        _plan_stage(workspace_dir),
        _execution_stage(workspace_dir, discovery_data),
        _bug_stage(output_dir, discovery_data),
        _evidence_stage(output_dir),
    ]
    passed = sum(1 for stage in stages if stage["status"] == "passed")
    partial = sum(1 for stage in stages if stage["status"] == "partial")
    missing = sum(1 for stage in stages if stage["status"] == "missing")
    first_blocked = next((stage for stage in stages if stage["status"] != "passed"), None)
    chain_ready = passed == len(stages)
    contract = {
        "phase": "main_enterprise_evidence_chain_contract",
        "project_id": project,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "chain_ready": chain_ready,
        "customer_defect_delivery_ready": bool(chain_ready and int(discovery_data.get("validated_bug_count") or 0) > 0),
        "stage_order": list(CHAIN_STAGES),
        "summary": {
            "passed_stage_count": passed,
            "partial_stage_count": partial,
            "missing_stage_count": missing,
            "first_blocked_stage": first_blocked.get("stage") if first_blocked else "",
            "first_blocked_next_action": first_blocked.get("next_action") if first_blocked else "",
        },
        "stages": stages,
        "artifact_paths": {
            "input_dir": str(paths["input_dir"].relative_to(root)).replace("\\", "/"),
            "workspace_dir": str(workspace_dir.relative_to(root)).replace("\\", "/"),
            "output_dir": str(output_dir.relative_to(root)).replace("\\", "/"),
            "defect_probes": str((workspace_dir / "defect_probes.json").relative_to(root)).replace("\\", "/"),
            "probe_execution_result": str((workspace_dir / "probe_execution_result.json").relative_to(root)).replace("\\", "/"),
            "discovered_issues": str((output_dir / "discovered_issues.json").relative_to(root)).replace("\\", "/"),
            "evidence_bundle": str((output_dir / "evidence_bundle.json").relative_to(root)).replace("\\", "/"),
            "real_project_defect_data": str((output_dir / "real_project_defect_data.json").relative_to(root)).replace("\\", "/"),
        },
        "commercial_rule": "客户缺陷只能来自完整链路：企业输入、结构化解析、计划、真实执行、缺陷发现和证据包全部通过。",
    }
    if persist:
        output_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        _write_json(output_dir / "main_chain_contract.json", contract)
        _write_json(workspace_dir / "main_chain_contract.json", contract)
    return contract
