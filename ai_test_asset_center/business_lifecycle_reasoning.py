from __future__ import annotations

"""Phase47: lifecycle state-machine and data-lifecycle counterexample engine.

This module operationalizes business *process* quality rather than checking a
single endpoint in isolation.  It learns/loads a state-machine contract from
PRD, OpenAPI and enterprise configuration, then uses read-only evidence to
look for contradictions that frequently escape conventional API testing:

* a state reaches a milestone before its prerequisite timestamp;
* a record in a given state is missing evidence that the milestone happened;
* logically deleted/archived data leaks into an active collection;
* an effective record stays active after its expiry window;
* an event history contains an illegal state jump or disagrees with the
  current state.

Operations that require mutation (skip-state, terminal re-entry, duplicate
transition and racing transitions) are deliberately emitted as
``sandbox_required`` plans.  ``safe_live`` sends GET requests only.

The engine is intentionally contract-first: inferred rules are useful for
coverage, while explicit enterprise rules raise confidence and reduce false
positives.  Every live finding remains ``needs_human_review`` and is redacted
before persistence.
"""

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .business_invariant_mining import _infer_identity, _is_collection_read, _item_fields, _parse_time
from .business_outcome_validation import (
    _build_url,
    _http_get,
    _normal_token,
    _private_leak_check,
    _redact,
    _update_registry,
)
from .llm_reasoning import compile_unverified_semantic_hypotheses, reason as _llm_reason
from .business_reconciliation import _extract_records, _fetch_source_pages, _parse_json
from .multisource_reasoning import _learning_bonus, ingest_confirmed_bug_feedback
from .real_project_onboarding import (
    ROOT,
    _html_escape,
    _load_json,
    _read_text,
    _safe_project_id,
    _write_json,
    config_paths,
    load_real_project_config,
)
from .universal_defect_mining import _operations


