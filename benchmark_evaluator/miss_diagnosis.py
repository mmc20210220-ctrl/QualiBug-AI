"""Evaluator-private miss diagnosis for the 131-bug SPC loop.

This module answers: why was each known bug not discovered?
It loads ground truth only on the evaluator side and must never feed GT
into discovery prompts, runtime context, or product-facing outputs.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .benchmark_compute import (
    _extract_api_paths,
    _finding_paths,
    _finding_text_blob,
    _load_truth_bugs,
    _paths_overlap,
    compute_benchmark,
)

FAILURE_STAGES: dict[int, str] = {
    1: "企业资料理解失败",
    2: "业务模型建立失败",
    3: "行为路径生成失败",
    4: "测试数据生成失败",
    5: "自动执行失败",
    6: "参数覆盖不足",
    7: "异常识别失败",
    8: "AI判断失败",
    9: "证据链不足",
}

_API_PATH_RE = re.compile(r"(?:GET|POST|PUT|PATCH|DELETE)\s+(/api/[^\s`]+)|(/api/[A-Za-z0-9_\-/{}.:]+)", re.I)
_MODULE_PREFIX = {
    "auth": "auth",
    "user": "user",
    "product": "product",
    "inv": "inventory",
    "cart": "cart",
    "coupon": "coupon",
    "order": "order",
    "pay": "payment",
    "refund": "refund",
    "report": "report",
    "db": "database",
    "ui": "ui",
}

_FIXTURE_BLOCK_MARKERS = (
    "fixture",
    "test_data",
    "disposable_fixture",
    "identity_mutation",
    "bootstrap",
    "seed",
)
_EXEC_FAIL_MARKERS = (
    "execution_failed",
    "request_failed",
    "timeout",
    "adapter",
    "http_error",
    "blocked_write",
)
_EVIDENCE_MARKERS = (
    "evidence",
    "reproduction",
    "observer",
    "cleanup",
    "audit_receipt",
)


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _path_from_url_or_path(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        return parsed.path or ""
    return text


def _normalize_path(path: str) -> str:
    cleaned = _path_from_url_or_path(path).split("?", 1)[0].rstrip("/").lower()
    cleaned = re.sub(r"/[0-9a-f]{8}-[0-9a-f-]{27,}", "/*", cleaned)
    cleaned = re.sub(r":[a-zA-Z_][a-zA-Z0-9_]*", "/*", cleaned)
    cleaned = re.sub(r"\{[^}]+\}", "/*", cleaned)
    cleaned = re.sub(r"/{2,}", "/", cleaned)
    return cleaned


def _bug_module(bug: dict[str, Any]) -> str:
    module = str(bug.get("module") or "").strip().lower()
    if module:
        module = module.replace("-service", "").replace("_service", "")
        return module
    bug_id = str(bug.get("bug_id") or "")
    prefix = bug_id.split("-", 1)[0].lower()
    return _MODULE_PREFIX.get(prefix, prefix or "unknown")


_MODULE_PATH_PREFIX: dict[str, tuple[str, ...]] = {
    "auth": ("/api/auth",),
    "user": ("/api/users", "/api/auth"),
    "product": ("/api/products",),
    "inventory": ("/api/inventory",),
    "cart": ("/api/cart",),
    "coupon": ("/api/coupons",),
    "order": ("/api/orders",),
    "payment": ("/api/payments",),
    "refund": ("/api/refunds",),
    "report": ("/api/reports",),
}


def _infer_paths_from_catalog(keywords: list[str], catalog_paths: set[str], module: str) -> set[str]:
    """Map bug keywords onto documented catalog paths (no GT hardcoding)."""
    inferred: set[str] = set()
    normalized_keywords = []
    for kw in keywords:
        raw = str(kw).strip().lower()
        if not raw:
            continue
        normalized_keywords.append(raw)
        normalized_keywords.append(raw.replace(" ", ""))
        if "/" in raw and raw.startswith("api"):
            normalized_keywords.append("/" + raw if not raw.startswith("/") else raw)
    for path in catalog_paths:
        path_l = path.lower()
        tokens = [t for t in path_l.strip("/").split("/") if t and t != "*"]
        for kw in normalized_keywords:
            if len(kw) < 3:
                continue
            if kw in path_l or any(kw == t or kw in t for t in tokens):
                inferred.add(path)
                break
    if not inferred:
        for prefix in _MODULE_PATH_PREFIX.get(module, ()):
            inferred |= {p for p in catalog_paths if p.startswith(prefix)}
    return inferred


def _bug_paths(bug: dict[str, Any], catalog_paths: set[str] | None = None) -> set[str]:
    parts = [
        str(bug.get("trigger") or ""),
        str(bug.get("title") or ""),
        str(bug.get("expected") or ""),
        str(bug.get("actual") or ""),
        " ".join(str(k) for k in (bug.get("match_keywords") or [])),
    ]
    for endpoint in bug.get("affected_endpoints") or bug.get("related_endpoints") or bug.get("related_apis") or []:
        if isinstance(endpoint, dict):
            parts.append(str(endpoint.get("path") or endpoint.get("api_path") or ""))
        else:
            parts.append(str(endpoint))
    paths = set()
    for part in parts:
        paths |= {_normalize_path(p) for p in _extract_api_paths(part)}
    paths = {p for p in paths if p.startswith("/api/")}
    if not paths and catalog_paths is not None:
        paths = _infer_paths_from_catalog(_bug_keywords(bug), catalog_paths, _bug_module(bug))
    return paths


def _bug_keywords(bug: dict[str, Any]) -> list[str]:
    raw = bug.get("match_keywords") if isinstance(bug.get("match_keywords"), list) else []
    keywords = [str(k).strip().lower() for k in raw if str(k).strip()]
    if not keywords:
        title = str(bug.get("title") or "").lower()
        keywords = [tok for tok in re.split(r"[\s/|:：,，]+", title) if len(tok) >= 3][:8]
    return keywords


def _load_catalog_paths(inputs_dir: Path) -> set[str]:
    paths: set[str] = set()
    openapi = _read_json(inputs_dir / "openapi.json")
    if isinstance(openapi, dict):
        for path in (openapi.get("paths") or {}):
            paths.add(_normalize_path(str(path)))
    for name in ("API_SPEC.md", "PRD.md", "BUSINESS_RULES.md"):
        text = _read_text(inputs_dir / name)
        for match in _API_PATH_RE.finditer(text):
            raw = match.group(1) or match.group(2) or ""
            if raw:
                paths.add(_normalize_path(raw))
    return {p for p in paths if p.startswith("/api/")}


def _load_prd_businesses(inputs_dir: Path) -> list[str]:
    text = _read_text(inputs_dir / "PRD.md")
    businesses: list[str] = []
    for match in re.finditer(r"^###\s+\d+\.\d+\s+(.+)$", text, re.M):
        name = match.group(1).strip()
        if name and name not in businesses:
            businesses.append(name)
    if not businesses:
        # Fallback: role/module headings from PRD/USER_ROLES
        for match in re.finditer(r"^##\s+\d+\.\s*(.+)$", text, re.M):
            name = match.group(1).strip()
            if name and name not in businesses:
                businesses.append(name)
    return businesses


def _collect_executed_paths(scan_result: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for item in list(scan_result.get("findings") or []) + list(scan_result.get("candidate_findings") or []):
        if not isinstance(item, dict):
            continue
        for path in _finding_paths(item):
            paths.add(_normalize_path(path))
        raw = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
        request = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
        if request.get("path"):
            paths.add(_normalize_path(str(request.get("path"))))
        repro = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
        if repro.get("path"):
            paths.add(_normalize_path(str(repro.get("path"))))
    for step in _iter_v12_execution_steps(scan_result):
        if _http_status(step) <= 0:
            continue
        if step.get("path"):
            paths.add(_normalize_path(str(step.get("path"))))
        if step.get("observation_path"):
            paths.add(_normalize_path(str(step.get("observation_path"))))
        response_bound = step.get("response_bound_observation")
        if isinstance(response_bound, dict) and _http_status(response_bound) > 0:
            if response_bound.get("path"):
                paths.add(_normalize_path(str(response_bound.get("path"))))
        governance = step.get("governance_receipt")
        if isinstance(governance, dict):
            for key in ("before", "write", "after", "response_bound_after"):
                row = governance.get(key)
                if isinstance(row, dict) and _http_status(row) > 0 and row.get("url"):
                    paths.add(_normalize_path(str(row.get("url"))))
    return {p for p in paths if p.startswith("/")}


def _collect_execution_blobs(scan_result: dict[str, Any]) -> list[str]:
    blobs: list[str] = []
    for item in list(scan_result.get("findings") or []) + list(scan_result.get("candidate_findings") or []):
        if not isinstance(item, dict):
            continue
        blobs.append(_finding_text_blob(item))
        raw = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
        request = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
        blobs.append(json.dumps(request, ensure_ascii=False, default=str).lower())
        repro = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
        blobs.append(json.dumps(repro, ensure_ascii=False, default=str).lower())
    for step in _iter_v12_execution_steps(scan_result):
        if _http_status(step) <= 0:
            continue
        blobs.append(json.dumps({
            "method": step.get("method"),
            "path": step.get("path"),
            "phase": step.get("phase"),
            "status_code": step.get("status_code"),
            "body": step.get("body"),
            "observation_path": step.get("observation_path"),
        }, ensure_ascii=False, default=str).lower())
        governance = step.get("governance_receipt")
        if isinstance(governance, dict):
            blobs.append(json.dumps({
                key: governance.get(key)
                for key in ("before", "write", "after", "response_bound_after")
                if isinstance(governance.get(key), dict)
            }, ensure_ascii=False, default=str).lower())
    return blobs


def _http_status(row: dict[str, Any]) -> int:
    try:
        return int(row.get("status_code") or row.get("status") or 0)
    except (TypeError, ValueError):
        return 0


def _iter_v12_execution_steps(scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    containers = [scan_result]
    v12 = scan_result.get("v12")
    if isinstance(v12, dict):
        containers.append(v12)
    for container in containers:
        execution = (
            container.get("experiment_execution")
            if isinstance(container.get("experiment_execution"), dict)
            else {}
        )
        for result in execution.get("results") or []:
            if not isinstance(result, dict):
                continue
            for step in result.get("steps") or []:
                if isinstance(step, dict):
                    steps.append(step)
    return steps


def _keyword_hits(keywords: list[str], text: str) -> int:
    return sum(1 for kw in keywords if kw and kw in text)


def _item_near_miss(item: dict[str, Any], bug: dict[str, Any], bug_paths: set[str]) -> tuple[bool, float]:
    blob = _finding_text_blob(item)
    kw_hits = _keyword_hits(_bug_keywords(bug), blob)
    path_hit = _paths_overlap(_finding_paths(item), bug_paths)
    score = (0.12 * kw_hits) + (0.4 if path_hit else 0.0)
    return score >= 0.24, score


def _blocking_reasons(scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    funnel = scan_result.get("discovery_funnel") if isinstance(scan_result.get("discovery_funnel"), dict) else {}
    reasons = funnel.get("top_blocking_reasons") or []
    return [r for r in reasons if isinstance(r, dict)]


def _funnel_stage_counts(scan_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    funnel = scan_result.get("discovery_funnel") if isinstance(scan_result.get("discovery_funnel"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for stage in funnel.get("stages") or []:
        if isinstance(stage, dict) and stage.get("name"):
            out[str(stage["name"])] = stage
    return out


def _module_understood(module: str, executed_paths: set[str], catalog_paths: set[str]) -> bool:
    markers = {
        "auth": "/api/auth",
        "user": "/api/users",
        "product": "/api/products",
        "inventory": "/api/inventory",
        "cart": "/api/cart",
        "coupon": "/api/coupons",
        "order": "/api/orders",
        "payment": "/api/payments",
        "refund": "/api/refunds",
        "report": "/api/reports",
    }
    prefix = markers.get(module)
    if not prefix:
        return any(module in p for p in executed_paths)
    in_catalog = any(p.startswith(prefix) for p in catalog_paths)
    in_exec = any(p.startswith(prefix) for p in executed_paths)
    return in_catalog and in_exec


def _module_path_reached(module: str, executed_paths: set[str]) -> bool:
    prefixes = _MODULE_PATH_PREFIX.get(module, ())
    if not prefixes:
        return False
    return any(any(p.startswith(prefix) for p in executed_paths) for prefix in prefixes)


def _bug_reached(
    bug_paths: set[str],
    *,
    module: str,
    keywords: list[str],
    executed_paths: set[str],
    joined_exec: str,
) -> bool:
    """Strict reach: related API path must overlap an executed request path."""
    del module, keywords, joined_exec  # reserved for richer future signals
    return bool(bug_paths) and _paths_overlap(bug_paths, executed_paths)


def _classify_miss(
    bug: dict[str, Any],
    *,
    catalog_paths: set[str],
    executed_paths: set[str],
    execution_blobs: list[str],
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    blocking: list[dict[str, Any]],
) -> dict[str, Any]:
    bug_id = str(bug.get("bug_id") or "")
    keywords = _bug_keywords(bug)
    module = _bug_module(bug)
    bug_paths = _bug_paths(bug, catalog_paths)
    joined_exec = "\n".join(execution_blobs)
    module_in_catalog = any(
        any(p.startswith(prefix) for p in catalog_paths)
        for prefix in _MODULE_PATH_PREFIX.get(module, ())
    ) or bool(bug_paths and _paths_overlap(bug_paths, catalog_paths))
    path_in_catalog = bool(bug_paths) and _paths_overlap(bug_paths, catalog_paths)
    path_reached = _bug_reached(
        bug_paths,
        module=module,
        keywords=keywords,
        executed_paths=executed_paths,
        joined_exec=joined_exec,
    )
    kw_in_exec = _keyword_hits(keywords, joined_exec)
    param_covered = path_reached and kw_in_exec >= max(1, min(2, len(keywords) // 3 or 1))

    near_candidates: list[dict[str, Any]] = []
    for cand in candidates:
        hit, score = _item_near_miss(cand, bug, bug_paths)
        if hit:
            near_candidates.append({"title": cand.get("title"), "score": round(score, 3), "gate_passed": cand.get("gate_passed")})

    near_findings: list[dict[str, Any]] = []
    for finding in findings:
        hit, score = _item_near_miss(finding, bug, bug_paths)
        if hit:
            near_findings.append(
                {
                    "title": finding.get("title"),
                    "score": round(score, 3),
                    "gate_passed": finding.get("gate_passed"),
                    "customer_delivery_status": finding.get("customer_delivery_status"),
                    "evidence_status": finding.get("evidence_status") or finding.get("business_evidence_status"),
                }
            )

    block_text = " ".join(str(r.get("reason") or "") for r in blocking).lower()
    fixture_pressure = any(m in block_text for m in _FIXTURE_BLOCK_MARKERS)
    exec_pressure = any(m in block_text for m in _EXEC_FAIL_MARKERS)
    evidence_pressure = any(m in block_text for m in _EVIDENCE_MARKERS)
    module_ok = _module_understood(module, executed_paths, catalog_paths)
    module_entered = _module_path_reached(module, executed_paths)

    if (
        not module_in_catalog
        and not module_entered
        and not path_reached
        and module not in {"database", "ui", "admin-web", "customer-web"}
    ):
        stage = 1
        reason = "相关模块/接口未进入企业资料理解结果（catalog 未覆盖且运行时未进入该模块）。"
        optimize = "提升企业资料/API 文档解析，把该模块接口纳入理解结果。"
    elif not module_ok and not path_reached and not module_entered:
        stage = 2
        reason = "资料中存在相关能力，但业务模型未建立到可调度的模块路径。"
        optimize = "检查业务模型/ontology 绑定，确保该模块可生成可执行行为。"
    elif not path_reached:
        if fixture_pressure and not module_entered:
            stage = 4
            reason = "相关路径未触达，且运行阻塞以测试数据/夹具原因为主。"
            optimize = "补齐可逆测试数据与 disposable fixture，使触发路径可执行。"
        elif exec_pressure and not module_entered:
            stage = 5
            reason = "相关路径未形成有效执行，阻塞偏执行/适配层失败。"
            optimize = "修复执行适配、鉴权或请求失败，使生成场景真正跑通。"
        elif module_entered and kw_in_exec < 1:
            stage = 6
            reason = "已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。"
            optimize = "只增强该模块异常/边界参数与触发条件生成。"
        else:
            stage = 3
            reason = "相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。"
            optimize = "优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。"
    elif not param_covered and not near_candidates and not near_findings:
        stage = 6
        reason = "已触达相关接口，但触发参数/关键词组合未覆盖。"
        optimize = "只增强该接口的异常/边界参数组合生成。"
    elif near_findings:
        evidence_weak = any(
            (not f.get("gate_passed"))
            or str(f.get("evidence_status") or "").lower() in {"incomplete", "missing", "weak"}
            or str(f.get("customer_delivery_status") or "") not in {"defect", "confirmed"}
            for f in near_findings
        )
        if evidence_weak or evidence_pressure:
            stage = 9
            reason = "已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。"
            optimize = "补强请求/响应/断言/清理证据，满足 customer delivery gate。"
        else:
            stage = 8
            reason = "已有近似发现，但 AI/门控判断未将其确认为该已知 Bug。"
            optimize = "检查判定与匹配信号，避免误杀真阳性（勿硬编码 GT）。"
    elif near_candidates:
        stage = 8
        reason = "已生成近似候选，但未被提升为正式发现。"
        optimize = "检查候选确认/AI 判定门槛，定位误杀原因。"
    elif path_reached and param_covered:
        stage = 7
        reason = "路径与参数已覆盖，但异常识别未产出候选/发现。"
        optimize = "增强该路径的结果/状态/业务规则异常识别。"
    else:
        stage = 7
        reason = "已触达相关路径，但未识别出异常信号。"
        optimize = "增强观察器与断言，识别已发生的异常。"

    return {
        "bug_id": bug_id,
        "expected_bug_type": bug.get("type") or bug.get("risk_type") or "",
        "title": bug.get("title") or "",
        "module": module,
        "severity": bug.get("severity") or "",
        "actual_result": "未发现",
        "undiscovered_reason": reason,
        "failure_stage": stage,
        "failure_stage_name": FAILURE_STAGES[stage],
        "detail": {
            "related_paths": sorted(bug_paths),
            "path_in_catalog": path_in_catalog,
            "module_in_catalog": module_in_catalog,
            "module_entered": module_entered,
            "path_reached": path_reached,
            "keyword_hits_in_execution": kw_in_exec,
            "param_covered": param_covered,
            "near_candidate_count": len(near_candidates),
            "near_finding_count": len(near_findings),
            "near_candidates": near_candidates[:5],
            "near_findings": near_findings[:5],
            "trigger": bug.get("trigger") or "",
            "expected": bug.get("expected") or "",
            "actual_bug_behavior": bug.get("actual") or "",
        },
        "suggested_optimization_locus": optimize,
    }


def diagnose_scan(
    scan_result: dict[str, Any],
    *,
    ground_truth_path: Path,
    inputs_dir: Path,
    project: str = "benchmark_mall",
    root: Path | None = None,
) -> dict[str, Any]:
    """Build per-miss diagnostics and SPC coverage metrics for one scan."""
    root = Path(root or Path.cwd())
    truth_bugs = _load_truth_bugs(Path(ground_truth_path))
    formal_projection = (
        scan_result.get("formal_count_projection")
        if isinstance(scan_result.get("formal_count_projection"), dict)
        else {}
    )
    raw_findings = formal_projection.get("canonical_representative_findings")
    if not isinstance(raw_findings, list):
        raw_findings = scan_result.get("findings") or []
    findings = [
        finding
        for finding in raw_findings
        if isinstance(finding, dict) and finding.get("archive_entry") is not True
    ]
    candidates = [c for c in (scan_result.get("candidate_findings") or []) if isinstance(c, dict)]

    metrics = compute_benchmark(
        project,
        findings,
        candidates=candidates,
        root=root,
        ground_truth_path=str(ground_truth_path),
    )
    matched_ids = {m.get("gt_bug_id") for m in (metrics.get("matched_bugs") or []) if m.get("gt_bug_id")}
    missed_bugs = [b for b in truth_bugs if str(b.get("bug_id") or "") not in matched_ids]

    catalog_paths = _load_catalog_paths(Path(inputs_dir))
    executed_paths = _collect_executed_paths(scan_result)
    execution_blobs = _collect_execution_blobs(scan_result)
    blocking = _blocking_reasons(scan_result)
    funnel_stages = _funnel_stage_counts(scan_result)
    prd_businesses = _load_prd_businesses(Path(inputs_dir))

    # Business understanding: PRD businesses vs modules with catalog+execution evidence
    modules = sorted({_bug_module(b) for b in truth_bugs})
    understood_modules = [m for m in modules if _module_understood(m, executed_paths, catalog_paths)]
    business_total = len(prd_businesses) if prd_businesses else len(modules)
    business_understood = 0
    if prd_businesses:
        module_aliases = {
            "用户下单": "order",
            "支付": "payment",
            "取消订单": "order",
            "退款": "refund",
            "售后": "refund",
            "库存": "inventory",
            "优惠券": "coupon",
        }
        for name in prd_businesses:
            alias = None
            for key, mod in module_aliases.items():
                if key in name:
                    alias = mod
                    break
            if alias and alias in understood_modules:
                business_understood += 1
            elif any(m in name.lower() for m in understood_modules):
                business_understood += 1
    else:
        business_understood = len(understood_modules)

    theoretical_paths = int((funnel_stages.get("candidate_generation") or {}).get("input") or 0)
    if theoretical_paths <= 0:
        ledger = scan_result.get("behavior_slice_ledger") if isinstance(scan_result.get("behavior_slice_ledger"), dict) else {}
        theoretical_paths = int(ledger.get("slice_budget") or 0) * int(ledger.get("round_limit") or 1)
    actual_executed_paths = int((funnel_stages.get("execution") or {}).get("output") or 0)
    if actual_executed_paths <= 0:
        actual_executed_paths = len(executed_paths)

    reached_bug_ids: list[str] = []
    unreached_bug_ids: list[str] = []
    for bug in truth_bugs:
        bug_paths = _bug_paths(bug, catalog_paths)
        keywords = _bug_keywords(bug)
        module = _bug_module(bug)
        if _bug_reached(
            bug_paths,
            module=module,
            keywords=keywords,
            executed_paths=executed_paths,
            joined_exec="\n".join(execution_blobs),
        ):
            reached_bug_ids.append(str(bug.get("bug_id")))
        else:
            unreached_bug_ids.append(str(bug.get("bug_id")))

    reports = [
        _classify_miss(
            bug,
            catalog_paths=catalog_paths,
            executed_paths=executed_paths,
            execution_blobs=execution_blobs,
            findings=findings,
            candidates=candidates,
            blocking=blocking,
        )
        for bug in missed_bugs
    ]
    stage_counts = Counter(r["failure_stage"] for r in reports)
    reach_misses = stage_counts.get(1, 0) + stage_counts.get(2, 0) + stage_counts.get(3, 0) + stage_counts.get(4, 0) + stage_counts.get(6, 0)
    detect_misses = stage_counts.get(7, 0) + stage_counts.get(8, 0) + stage_counts.get(9, 0)

    return {
        "schema_version": "qualibug.miss-diagnosis.v1",
        "spc_phase": "phase1_miss_diagnosis",
        "project": project,
        "ground_truth_bug_count": len(truth_bugs),
        "true_positives": metrics.get("true_positives"),
        "false_positives": metrics.get("false_positives"),
        "recall": metrics.get("recall"),
        "precision": metrics.get("precision"),
        "matched_bug_ids": sorted(matched_ids),
        "missed_bug_count": len(reports),
        "metrics": {
            "business_understanding": {
                "prd_business_count": business_total,
                "understood_business_count": business_understood,
                "business_understanding_coverage": round(business_understood / business_total, 4) if business_total else 0.0,
                "understood_modules": understood_modules,
                "all_modules": modules,
                "prd_businesses": prd_businesses,
            },
            "behavior_path_coverage": {
                "theoretical_behavior_paths": theoretical_paths,
                "actual_executed": actual_executed_paths,
                "behavior_coverage": round(actual_executed_paths / theoretical_paths, 4) if theoretical_paths else 0.0,
                "funnel_stages": funnel_stages,
                "unique_executed_api_paths": len(executed_paths),
            },
            "bug_reach_rate": {
                "known_bugs": len(truth_bugs),
                "reached_related_path": len(reached_bug_ids),
                "unreached_path": len(unreached_bug_ids),
                "reach_rate": round(len(reached_bug_ids) / len(truth_bugs), 4) if truth_bugs else 0.0,
                "reached_bug_ids": reached_bug_ids,
                "unreached_bug_ids": unreached_bug_ids,
            },
        },
        "failure_stage_histogram": {
            str(stage): {"name": FAILURE_STAGES[stage], "count": stage_counts.get(stage, 0)}
            for stage in FAILURE_STAGES
        },
        "top_failure_stage": (
            {
                "stage": stage_counts.most_common(1)[0][0],
                "name": FAILURE_STAGES[stage_counts.most_common(1)[0][0]],
                "count": stage_counts.most_common(1)[0][1],
            }
            if stage_counts
            else None
        ),
        "blocking_reasons": blocking,
        "miss_reports": reports,
        "optimization_priority_hint": (
            "Priority 1: 测试触达能力（行为/场景/参数覆盖）"
            if reach_misses >= detect_misses
            else "Priority 3/4: 异常识别或证据链"
        ),
    }


def render_miss_diagnosis_markdown(report: dict[str, Any]) -> str:
    metrics = report.get("metrics") or {}
    biz = metrics.get("business_understanding") or {}
    beh = metrics.get("behavior_path_coverage") or {}
    reach = metrics.get("bug_reach_rate") or {}
    top = report.get("top_failure_stage") or {}
    lines = [
        "# Bug 漏检诊断报告 (SPC Phase 1)",
        "",
        f"- 已知 Bug：{report.get('ground_truth_bug_count')}",
        f"- 真实发现 (TP)：{report.get('true_positives')}",
        f"- 漏检：{report.get('missed_bug_count')}",
        f"- Recall：{report.get('recall')}",
        f"- Precision：{report.get('precision')}",
        "",
        "## 核心分析指标",
        "",
        "### 5.1 业务理解覆盖率",
        f"- PRD业务数量: {biz.get('prd_business_count')}",
        f"- 已理解业务: {biz.get('understood_business_count')}",
        f"- 业务理解覆盖率: {round(100 * float(biz.get('business_understanding_coverage') or 0), 1)}%",
        "",
        "### 5.2 行为路径覆盖率",
        f"- 理论行为路径: {beh.get('theoretical_behavior_paths')}",
        f"- 实际执行: {beh.get('actual_executed')}",
        f"- 行为覆盖率: {round(100 * float(beh.get('behavior_coverage') or 0), 1)}%",
        "",
        "### 5.3 Bug触达率",
        f"- 已知Bug: {reach.get('known_bugs')}",
        f"- 进入相关代码路径: {reach.get('reached_related_path')}",
        f"- 未进入路径: {reach.get('unreached_path')}",
        f"- 触达率: {round(100 * float(reach.get('reach_rate') or 0), 1)}%",
        "",
        "## 失败阶段分布",
        "",
    ]
    for stage, meta in (report.get("failure_stage_histogram") or {}).items():
        lines.append(f"- {stage}. {meta.get('name')}: {meta.get('count')}")
    lines += [
        "",
        f"**当前最大漏检阶段**: {top.get('stage')}. {top.get('name')} ({top.get('count')})",
        f"**优化优先级提示**: {report.get('optimization_priority_hint')}",
        "",
        "## 逐 Bug 诊断（漏检）",
        "",
    ]
    for item in report.get("miss_reports") or []:
        lines += [
            f"### {item.get('bug_id')}",
            f"- 预期Bug类型: {item.get('expected_bug_type')}",
            f"- 实际结果: {item.get('actual_result')}",
            f"- 未发现原因: {item.get('undiscovered_reason')}",
            f"- 失败阶段: {item.get('failure_stage')}. {item.get('failure_stage_name')}",
            f"- 详细分析: path_reached={item.get('detail', {}).get('path_reached')}, "
            f"param_covered={item.get('detail', {}).get('param_covered')}, "
            f"near_candidates={item.get('detail', {}).get('near_candidate_count')}, "
            f"near_findings={item.get('detail', {}).get('near_finding_count')}",
            f"- 建议优化位置: {item.get('suggested_optimization_locus')}",
            "",
        ]
    return "\n".join(lines)
