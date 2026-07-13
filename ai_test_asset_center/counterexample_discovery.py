from __future__ import annotations

"""Phase42: semantic counterexample discovery and evidence memory.

Most API test generators stop at a single request assertion.  This module builds
relations between APIs that describe the same resource and tries to falsify those
relations with read-only observations:

* collection items must agree with their detail representation;
* adjacent pages must not duplicate identifiers;
* documented query bounds/enums must not silently succeed;
* state vocabularies and identifier types must not drift between operations.

A finding is never reported as a confirmed defect automatically.  It is emitted
as a reproducible, redacted counterexample with a stable fingerprint.  Repeated
observations are remembered across runs so flaky one-offs stay low confidence
while persistent violations become easier to prioritize.
"""

import hashlib
import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .real_project_onboarding import (
    ROOT,
    _html_escape,
    _join_url,
    _load_json,
    _read_text,
    _safe_project_id,
    _write_json,
    config_paths,
    load_real_project_config,
)
from .llm_reasoning import compile_unverified_semantic_hypotheses, reason as _llm_reason
from .universal_defect_mining import (
    PRIVATE_MARKERS,
    _http_get,
    _operations,
    _parameter_schema,
    _path_parameters,
    _render_path,
    _resolve_ref,
    _schema_type,
    _strip_dynamic,
)

MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SENSITIVE_KEY_RE = re.compile(r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|email|phone|mobile|id[_-]?card)", re.I)
DYNAMIC_KEY_RE = re.compile(r"(?:^|[_\-.])(time|timestamp|updated|created|trace|request|nonce|version|etag|cursor|next|last)(?:$|[_\-.])", re.I)
RESOURCE_SKIP = {"api", "v1", "v2", "v3", "public", "private", "internal", "open", "service", "services"}
ID_NAMES = {"id", "uuid", "guid", "key", "code", "number"}
LIST_KEYS = ("items", "data", "results", "records", "rows", "content", "list")
DETAIL_KEYS = ("data", "result", "item", "record", "content")
STATE_FIELDS = {"status", "state", "phase", "stage"}

RELATION_META: dict[str, dict[str, str]] = {
    "collection_detail_projection": {"risk_type": "data_consistency", "severity": "P1", "title": "列表与详情资源投影不一致"},
    "pagination_non_overlap": {"risk_type": "data_consistency", "severity": "P1", "title": "相邻分页出现重复资源"},
    "query_constraint_enforcement": {"risk_type": "business_rule", "severity": "P1", "title": "只读查询约束未强制执行"},
    "cross_actor_resource_isolation": {"risk_type": "permission_bypass", "severity": "P0", "title": "跨主体资源隔离候选"},
    "state_vocabulary_drift": {"risk_type": "state_consistency", "severity": "P1", "title": "同一资源状态词表漂移"},
    "identity_contract_drift": {"risk_type": "data_consistency", "severity": "P1", "title": "资源身份字段契约漂移"},
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _counter(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        value = str(value or "unknown")
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda item: (-item[1], item[0])))


def _resource_key(path: str) -> str:
    segments = [segment.lower() for segment in str(path).split("/") if segment and not segment.startswith("{")]
    segments = [segment for segment in segments if segment not in RESOURCE_SKIP]
    if not segments:
        return "root"
    candidate = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "", segments[-1]) or "resource"
    if candidate.endswith("ies") and len(candidate) > 4:
        return candidate[:-3] + "y"
    if candidate.endswith("ses") and len(candidate) > 4:
        return candidate[:-2]
    if candidate.endswith("s") and len(candidate) > 3:
        return candidate[:-1]
    return candidate


def _op_ref(operation: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": operation.get("method"),
        "path": operation.get("path"),
        "operation_id": operation.get("operation_id"),
        "summary": operation.get("summary") or "",
        "resource": _resource_key(str(operation.get("path") or "/")),
    }


def _schema_properties(schema: Any, components: dict[str, Any]) -> dict[str, dict[str, Any]]:
    node = _resolve_ref(schema, components)
    props = node.get("properties") if isinstance(node, dict) else {}
    return {str(key): _resolve_ref(value, components) for key, value in (props or {}).items() if isinstance(value, dict)}


def _array_item_schema(schema: Any, components: dict[str, Any], depth: int = 0) -> tuple[str, dict[str, Any]]:
    """Return (container key, item schema) for a typical collection response."""
    if depth > 2:
        return "", {}
    node = _resolve_ref(schema, components)
    if not node:
        return "", {}
    if _schema_type(node, components) == "array":
        item = node.get("items") or {}
        return "$", _resolve_ref(item, components) if isinstance(item, dict) else {}
    if _schema_type(node, components) == "object":
        props = _schema_properties(node, components)
        for key in LIST_KEYS:
            value = props.get(key)
            if value and _schema_type(value, components) == "array":
                item = value.get("items") or {}
                return key, _resolve_ref(item, components) if isinstance(item, dict) else {}
        # APIs often return {data: {items: [...]}}.
        for key in DETAIL_KEYS:
            value = props.get(key)
            if value and _schema_type(value, components) == "object":
                nested_key, nested = _array_item_schema(value, components, depth + 1)
                if nested:
                    return f"{key}.{nested_key}", nested
    return "", {}


def _detail_object_schema(schema: Any, components: dict[str, Any]) -> dict[str, Any]:
    node = _resolve_ref(schema, components)
    if _schema_type(node, components) != "object":
        return {}
    props = _schema_properties(node, components)
    for key in DETAIL_KEYS:
        nested = props.get(key)
        if nested and _schema_type(nested, components) == "object":
            return nested
    return node


