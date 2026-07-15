"""
Replay Engine — 实时复现引擎。

POST /api/v1/projects/{id}/replay 接收 finding_id + 可选 base_url，
从统一汇聚的 risks 中查找对应 finding 的 repro_method/repro_path/repro_params，
通过 ssrf_guard.validate_url() 校验目标 URL，
通过 multi_service_config.json 获取认证凭证，
真实调用被测系统接口，返回请求/响应/diff 对比结果。

支持并发复现（ThreadPoolExecutor, max_workers=4, timeout=30s）。
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


class ReplayEngine:
    """实时复现引擎：真实调用被测系统接口重新触发 Bug。"""

    def __init__(self, root: Path, project_id: str):
        self.root = root
        self.project_id = project_id
        self._config: dict | None = None

    # ── 配置加载 ──────────────────────────────────────────────────────

    def _load_service_config(self) -> dict:
        """从 multi_service_config.json 加载服务配置"""
        if self._config is not None:
            return self._config
        config_path = self.root / "platform_workspace" / self.project_id / "multi_service_config.json"
        if not config_path.exists():
            self._config = {}
            return self._config
        try:
            self._config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            self._config = {}
        return self._config

    def _get_base_url(self) -> str:
        """获取被测系统的 base_url：优先 multi_service_config.json，其次 connector_registry.json"""
        # 1. 先从 multi_service_config.json 获取
        config = self._load_service_config()
        services = config.get("services") or []
        for svc in services:
            if isinstance(svc, dict) and svc.get("base_url"):
                return str(svc["base_url"]).rstrip("/")

        # 2. 从 connector_registry.json 获取（endpoint_ref 字段）
        connector_path = self.root / "platform_workspace" / self.project_id / "enterprise_pilot_runtime" / "connector_registry.json"
        if connector_path.exists():
            try:
                conn_data = json.loads(connector_path.read_text(encoding="utf-8"))
                for conn in (conn_data.get("connectors") or []):
                    if isinstance(conn, dict) and conn.get("enabled", True):
                        endpoint = str(conn.get("endpoint_ref") or "").strip().rstrip("/")
                        if endpoint and endpoint.startswith("http"):
                            return endpoint
            except Exception:
                pass

        # 3. Fallback: 本地开发默认地址
        return ""

    def _get_auth_header(self) -> tuple[str, str]:
        """获取认证 header (name, value)"""
        # 1. 先从 multi_service_config.json 获取
        config = self._load_service_config()
        services = config.get("services") or []
        for svc in services:
            if not isinstance(svc, dict):
                continue
            auth = svc.get("auth") or {}
            bearer = auth.get("bearer_token") or ""
            if bearer:
                try:
                    from .credential_crypto import decrypt as _dec, is_encrypted as _is_enc
                    if _is_enc(bearer):
                        bearer = _dec(bearer)
                except Exception:
                    pass
                return "Authorization", f"Bearer {bearer}"
            api_key = auth.get("api_key") or ""
            if api_key:
                try:
                    from .credential_crypto import decrypt as _dec, is_encrypted as _is_enc
                    if _is_enc(api_key):
                        api_key = _dec(api_key)
                except Exception:
                    pass
                return "X-API-Key", api_key

        # 2. 从 connector_registry.json 获取凭证
        connector_path = self.root / "platform_workspace" / self.project_id / "enterprise_pilot_runtime" / "connector_registry.json"
        if connector_path.exists():
            try:
                conn_data = json.loads(connector_path.read_text(encoding="utf-8"))
                for conn in (conn_data.get("connectors") or []):
                    if not isinstance(conn, dict) or not conn.get("enabled", True):
                        continue
                    cred_ref = str(conn.get("credential_ref") or "").strip()
                    if cred_ref:
                        # 尝试从 credentials store 读取
                        try:
                            from .credential_crypto import decrypt as _dec, is_encrypted as _is_enc
                            if _is_enc(cred_ref):
                                token = _dec(cred_ref)
                                return "Authorization", f"Bearer {token}"
                        except Exception:
                            pass
            except Exception:
                pass

        return "Authorization", ""

    def _auto_login(self, base_url: str) -> tuple[str, str]:
        """
        自动登录获取认证 token。
        1. 先从 scan_result.json 的 HAR 里提取已有 token
        2. 如果没有或已过期，从 test_profile.test_credentials 读凭证自动登录
        """
        # 1. 从 HAR 提取已有 token
        try:
            scan_path = self.root / "platform_outputs" / self.project_id / "scan_result.json"
            if scan_path.exists():
                scan_data = json.loads(scan_path.read_text(encoding="utf-8"))
                har_entries = (scan_data.get("auto_har") or {}).get("entries") or []
                for entry in har_entries:
                    req = entry.get("request") or {}
                    resp = entry.get("response") or {}
                    if (req.get("url", "").endswith("/api/auth/login")
                            and resp.get("status") == 200):
                        body = resp.get("body") or ""
                        if isinstance(body, str):
                            try:
                                body_obj = json.loads(body)
                                token = str(body_obj.get("token") or body_obj.get("access_token") or "")
                                if token:
                                    return "Authorization", f"Bearer {token}"
                            except Exception:
                                pass
        except Exception:
            pass

        # 2. 从 test_profile.test_credentials 读凭证自动登录
        creds = self._load_test_credentials()
        buyer = creds.get("buyer") or {}
        email = str(buyer.get("email") or "")
        password = str(buyer.get("password") or "")
        if email and password:
            try:
                login_url = f"{base_url}/api/auth/login"
                login_body = json.dumps({"email": email, "password": password}).encode("utf-8")
                req = urllib.request.Request(
                    login_url, data=login_body,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                from .ssrf_guard import safe_urlopen
                resp = safe_urlopen(req, timeout=10, allow_internal=True)
                body = json.loads(resp.read().decode("utf-8"))
                token = str(body.get("token") or body.get("access_token") or "")
                if token:
                    return "Authorization", f"Bearer {token}"
            except Exception:
                pass

        return "Authorization", ""

    def _load_test_credentials(self) -> dict:
        """从 connector_registry.json 的 test_profile.test_credentials 读取凭证"""
        connector_path = self.root / "platform_workspace" / self.project_id / "enterprise_pilot_runtime" / "connector_registry.json"
        if not connector_path.exists():
            return {}
        try:
            conn_data = json.loads(connector_path.read_text(encoding="utf-8"))
            test_profile = conn_data.get("test_profile") or {}
            return test_profile.get("test_credentials") or {}
        except Exception:
            return {}

    # ── Finding 查找 ──────────────────────────────────────────────────

    def _find_finding(self, finding_id: str, risks: list[dict]) -> dict | None:
        """从统一汇聚的 risks 列表中查找对应 finding"""
        for r in risks:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id") or r.get("risk_id") or r.get("finding_id") or r.get("bug_id") or "")
            if rid == finding_id:
                return r
        return None

    # ── SSRF 校验 ─────────────────────────────────────────────────────

    def _validate_replay_url(self, url: str) -> str:
        """校验复现 URL 安全性。允许已注册的项目 base_url（即使内网地址）。"""
        from .ssrf_guard import validate_url, SsrfBlockedError

        # 先尝试标准校验（allow_internal 由环境变量 QUALIBUG_SSRF_ALLOW_INTERNAL 控制）
        try:
            return validate_url(url, allow_internal=True)
        except SsrfBlockedError:
            pass

        # 如果标准校验失败，检查是否匹配已注册的项目 base_url
        base_url = self._get_base_url()
        if base_url and url.startswith(base_url):
            return url  # 允许已注册的项目 base_url

        raise SsrfBlockedError(f"URL '{url}' 被阻止：不是 http/https 协议或目标为内网地址且未匹配已注册的项目地址")

    # ── 复现执行 ──────────────────────────────────────────────────────

    def replay(self, finding_id: str, risks: list[dict], base_url_override: str = "") -> dict:
        """
        复现单条 finding。

        Args:
            finding_id: finding 的 ID
            risks: 统一汇聚的 risks 列表（从 command-center 获取）
            base_url_override: 可选的 base_url 覆盖

        Returns:
            ReplayResult 字典
        """
        finding = self._find_finding(finding_id, risks)
        if not finding:
            return {
                "ok": False,
                "finding_id": finding_id,
                "error": f"未找到 finding: {finding_id}",
            }

        # 获取 method/path
        method = str(finding.get("repro_method") or finding.get("_api_method") or finding.get("method") or "GET").upper()
        path = str(finding.get("repro_path") or finding.get("_api_path") or finding.get("path") or "")

        if not path:
            return {
                "ok": False,
                "finding_id": finding_id,
                "error": "该 finding 没有可复现的接口路径",
            }

        # 检查 path 是否含占位符（如 /api/orders/{id}/pay），无法直接复现
        if "{" in path and "}" in path:
            return {
                "ok": False,
                "finding_id": finding_id,
                "error": f"接口路径含占位符 {path}，无法自动复现。请手动替换占位符为真实业务ID后执行。",
            }

        # 构建 URL
        base_url = (base_url_override or self._get_base_url()).rstrip("/")
        if path.startswith("http"):
            full_url = path
        else:
            full_url = f"{base_url}{path}" if path.startswith("/") else f"{base_url}/{path}"

        # SSRF 校验
        try:
            self._validate_replay_url(full_url)
        except Exception as e:
            return {
                "ok": False,
                "finding_id": finding_id,
                "error": f"SSRF 安全校验失败: {e}",
            }

        # 获取认证 header
        auth_header_name, auth_header_value = self._get_auth_header()

        # 如果没有凭证，尝试自动登录获取 token
        if not auth_header_value and base_url:
            auth_header_name, auth_header_value = self._auto_login(base_url)

        # 构建 headers
        headers: dict[str, str] = {}
        if auth_header_value:
            headers[auth_header_name] = auth_header_value
        if method in ("POST", "PUT", "PATCH"):
            headers["Content-Type"] = "application/json"

        # 请求体（如果有）— 从 har_evidence 或原始 evidence 获取
        body = None
        har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
        reproduction = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
        if method in ("POST", "PUT", "PATCH"):
            # 优先从 har_evidence.request_body 获取，其次从 reproduction 获取
            body_str = har.get("request_body") or reproduction.get("request_body") or ""
            if body_str:
                body = body_str.encode("utf-8") if isinstance(body_str, str) else str(body_str).encode("utf-8")

        # 记录请求信息
        request_info = {
            "method": method,
            "url": full_url,
            "headers": {k: (v if "auth" not in k.lower() and "token" not in k.lower() else "***") for k, v in headers.items()},
            "body": body.decode("utf-8") if body else "",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        # 真实调用被测系统接口
        start = time.perf_counter()
        try:
            req = urllib.request.Request(full_url, data=body, headers=headers, method=method)

            from .ssrf_guard import safe_urlopen
            # 允许内网地址（已通过 _validate_replay_url 校验过）
            resp = safe_urlopen(req, timeout=30, allow_internal=True)
            duration_ms = int((time.perf_counter() - start) * 1000)

            resp_status = resp.status
            resp_headers = dict(resp.headers)
            resp_body = resp.read().decode("utf-8", errors="replace")

        except urllib.error.HTTPError as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            resp_status = e.code
            resp_headers = dict(e.headers) if e.headers else {}
            try:
                resp_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                resp_body = ""
        except urllib.error.URLError as e:
            return {
                "ok": False,
                "finding_id": finding_id,
                "error": f"连接失败: {e.reason}",
                "request": request_info,
            }
        except Exception as e:
            return {
                "ok": False,
                "finding_id": finding_id,
                "error": f"复现执行失败: {type(e).__name__}: {e}",
                "request": request_info,
            }

        response_info = {
            "status_code": resp_status,
            "headers": resp_headers,
            "body": resp_body[:5000],  # 限制长度
            "duration_ms": duration_ms,
        }

        # 获取原始证据用于 diff 对比
        original_evidence = self._extract_original_evidence(finding)

        # 生成 diff 对比
        diff = self._compute_diff(original_evidence, response_info)

        # 判断是否成功复现
        success = self._is_reproduced(finding, original_evidence, response_info)

        return {
            "ok": True,
            "finding_id": finding_id,
            "request": request_info,
            "response": response_info,
            "success": success,
            "original_evidence": original_evidence,
            "diff": diff,
        }

    # ── 批量复现 ──────────────────────────────────────────────────────

    def replay_batch(self, finding_ids: list[str], risks: list[dict], max_workers: int = 4) -> list[dict]:
        """批量复现多条 finding"""
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.replay, fid, risks): fid
                for fid in finding_ids
            }
            for future in as_completed(futures, timeout=120):
                fid = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({
                        "ok": False,
                        "finding_id": fid,
                        "error": f"批量复现异常: {e}",
                    })
        return results

    # ── 原始证据提取 ──────────────────────────────────────────────────

    def _extract_original_evidence(self, finding: dict) -> dict:
        """从 finding 中提取原始扫描证据"""
        har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}

        return {
            "status_code": int(har.get("status_code") or evidence.get("status_code") or evidence.get("response_status") or 0),
            "response_body_excerpt": str(har.get("response_body") or evidence.get("response") or evidence.get("actual") or "")[:1000],
            "har_actor": str(har.get("actor") or ""),
        }

    # ── Diff 计算 ─────────────────────────────────────────────────────

    def _compute_diff(self, original: dict, replay_response: dict) -> dict:
        """计算原始证据与复现结果的差异"""
        orig_status = original.get("status_code") or 0
        replay_status = replay_response.get("status_code") or 0

        status_match = orig_status == replay_status

        orig_body = original.get("response_body_excerpt") or ""
        replay_body = replay_response.get("body") or ""
        body_match = orig_body.strip() == replay_body.strip() if orig_body and replay_body else False

        key_differences: list[str] = []
        if not status_match:
            key_differences.append(f"状态码不一致：原始 {orig_status}，复现 {replay_status}")
        if not body_match and orig_body and replay_body:
            key_differences.append("响应体内容存在差异")
        if not orig_body:
            key_differences.append("原始扫描未记录响应体，无法做内容对比")
        if not replay_body:
            key_differences.append("复现请求未返回响应体")

        return {
            "status_match": status_match,
            "body_match": body_match,
            "key_differences": key_differences,
        }

    # ── 复现判断 ──────────────────────────────────────────────────────

    def _is_reproduced(self, finding: dict, original: dict, replay_response: dict) -> bool:
        """
        判断是否成功复现了 Bug。
        逻辑：
        - 如果原始证据有状态码，且复现状态码一致 → 复现成功（Bug 仍然存在）
        - 如果原始证据没有状态码，但复现请求返回了错误状态码（4xx/5xx）→ 复现成功（Bug 触发了异常）
        - 如果原始证据没有状态码，且复现请求返回 2xx → 无法确定（可能是正常行为，也可能是 Bug 未触发）
        """
        orig_status = original.get("status_code") or 0
        replay_status = replay_response.get("status_code") or 0

        # 如果原始证据有状态码，且复现状态码一致，则复现成功
        if orig_status and replay_status and orig_status == replay_status:
            return True

        # 如果原始证据没有状态码（纯分析型 finding），看复现结果：
        # - 返回 4xx/5xx → Bug 可能复现（异常被触发）
        # - 返回 2xx → 无法确定（需要人工判断）
        if not orig_status and replay_status:
            return replay_status >= 400

        return False
