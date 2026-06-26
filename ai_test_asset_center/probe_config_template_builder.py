from __future__ import annotations

"""Probe config template builder for document-grounded probe plans.

Phase92M commercial-UX goal
--------------------------
``grounded_probe_plan.json`` is derived only from ``projects/<project>/input``.
Write probes cannot run until a customer provides sandbox-specific sample data,
test environment URL, username/password test accounts, safe object bindings and
cleanup strategy.  This module builds a reviewable ``probe_config.template.json``
from the document-grounded probe plan and the customer input documents, without
reading oracle/ground_truth/BUG_MATRIX/seed or answer files.

Raw Bearer-token/header entry remains supported as an advanced escape hatch, but
the default template asks for accounts and an auth flow because commercial users
normally have staging usernames/passwords, not API tokens.

Phase92N product rule: customers should not fill business object IDs, request
bodies, inventory IDs or snapshot paths.  QualiBug creates disposable test data
from OpenAPI/input documents at runtime.  Top-level request_bodies/path_params
remain only as advanced manual overrides for exceptional targets.

The generated template is deliberately **not executable**.  It uses explicit
``<FILL:...>`` placeholders and ``disposable_sandbox.enabled=false``.  The
executor blocks unresolved placeholders, so accidentally passing the template to
``bug-engine-grounded-execute`` cannot fire write requests.
"""

import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import yaml

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
READ_METHODS = {"GET", "HEAD"}
BLOCKED_INPUT_PART_RE = re.compile(r"(?:oracle|ground[_-]?truth|bug[_-]?matrix|answer|solution|seed)", re.I)
PATH_PARAM_RE = re.compile(r"\{([^{}]+)\}")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8") or "{}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _placeholder(name: str, hint: str = "") -> str:
    label = re.sub(r"[^A-Za-z0-9_./:-]+", "_", name).strip("_") or "value"
    if hint:
        return f"<FILL:{label}:{hint}>"
    return f"<FILL:{label}>"


def _contains_blocked_path(path: Path, root: Path) -> bool:
    try:
        rel = str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except Exception:
        rel = str(path)
    return bool(BLOCKED_INPUT_PART_RE.search(rel))


def _load_input_documents(input_dir: str | Path | None) -> tuple[dict[str, str], list[str]]:
    if not input_dir:
        return {}, []
    root = Path(input_dir).resolve()
    docs: dict[str, str] = {}
    blocked: list[str] = []
    if not root.exists():
        return docs, [f"missing_input_dir:{root}"]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        if _contains_blocked_path(path, root):
            blocked.append(rel)
            continue
        if path.suffix.lower() in {".md", ".txt", ".sql", ".yaml", ".yml", ".json"}:
            docs[rel] = path.read_text(encoding="utf-8", errors="replace")
    return docs, blocked


def _load_openapi(input_dir: str | Path | None) -> dict[str, Any]:
    if not input_dir:
        return {}
    root = Path(input_dir).resolve()
    for name in ("openapi.json", "swagger.json"):
        p = root / name
        if p.exists() and not _contains_blocked_path(p, root):
            try:
                return json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}")
            except Exception:
                return {}
    for name in ("openapi.yaml", "openapi.yml", "swagger.yaml", "swagger.yml"):
        p = root / name
        if p.exists() and not _contains_blocked_path(p, root):
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8", errors="replace") or "{}") or {}
            except Exception:
                return {}
    return {}


