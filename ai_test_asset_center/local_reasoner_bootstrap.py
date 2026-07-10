"""Deterministic local bootstrap hypotheses for QualiBug discovery.

This module is intentionally conservative: it only creates read-only HTTP GET
probes from the local PRD/API contract when live LLM reasoners are unavailable.
It is not a replacement for the semantic Reasoner layer; it keeps the discovery
and self-evolution loop runnable so the engine can collect evidence, update
memory, and surface configuration/evidence gaps without contacting a model.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_METHOD_RE = re.compile(r"(?:^|[\s`])(?P<method>GET|POST|PUT|PATCH|DELETE)\s+`?(?P<path>/[A-Za-z0-9_/{}/?&=.-]+)`?", re.I | re.M)
_HEADING_RE = re.compile(r"^###\s+(?P<method>GET|POST|PUT|PATCH|DELETE)\s+`(?P<path>/[^`]+)`", re.I | re.M)

_SENSITIVE_HINTS = (
    # Auth / identity / export surfaces — universal across industries
    "user", "users", "password", "token", "audit", "export", "all",
    "admin", "permission", "role", "tenant", "org",
    # Operational / stateful surfaces (manufacturing, logistics, healthcare, commerce)
    "inventory", "orders", "order", "work-orders", "workorders", "notifications",
    "inspection", "quality", "erp", "events", "maintenance", "machines",
    "stock", "warehouse", "patients", "cases", "permits", "approvals",
    "settlements", "prescriptions", "materials", "reservations",
)

_ANONYMOUS_HINTS = ("匿名", "anonymous", "public", "health", "login", "docs", "openapi")


def _api_prefix(path: str) -> str:
    path = str(path or "").strip().split()[0]
    if not path.startswith("/"):
        path = "/" + path
    if path.startswith("/api/"):
        return path
    return "/api" + path


def _route_fingerprint(method: str, path: str) -> str:
    raw = f"{method.upper()} {path}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:12].upper()


def _iter_routes(api_spec: str) -> list[dict[str, Any]]:
    text = str(api_spec or "")
    routes: list[dict[str, Any]] = []

    # OpenAPI JSON support.
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            spec = json.loads(text)
            for path, item in (spec.get("paths") or {}).items():
                if not isinstance(item, dict):
                    continue
                for method in ("get", "post", "put", "patch", "delete"):
                    if method in item:
                        desc = json.dumps(item.get(method) or {}, ensure_ascii=False)[:1200]
                        routes.append({"method": method.upper(), "path": _api_prefix(path), "context": desc})
        except Exception:
            pass

    # Markdown/API contract support.
    for m in _HEADING_RE.finditer(text):
        start = m.end()
        end = text.find("\n### ", start)
        block = text[start:end if end != -1 else min(len(text), start + 1600)]
        routes.append({"method": m.group("method").upper(), "path": _api_prefix(m.group("path")), "context": block[:1600]})

    # Loose fallback for non-heading route references.
    for m in _METHOD_RE.finditer(text):
        method = m.group("method").upper()
        path = _api_prefix(m.group("path").rstrip("`.，,。"))
        routes.append({"method": method, "path": path, "context": ""})

    seen = set()
    unique = []
    for route in routes:
        key = (route["method"], route["path"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(route)
    return unique


def build_local_bootstrap_hypotheses(
    *,
    prd_text: str = "",
    api_spec: str = "",
    reader_output: dict[str, Any] | None = None,
    prior_findings: list[dict[str, Any]] | None = None,
    max_hypotheses: int = 12,
) -> list[dict[str, Any]]:
    """Build a small set of executable, read-only hypotheses.

    Design constraints:
    - GET only: safe for bundled/demo targets and avoids accidental mutation.
    - Contract-derived: no hidden oracle catalog or known-bug seed file.
    - Evidence-first: every hypothesis contains a concrete verification step.
    """
    routes = [r for r in _iter_routes(api_spec) if r.get("method") == "GET"]
    if not routes:
        return []

    previous_titles = {str((f or {}).get("title") or "").lower() for f in (prior_findings or [])}
    candidates: list[tuple[int, dict[str, Any]]] = []

    for route in routes:
        path = route["path"]
        route_context = str(route.get("context") or "")
        joined = f"{path} {route_context}".lower()
        if any(hint in joined for hint in _ANONYMOUS_HINTS):
            continue

        sensitive_score = sum(1 for hint in _SENSITIVE_HINTS if hint in joined)
        role_score = 2 if any(k in route_context for k in ("权限", "ADMIN", "PLANNER", "认证", "已认证")) else 0
        if sensitive_score == 0 and role_score == 0:
            continue

        severity = "P0" if any(k in joined for k in ("export", "password", "users", "audit")) else "P1"
        title = f"{path} 应强制认证/权限边界，匿名或低权限用户不应读取敏感业务数据"
        if title.lower() in previous_titles:
            continue
        entity = path.strip("/").split("/")[-1] or "resource"
        candidates.append((
            sensitive_score + role_score,
            {
                "hypothesis_id": f"LOCAL_AUTH_{_route_fingerprint('GET', path)}",
                "title": title,
                "description": "Local deterministic bootstrap hypothesis generated because live LLM reasoners were unavailable.",
                "risk_type": "authorization_bypass",
                "entity": entity,
                "severity": severity,
                "priority": severity,
                "expected_behavior": "匿名访问应返回401/403；低权限角色不得读取ADMIN级数据、密码、审计日志、全量导出或跨角色业务数据。",
                "verification_method": {"step1": f"GET {path}"},
                "evidence_plan": ["admin_vs_viewer_vs_no_auth_status", "response_body_sensitivity_check"],
                "_reasoner_engine": "local_bootstrap",
                "_local_bootstrap": True,
            },
        ))

    candidates.sort(key=lambda item: (-item[0], item[1]["hypothesis_id"]))
    return [item[1] for item in candidates[: max(1, int(max_hypotheses or 1))]]
