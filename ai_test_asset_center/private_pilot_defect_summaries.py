"""Defect summary and request-identity helpers for the private pilot.

Extracted from ``private_pilot_service`` so the HTTP handler module stays thinner.
Symbols remain importable from ``private_pilot_service`` for compatibility.
"""
from __future__ import annotations

import re
from typing import Any

from .real_id_resolver import normalize_path_placeholders


def _validate_api_path(path: str) -> str:
    """验证提取的路径是合法 API 端点，防止描述文本被误判为路径。

    合法 API 路径规则（通用，非业务概念）：
    - 必须以 / 开头
    - 只包含 ASCII 字母、数字、/_-{}:.@
    - 不包含中文字符、空格或描述性文字
    - 长度合理（< 200 字符）
    - 至少有一个路径段（/ 后有内容）
    """
    if not path or not isinstance(path, str):
        return ""
    p = path.strip()
    if not p.startswith("/"):
        return ""
    if len(p) > 200:
        return ""
    # 只允许 ASCII 路径字符
    import re as _re
    if not _re.match(r'^/[a-zA-Z0-9_/{}:.@-]+$', p):
        return ""
    # 排除明显的描述性文本（包含连续的中文标点或描述词）
    # 例如“路径后紧跟中文叙述”已被 ASCII 正则过滤
    # 但 "/OFF_SALE/HIDDEN" 这种仍可能通过——检查是否像真实端点
    segments = [s for s in p.split("/") if s]
    if not segments:
        return ""
    # 至少有一个段以字母开头（端点名通常以字母开头）
    if not any(s[0].isalpha() for s in segments):
        return ""
    return p