def _is_collection_read(operation: dict[str, Any], components: dict[str, Any]) -> bool:
    if operation.get("method") != "GET" or _path_parameters(str(operation.get("path") or "")):
        return False
    _, item = _array_item_schema(operation.get("response_schema") or {}, components)
    return bool(item)


def _is_detail_read(operation: dict[str, Any], components: dict[str, Any]) -> bool:
    return operation.get("method") == "GET" and bool(_path_parameters(str(operation.get("path") or ""))) and bool(operation.get("response_schema"))


def _scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and not isinstance(value, bool) or isinstance(value, bool)


def _id_candidates(item: dict[str, Any], path_parameter: str, resource: str) -> list[str]:
    wanted = re.sub(r"[^a-z0-9]", "", path_parameter.lower())
    singular = re.sub(r"[^a-z0-9]", "", resource.lower())
    names: list[str] = [path_parameter, wanted, f"{resource}_id", f"{singular}_id", "id", "uuid", "guid", "code", "number"]
    names.extend(key for key in item if key.lower().endswith("_id"))
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = str(name)
        if key in seen:
            continue
        seen.add(key)
        value = item.get(key)
        if _scalar(value) and str(value).strip():
            out.append(key)
    return out


def _path_parameter_types(operation: dict[str, Any], components: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for param in operation.get("parameters") or []:
        if isinstance(param, dict) and str(param.get("in") or "") == "path":
            out[str(param.get("name") or "")] = _schema_type(_parameter_schema(param, components), components)
    return out


def _state_enums(operation: dict[str, Any], components: dict[str, Any]) -> dict[str, set[str]]:
    schema = _detail_object_schema(operation.get("response_schema") or {}, components)
    values: dict[str, set[str]] = {}
    for name, child in _schema_properties(schema, components).items():
        if name.lower() in STATE_FIELDS and child.get("enum"):
            values[name.lower()] = {str(value) for value in child.get("enum") or []}
    return values


def _query_constraints(operation: dict[str, Any], components: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for param in operation.get("parameters") or []:
        if not isinstance(param, dict) or str(param.get("in") or "") != "query":
            continue
        schema = _parameter_schema(param, components)
        name = str(param.get("name") or "")
        if not name:
            continue
        enum = list(schema.get("enum") or [])
        if enum:
            rows.append({"parameter": name, "mutation_kind": "invalid_enum", "value": "__QUALIBUG_INVALID_ENUM__", "expected": "4xx"})
        if schema.get("minimum") is not None:
            try:
                value: Any = float(schema.get("minimum")) - 1
                value = int(value) if value.is_integer() else value
            except Exception:
                value = -1
            rows.append({"parameter": name, "mutation_kind": "below_minimum", "value": value, "expected": "4xx"})
        if schema.get("maximum") is not None:
            try:
                value = float(schema.get("maximum")) + 1
                value = int(value) if value.is_integer() else value
            except Exception:
                value = 1_000_000
            rows.append({"parameter": name, "mutation_kind": "above_maximum", "value": value, "expected": "4xx"})
    return rows[:8]


def _pagination_parameters(operation: dict[str, Any]) -> dict[str, str]:
    names = {str(p.get("name") or "").lower(): str(p.get("name") or "") for p in operation.get("parameters") or [] if isinstance(p, dict) and str(p.get("in") or "") == "query"}
    page = next((names[key] for key in ("page", "page_no", "pageno", "pageindex") if key in names), "")
    offset = next((names[key] for key in ("offset", "start", "skip") if key in names), "")
    size = next((names[key] for key in ("limit", "size", "page_size", "pagesize", "per_page") if key in names), "")
    return {"page": page, "offset": offset, "size": size}


def _relation(number: int, relation_type: str, *, left: dict[str, Any] | None = None, right: dict[str, Any] | None = None, execution_policy: str = "safe_read_only", detail: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = RELATION_META[relation_type]
    return {
        "probe_id": f"CX_{number:04d}",
        "source": "counterexample_relation_mining",
        "counterexample_type": relation_type,
        "risk_type": meta["risk_type"],
        "severity": meta["severity"],
        "title": meta["title"],
        "method": (left or {}).get("method") or "GET",
        "path": (left or {}).get("path") or (right or {}).get("path") or "/",
        "operation_id": (left or {}).get("operation_id") or (right or {}).get("operation_id"),
        "left_operation": left or {},
        "right_operation": right or {},
        "execution_policy": execution_policy,
        "expected": {
            "collection_detail_projection": "同一资源在列表和详情接口中的标识与共享核心字段必须一致。",
            "pagination_non_overlap": "稳定排序的相邻页不应出现重复标识，total 不应小于本页资源数。",
            "query_constraint_enforcement": "违反 OpenAPI 枚举或范围的 GET 查询应返回明确 4xx，而不是静默成功。",
            "cross_actor_resource_isolation": "从主体 A 获取的资源标识，不应被主体 B 的低权限读取成功。",
            "state_vocabulary_drift": "同一资源的状态字段在不同接口中应使用兼容的状态词表。",
            "identity_contract_drift": "路径资源标识、列表标识和详情标识应使用兼容的类型与命名。",
        }[relation_type],
        "bug_signal": {
            "collection_detail_projection": "两个 2xx 响应中的同一标识或共享字段发生无解释冲突。",
            "pagination_non_overlap": "相邻页或单页出现重复资源标识，或 total 与本页数量矛盾。",
            "query_constraint_enforcement": "无效枚举/越界查询返回 2xx 或被静默接受。",
            "cross_actor_resource_isolation": "不同测试主体可读取不属于自己的资源。",
            "state_vocabulary_drift": "状态枚举集合冲突，导致状态机/客户端解释不一致。",
            "identity_contract_drift": "同一资源的 ID 类型定义冲突，易导致越权、缓存或序列化错误。",
        }[relation_type],
        "relation_detail": detail or {},
        "discovery_mode": "semantic_counterexample",
    }


def build_counterexample_relations(openapi: dict[str, Any], cfg: dict[str, Any], max_count: int = 240) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build resource graph, candidate relations and static semantic warnings."""
    components = openapi.get("components") or {}
    operations = _operations(openapi)
    groups: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        if operation.get("method") not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            continue
        groups.setdefault(_resource_key(str(operation.get("path") or "/")), []).append(operation)

    graph: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    relation_seen: set[tuple[str, str, str, str]] = set()

    def add_relation(kind: str, *, left: dict[str, Any] | None = None, right: dict[str, Any] | None = None, execution_policy: str = "safe_read_only", detail: dict[str, Any] | None = None) -> None:
        if len(relations) >= max_count:
            return
        left = left or {}
        right = right or {}
        detail = detail or {}
        key = (kind, str(left.get("method")), str(left.get("path")), _json_text({"right": right.get("path"), "detail": detail}))
        if key in relation_seen:
            return
        relation_seen.add(key)
        relations.append(_relation(len(relations) + 1, kind, left=left, right=right, execution_policy=execution_policy, detail=detail))

    for resource, rows in sorted(groups.items()):
        reads = [row for row in rows if row.get("method") == "GET"]
        collections = [row for row in reads if _is_collection_read(row, components)]
        details = [row for row in reads if _is_detail_read(row, components)]
        graph.append({
            "resource": resource,
            "operation_count": len(rows),
            "read_operation_count": len(reads),
            "collection_reads": [_op_ref(row) for row in collections],
            "detail_reads": [_op_ref(row) for row in details],
            "mutation_operations": [_op_ref(row) for row in rows if row.get("method") in MUTATION_METHODS],
        })

        # The most useful safe relation: observe the same resource through two endpoints.
        for collection in collections:
            for detail in details:
                add_relation("collection_detail_projection", left=_op_ref(collection), right=_op_ref(detail), detail={"resource": resource})
                # The cross-actor candidate is intentionally never read live unless the
                # project explicitly opts in with two controlled test identities.
                add_relation("cross_actor_resource_isolation", left=_op_ref(collection), right=_op_ref(detail), execution_policy="candidate_only", detail={"resource": resource, "requires_two_controlled_test_accounts": True})

            pagination = _pagination_parameters(collection)
            if pagination.get("page") or pagination.get("offset"):
                add_relation("pagination_non_overlap", left=_op_ref(collection), detail={"resource": resource, "pagination": pagination})
            for constraint in _query_constraints(collection, components):
                add_relation("query_constraint_enforcement", left=_op_ref(collection), detail={"resource": resource, "constraint": constraint})

        # State enum drift is a static but valuable specification/implementation signal.
        state_by_name: dict[str, list[tuple[dict[str, Any], set[str]]]] = {}
        for row in reads:
            for name, enum in _state_enums(row, components).items():
                if enum:
                    state_by_name.setdefault(name, []).append((row, enum))
        for name, pairs in state_by_name.items():
            unique_sets = {tuple(sorted(enum)) for _, enum in pairs}
            if len(unique_sets) > 1:
                base_op, base_enum = pairs[0]
                for other_op, other_enum in pairs[1:]:
                    if base_enum == other_enum:
                        continue
                    relation = _relation(len(relations) + 1, "state_vocabulary_drift", left=_op_ref(base_op), right=_op_ref(other_op), execution_policy="candidate_only", detail={"resource": resource, "field": name, "left_enum": sorted(base_enum), "right_enum": sorted(other_enum)})
                    relations.append(relation)
                    findings.append({
                        "finding_id": f"CX_STATIC_{len(findings)+1:03d}",
                        "risk_type": relation["risk_type"],
                        "severity": relation["severity"],
                        "title": relation["title"],
                        "detail": f"资源 {resource} 的 {name} 枚举在 {base_op.get('path')} 与 {other_op.get('path')} 不一致。",
                        "evidence": relation["relation_detail"],
                        "status": "needs_human_review",
                    })

        # Compare path parameter types with the corresponding detail response identity.
        for detail in details:
            path_types = _path_parameter_types(detail, components)
            response_props = _schema_properties(_detail_object_schema(detail.get("response_schema") or {}, components), components)
            for param, param_type in path_types.items():
                candidates = [param, f"{resource}_id", "id", "uuid", "guid", "code"]
                response_name = next((name for name in candidates if name in response_props), "")
                response_type = _schema_type(response_props.get(response_name, {}), components) if response_name else ""
                if param_type and response_type and param_type != response_type and {param_type, response_type} <= {"integer", "number", "string"}:
                    relation = _relation(len(relations) + 1, "identity_contract_drift", left=_op_ref(detail), execution_policy="candidate_only", detail={"resource": resource, "path_parameter": param, "path_type": param_type, "response_field": response_name, "response_type": response_type})
                    relations.append(relation)
                    findings.append({
                        "finding_id": f"CX_STATIC_{len(findings)+1:03d}",
                        "risk_type": relation["risk_type"],
                        "severity": relation["severity"],
                        "title": relation["title"],
                        "detail": f"资源 {resource} 的路径参数 {param} 类型为 {param_type}，但详情响应字段 {response_name} 类型为 {response_type}。",
                        "evidence": relation["relation_detail"],
                        "status": "needs_human_review",
                    })

    return graph[:120], relations[:max_count], findings[:160]


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = _json_text(data).lower()
    leaks = sorted(term for term in PRIVATE_MARKERS if term.lower() in text)
    return {"passed": not leaks, "leak_terms": leaks}


def _output_paths(project: str, root: Path) -> dict[str, Path]:
    return {
        "out": root / "platform_outputs" / project / "counterexample_discovery",
        "workspace": root / "platform_workspace" / project / "defect_discovery",
        "registry": root / "platform_workspace" / project / "defect_discovery" / "counterexample_registry.json",
    }


def _load_openapi(project: str, root: Path) -> dict[str, Any]:
    paths = config_paths(project, root)
    data = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {})
    if not isinstance(data, dict) or not data.get("paths"):
        data = _load_json(paths["input_dir"] / "openapi.json", {})
    return data if isinstance(data, dict) else {}


def _summary(graph: list[dict[str, Any]], relations: list[dict[str, Any]], findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "resource_count": len(graph),
        "resource_with_collection_detail_pair_count": sum(1 for row in graph if row.get("collection_reads") and row.get("detail_reads")),
        "relation_count": len(relations),
        "safe_read_relation_count": sum(1 for row in relations if row.get("execution_policy") == "safe_read_only"),
        "candidate_only_relation_count": sum(1 for row in relations if row.get("execution_policy") == "candidate_only"),
        "semantic_finding_count": len(findings),
        "relation_distribution": _counter([str(row.get("counterexample_type") or "unknown") for row in relations]),
    }


def build_counterexample_discovery_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    openapi = _load_openapi(project, root)
    max_count = max(30, min(int(options.get("preview_relation_count") or 240), 600))
    graph, relations, findings = build_counterexample_relations(openapi, cfg, max_count=max_count)
    result = {
        "phase": "phase42_semantic_counterexample_discovery",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": _summary(graph, relations, findings),
        "resource_graph": graph,
        "relations": relations,
        "semantic_findings": findings,
        "governance": {
            "domain_agnostic": True,
            "uses_only_project_openapi_and_controlled_runtime_observations": True,
            "safe_live_executes_get_only": True,
            "cross_actor_reads_require_explicit_opt_in": True,
            "write_replay_and_concurrency_are_not_executed": True,
            "evidence_is_redacted_before_persistence": True,
            "uses_no_benchmark_answer_files": True,
        },
    }
    result["private_leak_check"] = _private_leak_check(result)
    output = _output_paths(project, root)
    _write_json(output["out"] / "counterexample_discovery.json", result)
    _write_json(output["workspace"] / "counterexample_discovery.json", result)
    (output["out"] / "counterexample_discovery_report.html").parent.mkdir(parents=True, exist_ok=True)
    (output["out"] / "counterexample_discovery_report.html").write_text(render_counterexample_discovery_report(result), encoding="utf-8")
    return result


def load_counterexample_discovery(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = _output_paths(project, root)["workspace"] / "counterexample_discovery.json"
    data = _load_json(path, {})
    return data if isinstance(data, dict) and data else None


def generate_counterexample_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    """Adapter consumed by the global risk planner."""
    _, relations, _ = build_counterexample_relations(openapi, cfg, max_count=max_count or max(160, int(cfg.get("max_probe_count") or 100) * 2))
    return relations


def _redact(value: Any, key: str = "", depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if SENSITIVE_KEY_RE.search(key or ""):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k), depth + 1) for k, v in list(value.items())[:80]}
    if isinstance(value, list):
        return [_redact(item, key, depth + 1) for item in value[:30]]
    if isinstance(value, str):
        return value[:600]
    return value


def _body_json(response: dict[str, Any]) -> Any:
    try:
        return json.loads(response.get("body") or "")
    except Exception:
        return None


def _extract_list_items(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []
    for key in LIST_KEYS:
        value = body.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _extract_list_items(value)
            if nested:
                return nested
    return []


def _extract_detail_object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    for key in DETAIL_KEYS:
        value = body.get(key)
        if isinstance(value, dict):
            return value
    return body


def _extract_total(body: Any) -> int | None:
    if not isinstance(body, dict):
        return None
    for key in ("total", "count", "total_count", "totalCount"):
        value = body.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    for nested_key in DETAIL_KEYS:
        if isinstance(body.get(nested_key), dict):
            found = _extract_total(body[nested_key])
            if found is not None:
                return found
    return None


def _stable_value(value: Any, key: str) -> Any:
    if DYNAMIC_KEY_RE.search(key) or SENSITIVE_KEY_RE.search(key):
        return None
    if isinstance(value, (dict, list)):
        return _strip_dynamic(value, key)
    return value


def _comparison_mismatches(list_item: dict[str, Any], detail_item: dict[str, Any]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    shared = sorted(set(list_item).intersection(detail_item))
    for key in shared:
        left = _stable_value(list_item.get(key), key)
        right = _stable_value(detail_item.get(key), key)
        if left is None or right is None:
            continue
        if left != right:
            mismatches.append({"field": key, "list_value": _redact(left, key), "detail_value": _redact(right, key)})
    return mismatches[:12]


def _value_for_path(item: dict[str, Any], detail_path: str, resource: str) -> tuple[str, Any] | None:
    parameters = _path_parameters(detail_path)
    if not parameters:
        return None
    param = parameters[0]
    for key in _id_candidates(item, param, resource):
        return param, item.get(key)
    return None


def _render_path_with_value(path: str, parameter: str, value: Any) -> str:
    encoded = urllib.parse.quote(str(value), safe="")
    rendered = re.sub(r"\{" + re.escape(parameter) + r"\}", encoded, path)
    return _render_path(rendered)


def _url(base_url: str, path: str, query: dict[str, Any] | None = None) -> str:
    value = _join_url(base_url, path)
    clean = {str(k): str(v) for k, v in (query or {}).items() if v is not None and str(k)}
    return value + ("&" if "?" in value else "?") + urllib.parse.urlencode(clean) if clean else value


def _fingerprint(relation: dict[str, Any], violation: dict[str, Any]) -> str:
    raw = {
        "type": relation.get("counterexample_type"),
        "left": relation.get("left_operation", {}).get("path"),
        "right": relation.get("right_operation", {}).get("path"),
        "violation": violation,
    }
    return hashlib.sha256(_json_text(raw).encode("utf-8")).hexdigest()[:20]


def _evidence_response(response: dict[str, Any]) -> dict[str, Any]:
    body = _body_json(response)
    return {
        "status_code": response.get("status_code"),
        "error": str(response.get("error") or "")[:300] or None,
        "body": _redact(body) if body is not None else str(response.get("body") or "")[:600],
    }


def _finding(relation: dict[str, Any], actual: str, violation: dict[str, Any], evidence: dict[str, Any], confidence: float) -> dict[str, Any]:
    fp = _fingerprint(relation, violation)
    return {
        "issue_id": f"CX_ISSUE_{fp[:12]}",
        "fingerprint": fp,
        "source": "counterexample_relation_mining",
        "counterexample_type": relation.get("counterexample_type"),
        "risk_type": relation.get("risk_type"),
        "severity": relation.get("severity"),
        "title": relation.get("title"),
        "status": "needs_human_review",
        "confidence": round(float(confidence), 3),
        "expected": relation.get("expected"),
        "actual": actual,
        "violation": _redact(violation),
        "evidence": _redact(evidence),
        "reproduction": {
            "relation_probe_id": relation.get("probe_id"),
            "left_operation": relation.get("left_operation"),
            "right_operation": relation.get("right_operation"),
        },
    }


def _load_registry(path: Path) -> dict[str, Any]:
    data = _load_json(path, {})
    return data if isinstance(data, dict) else {}


def _update_registry(path: Path, findings: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry = _load_registry(path)
    now = _now()
    emitted: list[dict[str, Any]] = []
    for finding in findings:
        key = str(finding.get("fingerprint") or "")
        if not key:
            continue
        previous = registry.get(key) if isinstance(registry.get(key), dict) else {}
        sightings = int(previous.get("sightings") or 0) + 1
        row = {
            "first_seen_utc": previous.get("first_seen_utc") or now,
            "last_seen_utc": now,
            "sightings": sightings,
            "counterexample_type": finding.get("counterexample_type"),
            "severity": finding.get("severity"),
            "title": finding.get("title"),
            "last_actual": finding.get("actual"),
        }
        registry[key] = row
        persistent_boost = min(0.12, max(0, sightings - 1) * 0.03)
        finding["confidence"] = round(min(0.97, float(finding.get("confidence") or 0) + persistent_boost), 3)
        finding["persistence"] = {"first_seen_utc": row["first_seen_utc"], "last_seen_utc": now, "sightings": sightings, "persistent": sightings >= 2}
        emitted.append(finding)
    # Avoid turning memory into an unbounded data store; retain the newest 2,000 fingerprints.
    if len(registry) > 2_000:
        ordered = sorted(registry.items(), key=lambda item: str((item[1] or {}).get("last_seen_utc") or ""), reverse=True)[:2_000]
        registry = dict(ordered)
    _write_json(path, registry)
    return registry, emitted


def _normal_token(accounts: Any) -> str | None:
    if not isinstance(accounts, dict):
        return None
    for name in ("normal_user", "normal", "user", "default"):
        candidate = accounts.get(name)
        if isinstance(candidate, dict) and candidate.get("token"):
            return str(candidate.get("token"))
    return None


def _execute_collection_detail(relation: dict[str, Any], base_url: str, token: str | None, timeout: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    left = relation.get("left_operation") or {}
    right = relation.get("right_operation") or {}
    collection_url = _url(base_url, _render_path(str(left.get("path") or "/")))
    first = _http_get(collection_url, token, timeout)
    record: dict[str, Any] = {"probe_id": relation.get("probe_id"), "counterexample_type": relation.get("counterexample_type"), "requests": [{"url": collection_url, "response": _evidence_response(first)}], "checks": []}
    if first.get("status_code") is None or not (200 <= int(first.get("status_code")) < 300):
        record["checks"].append({"kind": "collection_read", "result": "skipped_non_2xx"})
        return record, []
    items = _extract_list_items(_body_json(first))
    resource = str((relation.get("relation_detail") or {}).get("resource") or _resource_key(str(left.get("path") or "/")))
    if not items:
        record["checks"].append({"kind": "collection_detail_projection", "result": "skipped_empty_or_unrecognized_collection"})
        return record, []
    identifier = _value_for_path(items[0], str(right.get("path") or "/"), resource)
    if not identifier:
        record["checks"].append({"kind": "collection_detail_projection", "result": "skipped_no_safe_identifier"})
        return record, []
    param, value = identifier
    detail_path = _render_path_with_value(str(right.get("path") or "/"), param, value)
    detail_url = _url(base_url, detail_path)
    second = _http_get(detail_url, token, timeout)
    record["requests"].append({"url": detail_url, "response": _evidence_response(second)})
    if second.get("status_code") is None or not (200 <= int(second.get("status_code")) < 300):
        record["checks"].append({"kind": "collection_detail_projection", "result": "skipped_detail_non_2xx", "identifier_field": param})
        return record, []
    detail = _extract_detail_object(_body_json(second))
    mismatches = _comparison_mismatches(items[0], detail)
    record["checks"].append({"kind": "collection_detail_projection", "identifier_field": param, "identifier_value": _redact(value, param), "shared_field_mismatch_count": len(mismatches), "mismatches": mismatches})
    if not mismatches:
        return record, []
    identity_mismatch = any(row.get("field", "").lower() in ID_NAMES or row.get("field", "").lower().endswith("_id") for row in mismatches)
    finding = _finding(
        relation,
        "同一资源在列表与详情 2xx 响应中的共享字段不一致。",
        {"identifier_field": param, "mismatches": mismatches},
        {"collection_url": collection_url, "detail_url": detail_url, "collection_response": _evidence_response(first), "detail_response": _evidence_response(second)},
        0.91 if identity_mismatch else 0.78,
    )
    return record, [finding]


def _page_query(detail: dict[str, Any], page_number: int) -> dict[str, Any]:
    pagination = detail.get("pagination") or {}
    query: dict[str, Any] = {}
    size_name = str(pagination.get("size") or "")
    if size_name:
        query[size_name] = 10
    if pagination.get("page"):
        query[str(pagination.get("page"))] = page_number
    elif pagination.get("offset"):
        query[str(pagination.get("offset"))] = (page_number - 1) * 10
    return query


def _ids(items: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for item in items:
        for key in ("id", "uuid", "guid", "code", "number"):
            value = item.get(key)
            if _scalar(value) and str(value).strip():
                values.append(str(value))
                break
        else:
            named = next((value for key, value in item.items() if key.lower().endswith("_id") and _scalar(value)), None)
            if named is not None:
                values.append(str(named))
    return values


def _execute_pagination(relation: dict[str, Any], base_url: str, token: str | None, timeout: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    left = relation.get("left_operation") or {}
    path = _render_path(str(left.get("path") or "/"))
    detail = relation.get("relation_detail") or {}
    url_one = _url(base_url, path, _page_query(detail, 1))
    url_two = _url(base_url, path, _page_query(detail, 2))
    first, second = _http_get(url_one, token, timeout), _http_get(url_two, token, timeout)
    record: dict[str, Any] = {"probe_id": relation.get("probe_id"), "counterexample_type": relation.get("counterexample_type"), "requests": [{"url": url_one, "response": _evidence_response(first)}, {"url": url_two, "response": _evidence_response(second)}], "checks": []}
    if not all(response.get("status_code") is not None and 200 <= int(response.get("status_code")) < 300 for response in (first, second)):
        record["checks"].append({"kind": "pagination_non_overlap", "result": "skipped_non_2xx"})
        return record, []
    items_one, items_two = _extract_list_items(_body_json(first)), _extract_list_items(_body_json(second))
    ids_one, ids_two = _ids(items_one), _ids(items_two)
    overlap = sorted(set(ids_one).intersection(ids_two))
    duplicate_one = sorted({value for value in ids_one if ids_one.count(value) > 1})
    duplicate_two = sorted({value for value in ids_two if ids_two.count(value) > 1})
    total_one = _extract_total(_body_json(first))
    invalid_total = total_one is not None and total_one < len(items_one)
    record["checks"].append({"kind": "pagination_non_overlap", "page_one_item_count": len(items_one), "page_two_item_count": len(items_two), "overlap_ids": overlap[:10], "duplicate_page_one": duplicate_one[:10], "duplicate_page_two": duplicate_two[:10], "total": total_one, "total_less_than_page_size": invalid_total})
    if not (overlap or duplicate_one or duplicate_two or invalid_total):
        return record, []
    violation = {"overlap_ids": overlap[:10], "duplicate_page_one": duplicate_one[:10], "duplicate_page_two": duplicate_two[:10], "total": total_one, "total_less_than_page_size": invalid_total}
    finding = _finding(relation, "分页结果出现重复资源或 total 与返回数量矛盾。", violation, {"page_one_url": url_one, "page_two_url": url_two, "page_one_response": _evidence_response(first), "page_two_response": _evidence_response(second)}, 0.89 if (duplicate_one or duplicate_two or invalid_total) else 0.76)
    return record, [finding]


def _execute_query_constraint(relation: dict[str, Any], base_url: str, token: str | None, timeout: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    left = relation.get("left_operation") or {}
    constraint = (relation.get("relation_detail") or {}).get("constraint") or {}
    parameter = str(constraint.get("parameter") or "")
    if not parameter:
        return {"probe_id": relation.get("probe_id"), "counterexample_type": relation.get("counterexample_type"), "checks": [{"kind": "query_constraint_enforcement", "result": "skipped_missing_parameter"}]}, []
    path = _render_path(str(left.get("path") or "/"))
    url = _url(base_url, path, {parameter: constraint.get("value")})
    response = _http_get(url, token, timeout)
    status = response.get("status_code")
    record = {"probe_id": relation.get("probe_id"), "counterexample_type": relation.get("counterexample_type"), "requests": [{"url": url, "response": _evidence_response(response)}], "checks": [{"kind": "query_constraint_enforcement", "parameter": parameter, "mutation_kind": constraint.get("mutation_kind"), "value": _redact(constraint.get("value"), parameter), "status": status}]}
    if status is not None and 200 <= int(status) < 300:
        finding = _finding(relation, "违反 OpenAPI 查询约束的 GET 请求返回成功。", {"parameter": parameter, "mutation_kind": constraint.get("mutation_kind"), "value": _redact(constraint.get("value"), parameter), "status": status}, {"url": url, "response": _evidence_response(response)}, 0.74)
        return record, [finding]
    return record, []


def _execute_relation(relation: dict[str, Any], base_url: str, token: str | None, timeout: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    kind = str(relation.get("counterexample_type") or "")
    if kind == "collection_detail_projection":
        return _execute_collection_detail(relation, base_url, token, timeout)
    if kind == "pagination_non_overlap":
        return _execute_pagination(relation, base_url, token, timeout)
    if kind == "query_constraint_enforcement":
        return _execute_query_constraint(relation, base_url, token, timeout)
    return {"probe_id": relation.get("probe_id"), "counterexample_type": kind, "checks": [{"kind": kind, "result": "candidate_only"}]}, []


def run_counterexample_discovery(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    execution_mode = str(options.get("execution_mode") or "plan_only").lower()
    if execution_mode not in {"plan_only", "safe_live"}:
        execution_mode = "plan_only"
    profile = build_counterexample_discovery_profile(project, root, options)
    cfg = load_real_project_config(project, root)
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    max_safe = max(1, min(int(options.get("max_safe_relation_count") or 36), 120))
    paths = config_paths(project, root)
    accounts = _load_json(paths["input_dir"] / "test_accounts.json", {})
    token = _normal_token(accounts)
    priority = {"collection_detail_projection": 0, "pagination_non_overlap": 1, "query_constraint_enforcement": 2}
    safe = [row for row in profile.get("relations") or [] if row.get("execution_policy") == "safe_read_only"]
    safe.sort(key=lambda row: (priority.get(str(row.get("counterexample_type") or ""), 9), str(row.get("probe_id") or "")))
    safe = safe[:max_safe]

    observations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    if execution_mode == "safe_live" and base_url:
        for relation in safe:
            observation, emitted = _execute_relation(relation, base_url, token, timeout)
            observations.append(observation)
            findings.extend(emitted)
    else:
        reason = "plan_only" if execution_mode == "plan_only" else "missing_base_url"
        for relation in safe:
            observations.append({"probe_id": relation.get("probe_id"), "counterexample_type": relation.get("counterexample_type"), "checks": [{"result": reason}]})

    # --- LLM-powered semantic counterexample reasoning (Phase61 moat upgrade) ---
    if execution_mode == "safe_live" and findings:
        try:
            # Build rich context from the relations and observations
            relation_summaries: list[dict[str, Any]] = []
            for rel in safe[:20]:
                relation_summaries.append({
                    "probe_id": rel.get("probe_id"),
                    "type": rel.get("counterexample_type"),
                    "left": {"method": (rel.get("left_operation") or {}).get("method"), "path": (rel.get("left_operation") or {}).get("path")},
                    "right": {"method": (rel.get("right_operation") or {}).get("method"), "path": (rel.get("right_operation") or {}).get("path")},
                })

            api_spec_path = config_paths(project, root).get("openapi")
            api_schema = _read_text(api_spec_path, "") if api_spec_path else ""

            llm_context = {
                "prd_text": _read_text(config_paths(project, root).get("prd") or "", "")[:6000],
                "api_schema": api_schema[:8000],
                "observed_data": json.dumps({
                    "relations": relation_summaries,
                    "observation_count": len(observations),
                }, ensure_ascii=False, default=str)[:6000],
                "heuristic_findings": json.dumps(findings[:30], ensure_ascii=False, default=str)[:6000],
                "resource_a": json.dumps(relation_summaries[:10], ensure_ascii=False),
                "resource_b": json.dumps(relation_summaries[10:20] if len(relation_summaries) > 10 else [], ensure_ascii=False),
                "relationship_context": json.dumps({
                    "total_relations": len(safe),
                    "executed_relations": len([r for r in observations if r.get("requests")]),
                }, ensure_ascii=False),
            }

            llm_result = _llm_reason("counterexample", llm_context)
            semantic_hypotheses.extend(compile_unverified_semantic_hypotheses(
                (llm_result or {}).get("counterexamples"),
                engine="counterexample",
                type_field="counterexample_type",
            ))
            observations.append({"llm_reasoning": {"status": "completed", "semantic_hypotheses_added": len(semantic_hypotheses)}})
        except Exception:
            observations.append({"llm_reasoning": {"status": "unavailable"}})

    output = _output_paths(project, root)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase42_semantic_counterexample_discovery",
        "project_id": project,
        "project_name": profile.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": {
            **profile.get("summary", {}),
            "execution_mode": execution_mode,
            "safe_relation_execution_count": len([row for row in observations if row.get("requests")]),
            "safe_relation_plan_count": len(safe),
            "counterexample_finding_count": len(findings),
            "persistent_counterexample_count": sum(1 for row in findings if (row.get("persistence") or {}).get("persistent")),
            "memory_fingerprint_count": len(registry),
        },
        "profile": profile,
        "safe_observations": observations,
        "semantic_hypotheses": semantic_hypotheses, "llm_governance": {"status": "unverified_hypothesis_only", "does_not_affect_finding_counts": True, "requires_deterministic_replay": True}, "counterexample_findings": findings,
        "candidate_relations": [row for row in profile.get("relations") or [] if row.get("execution_policy") == "candidate_only"],
        "memory_summary": {"fingerprint_count": len(registry), "updated_at_utc": _now(), "learning_policy": "重复观察提高置信度；未人工确认的结果始终保持 needs_human_review。"},
        "governance": {
            "execution_mode": execution_mode,
            "live_requests_limited_to_get": True,
            "write_replay_disabled": True,
            "concurrency_execution_disabled": True,
            "cross_actor_read_disabled_without_explicit_opt_in": True,
            "evidence_redacted_before_persistence": True,
            "uses_no_benchmark_answer_files": True,
        },
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "counterexample_discovery_run.json", result)
    _write_json(output["workspace"] / "counterexample_discovery_run.json", result)
    (output["out"] / "counterexample_discovery_run_report.html").write_text(render_counterexample_discovery_run_report(result), encoding="utf-8")
    return result


def render_counterexample_discovery_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items() if key != "relation_distribution")
    graph = "".join(
        f"<tr><td>{_html_escape(row.get('resource'))}</td><td>{_html_escape(row.get('operation_count'))}</td><td>{_html_escape(len(row.get('collection_reads') or []))}</td><td>{_html_escape(len(row.get('detail_reads') or []))}</td><td>{_html_escape(len(row.get('mutation_operations') or []))}</td></tr>"
        for row in (data.get("resource_graph") or [])[:80]
    )
    relations = "".join(
        f"<tr><td>{_html_escape(row.get('probe_id'))}</td><td>{_html_escape(row.get('severity'))}</td><td>{_html_escape(row.get('counterexample_type'))}</td><td>{_html_escape((row.get('left_operation') or {}).get('method'))} {_html_escape((row.get('left_operation') or {}).get('path'))}</td><td>{_html_escape((row.get('right_operation') or {}).get('method'))} {_html_escape((row.get('right_operation') or {}).get('path'))}</td><td>{_html_escape(row.get('execution_policy'))}</td></tr>"
        for row in (data.get("relations") or [])[:140]
    )
    static = "".join(
        f"<tr><td>{_html_escape(row.get('severity'))}</td><td>{_html_escape(row.get('title'))}</td><td>{_html_escape(row.get('detail'))}</td></tr>"
        for row in (data.get("semantic_findings") or [])[:80]
    )
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>语义反例发现引擎</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;word-break:break-word}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body>
<section class='hero'><span class='badge'>Phase42 · Semantic Counterexample Engine</span><h1>语义反例发现引擎</h1><p>把 API 看成资源关系网，而不是孤立接口：自动构造“列表 ↔ 详情”“第一页 ↔ 第二页”“OpenAPI 约束 ↔ 实际响应”等可证伪关系；持续观测同一反例，提高持久问题的优先级。</p></section>
<section class='panel'><h2>覆盖概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>资源关系图</h2><table><thead><tr><th>资源</th><th>操作</th><th>列表读取</th><th>详情读取</th><th>写操作</th></tr></thead><tbody>{graph or '<tr><td colspan="5">暂无可关联资源</td></tr>'}</tbody></table></section>
<section class='panel'><h2>可证伪关系探针</h2><table><thead><tr><th>ID</th><th>等级</th><th>关系</th><th>左侧接口</th><th>右侧接口</th><th>策略</th></tr></thead><tbody>{relations or '<tr><td colspan="6">暂无关系探针</td></tr>'}</tbody></table></section>
<section class='panel'><h2>静态语义漂移</h2><table><thead><tr><th>等级</th><th>问题</th><th>说明</th></tr></thead><tbody>{static or '<tr><td colspan="3">未发现</td></tr>'}</tbody></table></section></body></html>"""


def render_counterexample_discovery_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items() if key != "relation_distribution")
    findings = "".join(
        f"<tr><td>{_html_escape(row.get('severity'))}</td><td>{_html_escape(row.get('counterexample_type'))}</td><td>{_html_escape(row.get('confidence'))}</td><td>{_html_escape((row.get('persistence') or {}).get('sightings'))}</td><td>{_html_escape(row.get('actual'))}</td></tr>"
        for row in (data.get("counterexample_findings") or [])[:100]
    )
    observations = "".join(
        f"<tr><td>{_html_escape(row.get('probe_id'))}</td><td>{_html_escape(row.get('counterexample_type'))}</td><td>{_html_escape(row.get('checks'))}</td></tr>"
        for row in (data.get("safe_observations") or [])[:120]
    )
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>语义反例执行报告</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;word-break:break-word}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body>
<section class='hero'><span class='badge'>Phase42 Safe Live</span><h1>语义反例发现执行</h1><p>只执行 GET 观察。发现的是可复现反例候选，默认需要人工确认；相同反例跨运行持续出现时才会提高置信度。</p></section>
<section class='panel'><h2>执行概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>反例候选</h2><table><thead><tr><th>等级</th><th>关系</th><th>置信度</th><th>观测次数</th><th>实际结果</th></tr></thead><tbody>{findings or '<tr><td colspan="5">暂无反例候选</td></tr>'}</tbody></table></section>
<section class='panel'><h2>只读观察记录</h2><table><thead><tr><th>探针</th><th>关系</th><th>检查结果</th></tr></thead><tbody>{observations or '<tr><td colspan="3">plan_only 或暂无可执行关系</td></tr>'}</tbody></table></section></body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    execution_mode = os.environ.get("COUNTEREXAMPLE_EXECUTION_MODE") or "plan_only"
    result = run_counterexample_discovery(project, options={"execution_mode": execution_mode})
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
