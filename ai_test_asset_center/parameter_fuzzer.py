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
from typing import Any

from .real_id_resolver import (
    QUALIBUG_UNRESOLVED_ID,
    extract_first_entity_id,
    infer_path_params,
    path_has_placeholders,
    resolve_real_id_from_documented_list,
)

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class ParameterFuzzer:
    """Probe only documented parameters under the current execution policy."""

    VARIANT_VALUES = {
        "integer": ["-1", "0", "999999999"],
        "string": ["", "A" * 1024],
        "any": ["", "0", "null"],
    }

    def __init__(self, base_url: str, *, allow_write: bool = False) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.allow_write = bool(allow_write)
        self._token = ""
        self._tokens: dict[str, str] = {}
        self._real_ids: dict[tuple[str, str], str] = {}

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
        findings: list[dict[str, Any]] = []
        for name in self._declared_params(route, query_only=False)[:3]:
            if name not in template:
                continue
            for value in self.VARIANT_VALUES["any"][:max(1, max_variants)]:
                body = dict(template)
                body[name] = value
                status, response, elapsed = self._call(str(route.get("method") or "POST").upper(), path, body, token=self._token)
                if status >= 500:
                    findings.append(self._finding(str(route.get("method") or "POST").upper(), path, status, response, elapsed, "server_error_under_disposable_sandbox_mutation", route))
        return findings

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
        try:
            request = urllib.request.Request(url, data=data, method=method, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310 - explicit configured test target
                raw = response.read(300_000).decode("utf-8", errors="replace")
                return int(response.status), self._json_or_text(raw), (time.perf_counter() - started) * 1000
        except urllib.error.HTTPError as exc:
            raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
            return int(exc.code), self._json_or_text(raw), (time.perf_counter() - started) * 1000
        except Exception as exc:
            return 0, {"error": str(exc)}, (time.perf_counter() - started) * 1000

    @staticmethod
    def _json_or_text(value: str) -> Any:
        try:
            return json.loads(value)
        except Exception:
            return value[:5000]

    @staticmethod
    def _finding(method: str, path: str, status: int, body: Any, elapsed: float, rule: str, route: dict[str, Any]) -> dict[str, Any]:
        return {
            "severity": "P1", "title": f"[参数探测] {method} {path} returned HTTP {status}",
            "category": "input_validation", "source": "parameter_fuzzer",
            "description": rule, "confidence_score": 0.7,
            "method": method, "path": path,
            "evidence": {
                "source_refs": route.get("source_refs") or route.get("document_refs") or [],
                "calls": [{"call": f"{method} {path}", "results": {"execution": {"status": status, "body": body}}}],
                "verifier_rule": rule,
                "duration_ms": elapsed,
            },
        }

    def _to_finding(self, method: str, path: str, status: int, body: Any, description: str, category: str, severity: str) -> dict[str, Any]:
        finding = self._finding(method, path, status, body, 0.0, description, {})
        finding["category"] = str(category or finding["category"])
        finding["severity"] = str(severity or finding["severity"])
        return finding
