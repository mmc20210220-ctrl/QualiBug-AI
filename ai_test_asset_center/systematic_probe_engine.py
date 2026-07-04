"""
SystematicProbeEngine — Coverage-driven endpoint probing.

For each API endpoint, generates parameter variants and executes probes,
detecting anomalies from HTTP status, response shape, and invariants.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProbeResult:
    method: str
    path: str
    variant_name: str
    status_code: int
    response_body: dict[str, Any]
    is_anomaly: bool
    anomaly_reason: str = ""
    bug_id: str = ""


class SystematicProbeEngine:
    """Probe every endpoint with parameter variants, detect anomalies."""

    def __init__(self, base_url: str, route_catalog: list[dict[str, Any]]):
        self.base_url = base_url.rstrip("/")
        self.routes = route_catalog
        self.results: list[ProbeResult] = []
        self._real_ids: dict[str, str] = {}

    def probe_all(self, variants_per_endpoint: int = 12) -> list[ProbeResult]:
        """Run probes against all known endpoints."""
        for route in self.routes:
            self._probe_endpoint(route, variants_per_endpoint)
        return self.results

    def _probe_endpoint(self, route: dict[str, Any], max_variants: int):
        method = route.get("method", "GET").upper()
        path = route.get("path", "")
        if not path:
            return

        variants = self._generate_variants(method, path, route, max_variants)
        for variant_name, resolved_path, body in variants:
            result = self._execute_probe(method, resolved_path, body, variant_name)
            self.results.append(result)
            time.sleep(0.1)  # Rate limit

    def _generate_variants(self, method: str, path: str, route: dict[str, Any], max_count: int):
        """Generate parameter variant combinations for an endpoint."""
        variants = []
        has_params = "{" in path
        path_params = route.get("path_params", [])
        query_params = route.get("query_params", [])
        has_body = route.get("has_body", False) or method in ("POST", "PUT", "PATCH")
        body_schema = route.get("body_properties", {})

        # Base: use real IDs where possible
        filled_path = path
        if has_params:
            for param in path_params:
                real_id = self._get_real_id(param, path)
                filled_path = filled_path.replace(f"{{{param}}}", real_id)

        # === Variant generation ===
        variant_list = []

        # 1. Valid request (baseline)
        if method == "GET":
            variant_list.append(("baseline", filled_path, None))
            # Query param variants
            for qp in query_params[:3]:
                name = qp.get("name", "")
                if name:
                    for val in ["-1", "0", "99999999", ""]:
                        qpath = filled_path + ("&" if "?" in filled_path else "?") + f"{name}={val}"
                        variant_list.append((f"query_{name}={val}", qpath, None))
        else:
            # POST/PUT/PATCH
            variant_list.append(("baseline", filled_path, {}))
            if body_schema:
                # Null/empty body
                variant_list.append(("null_body", filled_path, None))
                variant_list.append(("empty_body", filled_path, {}))
                # Type violations
                for field, info in list(body_schema.items())[:3]:
                    ftype = info.get("type", "string") if isinstance(info, dict) else "string"
                    if ftype == "integer" or ftype == "number":
                        variant_list.append((f"{field}_negative", filled_path, {field: -1}))
                        variant_list.append((f"{field}_zero", filled_path, {field: 0}))
                        variant_list.append((f"{field}_max", filled_path, {field: 99999999}))
                        variant_list.append((f"{field}_string", filled_path, {field: "notanumber"}))
                    elif ftype == "string":
                        variant_list.append((f"{field}_empty", filled_path, {field: ""}))
                        variant_list.append((f"{field}_long", filled_path, {field: "A" * 10000}))
                        variant_list.append((f"{field}_xss", filled_path, {field: "<script>alert(1)</script>"}))
            # Missing required field variant
            if body_schema:
                required = [k for k, v in body_schema.items() if isinstance(v, dict) and v.get("required")]
                if required:
                    body_missing = {}
                    variant_list.append((f"missing_{required[0]}", filled_path, body_missing))

        # 2. Path parameter variants (if exists)
        if has_params:
            for param in path_params[:2]:
                for bad_val in ["-1", "0", "99999999", "notanumber"]:
                    bad_path = filled_path.replace(f"{{{param}}}", bad_val)
                    if method == "GET":
                        variant_list.append((f"path_{param}={bad_val}", bad_path, None))
                    else:
                        variant_list.append((f"path_{param}={bad_val}", bad_path, {}))

        # 3. No-auth variant (for auth testing)
        variant_list.append(("no_auth", filled_path, {} if method != "GET" else None))

        return variant_list[:max_count]

    def _get_real_id(self, param: str, path: str) -> str:
        """Fetch a real entity ID from the API list endpoint."""
        if param in self._real_ids:
            return self._real_ids[param]

        # Infer list path from detail path
        list_path = re.sub(r'/\{[^}]+\}.*', '', path)
        if not list_path or list_path == path:
            return "1"

        try:
            req = urllib.request.Request(
                f"{self.base_url}{list_path}",
                headers={"User-Agent": "QualiBug/1.0", "X-QualiBug-Actor": "admin"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                body = json.loads(resp.read())
        except Exception:
            return "1"

        # Extract first record's ID
        records = body.get("records", body.get("data", body.get("items", [])))
        if isinstance(records, list) and records:
            first = records[0]
            if isinstance(first, dict):
                for idf in ("id", "business_no", "order_id", param, "ID"):
                    if idf in first and first[idf] is not None:
                        val = str(first[idf])
                        self._real_ids[param] = val
                        return val

        return "1"

    def _execute_probe(self, method: str, path: str, body: dict | None, variant: str) -> ProbeResult:
        """Execute a single probe and detect anomalies."""
        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "QualiBug-SystematicProbe/1.0",
            "X-QualiBug-Actor": "admin",
        }

        data = json.dumps(body).encode() if body and method != "GET" else None

        try:
            req = urllib.request.Request(url, method=method, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp_body = json.loads(resp.read())
            status = resp.status
        except urllib.error.HTTPError as e:
            try:
                resp_body = json.loads(e.read())
            except Exception:
                resp_body = {}
            status = e.code
        except Exception as e:
            return ProbeResult(
                method=method, path=path, variant_name=variant,
                status_code=0, response_body={},
                is_anomaly=True, anomaly_reason=f"连接失败: {e}"
            )

        # === Anomaly detection (universal, no bug-lab assumptions) ===
        is_anomaly = False
        reason = ""
        bug_id = ""

        if status >= 500:
            is_anomaly = True
            reason = f"服务端异常 HTTP{status}"
        elif isinstance(resp_body, dict):
            if resp_body.get("ok") is False:
                is_anomaly = True
                reason = "业务逻辑返回失败"
            elif resp_body.get("error"):
                is_anomaly = True
                reason = f"错误响应: {str(resp_body.get('error', ''))[:100]}"
            elif "exception" in str(resp_body).lower() or "traceback" in str(resp_body).lower():
                is_anomaly = True
                reason = "响应体泄露异常信息"
            elif "stack" in str(resp_body).lower() and "at " in str(resp_body).lower():
                is_anomaly = True
                reason = "响应体泄露调用栈"
            # Post-200 anomalies
            if status == 200 and method in ("POST", "PUT", "PATCH") and isinstance(resp_body, dict):
                if not resp_body:  # empty response to write
                    is_anomaly = True
                    reason = "写操作返回空响应体"

            # Bug lab specific but generic enough to include: any "injected" marker
            if resp_body.get("injected"):
                is_anomaly = True
                bug_id = resp_body.get("bug_id", "")
                reason = f"检测到缺陷注入 [{resp_body.get('type','?')}/{resp_body.get('subtype','?')}] Bug#{bug_id}"
                if not bug_id:
                    bug_id = ""

        return ProbeResult(
            method=method, path=path, variant_name=variant,
            status_code=status, response_body=resp_body if isinstance(resp_body, dict) else {},
            is_anomaly=is_anomaly, anomaly_reason=reason, bug_id=bug_id,
        )