def _resolve_ref(ref: str, spec: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        return {}
    cur: Any = spec
    for part in ref[2:].split("/"):
        if isinstance(cur, dict):
            cur = cur.get(part.replace("~1", "/").replace("~0", "~"))
        else:
            return {}
    return cur if isinstance(cur, dict) else {}


def _schema_for_endpoint(spec: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    paths = spec.get("paths") if isinstance(spec, dict) else {}
    if not isinstance(paths, dict):
        return {}
    op = (paths.get(path) or {}).get(method.lower())
    if not isinstance(op, dict):
        # Try canonical suffix matching, because API.md may have /api/v1/<domain> prefix while OpenAPI may not.
        suffix = _canonical_suffix(path)
        for candidate_path, ops in paths.items():
            if _canonical_suffix(str(candidate_path)) == suffix and isinstance(ops, dict):
                op = ops.get(method.lower())
                if isinstance(op, dict):
                    break
    if not isinstance(op, dict):
        return {}
    content = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {})
    schema = content.get("schema") or {}
    if isinstance(schema, dict) and schema.get("$ref"):
        return _resolve_ref(str(schema.get("$ref")), spec)
    return schema if isinstance(schema, dict) else {}


def _openapi_parameters(spec: dict[str, Any], method: str, path: str) -> list[dict[str, Any]]:
    paths = spec.get("paths") if isinstance(spec, dict) else {}
    if not isinstance(paths, dict):
        return []
    op = (paths.get(path) or {}).get(method.lower())
    if not isinstance(op, dict):
        suffix = _canonical_suffix(path)
        for candidate_path, ops in paths.items():
            if _canonical_suffix(str(candidate_path)) == suffix and isinstance(ops, dict):
                op = ops.get(method.lower())
                if isinstance(op, dict):
                    break
    if not isinstance(op, dict):
        return []
    params: list[dict[str, Any]] = []
    for item in ((paths.get(path) or {}).get("parameters") or []) + (op.get("parameters") or []):
        if isinstance(item, dict):
            params.append(item)
    return params


def _canonical_suffix(path: str) -> str:
    p = str(path or "")
    p = re.sub(r"^/api/v\d+(?:/[^/]+)?", "", p)
    return p or path


def _schema_placeholder(schema: dict[str, Any], name: str = "value") -> Any:
    if not isinstance(schema, dict):
        return _placeholder(name)
    if schema.get("$ref"):
        return _placeholder(name, "schema_ref_not_expanded")
    typ = schema.get("type")
    if not typ and "properties" in schema:
        typ = "object"
    if typ == "object":
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = set(schema.get("required") or [])
        out: dict[str, Any] = {}
        selected = list(dict.fromkeys(list(required) + list(props.keys())))[:20]
        for key in selected:
            child = props.get(key) if isinstance(props.get(key), dict) else {"type": "string"}
            out[str(key)] = _schema_placeholder(child, str(key))
        return out or {"sample_field": _placeholder(name, "customer_safe_sample_value")}
    if typ == "array":
        return [_schema_placeholder(schema.get("items") if isinstance(schema.get("items"), dict) else {"type": "string"}, name + "_item")]
    if typ in {"integer", "number"}:
        return _placeholder(name, "number_from_disposable_fixture")
    if typ == "boolean":
        return _placeholder(name, "true_or_false_from_fixture")
    return _placeholder(name, "string_from_disposable_fixture")


def _fallback_body_for_probe(probe: dict[str, Any]) -> dict[str, Any]:
    ep = probe.get("endpoint") or {}
    path = str(ep.get("path") or "")
    risk = str(probe.get("risk_type") or "")
    body: dict[str, Any] = {
        "object_id": _placeholder("object_id", "safe_object_id_from_disposable_fixture"),
        "tenant_id": _placeholder("tenant_id", "sandbox_tenant_id"),
        "payload": {},
    }
    if risk == "state_transition_probe":
        terminals = ((probe.get("probe_plan") or {}).get("terminal_states") or [])[:3]
        body.update({
            "target_status": _placeholder("target_status", "illegal_or_terminal_transition"),
            "current_status_fixture": terminals[0] if terminals else _placeholder("current_status", "terminal_status_fixture"),
        })
    if risk == "idempotency_replay_probe":
        body.update({
            "business_key": _placeholder("business_key", "stable_duplicate_business_key"),
            "external_event_id": _placeholder("external_event_id", "stable_replay_event_id"),
        })
    if risk == "conservation_probe":
        body.update({
            "amount": _placeholder("amount", "small_safe_amount"),
            "resource_qty": _placeholder("resource_qty", "small_safe_quantity"),
        })
    if "tenant" in path:
        body["tenant_id"] = _placeholder("tenant_id", "tenant_A_for_cross_scope_fixture")
    return body


def _path_params_for_probe(probe: dict[str, Any]) -> dict[str, str]:
    ep = probe.get("endpoint") or {}
    path = str(ep.get("path") or "")
    out: dict[str, str] = {}
    risk = str(probe.get("risk_type") or "")
    for name in PATH_PARAM_RE.findall(path):
        hint = "safe_fixture_value"
        if "tenant" in name.lower():
            hint = "sandbox_tenant_id"
        elif name.lower() in {"id", "object_id", "order_id", "user_id"}:
            hint = "safe_object_id_from_disposable_fixture"
        if risk == "ownership_scope_probe" and name.lower() in {"id", "object_id", "order_id", "user_id"}:
            hint = "object_id_belonging_to_other_owner_in_sandbox"
        out[name] = _placeholder(name, hint)
    return out


def _headers_for_probe(probe: dict[str, Any]) -> dict[str, str]:
    risk = str(probe.get("risk_type") or "")
    headers: dict[str, str] = {}
    if risk in {"idempotency_replay_probe", "async_external_event_probe"}:
        headers["Idempotency-Key"] = _placeholder("Idempotency-Key", "stable_key_reused_for_replay_probe")
    if risk == "ownership_scope_probe":
        headers["X-Tenant-Id"] = _placeholder("X-Tenant-Id", "tenant_A_header_for_cross_scope_fixture")
    return headers


def _snapshot_template_for_probe(probe: dict[str, Any]) -> dict[str, Any] | None:
    risk = str(probe.get("risk_type") or "")
    if risk not in {"conservation_probe", "state_transition_probe", "idempotency_replay_probe", "async_external_event_probe"}:
        return None
    ep = probe.get("endpoint") or {}
    path = str(ep.get("path") or "")
    return {
        "before": [
            {"method": "GET", "path": _placeholder("snapshot_before_path", f"read_only_snapshot_for_{path}")}
        ],
        "after": [
            {"method": "GET", "path": _placeholder("snapshot_after_path", f"read_only_snapshot_for_{path}")}
        ],
    }


def _build_template_entries(probes: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    request_bodies: dict[str, Any] = {}
    path_params: dict[str, Any] = {"*": {}}
    headers: dict[str, Any] = {}
    replay: dict[str, Any] = {}
    snapshots: dict[str, Any] = {}
    readiness: list[dict[str, Any]] = []

    for probe in probes:
        cid = str(probe.get("candidate_id") or "")
        ep = probe.get("endpoint") or {}
        method = str(ep.get("method") or "GET").upper()
        path = str(ep.get("path") or "")
        risk = str(probe.get("risk_type") or "")
        policy = str(probe.get("execution_policy") or "")
        params = _path_params_for_probe(probe)
        if params:
            path_params[cid] = params
        h = _headers_for_probe(probe)
        if h:
            headers[cid] = h
        if method in WRITE_METHODS:
            schema = _schema_for_endpoint(spec, method, path)
            body = _schema_placeholder(schema, "request_body") if schema else _fallback_body_for_probe(probe)
            request_bodies[cid] = body
            if risk in {"idempotency_replay_probe", "async_external_event_probe"}:
                replay[cid] = {"count": 2, "reason": "same configured body and idempotency/business key should produce at most one side effect"}
            snap = _snapshot_template_for_probe(probe)
            if snap:
                snapshots[cid] = snap
        readiness.append({
            "candidate_id": cid,
            "risk_type": risk,
            "method": method,
            "path": path,
            "execution_policy": policy,
            "template_sections_to_fill": [
                section for section, present in {
                    "path_params": bool(params),
                    "headers": bool(h),
                    "request_bodies": method in WRITE_METHODS,
                    "snapshots": risk in {"conservation_probe", "state_transition_probe", "idempotency_replay_probe", "async_external_event_probe"},
                }.items() if present
            ],
            "source_ref_count": len(probe.get("source_refs") or []),
        })
    return {"request_bodies": request_bodies, "path_params": path_params, "headers": headers, "replay": replay, "snapshots": snapshots, "readiness": readiness}


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        f"# Probe Config Template — {report.get('project_id') or ''}",
        "",
        "## Guardrail",
        "",
        f"- strict_no_peek: `{report.get('strict_no_peek')}`",
        "- source: `grounded_probe_plan.json` plus optional `projects/<project>/input` only",
        "- hidden oracle / ground_truth / BUG_MATRIX / seed / answer files are not read",
        "- generated JSON is a non-executable template: `disposable_sandbox.enabled=false` and values contain `<FILL:...>` placeholders",
        "- `bug-engine-grounded-execute` blocks unresolved placeholders before executing probes",
        "",
        "## Summary",
        "",
        f"- probes total: {summary.get('probe_count')}",
        f"- write probes needing sandbox values: {summary.get('write_probe_count')}",
        f"- read-only probes: {summary.get('read_probe_count')}",
        f"- path-param entries: {summary.get('path_param_entry_count')}",
        f"- request-body entries: {summary.get('request_body_entry_count')}",
        f"- snapshot entries: {summary.get('snapshot_entry_count')}",
        f"- blocked input files skipped: {summary.get('blocked_input_file_count')}",
        f"- risk types: `{json.dumps(summary.get('by_risk_type') or {}, ensure_ascii=False)}`",
        "",
        "## How to use",
        "",
        "1. Copy `probe_config.template.json` to `probe_config.local.json`.",
        "2. Fill normal customer-facing values only: test environment URL, login flow, staging usernames/passwords and role labels.",
        "3. Do **not** fill order IDs, inventory IDs, request bodies or snapshots for normal use; QualiBug generates `qb_auto_*` disposable data from input/OpenAPI.",
        "4. Enable test writes only from the product UI/CLI after the production guard passes.",
        "",
        "## Candidate fill checklist",
        "",
    ]
    for item in (report.get("readiness") or [])[:160]:
        lines.append(f"- `{item.get('candidate_id')}` `{item.get('method')} {item.get('path')}` `{item.get('risk_type')}` → fill: {', '.join(item.get('template_sections_to_fill') or []) or 'none'}")
    return "\n".join(lines)


def build_probe_config_template(
    *,
    probe_plan_path: str | Path,
    out_dir: str | Path,
    input_dir: str | Path | None = None,
    base_url_hint: str = "",
    approval_id_hint: str = "",
    max_probes: int = 0,
) -> dict[str, Any]:
    plan_path = Path(probe_plan_path).resolve()
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan = _read_json(plan_path)
    probes = list(plan.get("probes") or [])
    if max_probes and max_probes > 0:
        probes = probes[:max_probes]

    docs, blocked = _load_input_documents(input_dir)
    spec = _load_openapi(input_dir)
    entries = _build_template_entries(probes, spec)

    by_risk = Counter(str(p.get("risk_type") or "unknown") for p in probes)
    write_count = sum(1 for p in probes if str((p.get("endpoint") or {}).get("method") or "").upper() in WRITE_METHODS)
    read_count = sum(1 for p in probes if str((p.get("endpoint") or {}).get("method") or "GET").upper() in READ_METHODS)

    template = {
        "template_version": "phase92n_account_login_probe_config_template_v1",
        "template_not_executable": True,
        "generated_at": _now(),
        "source_probe_plan": str(plan_path),
        "project_id": plan.get("project_id"),
        "base_url": base_url_hint or _placeholder("base_url", "staging_or_disposable_sandbox_base_url"),
        "environment_kind": "staging",
        "qualibug_auto_create_test_data": True,
        "customer_required_inputs": [
            "base_url",
            "auth_flow.login_path",
            "auth_flow.username_field",
            "auth_flow.password_field",
            "auth_flow.token_json_path_or_cookie_session",
            "accounts.*.username",
            "accounts.*.password"
        ],
        "customer_should_not_fill": [
            "order_id",
            "inventory_id",
            "object_id",
            "request_bodies",
            "path_params",
            "snapshots"
        ],
        "test_environment": {
            "enabled": True,
            "kind": "staging_or_test",
            "allow_write_probes": False,
            "note": "When enabled in product UI, QualiBug creates qb_auto_* test data and blocks obvious production-like hosts."
        },
        "auto_fixture": {
            "enabled": True,
            "strategy": "qualibug_generates_disposable_test_data_from_openapi_and_business_rules",
            "customer_business_data_required": False,
            "id_prefix": "qb_auto"
        },
        "auth_flow": {
            "login_path": _placeholder("login_path", "for_example_/api/login_or_/auth/login"),
            "method": "POST",
            "username_field": "username",
            "password_field": "password",
            "tenant_field": "",
            "token_json_path": _placeholder("token_json_path", "for_example_token_or_data.access_token"),
            "token_header_name": "Authorization",
            "token_header_prefix": "Bearer",
            "tenant_header_name": "X-Tenant-Id",
            "notes": "Customers fill staging username/password accounts; QualiBug logs in and derives token/cookie headers at runtime.",
        },
        "accounts": {
            "normal_user": {
                "role": "normal_user",
                "username": _placeholder("normal_user.username", "staging_test_username"),
                "password": _placeholder("normal_user.password", "staging_test_password"),
                "tenant_id": _placeholder("normal_user.tenant_id", "optional_sandbox_tenant_id"),
            },
            "admin": {
                "role": "admin",
                "username": _placeholder("admin.username", "staging_admin_username"),
                "password": _placeholder("admin.password", "staging_admin_password"),
                "tenant_id": _placeholder("admin.tenant_id", "optional_sandbox_tenant_id"),
            },
            "anonymous": {"role": "anonymous", "anonymous": True},
        },
        "default_account": "normal_user",
        "default_headers": {
            "User-Agent": "QualiBug-Sandbox-Probe",
            "X-Tenant-Id": _placeholder("X-Tenant-Id", "optional_sandbox_tenant_id_if_not_supplied_by_account"),
        },
        "advanced_manual_overrides": {
            "enabled": False,
            "notes": "Advanced only. Normal customers leave request_bodies/path_params/snapshots empty; QualiBug generates test data automatically.",
        },
        "advanced_header_override": {
            "enabled": False,
            "notes": "Advanced only: use default_headers.Authorization/Bearer token when account login is impossible. Account login is the recommended product path.",
        },
        "disposable_sandbox": {
            "enabled": False,
            "approval_id": approval_id_hint or _placeholder("approval_id", "human_approved_disposable_sandbox_run_id"),
            "target_kind": "local_disposable",
            "cleanup_strategy": _placeholder("cleanup_strategy", "fixture_reset_or_transaction_rollback_or_benchmark_reset"),
            "base_url_allowlist": ["127.0.0.1", "localhost"],
            "notes": "Set enabled=true only after filling every <FILL:...> placeholder and confirming this is a disposable sandbox.",
        },
        "path_params": entries["path_params"],
        "headers": entries["headers"],
        "request_bodies": entries["request_bodies"],
        "replay": entries["replay"],
        "snapshots": entries["snapshots"],
        "governance": {
            "input_only": True,
            "strict_no_peek": True,
            "oracle_files_read": False,
            "blocked_input_files_skipped": blocked,
            "write_request_bodies_invented_by_engine": False,
            "template_values_are_placeholders": True,
            "customer_auth_input_mode": "username_password_accounts_preferred",
            "raw_tokens_required_from_customer": False,
        },
    }

    report = {
        "engine": "probe_config_template_builder_phase92n",
        "mode": "document_grounded_probe_config_template",
        "strict_no_peek": True,
        "created_at": _now(),
        "project_id": plan.get("project_id"),
        "probe_plan": str(plan_path),
        "input_dir": str(Path(input_dir).resolve()) if input_dir else "",
        "input_documents_used": sorted(docs.keys()),
        "summary": {
            "probe_count": len(probes),
            "write_probe_count": write_count,
            "read_probe_count": read_count,
            "request_body_entry_count": len(entries["request_bodies"]),
            "path_param_entry_count": max(0, len(entries["path_params"]) - 1),
            "header_entry_count": len(entries["headers"]),
            "snapshot_entry_count": len(entries["snapshots"]),
            "blocked_input_file_count": len(blocked),
            "by_risk_type": dict(sorted(by_risk.items())),
        },
        "readiness": entries["readiness"],
        "outputs": {},
    }

    template_path = output / "probe_config.template.json"
    report_path = output / "probe_config_template_report.json"
    md_path = output / "probe_config_template_report.md"
    _write_json(template_path, template)
    report["outputs"] = {
        "probe_config_template": str(template_path),
        "probe_config_template_report": str(report_path),
        "probe_config_template_report_md": str(md_path),
    }
    _write_json(report_path, report)
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    return report