def _extract_step_calls(steps: list[str]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    pattern = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+((?:/api)?/[A-Za-z0-9_/\-{}:.@]+)", re.I)
    for step in steps:
        if not isinstance(step, str):
            continue
        match = pattern.search(step)
        if not match:
            continue
        method = str(match.group(1) or "").strip().upper()
        path = _validate_api_path(str(match.group(2) or "").strip())
        if method and path:
            rows.append((method, path))
    return rows


def _claim_request_identity(
    title: str,
    description: str,
    expected: str,
    actual: str,
    assertion: str,
    suggested_action: str,
    steps: list[str],
) -> tuple[str, str]:
    claim_text = " ".join(
        part for part in (title, description, expected, actual, assertion, suggested_action)
        if str(part or "").strip()
    )
    path_match = re.search(r"(/api/[A-Za-z0-9_/\-{}:.@]+|/[A-Za-z0-9{}.-]+/[A-Za-z0-9_/\-{}:.@]+)", claim_text)
    claim_path = _validate_api_path(path_match.group(1) if path_match else "")
    claim_method_match = re.search(r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b", claim_text, re.I)
    claim_method = str(claim_method_match.group(1) or "").strip().upper() if claim_method_match else ""
    step_calls = _extract_step_calls(steps)
    if claim_path:
        normalized_claim = normalize_path_placeholders(claim_path)
        for method, path in step_calls:
            if normalize_path_placeholders(path) == normalized_claim:
                return method, claim_path
    if claim_path:
        return claim_method, claim_path
    if step_calls:
        return step_calls[-1]
    return claim_method, claim_path


def _materialized_path_matches(template_path: str, observed_path: str) -> bool:
    declared = _validate_api_path(template_path)
    observed = _validate_api_path(observed_path)
    if not declared or not observed:
        return False
    if normalize_path_placeholders(declared) == normalize_path_placeholders(observed):
        return True
    declared_parts = [part for part in normalize_path_placeholders(declared).strip("/").split("/") if part]
    observed_parts = [part for part in normalize_path_placeholders(observed).strip("/").split("/") if part]
    if len(declared_parts) != len(observed_parts):
        return False
    for declared_part, observed_part in zip(declared_parts, observed_parts):
        if declared_part.startswith("{") and declared_part.endswith("}"):
            continue
        if declared_part != observed_part:
            return False
    return True


def _finding_request_identity(item: dict[str, Any]) -> tuple[str, str]:
    reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
    raw_evidence = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
    request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
    method = str(
        reproduction.get("method")
        or item.get("repro_method")
        or request_raw.get("method")
        or item.get("_api_method")
        or item.get("method")
        or ""
    ).strip().upper()
    path = _validate_api_path(str(
        reproduction.get("path")
        or item.get("repro_path")
        or request_raw.get("path")
        or item.get("_api_path")
        or item.get("path")
        or ""
    ).strip())
    if path:
        path = _normalize_summary_path(path)
    return method, path


def _normalize_summary_path(path: str) -> str:
    validated = _validate_api_path(path)
    if not validated:
        return ""
    normalized = normalize_path_placeholders(validated)
    parts = normalized.strip("/").split("/")
    rewritten: list[str] = []
    for part in parts:
        token = str(part or "").strip()
        if not token:
            continue
        if token.startswith("{") and token.endswith("}"):
            rewritten.append(token)
            continue
        lower = token.lower()
        looks_like_identifier = (
            any(ch.isdigit() for ch in token)
            or bool(re.fullmatch(r"[0-9a-f]{8,}", lower))
            or bool(re.fullmatch(r"[0-9a-f]{8,}(?:-[0-9a-f]{4,}){1,}", lower))
        )
        rewritten.append("{id}" if looks_like_identifier else token)
    return "/" + "/".join(rewritten)


def _build_defect_grouped_summary(defects: list[dict[str, Any]]) -> dict[str, Any]:
    by_risk_type: dict[str, dict[str, Any]] = {}
    by_endpoint: dict[str, dict[str, Any]] = {}
    for item in defects:
        if not isinstance(item, dict):
            continue
        risk_type = str(item.get("risk_type") or item.get("category") or "uncategorized").strip() or "uncategorized"
        method, path = _finding_request_identity(item)
        endpoint_key = f"{method} {path}".strip() if method or path else "UNKNOWN /"
        endpoint_bucket = by_endpoint.setdefault(endpoint_key, {
            "method": method,
            "path": path or "/",
            "count": 0,
            "risk_type_counts": {},
        })
        endpoint_bucket["count"] += 1
        endpoint_bucket["risk_type_counts"][risk_type] = int(endpoint_bucket["risk_type_counts"].get(risk_type) or 0) + 1

        risk_bucket = by_risk_type.setdefault(risk_type, {
            "risk_type": risk_type,
            "count": 0,
            "endpoints": {},
        })
        risk_bucket["count"] += 1
        endpoint_summary = risk_bucket["endpoints"].setdefault(endpoint_key, {
            "method": method,
            "path": path or "/",
            "count": 0,
        })
        endpoint_summary["count"] += 1

    return {
        "total_defects": len([item for item in defects if isinstance(item, dict)]),
        "by_risk_type": [
            {
                "risk_type": bucket["risk_type"],
                "count": bucket["count"],
                "endpoints": sorted(bucket["endpoints"].values(), key=lambda item: (-int(item.get("count") or 0), str(item.get("path") or ""), str(item.get("method") or ""))),
            }
            for bucket in sorted(by_risk_type.values(), key=lambda item: (-int(item.get("count") or 0), str(item.get("risk_type") or "")))
        ],
        "by_endpoint": sorted(
            by_endpoint.values(),
            key=lambda item: (-int(item.get("count") or 0), str(item.get("path") or ""), str(item.get("method") or "")),
        ),
    }


def _build_defect_priority_summary(defects: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in defects:
        if not isinstance(item, dict):
            continue
        risk_type = str(item.get("risk_type") or item.get("category") or "uncategorized").strip() or "uncategorized"
        severity = str(item.get("severity") or "P2").strip().upper() or "P2"
        method, path = _finding_request_identity(item)
        key = (risk_type, method, path or "/")
        bucket = groups.setdefault(key, {
            "risk_type": risk_type,
            "method": method,
            "path": path or "/",
            "count": 0,
            "p0_count": 0,
            "p1_count": 0,
            "sample_titles": [],
            "sample_ids": [],
        })
        bucket["count"] += 1
        if severity == "P0":
            bucket["p0_count"] += 1
        elif severity == "P1":
            bucket["p1_count"] += 1
        title = str(item.get("title") or "").strip()
        if title and title not in bucket["sample_titles"] and len(bucket["sample_titles"]) < 2:
            bucket["sample_titles"].append(title)
        sample_id = str(item.get("id") or item.get("risk_id") or "").strip()
        if sample_id and sample_id not in bucket["sample_ids"] and len(bucket["sample_ids"]) < 3:
            bucket["sample_ids"].append(sample_id)

    top_groups = sorted(
        groups.values(),
        key=lambda item: (
            -int(item.get("p0_count") or 0),
            -int(item.get("p1_count") or 0),
            -int(item.get("count") or 0),
            str(item.get("risk_type") or ""),
            str(item.get("path") or ""),
            str(item.get("method") or ""),
        ),
    )
    ranked_groups: list[dict[str, Any]] = []
    for index, bucket in enumerate(top_groups, start=1):
        top_severity = "P0" if int(bucket.get("p0_count") or 0) > 0 else ("P1" if int(bucket.get("p1_count") or 0) > 0 else "P2")
        ranked_groups.append({
            "rank": index,
            "risk_type": bucket["risk_type"],
            "method": bucket["method"],
            "path": bucket["path"],
            "count": bucket["count"],
            "p0_count": bucket["p0_count"],
            "p1_count": bucket["p1_count"],
            "top_severity": top_severity,
            "sample_titles": list(bucket["sample_titles"]),
            "sample_ids": list(bucket["sample_ids"]),
        })
    return {
        "total_defects": len([item for item in defects if isinstance(item, dict)]),
        "top_groups": ranked_groups,
    }


def _build_defect_repro_summary(defects: list[dict[str, Any]], top_limit: int = 3) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in defects:
        if not isinstance(item, dict):
            continue
        risk_type = str(item.get("risk_type") or item.get("category") or "uncategorized").strip() or "uncategorized"
        method, path = _finding_request_identity(item)
        key = (risk_type, method, path or "/")
        groups.setdefault(key, []).append(item)

    ranked_groups: list[dict[str, Any]] = []
    for (risk_type, method, path), items in groups.items():
        p0_count = sum(1 for item in items if str(item.get("severity") or "").strip().upper() == "P0")
        p1_count = sum(1 for item in items if str(item.get("severity") or "").strip().upper() == "P1")
        ranked_groups.append({
            "risk_type": risk_type,
            "method": method,
            "path": path,
            "count": len(items),
            "p0_count": p0_count,
            "p1_count": p1_count,
            "items": items,
        })
    ranked_groups.sort(
        key=lambda item: (
            -int(item.get("p0_count") or 0),
            -int(item.get("p1_count") or 0),
            -int(item.get("count") or 0),
            str(item.get("risk_type") or ""),
            str(item.get("path") or ""),
            str(item.get("method") or ""),
        )
    )

    top_groups: list[dict[str, Any]] = []
    for index, bucket in enumerate(ranked_groups[:top_limit], start=1):
        representative = bucket["items"][0] if bucket["items"] else {}
        expected_actual = representative.get("expected_actual_comparison") if isinstance(representative.get("expected_actual_comparison"), dict) else {}
        raw_evidence = representative.get("raw_evidence") if isinstance(representative.get("raw_evidence"), dict) else {}
        request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
        response_raw = raw_evidence.get("response_raw") if isinstance(raw_evidence.get("response_raw"), dict) else {}
        technical_details = representative.get("technical_details") if isinstance(representative.get("technical_details"), dict) else {}
        api_endpoint = technical_details.get("api_endpoint") if isinstance(technical_details.get("api_endpoint"), dict) else {}
        evidence_source = str(
            response_raw.get("source")
            or request_raw.get("source")
            or ((representative.get("reproduction") or {}).get("har_evidence") or {}).get("source")
            or ""
        ).strip()
        regression_suggestions = representative.get("regression_suggestions") if isinstance(representative.get("regression_suggestions"), list) else []
        top_groups.append({
            "rank": index,
            "risk_type": bucket["risk_type"],
            "method": bucket["method"],
            "path": bucket["path"],
            "count": bucket["count"],
            "p0_count": bucket["p0_count"],
            "p1_count": bucket["p1_count"],
            "top_severity": "P0" if int(bucket["p0_count"] or 0) > 0 else ("P1" if int(bucket["p1_count"] or 0) > 0 else "P2"),
            "title": str(representative.get("title") or "").strip(),
            "trigger_request": {
                "method": str(request_raw.get("method") or api_endpoint.get("method") or bucket["method"] or "").strip().upper(),
                "path": str(request_raw.get("path") or api_endpoint.get("path") or bucket["path"] or "").strip(),
                "actor": str(request_raw.get("actor") or api_endpoint.get("actor") or "").strip(),
            },
            "expected": str(expected_actual.get("expected") or representative.get("expected") or "").strip(),
            "actual": str(expected_actual.get("actual") or representative.get("actual") or "").strip(),
            "difference": str(expected_actual.get("difference") or "").strip(),
            "test_summary": str(representative.get("test_summary") or "").strip(),
            "dev_summary": str(representative.get("dev_summary") or "").strip(),
            "evidence_source": evidence_source,
            "response_status": response_raw.get("status_code") if response_raw.get("status_code") is not None else technical_details.get("response_status"),
            "sample_ids": [str(item.get("id") or item.get("risk_id") or "").strip() for item in bucket["items"][:3] if str(item.get("id") or item.get("risk_id") or "").strip()],
            "regression_suggestions": [str(item).strip() for item in regression_suggestions[:3] if str(item).strip()],
        })

    return {
        "total_defects": len([item for item in defects if isinstance(item, dict)]),
        "top_groups": top_groups,
    }


def _build_defect_delivery_cards(defects: list[dict[str, Any]], top_limit: int = 3) -> dict[str, Any]:
    repro_summary = _build_defect_repro_summary(defects, top_limit=top_limit)
    cards: list[dict[str, Any]] = []
    for item in repro_summary.get("top_groups") or []:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "").strip().upper()
        path = str(item.get("path") or "").strip()
        risk_type = str(item.get("risk_type") or "").strip()
        trigger_request = item.get("trigger_request") if isinstance(item.get("trigger_request"), dict) else {}
        cards.append({
            "rank": int(item.get("rank") or len(cards) + 1),
            "title": str(item.get("title") or f"{risk_type} · {method} {path}").strip(),
            "group_key": f"{risk_type}|{method}|{path}",
            "risk_type": risk_type,
            "severity": str(item.get("top_severity") or "").strip(),
            "affected_count": int(item.get("count") or 0),
            "endpoint": {
                "method": method,
                "path": path,
            },
            "risk_summary": str(item.get("difference") or item.get("actual") or "").strip(),
            "expected_behavior": str(item.get("expected") or "").strip(),
            "actual_behavior": str(item.get("actual") or "").strip(),
            "repro_entry": {
                "method": str(trigger_request.get("method") or method).strip().upper(),
                "path": str(trigger_request.get("path") or path).strip(),
                "actor": str(trigger_request.get("actor") or "").strip(),
            },
            "evidence": {
                "source": str(item.get("evidence_source") or "").strip(),
                "response_status": item.get("response_status"),
                "sample_ids": list(item.get("sample_ids") or []),
            },
            "delivery_notes": {
                "test_summary": str(item.get("test_summary") or "").strip(),
                "dev_summary": str(item.get("dev_summary") or "").strip(),
                "regression_suggestions": list(item.get("regression_suggestions") or []),
            },
        })
    return {
        "total_cards": len(cards),
        "cards": cards,
    }
