from __future__ import annotations

"""Document-grounded active contract validation for disposable test sandboxes.

This module closes the gap between requirement documents and executable
business probes.  It deliberately does **not** infer defects from words alone:
for every executable validation it keeps the original document sentence,
endpoint, sample payload and observed HTTP evidence together.

Safety model:
* compilation is always safe and read-only;
* execution is blocked unless the target is an explicitly approved,
  disposable sandbox;
* only document-backed request examples are mutated;
* every mutation is small (numeric boundary, enum, temporal ordering,
  duplicate business key or unauthorised role);
* a formal finding requires an observed success response where the document
  requires rejection, or a concrete duplicate/idempotency contradiction.

The compiler is intentionally domain-neutral.  It understands common
enterprise-document wording in English and Chinese, rather than endpoint
names for a particular product.
"""

import copy
import hashlib
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .concurrency_async_sandbox import _http
from .real_project_onboarding import ROOT, _join_url, _safe_project_id, _write_json
from .safety_boundary import safety_gate

_ENDPOINT_RE = re.compile(
    r"^###\s+(?P<methods>(?:GET|POST|PUT|PATCH|DELETE)(?:\s*/\s*(?:GET|POST|PUT|PATCH|DELETE))*)\s+`(?P<path>/[^`]+)`",
    re.I | re.M,
)
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(?P<body>.*?)\n```", re.S | re.I)
_ROLE_RE = re.compile(r"(?:\*\*权限\*\*|权限)\s*[：:]\s*([^\n。]+)")
_POSITIVE_RE = re.compile(r"(?:大于\s*0|>\s*0|positive\b|正数)", re.I)
_NONNEGATIVE_RE = re.compile(r"(?:大于等于\s*0|>=\s*0|非负|non[- ]?negative)", re.I)
_UNIQUE_RE = re.compile(r"(?:唯一|unique|不得重复|不能重复)", re.I)
_DATE_ORDER_RE = re.compile(r"(?:开始.{0,16}(?:早于|小于|before).{0,16}结束|start.{0,16}(?:before|earlier).{0,16}end|倒序)", re.I)
_IDEMPOTENCY_RE = re.compile(r"(?:幂等|idempotenc|重复提交|重复推送|重试)", re.I)
_ENUM_TOKEN_RE = re.compile(r"`([A-Z][A-Z0-9_\-]{1,48})`")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SENSITIVE_RE = re.compile(r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|session)", re.I)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:length]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def _redact(value: Any, key: str = "") -> Any:
    if _SENSITIVE_RE.search(str(key)):
        return "<REDACTED>"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value[:30]]
    if isinstance(value, str):
        return value[:500]
    return value


def _read_json_blocks(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in _JSON_BLOCK_RE.finditer(text or ""):
        try:
            value = json.loads(match.group("body"))
        except Exception:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _endpoint_sections(api_text: str) -> list[dict[str, Any]]:
    """Parse Markdown API sections without requiring an OpenAPI response schema."""
    matches = list(_ENDPOINT_RE.finditer(api_text or ""))
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(api_text)
        section = (api_text or "")[start:end]
        methods = [item.strip().upper() for item in re.split(r"\s*/\s*", match.group("methods"))]
        role_match = _ROLE_RE.search(section)
        roles: list[str] = []
        if role_match:
            roles = re.findall(r"\b[A-Z][A-Z0-9_]{1,30}\b", role_match.group(1))
        sample = next(iter(_read_json_blocks(section)), {})
        for method in methods:
            rows.append({
                "method": method,
                "path": match.group("path"),
                "roles": sorted(set(roles)),
                "sample_body": sample,
                "section_text": section,
                "source_excerpt": (match.group(0) + "\n" + section[:900]).strip(),
            })
    return rows


def _field_context(prd_text: str, api_text: str, field: str, endpoint_text: str) -> str:
    """Return only lines where the named field itself is discussed.

    We deliberately avoid whole-section matching here.  A MES document may say
    ``sampleQty > 0`` and ``passQty >= 0`` in the same paragraph; applying the
    first rule to every numeric field would create noisy, unsafe mutations.
    """
    key = re.escape(str(field))
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){key}(?![A-Za-z0-9_])", re.I)
    lines: list[str] = []
    for text in (endpoint_text, prd_text, api_text):
        for line in str(text or "").splitlines():
            if pattern.search(line):
                lines.append(line.strip())
    return "\n".join(lines[:30])


def _numeric_rule_for_field(context: str, field: str) -> str | None:
    """Infer a numeric rule only when the same field has a direct comparator."""
    key = re.escape(str(field))
    for line in str(context or "").splitlines():
        # Most Markdown requirement tables use either ``field >= 0`` or
        # ``大于 0 的 field``.  Do not use a broad paragraph window: quality
        # math often places several different constraints on one line.
        direct_nonnegative = re.search(rf"{key}\s*(?:大于等于|>=)\s*0", line, re.I)
        direct_positive = re.search(rf"{key}\s*(?:大于|>)\s*0", line, re.I)
        before_nonnegative = re.search(rf"(?:大于等于|>=)\s*0[^\n]{{0,24}}{key}", line, re.I)
        before_positive = re.search(rf"(?:大于|>)\s*0[^\n]{{0,24}}{key}", line, re.I)
        if direct_nonnegative or before_nonnegative or re.search(rf"{key}[^\n]{{0,24}}(?:非负|non[- ]?negative)", line, re.I):
            return "nonnegative_numeric"
        if direct_positive or before_positive or re.search(rf"{key}[^\n]{{0,24}}(?:正数|positive)", line, re.I):
            return "positive_numeric"
    return None


def _enum_values(context: str) -> list[str]:
    values = _ENUM_TOKEN_RE.findall(context or "")
    return sorted(set(values))[:12]


def _path_with_run_key(path: str, run_key: str) -> str:
    # A document sample normally covers request bodies.  Path parameters need
    # explicit configuration; never manufacture resource identifiers here.
    return str(path).replace("{run_key}", run_key)


def _configured_path(path: str, values: dict[str, Any]) -> str | None:
    rendered = str(path)
    for name in re.findall(r"\{([^{}]+)\}", rendered):
        if name not in values:
            return None
        rendered = rendered.replace("{" + name + "}", urllib.parse.quote(str(values[name]), safe=""))
    return rendered


def _replace_field(payload: dict[str, Any], field: str, value: Any) -> dict[str, Any]:
    body = copy.deepcopy(payload)
    body[field] = value
    return body


def _run_key(seed: str = "qb") -> str:
    return f"{seed}-{int(time.time() * 1000)}-{_hash(time.time(), 6)}"


def _mutate_unique_values(value: Any, run_key: str, key: str = "", preserve_fields: set[str] | None = None) -> Any:
    """Namespace owned keys while preserving configured fixture references.

    A field such as ``materialCode`` can be a foreign key in a BOM request;
    adding a run suffix there would invalidate the precondition instead of
    testing the intended constraint.  Scenario compilation may therefore pass
    an explicit, document/config-grounded preserve list.  Unconfigured calls
    keep the original behavior.
    """
    preserve = {str(field).lower() for field in (preserve_fields or set())}
    if isinstance(value, dict):
        return {str(k): _mutate_unique_values(v, run_key, str(k), preserve) for k, v in value.items()}
    if isinstance(value, list):
        return [_mutate_unique_values(item, run_key, key, preserve) for item in value]
    if str(key).lower() in preserve:
        return value
    if isinstance(value, str) and re.search(r"(?:code|version|lot|serial|ref|key|no)$", key, re.I):
        return f"{value}-{run_key}"[:80]
    return value


def _contract_id(kind: str, endpoint: dict[str, Any], field: str = "") -> str:
    return f"DOC_{kind.upper()}_{_hash({'m': endpoint['method'], 'p': endpoint['path'], 'f': field})}"


def compile_document_contracts(prd_text: str, api_text: str) -> dict[str, Any]:
    """Compile Markdown requirements into reviewable active-test contracts.

    This produces no network traffic and no findings.  It is safe to show to a
    business owner for review before sandbox execution.
    """
    endpoints = _endpoint_sections(api_text)
    contracts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for endpoint in endpoints:
        method = endpoint["method"]
        body = endpoint.get("sample_body") or {}
        text = endpoint.get("section_text") or ""
        if method not in _WRITE_METHODS or not isinstance(body, dict) or not body:
            continue

        # Numeric boundary probes only when the relevant field is documented.
        for field, original in body.items():
            if isinstance(original, bool) or not isinstance(original, (int, float)):
                continue
            context = _field_context(prd_text, api_text, field, text)
            kind = _numeric_rule_for_field(context, field) or ""
            values: list[Any] = []
            if kind == "positive_numeric":
                values = [0, -1]
            elif kind == "nonnegative_numeric":
                values = [-1]
            if kind:
                for value in values:
                    contracts.append({
                        "contract_id": _contract_id(kind, endpoint, f"{field}:{value}"),
                        "kind": kind,
                        "title": f"{method} {endpoint['path']} must reject {field}={value}",
                        "method": method,
                        "path": endpoint["path"],
                        "roles": endpoint["roles"],
                        "sample_body": body,
                        "mutation": {"field": field, "value": value},
                        "expected": "non_2xx",
                        "severity": "P1" if value < 0 else "P2",
                        "evidence_source": context[:1000],
                        "execution_policy": "approved_disposable_sandbox_only",
                    })

        # Enums are only probed when the document names a known closed set.
        for field, original in body.items():
            if not isinstance(original, str) or not re.search(r"(?:status|state|type|priority|uom|unit|disposition)$", field, re.I):
                continue
            context = _field_context(prd_text, api_text, field, text)
            values = _enum_values(context)
            if len(values) >= 2:
                contracts.append({
                    "contract_id": _contract_id("enum", endpoint, field),
                    "kind": "closed_enum",
                    "title": f"{method} {endpoint['path']} must reject undocumented {field}",
                    "method": method,
                    "path": endpoint["path"],
                    "roles": endpoint["roles"],
                    "sample_body": body,
                    "mutation": {"field": field, "value": "__QUALIBUG_INVALID_ENUM__", "allowed_values": values},
                    "expected": "non_2xx",
                    "severity": "P2",
                    "evidence_source": context[:1000],
                    "execution_policy": "approved_disposable_sandbox_only",
                })

        # Date pairs are a generic, document-backed temporal validation.
        keys = {str(key).lower(): str(key) for key in body}
        starts = [real for norm, real in keys.items() if "start" in norm or "begin" in norm or "开始" in norm]
        ends = [real for norm, real in keys.items() if "end" in norm or "finish" in norm or "结束" in norm]
        if starts and ends:
            context = "\n".join([_field_context(prd_text, api_text, starts[0], text), _field_context(prd_text, api_text, ends[0], text)])
            if _DATE_ORDER_RE.search(context):
                contracts.append({
                    "contract_id": _contract_id("date_order", endpoint, f"{starts[0]}:{ends[0]}"),
                    "kind": "temporal_order",
                    "title": f"{method} {endpoint['path']} must reject end before start",
                    "method": method,
                    "path": endpoint["path"],
                    "roles": endpoint["roles"],
                    "sample_body": body,
                    "mutation": {"swap_fields": [starts[0], ends[0]]},
                    "expected": "non_2xx",
                    "severity": "P2",
                    "evidence_source": context[:1000],
                    "execution_policy": "approved_disposable_sandbox_only",
                })

        # Explicitly documented unique business keys can be tested by two
        # identical creates.  The observation is a second success, not merely
        # a response format difference.
        for field in body:
            context = _field_context(prd_text, api_text, field, text)
            # Single-field duplicate tests are safe only for identifiers owned
            # by this create endpoint.  Foreign keys such as materialCode may
            # be globally unique in their own master table but legitimately
            # repeat across BOMs, orders and receipts.  Composite uniqueness is
            # represented as a coverage gap until a confirmed relation exists.
            owned_identifier = bool(re.search(r"^(?:code|serialNo|serial_no|orderNo|order_no|workOrderNo|work_order_no|inspectionNo|inspection_no|ncrNo|ncr_no|maintenanceNo|maintenance_no|txnNo|txn_no|externalRef|external_ref)$", str(field), re.I))
            if owned_identifier and _UNIQUE_RE.search(context):
                contracts.append({
                    "contract_id": _contract_id("duplicate", endpoint, field),
                    "kind": "duplicate_business_key",
                    "title": f"{method} {endpoint['path']} must reject duplicate {field}",
                    "method": method,
                    "path": endpoint["path"],
                    "roles": endpoint["roles"],
                    "sample_body": body,
                    "mutation": {"unique_field": field},
                    "expected": "second_non_2xx",
                    "severity": "P1" if field.lower() in {"code", "serialno", "serial_no", "orderno", "order_no"} else "P2",
                    "evidence_source": context[:1000],
                    "execution_policy": "approved_disposable_sandbox_only",
                })

        # Idempotency requires a supplied key in the public request contract.
        if _IDEMPOTENCY_RE.search(text) or any("idempotency" in str(key).lower() for key in body):
            key = next((str(k) for k in body if "idempotency" in str(k).lower()), "idempotencyKey")
            contracts.append({
                "contract_id": _contract_id("idempotency", endpoint, key),
                "kind": "replay_idempotency",
                "title": f"{method} {endpoint['path']} must preserve one result for repeated {key}",
                "method": method,
                "path": endpoint["path"],
                "roles": endpoint["roles"],
                "sample_body": body,
                "mutation": {"idempotency_key_field": key},
                "expected": "same_business_identity",
                "severity": "P1",
                "evidence_source": text[:1000],
                "execution_policy": "approved_disposable_sandbox_only",
            })

    # Role contracts are executable for all documented protected GET/POST/etc.
    all_roles = sorted({role for row in endpoints for role in row.get("roles", [])})
    for endpoint in endpoints:
        allowed = endpoint.get("roles") or []
        if not allowed:
            continue
        unauthorised = [role for role in all_roles if role not in allowed]
        if not unauthorised:
            continue
        contracts.append({
            "contract_id": _contract_id("permission", endpoint),
            "kind": "role_boundary",
            "title": f"{endpoint['method']} {endpoint['path']} rejects a documented unauthorised role",
            "method": endpoint["method"],
            "path": endpoint["path"],
            "roles": allowed,
            "unauthorised_roles": unauthorised[:3],
            "sample_body": endpoint.get("sample_body") or {},
            "mutation": {},
            "expected": "non_2xx_for_unauthorised_role",
            "severity": "P1",
            "evidence_source": endpoint.get("source_excerpt", "")[:1000],
            "execution_policy": "approved_disposable_sandbox_only" if endpoint["method"] in _WRITE_METHODS else "safe_read_only",
        })

    # Deduplicate exact semantic contracts while retaining a reason for skipped
    # sections (useful for coverage-gap reports).
    deduped: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        deduped.setdefault(contract["contract_id"], contract)
    for endpoint in endpoints:
        if endpoint["method"] in _WRITE_METHODS and not endpoint.get("sample_body"):
            skipped.append({"method": endpoint["method"], "path": endpoint["path"], "reason": "no_documented_json_example"})
    result = {
        "engine": "document_contract_fuzzing_v1",
        "generated_at_utc": _now(),
        "contract_count": len(deduped),
        "contracts": list(deduped.values()),
        "endpoint_count": len(endpoints),
        "coverage_gaps": skipped,
        "governance": {
            "document_backed_only": True,
            "no_finding_without_runtime_observation": True,
            "writes_require_disposable_sandbox": True,
            "does_not_infer_from_ground_truth": True,
        },
    }
    return result


def _sandbox_blockers(config: dict[str, Any], options: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    env = str(config.get("environment") or config.get("target_environment") or "").lower()
    if env != "sandbox":
        blockers.append("target_environment_must_be_sandbox")
    if not bool(config.get("disposable_sandbox")):
        blockers.append("disposable_sandbox_required")
    if not bool(options.get("approved_sandbox_execution")):
        blockers.append("approved_sandbox_execution_required")
    if not str(options.get("approval_id") or "").strip():
        blockers.append("approval_id_required")
    if not bool(options.get("execute")):
        blockers.append("execute_flag_required")
    return blockers


def _headers_for_role(config: dict[str, Any], role: str) -> dict[str, str]:
    accounts = config.get("role_headers") or {}
    value = accounts.get(role) if isinstance(accounts, dict) else None
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def _business_identity(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    stack: list[Any] = [payload]
    candidates = ("id", "code", "no", "orderNo", "workOrderNo", "inspectionNo", "txnNo", "eventId", "serialNo")
    while stack:
        current = stack.pop(0)
        if isinstance(current, dict):
            for field in candidates:
                value = current.get(field)
                if value not in {None, ""}:
                    return f"{field}:{value}"
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current[:10])
    return None


def _mutated_body(contract: dict[str, Any], run_key: str) -> dict[str, Any]:
    preserve_fields = {str(field) for field in (contract.get("preserve_fixture_fields") or [])}
    body = _mutate_unique_values(contract.get("sample_body") or {}, run_key, preserve_fields=preserve_fields)
    mutation = contract.get("mutation") or {}
    if "field" in mutation:
        body = _replace_field(body, str(mutation["field"]), mutation.get("value"))
    if "swap_fields" in mutation:
        left, right = mutation["swap_fields"]
        body[left], body[right] = body.get(right), body.get(left)
    if mutation.get("idempotency_key_field"):
        body[str(mutation["idempotency_key_field"])] = f"QB-IDEMP-{run_key}"
    return body


def _request(base_url: str, contract: dict[str, Any], headers: dict[str, str], body: dict[str, Any] | None, path_params: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_path = _path_with_run_key(str(contract["path"]), _run_key("path"))
    path = _configured_path(raw_path, path_params or {})
    if path is None:
        return {"ok": False, "status_code": None, "payload": None, "error": "path_parameters_not_configured"}
    return _http(_join_url(base_url, path), str(contract["method"]), body=body if str(contract["method"]).upper() in _WRITE_METHODS else None, headers=headers)



def _accepted(response: dict[str, Any]) -> bool:
    """True only for a transport and application-level accepted mutation."""
    status = response.get("status_code")
    if status is None or not (200 <= int(status) < 300):
        return False
    payload = response.get("payload")
    if isinstance(payload, dict) and (payload.get("success") is False or payload.get("ok") is False):
        return False
    return True


def _http_200_error(response: dict[str, Any]) -> bool:
    status = response.get("status_code")
    payload = response.get("payload")
    return bool(status is not None and 200 <= int(status) < 300 and isinstance(payload, dict) and (payload.get("success") is False or payload.get("ok") is False))

def execute_document_contracts(
    compiled: dict[str, Any],
    config: dict[str, Any],
    *,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a compiled contract set against an explicitly disposable sandbox."""
    options = dict(options or {})
    blockers = _sandbox_blockers(config, options)
    base_url = str(config.get("base_url") or "")
    if not base_url:
        blockers.append("base_url_required")
    gate = safety_gate("document_contract_sandbox", str(config.get("environment") or config.get("target_environment") or ""), base_url, execution_mode="safe_live").validate() if base_url else {"safe_to_proceed": False}
    # Local disposable sandbox is intentionally accepted only after all explicit
    # approval conditions above.  A production target is always rejected by
    # the preceding environment condition.
    results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if blockers:
        return {"status": "blocked", "blockers": blockers, "safety": gate, "results": results, "findings": findings}

    max_contracts = max(1, min(int(options.get("max_contracts") or 250), 500))
    for contract in (compiled.get("contracts") or [])[:max_contracts]:
        kind = str(contract.get("kind") or "")
        method = str(contract.get("method") or "GET").upper()
        if method in _WRITE_METHODS and contract.get("execution_policy") != "approved_disposable_sandbox_only":
            continue
        run_key = _run_key("doc")
        record: dict[str, Any] = {"contract_id": contract.get("contract_id"), "kind": kind, "title": contract.get("title"), "observations": []}

        if kind == "role_boundary":
            for role in contract.get("unauthorised_roles") or []:
                headers = _headers_for_role(config, str(role))
                if not headers:
                    record["observations"].append({"role": role, "skipped": "no_role_header_configured"})
                    continue
                body = _mutated_body(contract, run_key) if method in _WRITE_METHODS else None
                response = _request(base_url, contract, headers, body, (config.get("path_params") or {}).get(contract.get("contract_id"), {}))
                observation = {"role": role, "status_code": response.get("status_code"), "error": response.get("error")}
                record["observations"].append(observation)
                if _accepted(response):
                    findings.append({
                        "finding_id": f"DOCF_{_hash({'c': contract['contract_id'], 'r': role})}",
                        "title": f"Unauthorised role {role} can invoke {method} {contract['path']}",
                        "severity": contract.get("severity", "P1"),
                        "risk_type": "permission_bypass",
                        "status": "confirmed",
                        "evidence_strength": "runtime_strong",
                        "expected": "documented unauthorised role receives 401/403/409 validation failure",
                        "actual": f"HTTP {response.get('status_code')}",
                        "contract_id": contract["contract_id"],
                        "evidence": {"role": role, "status_code": response.get("status_code"), "response": _redact(response.get("payload"))},
                    })
        else:
            allowed = list(contract.get("roles") or [])
            role = allowed[0] if allowed else str(config.get("default_role") or "")
            headers = _headers_for_role(config, role)
            if not headers:
                record["observations"].append({"skipped": "no_authorised_role_header_configured", "role": role})
                results.append(record)
                continue
            body = _mutated_body(contract, run_key)
            if kind == "duplicate_business_key":
                path_params = (config.get("path_params") or {}).get(contract.get("contract_id"), {})
                first = _request(base_url, contract, headers, body, path_params)
                second = _request(base_url, contract, headers, body, path_params)
                record["observations"].extend([
                    {"attempt": 1, "status_code": first.get("status_code"), "error": first.get("error")},
                    {"attempt": 2, "status_code": second.get("status_code"), "error": second.get("error")},
                ])
                if _accepted(first) and _accepted(second):
                    findings.append({
                        "finding_id": f"DOCF_{_hash({'c': contract['contract_id'], 'run': run_key})}",
                        "title": contract["title"],
                        "severity": contract.get("severity", "P2"),
                        "risk_type": "business_composite_duplicate",
                        "status": "confirmed",
                        "evidence_strength": "runtime_strong",
                        "expected": "second create is rejected by documented uniqueness constraint",
                        "actual": f"both creates succeeded ({first.get('status_code')}, {second.get('status_code')})",
                        "contract_id": contract["contract_id"],
                        "evidence": {"first_status": first.get("status_code"), "second_status": second.get("status_code"), "request": _redact(body)},
                    })
            elif kind == "replay_idempotency":
                path_params = (config.get("path_params") or {}).get(contract.get("contract_id"), {})
                first = _request(base_url, contract, headers, body, path_params)
                second = _request(base_url, contract, headers, body, path_params)
                first_id, second_id = _business_identity(first.get("payload")), _business_identity(second.get("payload"))
                record["observations"].extend([
                    {"attempt": 1, "status_code": first.get("status_code"), "business_identity": first_id},
                    {"attempt": 2, "status_code": second.get("status_code"), "business_identity": second_id},
                ])
                if _accepted(first) and _accepted(second) and first_id and second_id and first_id != second_id:
                    findings.append({
                        "finding_id": f"DOCF_{_hash({'c': contract['contract_id'], 'run': run_key})}",
                        "title": contract["title"],
                        "severity": contract.get("severity", "P1"),
                        "risk_type": "duplicate_side_effect",
                        "status": "confirmed",
                        "evidence_strength": "runtime_strong",
                        "expected": "same idempotency key yields same business identity",
                        "actual": f"identities differ: {first_id} vs {second_id}",
                        "contract_id": contract["contract_id"],
                        "evidence": {"first_status": first.get("status_code"), "second_status": second.get("status_code"), "first_identity": first_id, "second_identity": second_id},
                    })
            else:
                response = _request(base_url, contract, headers, body, (config.get("path_params") or {}).get(contract.get("contract_id"), {}))
                record["observations"].append({"status_code": response.get("status_code"), "error": response.get("error")})
                if _accepted(response):
                    findings.append({
                        "finding_id": f"DOCF_{_hash({'c': contract['contract_id'], 'run': run_key})}",
                        "title": contract["title"],
                        "severity": contract.get("severity", "P2"),
                        "risk_type": "business_rule",
                        "status": "confirmed",
                        "evidence_strength": "runtime_strong",
                        "expected": "documented invalid input is rejected",
                        "actual": f"HTTP {response.get('status_code')} accepted documented invalid mutation",
                        "contract_id": contract["contract_id"],
                        "evidence": {"status_code": response.get("status_code"), "request": _redact(body), "response": _redact(response.get("payload"))},
                    })
                elif _http_200_error(response):
                    findings.append({
                        "finding_id": f"DOCF_{_hash({'c': contract['contract_id'], 'run': run_key, 'kind': 'http200'})}",
                        "title": f"{contract['method']} {contract['path']} returns HTTP 200 for a rejected business request",
                        "severity": "P3",
                        "risk_type": "http_status_semantics",
                        "status": "confirmed",
                        "evidence_strength": "runtime_strong",
                        "expected": "documented invalid request uses a 4xx business error status",
                        "actual": "HTTP 200 with success=false",
                        "contract_id": contract["contract_id"],
                        "evidence": {"status_code": response.get("status_code"), "request": _redact(body), "response": _redact(response.get("payload"))},
                    })
        results.append(record)

    return {
        "engine": "document_contract_fuzzing_v1",
        "status": "completed",
        "generated_at_utc": _now(),
        "safety": gate,
        "governance": {"sandbox_only": True, "approval_id_present": bool(options.get("approval_id")), "formal_findings_runtime_evidence_only": True},
        "summary": {"executed_contract_count": len(results), "confirmed_finding_count": len(findings)},
        "results": results,
        "findings": findings,
    }


def save_document_contract_artifacts(project_id: str, root: Path, compiled: dict[str, Any], execution: dict[str, Any] | None = None) -> dict[str, str]:
    project = _safe_project_id(project_id)
    out = root / "platform_outputs" / project / "document_contract_fuzzing"
    out.mkdir(parents=True, exist_ok=True)
    compilation_path = out / "document_contracts.json"
    _write_json(compilation_path, compiled)
    output = {"compiled": str(compilation_path)}
    if execution is not None:
        execution_path = out / "document_contract_execution.json"
        _write_json(execution_path, execution)
        output["execution"] = str(execution_path)
    return output

# ---------------------------------------------------------------------------
# Manufacturing-domain extensions
# ---------------------------------------------------------------------------
# These terms are not injected bug patterns.  They define a reusable MES
# vocabulary used only to select the relevant requirement section before a
# generic numeric/reference/order mutation is generated.
_INDUSTRY_SECTION_TERMS: dict[str, tuple[str, ...]] = {
    "/master/materials": ("物料", "material"),
    "/master/boms": ("bom", "物料清单"),
    "/master/routings": ("工艺路线", "routing", "工序"),
    "/production/orders": ("生产订单", "production order", "工序"),
    "/production/work-orders": ("工序", "work order", "报工"),
    "/warehouse/": ("仓储", "库存", "领料", "收货", "调拨", "盘点"),
    "/quality/": ("质量", "检验", "ncr"),
    "/equipment/": ("设备", "维修", "machine", "maintenance"),
    "/reports/": ("报表", "oee", "看板"),
    "/trace/": ("追溯", "批次", "trace"),
    "/integrations/": ("erp", "集成", "integration"),
}


def _prd_sections(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^#{2,4}\s+(.+?)\s*$", text or "", re.M))
    rows: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text or "")
        rows.append((match.group(1), (text or "")[match.start():end]))
    return rows


def _industry_context(prd_text: str, path: str) -> str:
    target = str(path or "")
    terms: tuple[str, ...] = ()
    for prefix, values in _INDUSTRY_SECTION_TERMS.items():
        if prefix in target:
            terms = values
            break
    if not terms:
        return ""
    selected = [body for heading, body in _prd_sections(prd_text) if any(_norm(term) in _norm(heading) for term in terms)]
    return "\n".join(selected[:3])


def _walk_leaf_paths(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_walk_leaf_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value[:3]):
            rows.extend(_walk_leaf_paths(item, f"{prefix}.{index}" if prefix else str(index)))
    else:
        rows.append((prefix, value))
    return rows


def _set_dotted(payload: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    body = copy.deepcopy(payload)
    current: Any = body
    parts = [part for part in str(dotted).split(".") if part]
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if isinstance(current, dict):
            if last:
                current[part] = value
                break
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            if last:
                current[int(part)] = value
                break
            current = current[int(part)]
        else:
            break
    return body


def _append_industry_contracts(compiled: dict[str, Any], prd_text: str) -> None:
    """Add generic industry-mutation contracts where public requirements supply the oracle."""
    existing = {str(row.get("contract_id")) for row in compiled.get("contracts") or []}
    additions: list[dict[str, Any]] = []
    endpoints = _endpoint_sections(compiled.get("_api_text", ""))
    for endpoint in endpoints:
        if endpoint["method"] not in _WRITE_METHODS or not endpoint.get("sample_body"):
            continue
        path = str(endpoint["path"])
        sector = _industry_context(prd_text, path)
        rule_text = f"{endpoint.get('section_text','')}\n{sector}"
        body = endpoint.get("sample_body") or {}
        leaves = _walk_leaf_paths(body)

        # Quantity and rate constraints when a requirement names a positive
        # quantity/rate but uses a nested or generic field label.
        if re.search(r"(?:数量[^\n。]{0,36}(?:大于|正数)|用量[^\n。]{0,36}(?:大于|正数)|良率[^\n。]{0,36}(?:大于|取值)|quantity[^\n.]{0,36}(?:positive|greater))", rule_text, re.I):
            for dotted, original in leaves:
                leaf = dotted.rsplit(".", 1)[-1].lower()
                if isinstance(original, bool) or not isinstance(original, (int, float)):
                    continue
                if not re.search(r"(?:qty|quantity|rate|yield|amount|planqty|sampleqty|passqty|failqty)", leaf, re.I):
                    continue
                for value in (0, -1):
                    cid = _contract_id("mes_positive", endpoint, f"{dotted}:{value}")
                    if cid in existing:
                        continue
                    additions.append({
                        "contract_id": cid,
                        "kind": "nested_positive_numeric",
                        "title": f"{endpoint['method']} {path} must reject {dotted}={value}",
                        "method": endpoint["method"], "path": path, "roles": endpoint.get("roles") or [],
                        "sample_body": body, "mutation": {"dotted_field": dotted, "value": value},
                        "expected": "non_2xx", "severity": "P1" if value < 0 else "P2",
                        "evidence_source": rule_text[:1200],
                        "execution_policy": "approved_disposable_sandbox_only",
                    })
                    existing.add(cid)

        # Empty lines/operations are a generic collection integrity check.
        for field, original in body.items():
            if not isinstance(original, list) or not original:
                continue
            if re.search(rf"(?:{re.escape(field)}|行|工序).{{0,32}}(?:不得为空|不能为空|at least one|non[- ]?empty)", rule_text, re.I):
                cid = _contract_id("mes_empty_collection", endpoint, field)
                if cid not in existing:
                    additions.append({
                        "contract_id": cid, "kind": "required_collection", "title": f"{endpoint['method']} {path} must reject empty {field}",
                        "method": endpoint["method"], "path": path, "roles": endpoint.get("roles") or [], "sample_body": body,
                        "mutation": {"dotted_field": field, "value": []}, "expected": "non_2xx", "severity": "P2",
                        "evidence_source": rule_text[:1200], "execution_policy": "approved_disposable_sandbox_only",
                    })
                    existing.add(cid)

        # Referential integrity: only mutate an identifier when the selected
        # document section explicitly says it must exist / be active / valid.
        if re.search(r"(?:必须存在|有效物料|引用的.{0,16}(?:有效|存在)|不存在.*(?:拒绝|阻断)|must exist|active material)", rule_text, re.I):
            for dotted, original in leaves:
                leaf = dotted.rsplit(".", 1)[-1]
                if not isinstance(original, str) or not re.search(r"(?:materialCode|machineCode|workCenter|bomVersion|routingVersion)$", leaf, re.I):
                    continue
                cid = _contract_id("mes_reference", endpoint, dotted)
                if cid in existing:
                    continue
                additions.append({
                    "contract_id": cid, "kind": "invalid_reference", "title": f"{endpoint['method']} {path} must reject unknown/inactive {dotted}",
                    "method": endpoint["method"], "path": path, "roles": endpoint.get("roles") or [], "sample_body": body,
                    "mutation": {"dotted_field": dotted, "value": f"QB-NONEXISTENT-{_hash(dotted, 8)}"}, "expected": "non_2xx", "severity": "P1",
                    "evidence_source": rule_text[:1200], "execution_policy": "approved_disposable_sandbox_only",
                })
                existing.add(cid)

        # Ordered process steps: duplicate an existing sequence number.  This
        # does not assume a particular MES API; it only activates when a list
        # has operation/order/sequence numbers and the documents require order.
        for field, original in body.items():
            if not isinstance(original, list) or len(original) < 2 or not all(isinstance(item, dict) for item in original[:2]):
                continue
            numeric_key = next((key for key in original[0] if re.search(r"(?:operationNo|sequence|seq|stepNo|orderNo)$", str(key), re.I)), None)
            if not numeric_key:
                continue
            if not re.search(r"(?:严格递增|唯一且递增|顺序|前后关系|strictly increasing|sequence)", rule_text, re.I):
                continue
            dotted = f"{field}.1.{numeric_key}"
            cid = _contract_id("mes_order", endpoint, dotted)
            if cid not in existing:
                additions.append({
                    "contract_id": cid, "kind": "ordered_steps", "title": f"{endpoint['method']} {path} must reject duplicate/out-of-order {numeric_key}",
                    "method": endpoint["method"], "path": path, "roles": endpoint.get("roles") or [], "sample_body": body,
                    "mutation": {"dotted_field": dotted, "value": original[0].get(numeric_key)}, "expected": "non_2xx", "severity": "P2",
                    "evidence_source": rule_text[:1200], "execution_policy": "approved_disposable_sandbox_only",
                })
                existing.add(cid)

    compiled["contracts"].extend(additions)
    compiled["industry_extension"] = {"added_contract_count": len(additions), "industry_vocabulary": "document_terms_v1"}


# Wrap the generic compiler so existing callers receive the MES extension only
# when the supplied documents genuinely contain MES vocabulary.
_compile_document_contracts_generic = compile_document_contracts

def compile_document_contracts(prd_text: str, api_text: str) -> dict[str, Any]:  # type: ignore[no-redef]
    result = _compile_document_contracts_generic(prd_text, api_text)
    result["_api_text"] = api_text
    if re.search(r"(?:BOM|生产订单|仓储|库存|工序|质量管理|MES)", f"{prd_text}\n{api_text}", re.I):
        _append_industry_contracts(result, prd_text)
    result.pop("_api_text", None)
    result["contract_count"] = len(result.get("contracts") or [])
    return result


# Teach the executor how to apply a nested document mutation without making
# domain-specific assumptions about the surrounding payload.
_mutated_body_generic = _mutated_body

def _mutated_body(contract: dict[str, Any], run_key: str) -> dict[str, Any]:  # type: ignore[no-redef]
    body = _mutated_body_generic(contract, run_key)
    mutation = contract.get("mutation") or {}
    if mutation.get("dotted_field"):
        return _set_dotted(body, str(mutation["dotted_field"]), mutation.get("value"))
    return body