STATE_FIELD_RE = re.compile(r"(?:^|[_\-.])(status|state|phase|stage|workflow|lifecycle)(?:$|[_\-.])|状态|阶段|流程", re.I)
TIME_FIELD_RE = re.compile(r"(?:^|[_\-.])(at|time|date|timestamp)(?:$|[_\-.])|时间|日期", re.I)
SOFT_DELETE_RE = re.compile(r"(?:^|[_\-.])(is_)?(deleted|archived|removed|inactive)(?:$|[_\-.])|删除|归档", re.I)
WINDOW_START_RE = re.compile(r"(?:^|[_\-.])(effective|valid|active|start|begin)(?:_?from|_?at|_?time|_?date)?(?:$|[_\-.])|生效|开始", re.I)
WINDOW_END_RE = re.compile(r"(?:^|[_\-.])(effective|valid|expire|expired|end|finish)(?:_?to|_?at|_?time|_?date)?(?:$|[_\-.])|失效|截止|结束|过期", re.I)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
FLOW_WORD_RE = re.compile(r"状态|流转|审批|支付|发货|完成|取消|关闭|退款|驳回|发布|下架|迁移|生命周期|state|transition|workflow|approve|pay|ship|complete|cancel|refund", re.I)
TERMINAL_WORD_RE = re.compile(r"cancel|closed|complete|finish|reject|refund|archive|delete|取消|关闭|完成|结束|拒绝|退款|归档|删除", re.I)
ACTION_WORD_RE = re.compile(r"pay|paid|ship|shipped|confirm|approve|reject|cancel|complete|refund|close|publish|archive|delete|支付|发货|确认|审批|驳回|取消|完成|退款|关闭|发布|归档|删除", re.I)
TRUE_DELETE_TOKENS = {"1", "true", "yes", "y", "deleted", "archived", "removed", "inactive", "已删除", "已归档"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8", errors="replace")).hexdigest()


def _short(value: Any, length: int = 12) -> str:
    return _hash(value)[:length]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _canon(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value).strip()


def _as_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "data", "rows", "records", "events", "list", "content"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    value = cfg.get("business_lifecycle_reasoning") or cfg.get("lifecycle_reasoning") or cfg.get("state_machine_reasoning") or {}
    return value if isinstance(value, dict) else {}


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    return {
        "out": root / "platform_outputs" / project / "business_lifecycle_reasoning",
        "workspace": workspace,
        "registry": workspace / "business_lifecycle_evidence_registry.json",
    }


def _field_value(row: dict[str, Any], field: str | None, mappings: dict[str, Any] | None = None) -> Any:
    if not isinstance(row, dict) or not field:
        return None
    mappings = mappings or {}
    candidates = [str(field)]
    mapped = mappings.get(str(field))
    if mapped:
        candidates.append(str(mapped))
    candidates.extend(str(key) for key, value in mappings.items() if _norm(value) == _norm(field))
    wanted = {_norm(item) for item in candidates if _norm(item)}
    for key, value in row.items():
        if _norm(key) in wanted:
            return value
    return None


def _field_name(fields: dict[str, Any], desired: str | None) -> str | None:
    target = _norm(desired)
    if not target:
        return None
    for name in fields:
        if _norm(name) == target:
            return str(name)
    for name in fields:
        norm = _norm(name)
        if target in norm or norm in target:
            return str(name)
    return None


def _resource_key(path: str) -> str:
    parts = [part for part in str(path or "").split("/") if part and not part.startswith("{")]
    raw = parts[-1] if parts else "resource"
    return _norm(raw).rstrip("s") or "resource"


def _configured_rules(section: dict[str, Any]) -> list[dict[str, Any]]:
    raw = section.get("lifecycle_rules") or section.get("state_machines") or section.get("contracts") or []
    if isinstance(raw, dict):
        raw = [raw]
    return [item for item in raw if isinstance(item, dict)]


def _rule_for_path(rules: list[dict[str, Any]], path: str) -> dict[str, Any]:
    wanted = str(path or "").rstrip("/") or "/"
    for row in rules:
        candidate = str(row.get("path") or row.get("collection_path") or "").rstrip("/") or "/"
        if candidate == wanted:
            return row
    return {}


def _infer_state_field(fields: dict[str, Any], configured: dict[str, Any]) -> str | None:
    for candidate in (configured.get("state_field"), configured.get("status_field"), configured.get("phase_field")):
        matched = _field_name(fields, str(candidate or ""))
        if matched:
            return matched
    for name in fields:
        if STATE_FIELD_RE.search(str(name)):
            return str(name)
    return None


def _enum_states(fields: dict[str, Any], state_field: str | None, configured: dict[str, Any]) -> list[str]:
    explicit = configured.get("states") or configured.get("state_values") or configured.get("statuses") or []
    if isinstance(explicit, str):
        explicit = [item.strip() for item in re.split(r"[,，|/]+", explicit) if item.strip()]
    states = [str(item).strip() for item in explicit if str(item).strip()]
    if not states and state_field and isinstance(fields.get(state_field), dict):
        states = [str(item).strip() for item in (fields[state_field].get("enum") or []) if str(item).strip()]
    return list(dict.fromkeys(states))[:80]


def _clean_state_token(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(?:订单|工单|审批|流程|状态|阶段|state|status|workflow)\s*(?:状态)?\s*[:：]", "", text, flags=re.I)
    text = re.sub(r"[()（）\[\]【】]", "", text).strip()
    text = re.sub(r"^(?:从|to|到|变为|变成)\s*", "", text, flags=re.I)
    return text.strip(" .，,;；:：")


def _prd_state_sequences(prd: str) -> list[list[str]]:
    sequences: list[list[str]] = []
    text = str(prd or "").replace("⇒", "->").replace("→", "->").replace("—>", "->")
    for fragment in re.findall(r"[^\n。；;]{0,120}->[^\n。；;]{0,120}", text):
        tokens = [_clean_state_token(item) for item in fragment.split("->")]
        tokens = [item for item in tokens if item and len(item) <= 40]
        if len(tokens) >= 2:
            sequences.append(tokens[:12])
    return sequences[:30]


def _best_prd_sequence(states: list[str], prd: str) -> list[str]:
    candidates = _prd_state_sequences(prd)
    if not candidates:
        return []
    known = {_norm(item): item for item in states}
    scored: list[tuple[int, list[str]]] = []
    for seq in candidates:
        normalized: list[str] = []
        for token in seq:
            match = known.get(_norm(token))
            if match:
                normalized.append(match)
        normalized = list(dict.fromkeys(normalized))
        if len(normalized) >= 2:
            scored.append((len(normalized), normalized))
    return max(scored, key=lambda item: item[0])[1] if scored else []


def _transition_pairs(configured: dict[str, Any], states: list[str], prd: str) -> tuple[dict[str, list[str]], list[str], str]:
    raw = configured.get("allowed_transitions") or configured.get("transitions") or {}
    edges: dict[str, list[str]] = defaultdict(list)
    sequence: list[str] = []
    if isinstance(raw, dict):
        for source, targets in raw.items():
            values = targets if isinstance(targets, list) else [targets]
            source_text = str(source).strip()
            if source_text:
                edges[source_text].extend(str(item).strip() for item in values if str(item).strip())
        return {key: list(dict.fromkeys(value)) for key, value in edges.items()}, states, "enterprise_config"
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, dict):
                source = str(row.get("from") or row.get("source") or "").strip()
                targets = row.get("to") or row.get("target") or row.get("targets") or []
                targets = targets if isinstance(targets, list) else [targets]
                if source:
                    edges[source].extend(str(item).strip() for item in targets if str(item).strip())
            elif isinstance(row, str) and "->" in row:
                tokens = [_clean_state_token(item) for item in row.split("->")]
                for left, right in zip(tokens, tokens[1:]):
                    if left and right:
                        edges[left].append(right)
        if edges:
            return {key: list(dict.fromkeys(value)) for key, value in edges.items()}, states, "enterprise_config"
    sequence = _best_prd_sequence(states, prd)
    if len(sequence) >= 2:
        for left, right in zip(sequence, sequence[1:]):
            edges[left].append(right)
        return {key: list(dict.fromkeys(value)) for key, value in edges.items()}, sequence, "prd_sequence"
    return {}, states, "enum_only"


def _terminal_states(configured: dict[str, Any], states: list[str], transitions: dict[str, list[str]]) -> list[str]:
    raw = configured.get("terminal_states") or configured.get("final_states") or []
    if isinstance(raw, str):
        raw = [item.strip() for item in re.split(r"[,，|/]+", raw) if item.strip()]
    chosen = [str(item).strip() for item in raw if str(item).strip()]
    if not chosen and transitions:
        all_values = {target for targets in transitions.values() for target in targets}
        chosen = [state for state in states if state in all_values and not transitions.get(state)]
    if not chosen:
        chosen = [state for state in states if TERMINAL_WORD_RE.search(str(state))]
    return list(dict.fromkeys(chosen))[:30]


def _timeline_fields(fields: dict[str, Any], states: list[str], configured: dict[str, Any]) -> list[dict[str, Any]]:
    raw = configured.get("timeline_fields") or configured.get("state_timeline") or []
    if isinstance(raw, dict):
        raw = [{"state": key, "field": value} for key, value in raw.items()]
    output: list[dict[str, Any]] = []
    for index, row in enumerate(raw if isinstance(raw, list) else []):
        if isinstance(row, str):
            row = {"field": row}
        if not isinstance(row, dict):
            continue
        field = _field_name(fields, str(row.get("field") or row.get("time_field") or ""))
        if field:
            output.append({"state": str(row.get("state") or ""), "field": field, "order": int(row.get("order", index))})
    if output:
        return output[:30]
    used: set[str] = set()
    ordered_states = states or []
    for index, state in enumerate(ordered_states):
        token = _norm(state)
        candidates = [f"{token}_at", f"{token}_time", f"{token}_date", f"{token}at", f"{token}time"]
        match = next((_field_name(fields, candidate) for candidate in candidates if _field_name(fields, candidate)), None)
        if match and match not in used:
            output.append({"state": state, "field": match, "order": index})
            used.add(match)
    for default in ("created_at", "create_time", "created_time", "submitted_at", "start_at"):
        match = _field_name(fields, default)
        if match and match not in used:
            output.insert(0, {"state": "created", "field": match, "order": -1})
            used.add(match)
            break
    return output[:30]


def _required_by_state(fields: dict[str, Any], timeline: list[dict[str, Any]], configured: dict[str, Any]) -> dict[str, list[str]]:
    raw = configured.get("required_fields_by_state") or configured.get("state_requirements") or {}
    output: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for state, values in raw.items():
            values = values if isinstance(values, list) else [values]
            matched = [_field_name(fields, str(item)) for item in values]
            output[str(state)] = [item for item in matched if item]
    if output:
        return output
    for item in timeline:
        state, field = str(item.get("state") or ""), str(item.get("field") or "")
        if state and field and _norm(state) not in {_norm("created"), _norm("new"), _norm("draft")}:
            output.setdefault(state, []).append(field)
    return output


def _soft_delete_fields(fields: dict[str, Any], configured: dict[str, Any]) -> list[str]:
    raw = configured.get("soft_delete_field") or configured.get("soft_delete_fields") or []
    if isinstance(raw, str):
        raw = [raw]
    selected = [_field_name(fields, str(item)) for item in raw if str(item).strip()]
    selected = [item for item in selected if item]
    if selected:
        return list(dict.fromkeys(selected))
    return [str(name) for name in fields if SOFT_DELETE_RE.search(str(name))][:4]


def _effective_windows(fields: dict[str, Any], configured: dict[str, Any]) -> list[dict[str, Any]]:
    raw = configured.get("effective_windows") or configured.get("validity_windows") or []
    if isinstance(raw, dict):
        raw = [raw]
    windows: list[dict[str, Any]] = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        start = _field_name(fields, str(row.get("start_field") or row.get("from_field") or ""))
        end = _field_name(fields, str(row.get("end_field") or row.get("to_field") or row.get("expiry_field") or ""))
        active = _field_name(fields, str(row.get("active_field") or row.get("state_field") or ""))
        values = row.get("active_values") or row.get("active_when") or []
        values = values if isinstance(values, list) else [values]
        if end:
            windows.append({"start_field": start, "end_field": end, "active_field": active, "active_values": [str(item) for item in values if str(item).strip()], "source": "enterprise_config"})
    if windows:
        return windows[:10]
    ends = [str(name) for name in fields if WINDOW_END_RE.search(str(name)) and TIME_FIELD_RE.search(str(name))]
    starts = [str(name) for name in fields if WINDOW_START_RE.search(str(name)) and TIME_FIELD_RE.search(str(name))]
    if ends:
        return [{"start_field": starts[0] if starts else None, "end_field": ends[0], "active_field": None, "active_values": [], "source": "openapi_inferred"}]
    return []


def _history_config(configured: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any] | None:
    template = str(configured.get("history_path_template") or configured.get("event_history_path") or configured.get("history_path") or "").strip()
    if not template:
        return None
    return {
        "path_template": template,
        "event_state_field": str(configured.get("history_event_state_field") or configured.get("event_state_field") or "to_status"),
        "event_from_field": str(configured.get("history_event_from_field") or configured.get("event_from_field") or "from_status"),
        "event_time_field": str(configured.get("history_event_time_field") or configured.get("event_time_field") or "created_at"),
        "history_must_match_current": bool(configured.get("history_must_match_current", False)),
        "sample_limit": max(1, min(int(configured.get("history_sample_limit") or 10), 100)),
    }


def _write_actions(openapi: dict[str, Any], collection_path: str, resource: str, configured: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = configured.get("write_actions") or configured.get("transition_actions") or []
    if isinstance(explicit, dict):
        explicit = [explicit]
    output: list[dict[str, Any]] = []
    for row in explicit if isinstance(explicit, list) else []:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "")
        method = str(row.get("method") or "POST").upper()
        if path and method in WRITE_METHODS:
            output.append({"path": path, "method": method, "action": str(row.get("action") or "transition"), "source": "enterprise_config"})
    if output:
        return output[:30]
    root = str(collection_path or "").rstrip("/")
    for operation in _operations(openapi):
        method, path = str(operation.get("method") or "").upper(), str(operation.get("path") or "")
        if method not in WRITE_METHODS or not path:
            continue
        same_resource = root and (path.startswith(root + "/") or _resource_key(path) == resource)
        if same_resource and ACTION_WORD_RE.search(path + " " + str(operation.get("summary") or "")):
            output.append({"path": path, "method": method, "action": str(operation.get("summary") or path.rsplit("/", 1)[-1]), "source": "openapi_inferred"})
    return output[:30]


def build_business_lifecycle_contracts(openapi: dict[str, Any], cfg: dict[str, Any], prd_text: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    components = openapi.get("components") or {}
    section = _section(cfg)
    rules = _configured_rules(section)
    contracts: list[dict[str, Any]] = []
    for operation in _operations(openapi):
        if not _is_collection_read(operation, components):
            continue
        path = str(operation.get("path") or "")
        configured = _rule_for_path(rules, path)
        fields = _item_fields(operation, components)
        state_field = _infer_state_field(fields, configured)
        if not state_field:
            continue
        resource = str(configured.get("resource") or _resource_key(path))
        identity = _field_name(fields, str(configured.get("identity_field") or "")) or _infer_identity(resource, fields, configured)
        states = _enum_states(fields, state_field, configured)
        transitions, ordered_states, transition_source = _transition_pairs(configured, states, prd_text)
        terminal = _terminal_states(configured, states or ordered_states, transitions)
        timeline = _timeline_fields(fields, ordered_states or states, configured)
        requirements = _required_by_state(fields, timeline, configured)
        soft_delete = _soft_delete_fields(fields, configured)
        windows = _effective_windows(fields, configured)
        history = _history_config(configured, fields)
        actions = _write_actions(openapi, path, resource, configured)
        contracts.append({
            "contract_id": f"BLR_CONTRACT_{len(contracts)+1:04d}",
            "resource": resource,
            "collection": {"path": path, "method": "GET", "parameters": operation.get("parameters") or [], "summary": operation.get("summary") or ""},
            "source": {"path": path, "method": "GET", "parameters": operation.get("parameters") or []},
            "sample_query": dict(configured.get("sample_query") or configured.get("query") or {}),
            "pagination": dict(configured.get("pagination") or {}),
            "field_mappings": dict(configured.get("field_mappings") or {}),
            "identity_field": identity,
            "state_field": state_field,
            "states": states,
            "allowed_transitions": transitions,
            "ordered_states": ordered_states,
            "terminal_states": terminal,
            "timeline_fields": timeline,
            "required_fields_by_state": requirements,
            "soft_delete_fields": soft_delete,
            "effective_windows": windows,
            "history": history,
            "write_actions": actions,
            "transition_source": transition_source,
            "oracle_family": "business_lifecycle",
            "execution_policy": "safe_read_only",
            "discovery": "configured" if configured else "openapi_prd_inferred",
            "source_evidence": [source for source, enabled in (("openapi", True), ("prd", bool(prd_text.strip())), ("enterprise_config", bool(configured))) if enabled],
        })
    candidates: list[dict[str, Any]] = []
    if FLOW_WORD_RE.search(prd_text or "") and not contracts:
        candidates.append({"candidate_id": "BLR_PRD_FLOW_UNMAPPED", "title": "PRD 包含流程/状态机要求，但未发现可读取的状态集合接口", "severity": "P2", "risk_type": "lifecycle_contract_gap", "detail": "为状态列表接口补充 OpenAPI 响应 schema，或在 business_lifecycle_reasoning.lifecycle_rules 中配置 path、state_field 与状态流转。"})
    for contract in contracts:
        if not contract.get("allowed_transitions") and contract.get("write_actions"):
            candidates.append({"candidate_id": f"{contract['contract_id']}_TRANSITION_UNMAPPED", "title": f"{contract.get('resource')} 存在状态写操作但未映射允许流转", "severity": "P2", "risk_type": "lifecycle_contract_gap", "detail": "配置 allowed_transitions 或在 PRD 中写明 A -> B 流转，才能验证历史是否跳步/回退。"})
    return contracts[:150], candidates[:100]


def _summary(contracts: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "lifecycle_contract_count": len(contracts),
        "transition_contract_count": sum(1 for item in contracts if item.get("allowed_transitions")),
        "timeline_contract_count": sum(1 for item in contracts if len(item.get("timeline_fields") or []) >= 2),
        "state_evidence_contract_count": sum(1 for item in contracts if item.get("required_fields_by_state")),
        "history_contract_count": sum(1 for item in contracts if item.get("history")),
        "soft_delete_contract_count": sum(1 for item in contracts if item.get("soft_delete_fields")),
        "effective_window_contract_count": sum(1 for item in contracts if item.get("effective_windows")),
        "sandbox_transition_action_count": sum(len(item.get("write_actions") or []) for item in contracts),
        "contract_gap_count": len(candidates),
    }


def build_business_lifecycle_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    prd = _read_text(paths["input_dir"] / "prd.md")
    contracts, candidates = build_business_lifecycle_contracts(openapi, cfg, prd)
    learning = ingest_confirmed_bug_feedback(project, root)
    memory = learning.get("memory") or {}
    for contract in contracts:
        bonus, matches = _learning_bonus(contract, memory)
        contract["learning_bonus"] = bonus
        contract["learning_matches"] = matches
    result = {
        "phase": "phase47_business_lifecycle_reasoning",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "source_inventory": {"prd_available": bool(prd.strip()), "api_operation_count": len(_operations(openapi)), "stateful_collection_count": len(contracts)},
        "contracts": contracts,
        "candidates": candidates,
        "summary": {**_summary(contracts, candidates), "confirmed_bug_memory_count": int((learning.get("summary") or {}).get("confirmed_bug_memory_count") or 0), "learned_pattern_count": int((learning.get("summary") or {}).get("learned_pattern_count") or 0)},
        "confirmed_bug_learning": {"summary": learning.get("summary") or {}, "patterns": (memory.get("patterns") or [])[:50]},
        "governance": {"default_execution": "plan_only", "safe_live_only_uses_get": True, "state_mutation_and_concurrency_are_sandbox_required": True, "history_samples_are_bounded": True, "findings_need_human_review": True, "raw_business_payloads_are_not_persisted": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    output = _output_paths(project, root)
    _write_json(output["out"] / "business_lifecycle_profile.json", result)
    _write_json(output["workspace"] / "business_lifecycle_profile.json", result)
    output["out"].mkdir(parents=True, exist_ok=True)
    (output["out"] / "business_lifecycle_profile_report.html").write_text(render_business_lifecycle_profile_report(result), encoding="utf-8")
    return result


def load_business_lifecycle_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_output_paths(project, root)["workspace"] / "business_lifecycle_profile.json", {})
    return data if isinstance(data, dict) and data else None


def _probe(contract: dict[str, Any], number: int, kind: str, title: str, risk_type: str, method: str = "GET", path: str | None = None, execution_policy: str = "safe_read_only", destructive: bool = False, **extra: Any) -> dict[str, Any]:
    return {
        "probe_id": f"BLR_PROBE_{number:04d}",
        "source": "business_lifecycle_reasoning",
        "risk_type": risk_type,
        "lifecycle_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "severity": extra.pop("severity", "P1"),
        "expected": extra.pop("expected", "业务生命周期约束必须持续成立。"),
        "method": method,
        "path": path if path is not None else str((contract.get("collection") or {}).get("path") or ""),
        "actor": "normal_user",
        "destructive": destructive,
        "execution_policy": execution_policy,
        "learning_bonus": contract.get("learning_bonus") or 0.0,
        "learning_matches": contract.get("learning_matches") or [],
        **extra,
    }


def generate_business_lifecycle_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    profile = load_business_lifecycle_profile(project_id, root) or build_business_lifecycle_profile(project_id, root)
    probes: list[dict[str, Any]] = []
    limit = max(1, int(max_count or cfg.get("max_probe_count") or 160))
    for contract in profile.get("contracts") or []:
        resource = str(contract.get("resource") or "resource")
        if len(contract.get("timeline_fields") or []) >= 2:
            probes.append(_probe(contract, len(probes)+1, "timeline_order", f"生命周期时间线：{resource} 里程碑不得倒置", "lifecycle_temporal_order", expected="生命周期里程碑时间必须按照业务流程顺序递增。"))
        if contract.get("required_fields_by_state"):
            probes.append(_probe(contract, len(probes)+1, "state_evidence", f"状态证据：{resource} 当前状态必须有对应业务凭证", "lifecycle_state_evidence", expected="状态到达后必须存在与该阶段匹配的时间戳、凭证或业务字段。"))
        if contract.get("soft_delete_fields"):
            probes.append(_probe(contract, len(probes)+1, "soft_delete_visibility", f"数据生命周期：{resource} 已删除/归档记录不得泄漏到活跃列表", "lifecycle_soft_delete", expected="活跃集合中不应出现已删除、归档或无效的数据。"))
        if contract.get("effective_windows"):
            probes.append(_probe(contract, len(probes)+1, "effective_window", f"数据生命周期：{resource} 过期记录不得继续处于有效状态", "lifecycle_effective_window", expected="超过有效期的记录不应仍满足有效状态条件。", severity="P2"))
        if contract.get("history") and contract.get("allowed_transitions"):
            probes.append(_probe(contract, len(probes)+1, "history_transition", f"状态历史：{resource} 不得发生跳步、回退或非法流转", "lifecycle_history", expected="事件历史的每一次状态变化必须属于允许流转。"))
        if contract.get("write_actions") and contract.get("allowed_transitions"):
            states = list(contract.get("ordered_states") or contract.get("states") or [])
            terminal = list(contract.get("terminal_states") or [])
            for action in contract.get("write_actions") or []:
                action_path = str(action.get("path") or "")
                action_method = str(action.get("method") or "POST").upper()
                probes.append(_probe(contract, len(probes)+1, "duplicate_transition", f"沙箱状态机：{resource} {action.get('action')} 重复推进不得产生副作用", "lifecycle_transition", method=action_method, path=action_path, execution_policy="sandbox_required", destructive=True, severity="P1", expected="同一状态动作重复执行必须幂等或被拒绝。", mutation_scenario="duplicate_transition"))
                if terminal and states:
                    probes.append(_probe(contract, len(probes)+1, "terminal_reentry", f"沙箱状态机：{resource} 终态不得重新进入可操作状态", "lifecycle_transition", method=action_method, path=action_path, execution_policy="sandbox_required", destructive=True, severity="P0", expected="取消、完成、关闭等终态不能被重新支付、审批、发货或回退。", mutation_scenario="terminal_reentry", terminal_states=terminal, target_state=states[0]))
                if len(states) >= 3:
                    probes.append(_probe(contract, len(probes)+1, "skip_transition", f"沙箱状态机：{resource} 不得跳过中间状态", "lifecycle_transition", method=action_method, path=action_path, execution_policy="sandbox_required", destructive=True, severity="P1", expected="状态只能沿允许边逐步推进，不能跳过前置业务动作。", mutation_scenario="skip_transition", from_state=states[0], target_state=states[2]))
        if len(probes) >= limit:
            return probes[:limit]
    for gap in profile.get("candidates") or []:
        probes.append({"probe_id": f"BLR_GAP_{len(probes)+1:04d}", "source": "business_lifecycle_reasoning", "risk_type": gap.get("risk_type") or "lifecycle_contract_gap", "lifecycle_type": "contract_gap", "title": gap.get("title"), "severity": gap.get("severity") or "P2", "expected": gap.get("detail"), "method": "GET", "path": "", "actor": "normal_user", "destructive": False, "execution_policy": "candidate_only", "contract_id": gap.get("candidate_id")})
        if len(probes) >= limit:
            break
    return probes[:limit]


def _finding(contract: dict[str, Any], kind: str, title: str, expected: str, actual: str, evidence: dict[str, Any], severity: str = "P1", confidence: float = 0.87, key: Any | None = None) -> dict[str, Any]:
    fingerprint = _hash({"contract": contract.get("contract_id"), "kind": kind, "key": key})
    return {
        "issue_id": f"BLR_{fingerprint[:12].upper()}",
        "fingerprint": fingerprint,
        "source": "business_lifecycle_reasoning",
        "risk_type": f"lifecycle_{kind}",
        "lifecycle_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "severity": severity,
        "status": "needs_human_review",
        "confidence": confidence,
        "expected": expected,
        "actual": actual,
        "evidence": _redact(evidence),
    }


def _is_deleted(value: Any, field: str) -> bool:
    if value is None:
        return False
    if "at" in _norm(field) or "time" in _norm(field) or "date" in _norm(field):
        return bool(_canon(value))
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _canon(value).lower() in TRUE_DELETE_TOKENS


def _same_state(value: Any, target: Any) -> bool:
    return _norm(value) == _norm(target)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: Any) -> datetime | None:
    parsed = _parse_time(value)
    if not parsed:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def audit_lifecycle_snapshot(contract: dict[str, Any], source_context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Audit lifecycle facts using already-fetched collection data only."""
    rows = [row for row in (source_context.get("records") or []) if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    identity = str(contract.get("identity_field") or "")
    state_field = str(contract.get("state_field") or "")
    coverage = {"complete": bool(source_context.get("complete")), "source_total": source_context.get("total"), "fetched_row_count": len(rows)}
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []

    # Milestone chronology (created <= paid <= shipped <= completed, etc.).
    timeline = sorted(contract.get("timeline_fields") or [], key=lambda item: int(item.get("order") or 0))
    inverted: list[dict[str, Any]] = []
    if len(timeline) >= 2:
        for index, row in enumerate(rows, start=1):
            previous: tuple[str, datetime] | None = None
            for item in timeline:
                field = str(item.get("field") or "")
                timestamp = _as_aware(_field_value(row, field, mappings))
                if not timestamp:
                    continue
                if previous and timestamp < previous[1]:
                    inverted.append({"row_index": index, "identity_hash": _short(_canon(_field_value(row, identity, mappings))) if identity else None, "earlier_field": previous[0], "later_field": field})
                previous = (field, timestamp)
        observations.append({"kind": "timeline_order", "timeline_fields": [item.get("field") for item in timeline], "inverted_count": len(inverted)})
        if inverted:
            findings.append(_finding(contract, "temporal_order", f"生命周期时间线倒置：{contract.get('resource')}", "生命周期里程碑必须按业务顺序递增。", f"发现 {len(inverted)} 条记录的后续里程碑早于前置里程碑。", {"request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": contract.get("sample_query") or {}}, "violations": inverted[:20], "coverage": coverage}, confidence=0.95, key="timeline"))

    # State must have evidence (usually a state-specific time or receipt).
    missing_evidence: list[dict[str, Any]] = []
    requirements = contract.get("required_fields_by_state") or {}
    if state_field and requirements:
        for index, row in enumerate(rows, start=1):
            state = _canon(_field_value(row, state_field, mappings))
            matched_state = next((key for key in requirements if _same_state(key, state)), None)
            if not matched_state:
                continue
            missing = [field for field in requirements.get(matched_state) or [] if not _canon(_field_value(row, str(field), mappings))]
            if missing:
                missing_evidence.append({"row_index": index, "identity_hash": _short(_canon(_field_value(row, identity, mappings))) if identity else None, "state_hash": _short(state), "missing_fields": missing})
        observations.append({"kind": "state_evidence", "state_field": state_field, "violation_count": len(missing_evidence)})
        if missing_evidence:
            inferred = contract.get("discovery") != "configured"
            findings.append(_finding(contract, "state_evidence", f"状态缺少业务凭证：{contract.get('resource')} {state_field}", "状态达到业务阶段后应保留对应时间戳、回执或必要业务字段。", f"发现 {len(missing_evidence)} 条记录的状态与阶段凭证不一致。", {"request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": contract.get("sample_query") or {}}, "violations": missing_evidence[:20], "coverage": coverage, "rule_source": contract.get("discovery")}, severity="P1" if not inferred else "P2", confidence=0.92 if not inferred else 0.76, key="state_evidence"))

    # Soft-deleted records must not leak through an active list.
    deleted_rows: list[dict[str, Any]] = []
    for field in contract.get("soft_delete_fields") or []:
        for index, row in enumerate(rows, start=1):
            if _is_deleted(_field_value(row, str(field), mappings), str(field)):
                deleted_rows.append({"row_index": index, "identity_hash": _short(_canon(_field_value(row, identity, mappings))) if identity else None, "delete_field": field})
    observations.append({"kind": "soft_delete_visibility", "fields": contract.get("soft_delete_fields") or [], "leak_count": len(deleted_rows)})
    if deleted_rows:
        inferred = contract.get("discovery") != "configured"
        findings.append(_finding(contract, "soft_delete_visibility", f"已删除/归档数据泄漏：{contract.get('resource')}", "活跃集合不应返回已删除、归档或无效的业务记录。", f"发现 {len(deleted_rows)} 条逻辑删除/归档记录仍出现在读取结果中。", {"request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": contract.get("sample_query") or {}}, "violations": deleted_rows[:20], "coverage": coverage}, severity="P1" if not inferred else "P2", confidence=0.95 if not inferred else 0.78, key="soft_delete"))

    # A record past its declared end time cannot remain active.
    expired_rows: list[dict[str, Any]] = []
    now = _utc_now()
    for window in contract.get("effective_windows") or []:
        end_field, active_field = str(window.get("end_field") or ""), str(window.get("active_field") or "")
        active_values = {_norm(item) for item in (window.get("active_values") or [])}
        for index, row in enumerate(rows, start=1):
            end = _as_aware(_field_value(row, end_field, mappings))
            if not end or end >= now:
                continue
            active = _field_value(row, active_field, mappings) if active_field else None
            if active_values and _norm(active) not in active_values:
                continue
            # Auto-inferred windows are only high confidence when an explicit active condition is available.
            if not active_values and not active_field and str(window.get("source")) != "enterprise_config":
                continue
            expired_rows.append({"row_index": index, "identity_hash": _short(_canon(_field_value(row, identity, mappings))) if identity else None, "end_field": end_field, "active_field": active_field or None})
    observations.append({"kind": "effective_window", "window_count": len(contract.get("effective_windows") or []), "expired_active_count": len(expired_rows)})
    if expired_rows:
        findings.append(_finding(contract, "effective_window", f"过期数据仍有效：{contract.get('resource')}", "超过有效期的记录不得继续满足有效/可用状态。", f"发现 {len(expired_rows)} 条记录已过期但仍保持有效状态。", {"request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": contract.get("sample_query") or {}}, "violations": expired_rows[:20], "coverage": coverage}, severity="P1", confidence=0.91, key="effective_window"))
    return findings, observations


def _history_url(base_url: str, template: str, identity_field: str, identity_value: Any) -> str:
    rendered = str(template)
    replacement = str(identity_value)
    rendered = rendered.replace("{" + identity_field + "}", replacement)
    rendered = re.sub(r"\{[^}]+\}", replacement, rendered)
    return _build_url(base_url, rendered, {})


def _event_rows(payload: Any) -> list[dict[str, Any]]:
    rows = _as_rows(payload)
    if rows:
        return rows
    extracted, _ = _extract_records(payload)
    return [item for item in extracted if isinstance(item, dict)]


def audit_lifecycle_history(contract: dict[str, Any], source_context: dict[str, Any], base_url: str, token: str | None, timeout: int, max_bytes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read a bounded set of event histories and check state-machine edges."""
    history = contract.get("history") or {}
    if not history or not contract.get("allowed_transitions"):
        return [], []
    rows = [row for row in (source_context.get("records") or []) if isinstance(row, dict)]
    identity = str(contract.get("identity_field") or "")
    if not identity:
        return [], [{"kind": "history_transition", "result": "skipped_missing_identity"}]
    mappings = dict(contract.get("field_mappings") or {})
    events_state = str(history.get("event_state_field") or "to_status")
    events_from = str(history.get("event_from_field") or "from_status")
    events_time = str(history.get("event_time_field") or "created_at")
    state_field = str(contract.get("state_field") or "")
    allowed = {_norm(source): {_norm(target) for target in targets} for source, targets in (contract.get("allowed_transitions") or {}).items()}
    known_states = {_norm(item) for item in (contract.get("states") or [])}
    sample_limit = max(1, min(int(history.get("sample_limit") or 10), len(rows)))
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows[:sample_limit], start=1):
        identity_value = _field_value(row, identity, mappings)
        if not _canon(identity_value):
            continue
        url = _history_url(base_url, str(history.get("path_template") or ""), identity, identity_value)
        response = _http_get(url, token, timeout, max_bytes)
        if not response.get("ok"):
            observations.append({"kind": "history_transition", "row_index": row_index, "identity_hash": _short(_canon(identity_value)), "result": "http_error", "status_code": response.get("status_code")})
            continue
        payload = _parse_json(response)
        events = _event_rows(payload)
        decorated: list[tuple[int, dict[str, Any]]] = []
        for index, event in enumerate(events):
            moment = _as_aware(_field_value(event, events_time))
            decorated.append((int(moment.timestamp()) if moment else index, event))
        events = [item[1] for item in sorted(decorated, key=lambda item: item[0])]
        previous_state: str | None = None
        invalid: list[dict[str, Any]] = []
        final_event_state: str | None = None
        for index, event in enumerate(events, start=1):
            target = _canon(_field_value(event, events_state)) or _canon(_field_value(event, "status")) or _canon(_field_value(event, "state"))
            source = _canon(_field_value(event, events_from)) or previous_state or ""
            if not target:
                continue
            if known_states and _norm(target) not in known_states:
                invalid.append({"event_index": index, "kind": "unknown_state", "target_hash": _short(target)})
            elif source and _norm(source) != _norm(target) and _norm(target) not in allowed.get(_norm(source), set()):
                invalid.append({"event_index": index, "kind": "illegal_transition", "from_hash": _short(source), "to_hash": _short(target)})
            previous_state = target
            final_event_state = target
        current_state = _canon(_field_value(row, state_field, mappings))
        if bool(history.get("history_must_match_current")) and final_event_state and current_state and not _same_state(final_event_state, current_state):
            invalid.append({"kind": "history_current_mismatch", "history_state_hash": _short(final_event_state), "current_state_hash": _short(current_state)})
        observations.append({"kind": "history_transition", "row_index": row_index, "identity_hash": _short(_canon(identity_value)), "event_count": len(events), "invalid_count": len(invalid)})
        if invalid:
            findings.append(_finding(contract, "history", f"状态历史存在非法流转：{contract.get('resource')}", "事件历史每一步都必须符合允许状态流转，并与当前状态一致（若该规则已启用）。", f"发现 {len(invalid)} 个非法状态历史证据。", {"request": {"method": "GET", "url": url}, "identity_hash": _short(_canon(identity_value)), "violations": invalid[:20], "event_count": len(events)}, severity="P1", confidence=0.93, key={"identity": _short(_canon(identity_value)), "issues": invalid}))
    return findings, observations


def run_business_lifecycle_reasoning(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    section = _section(cfg)
    profile = build_business_lifecycle_profile(project, root, options)
    execution_mode = str(options.get("execution_mode") or cfg.get("business_lifecycle_execution_mode") or "plan_only").lower()
    if execution_mode not in {"plan_only", "safe_live"}:
        execution_mode = "plan_only"
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    max_bytes = max(100_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 3_000_000), 20_000_000))
    max_pages = max(1, min(int(options.get("max_source_pages") or section.get("max_source_pages") or 12), 100))
    base_url = str(cfg.get("base_url") or "")
    token = _normal_token(cfg, project, root, timeout) if execution_mode == "safe_live" and base_url else None
    executions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    for contract in profile.get("contracts") or []:
        if execution_mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract.get("contract_id"), "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        source = _fetch_source_pages(base_url, contract, token, timeout, max_bytes, max_pages)
        response_rows = source.get("responses") or []
        if not response_rows or not response_rows[0].get("status_code"):
            executions.append({"contract_id": contract.get("contract_id"), "status": "error", "reason": "collection_fetch_failed", "responses": response_rows})
            continue
        current_findings, observations = audit_lifecycle_snapshot(contract, source)
        history_findings, history_observations = audit_lifecycle_history(contract, source, base_url, token, timeout, max_bytes)
        current_findings.extend(history_findings)
        findings.extend(current_findings)
        executions.append({"contract_id": contract.get("contract_id"), "status": "executed", "source_complete": bool(source.get("complete")), "source_total": source.get("total"), "fetched_source_rows": len(source.get("records") or []), "source_responses": response_rows, "runtime_observations": observations, "history_observations": history_observations, "finding_count": len(current_findings)})

    # --- LLM-powered semantic lifecycle reasoning (Phase61 moat upgrade) ---
    if execution_mode == "safe_live" and findings:
        try:
            llm_result = _llm_reason("lifecycle", {
                "prd_text": "", "api_schema": "", "observed_data": json.dumps(executions[-5:], ensure_ascii=False, default=str)[:4000],
                "heuristic_findings": json.dumps(findings[:15], ensure_ascii=False, default=str)[:4000],
                "lifecycle_definition": json.dumps([c.get("states","") for c in profile.get("contracts",[])[:5]], ensure_ascii=False),
                "observed_transitions": json.dumps(executions[-5:], ensure_ascii=False, default=str)[:4000],
                "schema_context": "{}",
            })
            semantic_hypotheses.extend(compile_unverified_semantic_hypotheses(
                (llm_result or {}).get("findings"),
                engine="lifecycle",
                type_field="lifecycle_type",
            ))
        except Exception:
            pass

    # Mutation plans are present in the output, but never executed here.
    sandbox_plans = [probe for probe in generate_business_lifecycle_probes({}, cfg, project, root, max_count=1000) if probe.get("execution_policy") == "sandbox_required"]
    for plan in sandbox_plans:
        executions.append({"contract_id": plan.get("contract_id"), "status": "candidate_only", "family": "lifecycle_mutation", "reason": "sandbox_required_no_production_write", "probe_id": plan.get("probe_id"), "scenario": plan.get("mutation_scenario"), "path": plan.get("path"), "method": plan.get("method")})
    output = _output_paths(project, root)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase47_business_lifecycle_reasoning",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": {**profile.get("summary", {}), "execution_mode": execution_mode, "executed_contract_count": sum(1 for item in executions if item.get("status") == "executed"), "business_lifecycle_finding_count": len(findings), "persistent_business_lifecycle_count": sum(1 for item in findings if (item.get("evidence_stability") or {}).get("persistent")), "sandbox_transition_candidate_count": len(sandbox_plans), "memory_fingerprint_count": len((registry or {}).get("entries") or {})},
        "profile": profile,
        "executions": executions,
        "semantic_hypotheses": semantic_hypotheses, "llm_governance": {"status": "unverified_hypothesis_only", "does_not_affect_finding_counts": True, "requires_deterministic_replay": True}, "findings": findings,
        "memory_summary": {"fingerprint_count": len((registry or {}).get("entries") or {}), "updated_at_utc": _now(), "learning_policy": "同一生命周期反例跨运行重复出现时提升置信度；未经人工确认始终保持 needs_human_review。"},
        "governance": {"execution_mode": execution_mode, "live_requests_limited_to_get": True, "writes_never_executed_by_this_engine": True, "history_sampling_bounded": True, "evidence_redacted_before_persistence": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "business_lifecycle_run.json", result)
    _write_json(output["workspace"] / "business_lifecycle_run.json", result)
    (output["out"] / "business_lifecycle_run_report.html").write_text(render_business_lifecycle_run_report(result), encoding="utf-8")
    return result


def _render_html(title: str, badge: str, subtitle: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{_html_escape(title)}</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body>
<section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(subtitle)}</p></section><section class='panel'><div class='grid'>{cards}</div></section><section class='panel'>{body}</section></body></html>"""


def render_business_lifecycle_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items())
    rows = []
    for contract in data.get("contracts") or []:
        rows.append(f"<tr><td>{_html_escape(contract.get('contract_id'))}</td><td>{_html_escape(contract.get('resource'))}</td><td>{_html_escape((contract.get('collection') or {}).get('path'))}</td><td>{_html_escape(contract.get('state_field'))}</td><td>{_html_escape(', '.join(str(item.get('field')) for item in contract.get('timeline_fields') or []) or '-')}</td><td>{_html_escape(contract.get('transition_source'))}</td></tr>")
    return _render_html("业务生命周期推理", "Phase47 · Lifecycle Counterexample Engine", "从 PRD、OpenAPI、状态数据和事件历史中构建可证伪状态机；写入式反例只生成隔离沙箱计划。", cards, "<h2>已发现的生命周期契约</h2><table><thead><tr><th>ID</th><th>资源</th><th>集合接口</th><th>状态字段</th><th>时间线</th><th>流转来源</th></tr></thead><tbody>" + ("".join(rows) or "<tr><td colspan='6'>暂无可执行状态集合契约</td></tr>") + "</tbody></table>")


def render_business_lifecycle_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items())
    rows = []
    for finding in data.get("findings") or []:
        rows.append(f"<tr><td>{_html_escape(finding.get('severity'))}</td><td>{_html_escape(finding.get('lifecycle_type'))}</td><td>{_html_escape(finding.get('title'))}</td><td>{_html_escape(finding.get('actual'))}</td><td>{_html_escape(finding.get('confidence'))}</td><td>{_html_escape((finding.get('evidence_stability') or {}).get('observations', 1))}</td></tr>")
    return _render_html("业务生命周期运行结果", "Phase47 · Lifecycle Counterexample Engine", "只读运行基于实际集合与事件历史发现状态机、时间线和数据生命周期反例；写入路径始终保持隔离。", cards, "<h2>发现的流程反例</h2><table><thead><tr><th>等级</th><th>类型</th><th>问题</th><th>实际</th><th>置信度</th><th>观测次数</th></tr></thead><tbody>" + ("".join(rows) or "<tr><td colspan='6'>未发现已证伪的生命周期约束</td></tr>") + "</tbody></table>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QualiBug Phase47 business lifecycle reasoning")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--mode", choices=["plan_only", "safe_live"], default="plan_only")
    args = parser.parse_args(argv)
    result = run_business_lifecycle_reasoning(args.project, options={"execution_mode": args.mode})
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
