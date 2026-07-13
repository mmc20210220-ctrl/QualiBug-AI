"""Source-constrained parameter fuzzer.

The original V12 adapter remains, but it no longer auto-registers users,
assumes roles, probes undocumented paths, or sends write requests by default.
Every HTTP mutation requires a source-bound disposable-sandbox contract.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .disposable_identity_materializer import (
    disposable_identity_nonce,
    has_disposable_identity_anchor,
    materialize_disposable_identity_fields,
)
from .real_id_resolver import (
    QUALIBUG_UNRESOLVED_ID,
    extract_first_entity_id,
    infer_path_params,
    path_has_placeholders,
    resolve_real_id_from_documented_list,
)

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SENSITIVE_FIELD_TOKENS = ("password", "token", "secret", "credential", "authorization", "cookie", "api_key", "apikey")


def _as_response_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class ParameterFuzzer:
    """Probe only documented parameters under the current execution policy."""

    VARIANT_VALUES = {
        "integer": ["-1", "0", "999999999"],
        "string": ["", "A" * 1024],
        "any": ["", "0", "null", "-1"],
        "quantity": ["-1", "0", "-10"],
        "amount": ["-1", "0", "-0.01"],
    }

    def __init__(
        self,
        base_url: str,
        *,
        allow_write: bool = False,
        governed_write_executor: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.allow_write = bool(allow_write)
        self._governed_write_executor = governed_write_executor
        self._token = ""
        self._tokens: dict[str, str] = {}
        self._real_ids: dict[tuple[str, str], str] = {}
        self.execution_receipts: list[dict[str, Any]] = []

    def login(self, email: str = "", password: str = "", login_path: str = "", body_template: dict[str, Any] | None = None) -> bool:
        """Authenticate only with caller-supplied, source-authorized credentials.

        Empty credentials are intentionally not replaced with demo accounts and no
        registration endpoint is ever invoked.
        """
        if not email or not password or not login_path:
            return False
        body = dict(body_template or {})
        body.setdefault("email", email)
        body.setdefault("password", password)
        status, response, _ = self._call("POST", login_path, body)
        token = self._extract_token(response)
        if 200 <= status < 300 and token:
            self._token = token
            return True
        return False

    def fuzz_all(self, routes: list[dict[str, Any]], max_variants: int = 3) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for route in routes or []:
            if not isinstance(route, dict):
                continue
            method = str(route.get("method") or "GET").upper()
            path = str(route.get("path") or "")
            if not path or not path.startswith("/"):
                continue
            if method in _READ_METHODS:
                findings.extend(self._fuzz_read_route(route, max_variants))
            elif method in _WRITE_METHODS and self._write_allowed(route):
                findings.extend(self._fuzz_write_route(route, max_variants))
        return findings

    def _fuzz_read_route(self, route: dict[str, Any], max_variants: int) -> list[dict[str, Any]]:
        path = self._resolve_read_path(route)
        if not path:
            return []
        params = self._declared_params(route, query_only=True)
        findings: list[dict[str, Any]] = []
        if not params:
            status, body, elapsed = self._call("GET", path, token=self._token)
            if status >= 500:
                findings.append(self._finding("GET", path, status, body, elapsed, "server_error_under_source_declared_read_route", route))
            return findings
        for name in params[:3]:
            for value in self.VARIANT_VALUES["any"][:max(1, max_variants)]:
                url_path = path + ("&" if "?" in path else "?") + urllib.parse.urlencode({name: value})
                status, body, elapsed = self._call("GET", url_path, token=self._token)
                if status >= 500:
                    findings.append(self._finding("GET", url_path, status, body, elapsed, "server_error_under_source_declared_parameter", route))
        return findings

    def _resolve_read_path(self, route: dict[str, Any]) -> str:
        path = str(route.get("path") or "")
        path_params = infer_path_params(path, route.get("path_params") or [])
        if not path_params and not path_has_placeholders(path):
            return path
        replacements = self._resolve_path_params(route, path_params)
        resolved = path
        for name, value in replacements.items():
            token = str(value or "").strip()
            if not token:
                continue
            resolved = resolved.replace(f"{{{name}}}", token)
            resolved = re.sub(rf":{re.escape(name)}\b", token, resolved)
        return "" if path_has_placeholders(resolved) else resolved

    def _resolve_path_params(self, route: dict[str, Any], path_params: list[str]) -> dict[str, str]:
        path = str(route.get("path") or "")
        path_params = infer_path_params(path, path_params)
        if not path_params:
            return {}
        resolved: dict[str, str] = {}
        for name in path_params:
            candidate = self._get_real_id(path, name)
            if candidate and candidate != QUALIBUG_UNRESOLVED_ID:
                resolved[name] = candidate
        return resolved

    def _get_real_id(self, path_pattern: str, param_name: str) -> str:
        cache_key = (str(path_pattern or ""), str(param_name or ""))
        if cache_key in self._real_ids:
            return self._real_ids[cache_key]
        resolved = resolve_real_id_from_documented_list(
            str(path_pattern or ""),
            str(param_name or ""),
            self._try_extract_id_from_list,
        )
        self._real_ids[cache_key] = resolved
        return resolved

    def _try_extract_id_from_list(self, list_path: str, param_name: str) -> str | None:
        status, body, _ = self._call("GET", list_path, token=self._token)
        if status != 200:
            return None
        return extract_first_entity_id(body, param_name)

    def _fuzz_write_route(self, route: dict[str, Any], max_variants: int) -> list[dict[str, Any]]:
        template = route.get("request_template")
        if not isinstance(template, dict) or not template:
            return []
        path = str(route.get("path") or "")
        if self._governed_write_executor is None:
            raise RuntimeError("governed_write_executor_required")
        findings: list[dict[str, Any]] = []
        method = str(route.get("method") or "POST").upper()
        for name in self._declared_params(route, query_only=False)[:3]:
            if name not in template:
                continue
            variants = self.VARIANT_VALUES["any"]
            low_name = name.lower()
            if any(token in low_name for token in ("qty", "quantity", "count", "stock")):
                variants = self.VARIANT_VALUES["quantity"]
            elif any(token in low_name for token in ("amount", "price", "balance", "total")):
                variants = self.VARIANT_VALUES["amount"]
            for value in variants[:max(1, max_variants)]:
                body = dict(template)
                body[name] = value
                materialized_fields: list[str] = []
                if has_disposable_identity_anchor(body):
                    nonce = disposable_identity_nonce("parameter_fuzzer", method, path, name, value)
                    body, materialized_fields = materialize_disposable_identity_fields(
                        body,
                        nonce,
                        skip_keys={name},
                    )
                status, response, elapsed, trace = self._governed_write_call(method, path, body, route)
                if status >= 500:
                    finding = self._finding(
                        method,
                        path,
                        status,
                        response,
                        elapsed,
                        "server_error_under_disposable_sandbox_mutation",
                        route,
                        request_body=body,
                        trace=trace,
                    )
                    sandbox_write = trace.get("sandbox_write") if isinstance(trace, dict) else {}
                    if isinstance(sandbox_write, dict) and sandbox_write:
                        finding["evidence"]["sandbox_write"] = sandbox_write
                    audit_path = str((sandbox_write or {}).get("audit_path") or trace.get("audit_path") or "")
                    if audit_path:
                        finding["evidence"]["audit_path"] = audit_path
                    if materialized_fields:
                        finding["evidence"]["disposable_identity_materialization"] = {
                            "mutated_field": name,
                            "materialized_fields": materialized_fields,
                        }
                    findings.append(finding)
        return findings

    def _governed_write_call(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
        route: dict[str, Any],
    ) -> tuple[int, Any, float, dict[str, Any]]:
        if self._governed_write_executor is None:
            raise RuntimeError("governed_write_executor_required")
        started = time.perf_counter()
        result = self._governed_write_executor(
            method=method,
            path=path,
            body=dict(body),
            route=dict(route),
            token=self._token,
        )
        if not isinstance(result, dict):
            raise RuntimeError("governed_write_executor_invalid_result")
        trace = result.get("trace") if isinstance(result.get("trace"), dict) else result
        status = self._governed_status(result, trace)
        response = result.get("response")
        if response in (None, {}) and isinstance(trace, dict):
            response = self._governed_response(trace, method, path)
        elapsed = float(result.get("duration_ms") or ((time.perf_counter() - started) * 1000))
        sandbox_write = trace.get("sandbox_write") if isinstance(trace, dict) else {}
        audit_path = str(result.get("audit_path") or (sandbox_write or {}).get("audit_path") or "")
        self.execution_receipts.append({
            "method": method,
            "path": path,
            "status": int(status),
            "duration_ms": elapsed,
            "governed": True,
            "audit_path": audit_path,
            "sandbox_status": str((sandbox_write or {}).get("status") or ""),
        })
        return int(status), response if response is not None else {}, elapsed, trace if isinstance(trace, dict) else {}

    @staticmethod
    def _governed_status(result: dict[str, Any], trace: dict[str, Any]) -> int:
        for value in (
            result.get("status"),
            _as_response_dict(result.get("response")).get("status_code"),
            _as_response_dict(result.get("response")).get("status"),
        ):
            try:
                status = int(value or 0)
            except (TypeError, ValueError):
                status = 0
            if status:
                return status
        for step in trace.get("steps") or []:
            if not isinstance(step, dict):
                continue
            response = _as_response_dict(step.get("response"))
            for value in (response.get("status_code"), response.get("status"), step.get("status")):
                try:
                    status = int(value or 0)
                except (TypeError, ValueError):
                    status = 0
                if status:
                    return status
        return 0

    @staticmethod
    def _governed_response(trace: dict[str, Any], method: str, path: str) -> Any:
        for step in trace.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if str(step.get("method") or "").upper() != method:
                continue
            if str(step.get("path") or "") != path:
                continue
            response = _as_response_dict(step.get("response"))
            if "body" in response:
                return response.get("body")
        return {}

    def _write_allowed(self, route: dict[str, Any]) -> bool:
        policy = str(route.get("execution_policy") or "")
        sandbox = route.get("disposable_sandbox")
        return self.allow_write and policy == "disposable_sandbox_required" and isinstance(sandbox, dict) and bool(sandbox.get("approved"))

    def _test_acl(self, routes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for route in routes or []:
            if not isinstance(route, dict):
                continue
            path = str(route.get("path") or "")
            method = str(route.get("method") or "GET").upper()
            if not path or method not in _READ_METHODS:
                continue
            status, body, elapsed = self._call(method, path)
            if 200 <= status < 300:
                findings.append(self._finding(method, path, status, body, elapsed, "acl_probe_unexpected_success", route))
        return findings

    def _declared_params(self, route: dict[str, Any], *, query_only: bool) -> list[str]:
        values: list[str] = []
        if query_only:
            for item in route.get("query_params") or []:
                if isinstance(item, dict) and item.get("name"):
                    values.append(str(item["name"]))
                elif isinstance(item, str) and item:
                    values.append(item)
        else:
            body = route.get("body_properties")
            if isinstance(body, dict):
                values.extend(str(key) for key in body if str(key))
            raw = route.get("params_raw")
            if isinstance(raw, str):
                values.extend(part.strip() for part in raw.replace("{", "").replace("}", "").split(",") if part.strip() and part.strip() not in {"-", "|"})
        return list(dict.fromkeys(values))

    @staticmethod
    def _extract_token(body: Any) -> str:
        if not isinstance(body, dict):
            return ""
        for key in ("token", "access_token", "jwt"):
            if body.get(key):
                return str(body[key])
        data = body.get("data")
        if isinstance(data, dict):
            for key in ("token", "access_token", "jwt"):
                if data.get(key):
                    return str(data[key])
        return ""

    def _call(self, method: str, path: str, body: Any | None = None, token: str = "") -> tuple[int, Any, float]:
        if not self.base_url:
            return 0, {"error": "base_url_missing"}, 0.0
        url = self.base_url + (path if str(path).startswith("/") else "/" + str(path))
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        started = time.perf_counter()
        def _record(status: int, response_body: Any, elapsed_ms: float) -> tuple[int, Any, float]:
            self.execution_receipts.append({
                "method": method,
                "path": path,
                "status": int(status),
                "duration_ms": float(elapsed_ms),
            })
            return int(status), response_body, float(elapsed_ms)

        try:
            request = urllib.request.Request(url, data=data, method=method, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310 - explicit configured test target
                raw = response.read(300_000).decode("utf-8", errors="replace")
                return _record(int(response.status), self._json_or_text(raw), (time.perf_counter() - started) * 1000)
        except urllib.error.HTTPError as exc:
            raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
            return _record(int(exc.code), self._json_or_text(raw), (time.perf_counter() - started) * 1000)
        except Exception as exc:
            return _record(0, {"error": str(exc)}, (time.perf_counter() - started) * 1000)

    @staticmethod
    def _json_or_text(value: str) -> Any:
        try:
            return json.loads(value)
        except Exception:
            return value[:5000]

    @staticmethod
    def _finding(
        method: str,
        path: str,
        status: int,
        body: Any,
        elapsed: float,
        rule: str,
        route: dict[str, Any],
        *,
        request_body: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        request_raw: dict[str, Any] = {"method": method, "path": path}
        if request_body is not None:
            request_raw["body"] = _redact_sensitive_payload(request_body)
        response_raw = {"status_code": int(status), "body": _redact_sensitive_payload(body)}
        trace_payload = trace if isinstance(trace, dict) else {}
        sandbox_write = trace_payload.get("sandbox_write") if isinstance(trace_payload.get("sandbox_write"), dict) else {}
        reproduction_steps = [f"{method} {path} -> HTTP {int(status)}"]
        raw_evidence: dict[str, Any] = {
            "has_real_evidence": True,
            "timestamp": timestamp,
            "request_raw": request_raw,
            "response_raw": response_raw,
            "execution_source": "parameter_fuzzer",
        }
        if sandbox_write:
            raw_evidence["sandbox_write"] = sandbox_write
        return {
            "severity": "P1", "title": f"[参数探测] {method} {path} returned HTTP {status}",
            "category": "input_validation", "source": "parameter_fuzzer",
            "description": rule, "confidence_score": 0.7,
            "method": method, "path": path,
            "timestamp": timestamp,
            "execution_status": "executed",
            "confirmation_status": "candidate",
            "bug_status": "suspected",
            "gate_passed": False,
            "customer_delivery_status": "candidate",
            "evidence": {
                "source_refs": route.get("source_refs") or route.get("document_refs") or [],
                "calls": [{"call": f"{method} {path}", "results": {"execution": {"status": status, "body": body}}}],
                "verifier_rule": rule,
                "duration_ms": elapsed,
            },
            "raw_evidence": raw_evidence,
            "reproduction": {
                "method": method,
                "path": path,
                "is_synthetic": False,
                "request_body": request_raw.get("body", {}),
                "har_evidence": {
                    "status_code": int(status),
                    "response_body": response_raw["body"],
                },
                "reproduction_steps": reproduction_steps,
            },
            "reproduction_steps": reproduction_steps,
            "evidence_quality": {
                "level": "needs_evidence",
                "score": 40,
                "can_reproduce": False,
            },
            "evidence_status": {
                "semantic_verdict": "SEMANTIC_CANDIDATE",
                "business_evidence_status": "PENDING_EVIDENCE",
                "final_review_status": "NEEDS_MORE_EVIDENCE",
                "missing_requirements": [
                    "BUSINESS_ASSERTION_REQUIRED",
                    "CONTROL_OR_ORACLE_CONFIRMATION_REQUIRED",
                ],
            },
        }

    def _to_finding(self, method: str, path: str, status: int, body: Any, description: str, category: str, severity: str) -> dict[str, Any]:
        finding = self._finding(method, path, status, body, 0.0, description, {})
        finding["category"] = str(category or finding["category"])
        finding["severity"] = str(severity or finding["severity"])
        return finding


def _redact_sensitive_payload(value: Any) -> Any:
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, child in value.items():
            key_l = re.sub(r"[^a-z0-9]+", "", str(key or "").lower())
            if any(token in key_l for token in _SENSITIVE_FIELD_TOKENS):
                rendered[str(key)] = "<REDACTED>"
            else:
                rendered[str(key)] = _redact_sensitive_payload(child)
        return rendered
    if isinstance(value, list):
        return [_redact_sensitive_payload(child) for child in value]
    return value
