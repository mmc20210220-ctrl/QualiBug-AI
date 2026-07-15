"""Historical-bug → executable behavior-slice bridge.

Enterprise customers supply past defect write-ups (HISTORICAL_BUGS.md,
historical_bugs/*.json, tickets). This module turns those narratives into
source-bound behavior slices by matching risk semantics to the live API catalog.

No ground-truth peeking, no per-industry endpoint tables.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .business_state_graph import _api_facts, behavior_slice_id
from .historical_bug_importer import RISK_KEYWORDS, _extract_api_paths, _infer_risk_type
from .hypothesis_slice_bridge import _bind_endpoint

_HISTORICAL_FILENAMES = (
    "HISTORICAL_BUGS.md",
    "historical_bugs.md",
    "HISTORICAL_BUGS.txt",
    "historical_bugs.json",
)

_RISK_ENTITY_HINTS: dict[str, str] = {
    "money_consistency": "payment",
    "coupon_abuse": "coupon",
    "payment": "payment",
    "stock_consistency": "order",
    "idempotency": "order",
    "refund": "refund",
    "idor": "order",
    "tenant_isolation": "order",
    "permission_bypass": "order",
    "order_state": "order",
    "data_consistency": "order",
}

_RISK_TO_SLICE: dict[str, tuple[str, str, float]] = {
    "money_consistency": ("money", "_money_oracle", 0.88),
    "coupon_abuse": ("money", "_money_oracle", 0.86),
    "payment": ("money", "_money_oracle", 0.90),
    "stock_consistency": ("concurrency", "_concurrency_oracle", 0.86),
    "idempotency": ("concurrency", "_concurrency_oracle", 0.84),
    "refund": ("concurrency", "_concurrency_oracle", 0.87),
    "idor": ("isolation", "_isolation_oracle", 0.90),
    "tenant_isolation": ("isolation", "_isolation_oracle", 0.88),
    "permission_bypass": ("permission", "_permission_oracle", 0.85),
    "order_state": ("invariant", "_consistency_oracle", 0.80),
    "data_consistency": ("invariant", "_consistency_oracle", 0.82),
    "business_rule": ("invariant", "_consistency_oracle", 0.75),
}

_SECTION_RE = re.compile(
    r"^##\s+((?:HB-|BUG-|HIST-|DEFECT-)?[A-Za-z0-9][A-Za-z0-9_-]*)\s+(.+?)\s*$",
    re.MULTILINE,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _historical_input_dirs(root: Path, project: str) -> list[Path]:
    safe = "".join(ch for ch in str(project or "") if ch.isalnum() or ch in "_-.") or "project"
    return [
        root / "platform_inputs" / safe,
        root / "platform_inputs" / safe / "historical_bugs",
        root / "platform_workspace" / safe / "input",
        root / "platform_workspace" / safe / "historical_bugs",
        root / "projects" / safe / "input",
        root / "projects" / safe / "input" / "historical_bugs",
    ]


def _parse_markdown_sections(text: str, source_name: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        blob = text.strip()
        if len(blob) < 20:
            return []
        return [{
            "historical_bug_id": f"hist_{source_name}",
            "title": source_name,
            "description": blob[:1200],
            "source_ref": source_name,
        }]
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        bug_id = _text(match.group(1)) or f"section_{index + 1}"
        title = _text(match.group(2)) or bug_id
        rows.append({
            "historical_bug_id": bug_id,
            "title": title,
            "description": body[:1200],
            "source_ref": f"{source_name}#{bug_id}",
        })
    return rows


def _parse_json_bugs(payload: Any, source_name: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("bugs") or payload.get("items") or payload.get("records") or []
    else:
        return []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title") or item.get("summary") or item.get("name"))
        if not title:
            continue
        rows.append({
            "historical_bug_id": _text(item.get("historical_bug_id") or item.get("id") or item.get("bug_id") or f"row_{index + 1}"),
            "title": title,
            "description": _text(item.get("description") or item.get("detail") or item.get("text")),
            "severity": _text(item.get("severity")),
            "trigger": _text(item.get("trigger") or item.get("endpoint") or item.get("path")),
            "source_ref": source_name,
        })
    return rows


def load_historical_bug_records(root: Path, project: str) -> list[dict[str, Any]]:
    """Load normalized historical defect rows from all standard project dirs."""
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for directory in _historical_input_dirs(root, project):
        if not directory.is_dir():
            continue
        try:
            paths = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for path in paths:
            if not path.is_file():
                continue
            name_low = path.name.lower()
            if name_low.endswith((".md", ".txt")) and (
                "historical" in name_low or "bug" in name_low or name_low in {n.lower() for n in _HISTORICAL_FILENAMES}
            ):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for row in _parse_markdown_sections(text, path.name):
                    key = (str(row.get("historical_bug_id")), str(row.get("title")))
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(row)
            elif name_low.endswith(".json") and ("historical" in name_low or "bug" in name_low):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
                except (json.JSONDecodeError, OSError):
                    continue
                for row in _parse_json_bugs(payload, path.name):
                    key = (str(row.get("historical_bug_id")), str(row.get("title")))
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(row)
    return records


def _infer_historical_risk(blob: str) -> str:
    """Risk typing tuned for narrative historical-bug write-ups."""
    text = str(blob or "")
    low = text.lower()
    if any(token in text for token in ("报表", "角色过滤", "敏感金额")) or "report" in low:
        return "permission_bypass"
    if any(token in text for token in ("下架", "旧链接", "旧购物车")):
        return "order_state"
    if any(token in text for token in ("禁用账号", "token未过期", "仍可访问")):
        return "permission_bypass"
    return _infer_risk_type(text)


def _load_actors_and_login(root: Path, project: str, api_spec_text: str) -> tuple[list[dict[str, str]], str, dict[str, Any]]:
    from .supplementary_behavior_slices import (
        _discover_login_endpoint,
        _load_test_accounts,
        _parse_md_accounts,
        load_settings_accounts,
    )

    import re as _re

    state_re = _re.compile(
        r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])",
        _re.I,
    )
    _entities, _states, endpoints = _api_facts(api_spec_text, state_re)
    actors, settings_login = load_settings_accounts(root, project)
    if not actors:
        actors = _load_test_accounts(root, project)
    if not actors:
        actors = _parse_md_accounts(root, project)
    auto_login_path, auto_login_body = _discover_login_endpoint(endpoints)
    return actors, settings_login or auto_login_path, dict(auto_login_body or {})


def _risk_family(risk_type: str) -> str:
    mapping = {
        "money_consistency": "money",
        "coupon_abuse": "money",
        "payment": "money",
        "stock_consistency": "concurrency",
        "idempotency": "concurrency",
        "refund": "concurrency",
        "idor": "isolation",
        "tenant_isolation": "isolation",
        "permission_bypass": "permission",
        "order_state": "state_machine",
        "data_consistency": "invariant",
    }
    return mapping.get(risk_type, "invariant")


def _historical_to_slice(
    record: dict[str, Any],
    endpoints: list[dict[str, Any]],
) -> dict[str, Any] | None:
    title = _text(record.get("title"))
    description = _text(record.get("description"))
    blob = f"{title} {description} {_text(record.get('trigger'))}"
    risk_type = _infer_historical_risk(blob)
    kind, oracle_field, oracle_name = _RISK_TO_SLICE.get(risk_type, ("invariant", "_consistency_oracle", "ConsistencyOracle"))
    family = _risk_family(risk_type)

    hinted_paths = _extract_api_paths(blob) + _extract_api_paths(_text(record.get("trigger")))
    hypothesis: dict[str, Any] = {
        "hypothesis_id": _text(record.get("historical_bug_id"))[:80],
        "title": title,
        "description": description,
        "category": risk_type,
        "family": family,
        "entity": _RISK_ENTITY_HINTS.get(risk_type, ""),
        "severity": _text(record.get("severity")) or "P1",
        "related_endpoints": hinted_paths,
        "source_refs": [{"kind": "historical_bug", "quote": title[:300]}],
    }
    if risk_type == "refund" and "审批" in blob:
        hypothesis["method"] = "POST"
        hypothesis["trigger"] = "POST /api/refunds/:id/approve"
    if risk_type in {"idor", "tenant_isolation"}:
        hypothesis["method"] = "GET"
    if risk_type == "permission_bypass":
        hypothesis["method"] = "GET"
        if "报表" in blob or "report" in blob.lower():
            hypothesis["entity"] = "report"
    binding = _bind_endpoint(hypothesis, endpoints)
    if not binding:
        return None

    entity = binding["entity"] or "resource"
    method = binding["method"]
    path = binding["path"]
    slice_id = behavior_slice_id(
        f"historical_{kind}",
        entity,
        _text(record.get("historical_bug_id"))[:24],
        method,
        path,
    )
    row: dict[str, Any] = {
        "slice_id": slice_id,
        "entity": entity,
        "kind": kind,
        "states": [],
        "endpoints": [path],
        "priority": _RISK_TO_SLICE.get(risk_type, ("invariant", "_consistency_oracle", 0.75))[2],
        "source_refs": [
            {"kind": "historical_bug", "quote": title[:300]},
            {"kind": "historical_bug_id", "quote": _text(record.get("historical_bug_id"))[:80]},
        ],
        "evidence_gaps": [],
        oracle_field: oracle_name,
        "_hypothesis_origin": "historical_bug",
        "_historical_bug_id": _text(record.get("historical_bug_id")),
        "_historical_risk_type": risk_type,
        "_bound_method": method,
        "_bound_path": path,
        "_selection_family": f"historical:{risk_type}",
        "_invariant_text": description[:500] or title,
    }
    if kind == "permission":
        row["_permission_method"] = method
        row["_permission_path"] = path
        row["_permission_oracle"] = oracle_name
        row["_permission_expected_permitted"] = []
    elif kind == "isolation":
        row["_isolation_path"] = path
        row["_isolation_oracle"] = oracle_name
    elif kind == "concurrency":
        row["_concurrency_method"] = method
        row["_concurrency_path"] = path
        row["_concurrency_oracle"] = oracle_name
    elif kind == "money":
        row["_money_method"] = method
        row["_money_path"] = path
        row["_money_oracle"] = oracle_name
    return row


def generate_historical_behavior_slices(
    root: Path,
    project: str,
    api_spec_text: str,
) -> list[dict[str, Any]]:
    """Convert customer historical-bug materials into source-bound behavior slices."""
    if not str(api_spec_text or "").strip():
        return []
    records = load_historical_bug_records(root, project)
    if not records:
        return []

    state_re = re.compile(
        r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])",
        re.I,
    )
    _entities, _states, endpoints = _api_facts(api_spec_text, state_re)
    try:
        from .system_behavior_space import _merge_api_endpoints, _openapi_route_facts

        for input_dir in _historical_input_dirs(root, project):
            for name in ("openapi.json", "swagger.json"):
                openapi_path = input_dir.parent / name if input_dir.name == "historical_bugs" else input_dir / name
                if openapi_path.is_file():
                    extra = _openapi_route_facts(openapi_path.read_text(encoding="utf-8", errors="replace"))
                    if extra:
                        endpoints = _merge_api_endpoints(endpoints, extra)
    except Exception:
        pass

    actors, login_path, login_body = _load_actors_and_login(root, project, api_spec_text)
    low_priv = [a for a in actors if "admin" not in str(a.get("role") or a.get("email") or "").lower()] or actors

    slices: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in records:
        row = _historical_to_slice(record, endpoints)
        if not row:
            continue
        sid = _text(row.get("slice_id"))
        if not sid or sid in seen_ids:
            continue
        if login_path:
            row["_login_path"] = login_path
            row["_login_body"] = dict(login_body)
        kind = _text(row.get("kind"))
        if kind == "isolation" and len(actors) >= 2:
            viewer = low_priv[0] if low_priv else actors[0]
            owner = actors[1] if actors[1] != viewer else actors[0]
            row["_isolation_viewer_role"] = _text(viewer.get("role") or viewer.get("name"))
            row["_isolation_viewer_email"] = _text(viewer.get("email"))
            row["_isolation_viewer_password"] = _text(viewer.get("password"))
            row["_isolation_owner_role"] = _text(owner.get("role") or owner.get("name"))
            row["_isolation_owner_email"] = _text(owner.get("email"))
        elif kind == "permission" and low_priv:
            actor = low_priv[0]
            row["_permission_actor"] = _text(actor.get("role") or actor.get("name"))
            row["_permission_email"] = _text(actor.get("email"))
            row["_permission_password"] = _text(actor.get("password"))
        elif kind == "money" and actors:
            actor = actors[0]
            row["_money_actor_email"] = _text(actor.get("email"))
            row["_money_actor_password"] = _text(actor.get("password"))
        elif kind == "concurrency" and actors:
            actor = actors[0]
            row["_concurrency_actor_email"] = _text(actor.get("email"))
            row["_concurrency_actor_password"] = _text(actor.get("password"))
        seen_ids.add(sid)
        slices.append(row)
    return slices
