"""AutonomousDiscoveryEngine: read, reason, execute, verify pipeline."""
from __future__ import annotations

import base64
import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from ._budget import _apply_execution_budget_profile, _get_execution_budget_settings, _plan_execution_budget, _summarize_execution_feedback  # noqa: F401

logger = logging.getLogger(__name__)

from ._common import *  # noqa: F401,F403
from ._budget import *  # noqa: F401,F403
from ..budget_feedback_store import (
    load_budget_feedback_profile,
    persist_budget_feedback_profile,
    resolve_budget_learning_context,
)
from ..console_output import safe_print as print
from ..deployment_config_resolver import (
    build_deployment_config_snapshot,
    detect_deployment_config_drift,
    evaluate_deployment_drift_unlock,
    load_deployment_config_snapshot,
    persist_deployment_config_snapshot,
)
from ..llm_reasoning import _get_client, ReasoningClient, ReasoningClientError
from ..real_id_resolver import (
    QUALIBUG_UNRESOLVED_ID,
    extract_first_entity_id,
    resolve_real_id_from_documented_list,
)


class AutonomousDiscoveryEngine:
    """自主发现引擎：文档 → 假设 → 探针 → 确认"""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        project_id: str | None = None,
        root: Path | str | None = None,
    ):
        # Target resolution is governed and fail-closed: discovery must never
        # silently default to the QualiBug backend. Callers must declare a
        # target explicitly or via QUALIBUG_TARGET_BASE_URL / QUALIBUG_DEFAULT_BASE_URL.
        from ai_test_asset_center.target_endpoint import resolve_target_base_url

        self.base = resolve_target_base_url(base_url)
        self._project = str(
            project_id
            or os.environ.get("QUALIBUG_PROJECT_ID")
            or os.environ.get("QUALIBUG_DEFAULT_PROJECT_ID")
            or "default_project"
        ).strip() or "default_project"
        self._root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        self.client = _get_client()
        
        # Phase81: Read execution policy from Policy Registry (fallback to hardcoded defaults)
        from ai_test_asset_center.policy_wiring import get_policy_value
        model = get_policy_value("execution", "model", "deepseek-v4-pro")
        max_tokens = get_policy_value("execution", "max_tokens", 32768)
        http_timeout = get_policy_value("execution", "http_timeout_seconds", 10)
        
        try:
            current = getattr(self.client.config, "model", "") or getattr(self.client, "model", "")
            if not current or "chat" in str(current):
                self.client.config.model = model
        except Exception as e:
            import sys
            print(f"[discovery_engine] Model config set failed: {e}. Engine may produce degraded results.", file=sys.stderr)
            self._model_config_warning = str(e)
        self.client.config.max_tokens = max(getattr(self.client.config, 'max_tokens', 0), max_tokens)
        # ⛔ CRITICAL: Reader prompt (8000 chars) needs 150-200s on DeepSeek.
        # Removing this line causes silent loop death (API timeout → crash disguised as "process died").
        # DO NOT refactor/remove/relocate. Test: discovery_engine must have timeout_seconds ≥ 300.
        self.client.config.timeout_seconds = max(getattr(self.client.config, 'timeout_seconds', 120), 300)
        # Critical Configuration Guardrails (AGENTS.md): floors must never be
        # lowered; fail fast instead of silently degrading to API timeouts.
        assert self.client.config.timeout_seconds >= 300, "timeout too low"
        assert self.client.config.max_tokens >= 32768, "max_tokens too low"
        self._http_timeout = http_timeout
        self._production_blocked = str(os.environ.get("QUALIBUG_PRODUCTION", "")).lower() in {"1", "true", "yes", "on"}
        self.findings: list[DiscoveryFinding] = []
        self._budget_learning_context = resolve_budget_learning_context(
            project_id=os.environ.get("QUALIBUG_PROJECT_ID"),
        )
        self._deployment_config_snapshot = build_deployment_config_snapshot(self._budget_learning_context)
        self._previous_deployment_config_snapshot = load_deployment_config_snapshot(
            self._deployment_config_snapshot.get(
                "project_id",
                os.environ.get("QUALIBUG_DEFAULT_PROJECT_ID", "default_project"),
            )
        )
        self._deployment_config_drift = detect_deployment_config_drift(
            self._deployment_config_snapshot,
            self._previous_deployment_config_snapshot,
        )
        self._deployment_drift_unlock = evaluate_deployment_drift_unlock(
            self._deployment_config_snapshot,
            self._deployment_config_drift,
        )
        self._budget_feedback_summary: dict[str, Any] = load_budget_feedback_profile(self._budget_learning_context)
        # Set by LoopRuntime; emits semantic stage transitions without coupling
        # the engine to a specific scheduler implementation.
        self.progress_callback = None

        # Apply only an explicit project guard; never inject benchmark-specific context.
        self._inject_context()

        # ── Multi-module credential manager (enterprise) ──
        self._tokens: dict[str, str] = {}  # legacy single-service (role → token)
        self._credential_manager = None    # EnterpriseCredentialManager (service×role)
        self._service_tokens: dict[str, dict[str, str]] = {}  # service → {role: token}

        self._tokens_authentic: bool = False
        self._auth_warnings: list[str] = []

        # Runtime auth is optional. In production mode discovery is dry/blocked.
        if not self._production_blocked:
            # Try multi-module credential manager first
            self._init_multi_module_auth()
            if self._service_tokens:
                self._tokens_authentic = True
            else:
                # Fall back to legacy single-service
                authenticated = self._login()
                self._tokens_authentic = authenticated

        # Auto HAR recorder — captures all probe HTTP traffic automatically.
        # Users never need to provide HAR files; the system collects traffic
        # during every discovery run.
        self._har_entries: list[dict[str, Any]] = []
        self._har_error_patterns: list[dict[str, Any]] = []
        self._MAX_HAR_ENTRIES = 10000  # Prevent unbounded memory growth

    def _inject_context(self):
        """Apply an explicit project-scoped guard, never an embedded industry template.

        The platform must learn business constraints from the current project
        artifact/configuration.  A deployment may supply a concise, reviewed
        guard via ``QUALIBUG_PROJECT_CONTEXT_GUARD`` (for example, an explicit
        single-tenant statement).  With no guard configured we leave the shared
        Reasoner prompt untouched rather than injecting MES assumptions.
        """
        guard = os.environ.get("QUALIBUG_PROJECT_CONTEXT_GUARD", "").strip()
        if not guard:
            return
        from ai_test_asset_center import reasoner_prompt
        guard_key = f"_context_guard_{hash(guard)}"
        if getattr(reasoner_prompt, guard_key, False):
            return
        reasoner_prompt.REASONER_SYSTEM_PROMPT = (
            "PROJECT-SCOPED, REVIEWED CONTEXT GUARD:\n"
            + guard[:2000]
            + "\n\n"
            + reasoner_prompt.REASONER_SYSTEM_PROMPT
        )
        setattr(reasoner_prompt, guard_key, True)

    def _init_multi_module_auth(self):
        """Initialize multi-module credentials via EnterpriseCredentialManager.

        For enterprise scenarios with multiple independent services (e.g. order-service,
        quality-service, finance-service), each service has its own base URL, login API,
        and credentials. This method loads them and acquires real tokens.
        """
        try:
            from ai_test_asset_center.enterprise_credential_manager import EnterpriseCredentialManager
            mgr = EnterpriseCredentialManager(self._project, self._root)
            mgr.load_legacy_fallback()
            mgr.load_from_env()
            # Also try loading from multi_service_config.json
            config_path = (self._root / "platform_workspace" /
                          self._project / "multi_service_config.json")
            if config_path.exists():
                mgr.load_from_file(config_path)
            mgr.load_from_env()  # env vars override file config

            # Acquire real tokens for all services
            results = mgr.login_all_services()
            if results:
                self._credential_manager = mgr
                # Build service_tokens from the credential manager
                for svc in mgr.store.list_services():
                    self._service_tokens.setdefault(svc, {})
                    for role in ("admin", "viewer"):
                        token = mgr.get_token(svc, role)
                        if token:
                            self._service_tokens[svc][role] = token
                svc_count = len(self._service_tokens)
                if svc_count:
                    print(f"  [OK] Multi-module auth initialized: "
                          f"{svc_count} service(s) configured", flush=True)
        except ImportError as import_error:
            warning = f"enterprise_credential_manager_unavailable:{import_error}"
            self._auth_warnings.append(warning)
            logger.warning("%s", warning)
            print(f"  [WARN] {warning}", flush=True)

    def _login(self):
        try:
            admin_user = os.environ.get("QUALIBUG_ADMIN_USER", "")
            admin_pass = os.environ.get("QUALIBUG_ADMIN_PASS", "")
            if not admin_user or not admin_pass:
                logger.info("QUALIBUG_ADMIN_USER/PASS not set — skipping auth login")
                print(f"  [INFO] QUALIBUG_ADMIN_USER/PASS not set — skipping auth login", flush=True)
                return False
            r = self._http("POST", "/api/auth/login",
                          data={"username": admin_user, "password": admin_pass},
                          no_auth=True)
            token = (r.get("data", {}) or {}).get("accessToken", "")
            if token:
                self._tokens["admin"] = token
                logger.info("Real admin token obtained from /api/auth/login")
                print(f"  [OK] Real admin token obtained from /api/auth/login", flush=True)
                # Attempt viewer token — try same credentials with a different role
                # If the system has role-specific endpoints, try to get a viewer token
                viewer_user = os.environ.get("QUALIBUG_VIEWER_USER", "")
                viewer_pass = os.environ.get("QUALIBUG_VIEWER_PASS", "")
                if viewer_user and viewer_pass:
                    try:
                        vr = self._http("POST", "/api/auth/login",
                                       data={"username": viewer_user, "password": viewer_pass},
                                       no_auth=True)
                        vtoken = (vr.get("data", {}) or {}).get("accessToken", "")
                        if vtoken:
                            self._tokens["viewer"] = vtoken
                            logger.info("Real viewer token obtained")
                            print(f"  [OK] Real viewer token obtained", flush=True)
                            return True
                        self._auth_warnings.append("viewer_login_returned_no_token")
                    except Exception as viewer_error:
                        warning = f"viewer_login_failed:{type(viewer_error).__name__}:{viewer_error}"
                        self._auth_warnings.append(warning)
                        logger.warning("%s", warning)
                        print(f"  [WARN] {warning}", flush=True)
                # Fallback 1: explicit viewer token from env var
                env_viewer_token = os.environ.get("QUALIBUG_VIEWER_TOKEN", "").strip()
                if env_viewer_token:
                    self._tokens["viewer"] = env_viewer_token
                    logger.info("Using QUALIBUG_VIEWER_TOKEN for viewer role")
                    print(f"  [OK] Using QUALIBUG_VIEWER_TOKEN for viewer role", flush=True)
                else:
                    self._auth_warnings.append("viewer_identity_missing")
                    print(
                        "  [WARN] Viewer identity missing; role-differential tests will be SKIPPED.",
                        flush=True,
                    )
                return True
            # Real token not obtained — do NOT use synthetic tokens.
            # Without real auth, all permission/role tests are unreliable.
            print(f"  [WARN] /api/auth/login returned no token.", flush=True)
            print(f"  [WARN] Permission/role-based tests will be SKIPPED.", flush=True)
            print(f"  [WARN] Set QUALIBUG_ADMIN_USER / QUALIBUG_ADMIN_PASS for real auth.", flush=True)
        except Exception as login_err:
            print(f"  [WARN] /api/auth/login failed ({login_err}).", flush=True)
            print(f"  [WARN] Permission/role-based tests will be SKIPPED.", flush=True)
        # No synthetic tokens — leave tokens empty so probes fail early
        return False

    def _http(self, method: str, path: str, data=None, no_auth=False, role="admin",
              service: str = ""):
        if getattr(self, "_production_blocked", False) or str(os.environ.get("QUALIBUG_PRODUCTION", "")).lower() in {"1", "true", "yes", "on"}:
            return {"_http": 0, "_error": "blocked_by_production_safety_gate", "blocked_action": "http_request"}
        url = path if path.startswith("http") else f"{self.base}{path}"
        # Fix double /api prefix
        url = url.replace("/api/api/", "/api/")
        headers = {"Content-Type": "application/json"}
        # ── Auth routing: multi-module → service×role; legacy → role ──
        if not no_auth:
            token = ""
            if service and self._service_tokens:
                # Multi-module: find token for specific service×role
                svc_tokens = self._service_tokens.get(service, {})
                token = svc_tokens.get(role, "")
                if not token and self._credential_manager:
                    token = self._credential_manager.get_token(service, role)
            if not token and role in self._tokens:
                # Legacy single-service
                token = self._tokens[role]
            if role == "viewer" and not token:
                return {
                    "_http": 0,
                    "_error": "role_token_missing:viewer",
                    "blocked_action": "http_request",
                    "_request": {"method": method, "url": url, "role": role, "no_auth": no_auth},
                }
            if token:
                headers["Authorization"] = f"Bearer {token}"
        body_bytes = json.dumps(data).encode() if data else None
        request_snapshot = {
            "method": method, "url": url,
            "has_body": data is not None,
            "role": role,
            "no_auth": no_auth,
        }
        retry_count = 0
        max_retries = 3
        retryable_statuses = {429, 500, 502, 503, 504}
        t_start = time.time()
        last_result: dict[str, Any] = {}
        while True:
            req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=getattr(self, '_http_timeout', 10)) as resp:
                    resp_body = resp.read()
                    resp_text = resp_body.decode("utf-8", errors="replace")
                    try:
                        parsed = json.loads(resp_body)
                    except (json.JSONDecodeError, TypeError):
                        parsed = {"_raw_body": resp_text[:2000]}
                    result = {"_http": resp.status, "_request": request_snapshot, **parsed}
                    self._record_har(method, url, request_snapshot, resp.status,
                                     resp_text[:5000],
                                     time.time() - t_start, retry_count)
                    return result
            except urllib.error.HTTPError as e:
                status = e.code
                err_body = e.read().decode()[:500]
                if status in retryable_statuses and retry_count < max_retries:
                    retry_count += 1
                    retry_after = e.headers.get("Retry-After", "")
                    try:
                        wait_s = int(retry_after) if retry_after else 2 ** retry_count
                    except ValueError:
                        wait_s = 2 ** retry_count
                    jitter = random.uniform(0.5, 1.5)
                    time.sleep(wait_s * jitter)
                    continue
                last_result = {"_http": status, "_request": request_snapshot,
                               "_error": err_body, "_retries": retry_count}
                self._record_har(method, url, request_snapshot, status,
                                 err_body, time.time() - t_start, retry_count)
                return last_result
            except Exception as e:
                if retry_count < max_retries:
                    retry_count += 1
                    time.sleep((2 ** retry_count) * random.uniform(0.5, 1.5))
                    continue
                last_result = {"_http": 0, "_request": request_snapshot,
                               "_error": str(e), "_retries": retry_count}
                self._record_har(method, url, request_snapshot, 0,
                                 str(e), time.time() - t_start, retry_count)
                return last_result

    def _record_har(self, method: str, url: str, request_snapshot: dict,
                    status: int, response_body: str,
                    elapsed_s: float, retries: int) -> None:
        """Automatically record every HTTP probe into an in-memory HAR log."""
        har_entry = {
            "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "time": elapsed_s * 1000,
            "request": {
                "method": method,
                "url": url,
                "headers": [
                    {"name": k, "value": v} for k, v in {
                        "Content-Type": "application/json",
                        "role": request_snapshot.get("role", "admin"),
                        "no_auth": str(request_snapshot.get("no_auth", False)),
                    }.items()
                ],
                "postData": {"text": request_snapshot.get("has_body", False) and "{}" or ""},
            },
            "response": {
                "status": status,
                "content": {
                    "mimeType": "application/json",
                    "text": response_body[:5000],
                },
            },
            "timings": {"send": 1, "wait": max(0, (elapsed_s * 1000) - 2), "receive": 1},
            "_role": request_snapshot.get("role", "admin"),
            "_retries": retries,
        }
        self._har_entries.append(har_entry)
        # FIFO eviction to prevent unbounded memory growth
        if len(self._har_entries) > self._MAX_HAR_ENTRIES:
            self._har_entries = self._har_entries[-self._MAX_HAR_ENTRIES:]

        # Also record error patterns for log analysis
        if status >= 400 and response_body:
            self._har_error_patterns.append({
                "endpoint": url,
                "method": method,
                "status": status,
                "response_body": response_body[:2000],
                "role": request_snapshot.get("role", "admin"),
            })
            if len(self._har_error_patterns) > self._MAX_HAR_ENTRIES // 2:
                self._har_error_patterns = self._har_error_patterns[-self._MAX_HAR_ENTRIES // 2:]

    def _fill_template(self, template: str, **kwargs) -> str:
        """Safe template filling using str.replace (avoids .format() JSON conflicts)"""
        result = template
        for key, value in kwargs.items():
            result = result.replace("{" + key + "}", str(value or ""))
        return result

    def _emit_progress(self, stage: str, detail: str = "") -> None:
        """Emit durable-loop progress; callback failure is a real runtime failure."""
        if self.progress_callback is not None:
            self.progress_callback(stage, detail)

    # ==================================================================
    # Stage 1: Reader — 从文档提取业务事实
    # ==================================================================

    def stage_read(self, prd_text: str, api_spec_text: str, project_context: dict = None) -> dict:
        if not self.client.config.enabled:
            return {"error": "LLM not configured"}

        from ai_test_asset_center.reader_prompt import READER_BUSINESS_WORLD_PROMPT, READER_SYSTEM_PROMPT
        
        # Phase79+: Inject structured project context alongside raw text
        context_hint = "{}"
        if project_context and project_context.get("entities"):
            entities_summary = []
            for e in project_context["entities"][:30]:
                alias = e.get("entity_alias", "?")
                state_f = e.get("state_fields", [])
                amt_f = e.get("amount_fields", [])
                qty_f = e.get("quantity_fields", [])
                entities_summary.append(
                    f"- {alias}: states={state_f[:3]}, amounts={amt_f[:3]}, quantities={qty_f[:3]}, confidence={e.get('confidence', 0):.0%}"
                )
            apis_summary = []
            for a in project_context.get("apis", [])[:20]:
                apis_summary.append(
                    f"  {a.get('method','?')} {a.get('path','?')} → {a.get('capability','?')}"
                )
            context_hint = json.dumps({
                "pre_extracted_entities": entities_summary,
                "pre_mapped_apis": apis_summary,
                "total_entities": len(project_context.get("entities", [])),
                "total_apis": len(project_context.get("apis", [])),
            }, ensure_ascii=False, default=str)[:3000]

        # Structured project summary (business rules / state machine / permission
        # matrix) extracted deterministically from the same source texts. This is
        # an additive Reader hint and must never block the pipeline.
        try:
            from ai_test_asset_center.project_summary_builder import build_project_summary

            project_summary = build_project_summary(prd_text, api_spec_text)
            if project_summary and any(project_summary.values()):
                context_hint = json.dumps({
                    **(json.loads(context_hint) if context_hint and context_hint != "{}" else {}),
                    "project_summary": project_summary,
                }, ensure_ascii=False, default=str)[:3000]
        except Exception:
            pass

        prompt = self._fill_template(READER_BUSINESS_WORLD_PROMPT,
            documents=prd_text[:8000],
            api_contracts=api_spec_text[:8000],
            current_matches=context_hint)
        try:
            raw = self.client._chat(prompt, system_prompt=READER_SYSTEM_PROMPT)
            return self.client._parse_json(raw)
        except Exception as e:
            # Truncation salvage: when the LLM output is cut mid-object (e.g. by a
            # provider output cap), try to recover a partial but usable world by
            # closing the JSON progressively. Falls through to cache degrade on
            # total failure, so this never blocks the pipeline.
            salvaged = self._salvage_reader_output(raw if 'raw' in dir() else "", str(e))
            if salvaged is not None:
                print(f"  [WARN] Reader JSON truncated, salvaged {len(salvaged.get('entities', []))} entities")
                return salvaged
            return {"error": str(e)[:300]}

    @staticmethod
    def _salvage_reader_output(raw_response: str, err_msg: str) -> dict | None:
        """Best-effort recovery of a Reader world dict from a truncated LLM response.

        Tries: (1) extract content, strip code fences; (2) json.loads; (3) progressively
        close truncated JSON. Returns a dict with whatever fields survived, or None.
        """
        if not raw_response:
            return None
        try:
            outer = json.loads(raw_response)
            content = outer["choices"][0]["message"]["content"]
        except Exception:
            content = raw_response  # raw_response may already be the content string
        if not isinstance(content, str) or not content.strip():
            return None
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].lstrip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        # Direct parse first
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        # Stack-based closure for truncated JSON: track open {/[ and string state,
        # then close them in reverse order. Handles mid-string truncation.
        try:
            stack = []
            in_str = False
            escape = False
            for ch in cleaned:
                if escape:
                    escape = False
                    continue
                if ch == '\\':
                    escape = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch in '{[':
                    stack.append(ch)
                elif ch == '}' and stack and stack[-1] == '{':
                    stack.pop()
                elif ch == ']' and stack and stack[-1] == '[':
                    stack.pop()
            suffix = ''
            if in_str:
                suffix += '"'  # close the dangling string
            for opener in reversed(stack):
                suffix += '}' if opener == '{' else ']'
            if suffix:
                parsed = json.loads(cleaned + suffix)
                if isinstance(parsed, dict):
                    if any(k in parsed for k in ("entities", "inferred_industries", "documented_rules")):
                        return parsed
        except (json.JSONDecodeError, Exception):
            pass
        return None


    # ==================================================================
    # Stage 2: Reasoner — 从事实生成假设
    # Stage 2: Reasoner — 11 engines parallel, max 4 workers, independent clients
    # ==================================================================
    def stage_reason_all(self, reader_output: dict, prd_text: str, api_spec: str,
                         prior_findings: list[dict] = None) -> list[dict]:
        """Run ALL reasoner engines in parallel with thread-safe independent clients.

        11 engines, max 4 parallel workers (configurable via QUALIBUG_REASONER_MAX_WORKERS).
        Each engine gets its own ReasoningClient, 2 attempts max.
        JSON truncation recovered via raw_decode — complete hypotheses salvaged.
        """
        from ai_test_asset_center.stage_reason_all_v2 import _stage_reason_all_v2
        return _stage_reason_all_v2(self, prd_text, api_spec, reader_output, prior_findings)

    def _build_route_map(self, api_spec_text: str = "") -> dict:
        """Build a route lookup table from API documents.
        
        Uses RouteCatalogBuilder to parse OpenAPI, Swagger, Markdown, etc.
        Priority: in-memory spec > target server > local file.
        """
        import re, json
        from ai_test_asset_center.route_catalog_builder import RouteCatalogBuilder
        
        route_map = {}
        spec_texts = []
        
        # 1. Use in-memory API document if provided
        if api_spec_text:
            spec_texts.append(api_spec_text)
        
        # 2. Try fetching from target server (as fallback)
        if not spec_texts:
            try:
                r = self._http("GET", "/openapi.json", no_auth=True)
                if isinstance(r, dict) and "paths" in r:
                    spec_texts.append(json.dumps(r))
            except Exception as exc:
                logger.warning(
                    "discovery: OpenAPI spec获取失败，将无法生成API探针",
                    extra={"error_code": "QB-D003", "context": {"target": getattr(self, '_base_url', '?'), "error": str(exc)[:200]}},
                )
        
        if not spec_texts:
            return route_map
        
        # Build unified catalog from all sources
        builder = RouteCatalogBuilder()
        entries = builder.build(*spec_texts)
        route_map = builder.to_route_map()
        
        # Log catalog summary
        summary = builder.to_summary()
        if summary["total_routes"] > 0:
            pass  # Summary is logged by caller
        
        return route_map

    def _resolve_call(self, path: str, method: str, route_map: dict) -> dict | None:
        """Match an LLM-generated path+method to the best actual API route.
        
        Enhanced 4-level matching (ordered by confidence):
        1. Exact match (path + method)
        2. Normalized-param exact match ({id} ≈ {materialId} ≈ {material_id})
        3. Segment edit-distance fuzzy match (±1 segment tolerance)
        4. Cross-method fuzzy match (last resort)
        """
        import re

        path_candidates = self._candidate_route_paths(path, method, route_map)

        # === Level 1: Exact match ===
        for candidate_path in path_candidates:
            key = f"{method} {candidate_path}"
            if key in route_map:
                return route_map[key]
        
        # === Level 2: Normalized-param exact match ===
        # Replace all {param} → {_} so {id}/{materialId}/{material_id} are equivalent
        for candidate_path in path_candidates:
            llm_normalized = re.sub(r'\{[^}]+\}', '{_}', candidate_path)
            for _key, info in route_map.items():
                if info["method"] != method:
                    continue
                route_normalized = re.sub(r'\{[^}]+\}', '{_}', info["path_pattern"])
                if llm_normalized == route_normalized:
                    return info
        
        llm_parts = self._split_path_segments(path_candidates[0] if path_candidates else path)
        
        # === Level 3: Segment edit-distance fuzzy (same method, ±1 segment tolerance) ===
        best_score = -1.0
        best_info = None
        best_literal = 0
        for _key, info in route_map.items():
            if info["method"] != method:
                continue
            route_parts = [p for p in info["path_pattern"].split("/") if p]
            len_diff = abs(len(llm_parts) - len(route_parts))
            if len_diff > 1:
                continue
            score = self._segment_similarity(llm_parts, route_parts)
            literal = self._literal_match_count(llm_parts, route_parts)
            adjusted = score - len_diff * 0.3
            if adjusted > best_score:
                best_score = adjusted
                best_info = info
                best_literal = literal
        
        if best_info is not None and best_score >= 0.40 and best_literal >= 2:
            return best_info
        
        # === Level 4: Cross-method fuzzy (any method, ±1 segment tolerance) ===
        best_score = -1.0
        best_info_cross = None
        best_literal = 0
        for _key, info in route_map.items():
            route_parts = [p for p in info["path_pattern"].split("/") if p]
            len_diff = abs(len(llm_parts) - len(route_parts))
            if len_diff > 1:
                continue
            score = self._segment_similarity(llm_parts, route_parts)
            literal = self._literal_match_count(llm_parts, route_parts)
            adjusted = score - len_diff * 0.3
            if adjusted > best_score:
                best_score = adjusted
                best_info_cross = info
                best_literal = literal
        
        if best_info_cross is not None and best_score >= 0.50 and best_literal >= 2:
            return best_info_cross

        # === Level 5: OperationId matching (Enhanced P2: 1.2) ===
        # Match based on OpenAPI operationId or semantic operation name
        operation_id_hints = self._extract_operation_id_hints(path, method)
        if operation_id_hints:
            for _key, info in route_map.items():
                route_op_id = str(info.get("operation_id") or info.get("operationId") or "").lower()
                if not route_op_id:
                    continue
                for hint in operation_id_hints:
                    if hint in route_op_id or route_op_id in hint:
                        return info
                    # Fuzzy operationId match (e.g., "getUser" ≈ "get_user" ≈ "get-user")
                    hint_normalized = hint.replace("_", "").replace("-", "").lower()
                    route_normalized = route_op_id.replace("_", "").replace("-", "").lower()
                    if hint_normalized == route_normalized:
                        return info

        # === Level 6: Semantic schema matching (Enhanced P2: 1.2) ===
        # Match based on request/response schema similarity
        schema_hints = self._extract_schema_hints(path, method)
        if schema_hints:
            best_schema_score = 0.0
            best_schema_info = None
            for _key, info in route_map.items():
                if info.get("method") != method:
                    continue
                route_schema = info.get("request_schema") or info.get("response_schema") or {}
                if not isinstance(route_schema, dict):
                    continue
                # Compare schema field names
                route_fields = set()
                for schema_key in ("properties", "fields", "parameters"):
                    schema_part = route_schema.get(schema_key, {})
                    if isinstance(schema_part, dict):
                        route_fields.update(schema_part.keys())
                    elif isinstance(schema_part, list):
                        for item in schema_part:
                            if isinstance(item, dict) and item.get("name"):
                                route_fields.add(item["name"])
                if not route_fields:
                    continue
                # Calculate field overlap
                overlap = len(schema_hints & route_fields)
                if overlap > 0:
                    score = overlap / max(len(schema_hints), len(route_fields), 1)
                    if score > best_schema_score:
                        best_schema_score = score
                        best_schema_info = info
            if best_schema_info is not None and best_schema_score >= 0.40:
                return best_schema_info

        # === Level 7: Runtime route discovery fallback (Enhanced P2: 1.2) ===
        # Mark as unresolved for potential runtime discovery
        # This doesn't return a match but records the attempt for learning
        self._record_unresolved_route(path, method, route_map)

        return None

    def _extract_operation_id_hints(self, path: str, method: str) -> list[str]:
        """Extract operationId hints from path and method."""
        hints = []
        # Convert path to camelCase operation name
        # e.g., /api/users/{id}/orders → getUserOrders, listUserOrders
        parts = [p for p in path.split("/") if p and not p.startswith("{")]
        if parts:
            # Remove common prefixes
            parts = [p for p in parts if p.lower() not in ("api", "v1", "v2", "v3")]
            if parts:
                # Build operation name
                name_parts = []
                for p in parts:
                    # Split by hyphen/underscore and capitalize
                    for sub in p.replace("-", "_").split("_"):
                        if sub:
                            name_parts.append(sub.capitalize())
                if name_parts:
                    base_name = "".join(name_parts)
                    # Add method prefix
                    method_prefix = {
                        "GET": "get" if len(parts) == 1 else "list",
                        "POST": "create",
                        "PUT": "update",
                        "PATCH": "patch",
                        "DELETE": "delete",
                    }.get(method.upper(), method.lower())
                    hints.append(f"{method_prefix}{base_name}")
                    hints.append(base_name.lower())
        return hints

    def _extract_schema_hints(self, path: str, method: str) -> set[str]:
        """Extract schema field hints from path segments."""
        hints = set()
        # Extract entity names from path
        parts = [p for p in path.split("/") if p and not p.startswith("{")]
        for p in parts:
            if p.lower() not in ("api", "v1", "v2", "v3"):
                # Add singular and plural forms
                hints.add(p.lower())
                if p.endswith("s"):
                    hints.add(p[:-1].lower())
                else:
                    hints.add(f"{p}s".lower())
                # Add common field names
                hints.add(f"{p.lower()}_id")
                hints.add(f"{p.lower()}id")
                hints.add("id")
                hints.add("name")
                hints.add("status")
        return hints

    def _record_unresolved_route(self, path: str, method: str, route_map: dict) -> None:
        """Record unresolved route for learning and runtime discovery."""
        if not hasattr(self, "_unresolved_routes"):
            self._unresolved_routes = []
        self._unresolved_routes.append({
            "path": path,
            "method": method,
            "route_map_size": len(route_map) if route_map else 0,
        })

    @staticmethod
    def _split_path_segments(path: str) -> list[str]:
        return [segment for segment in str(path or "").split("/") if segment]

    @staticmethod
    def _is_path_param(segment: str) -> bool:
        text = str(segment or "")
        return text.startswith("{") and text.endswith("}")

    @classmethod
    def _path_segments_compatible(cls, left: list[str], right: list[str]) -> bool:
        if len(left) != len(right):
            return False
        for left_part, right_part in zip(left, right):
            if left_part == right_part:
                continue
            if cls._is_path_param(left_part) and cls._is_path_param(right_part):
                continue
            return False
        return True

    def _candidate_route_paths(self, path: str, method: str, route_map: dict | None) -> list[str]:
        normalized_path = "/" + str(path or "").lstrip("/")
        if not isinstance(route_map, dict) or not route_map:
            return [normalized_path]

        candidates: list[str] = []

        def _append(candidate: str) -> None:
            clean = "/" + str(candidate or "").lstrip("/")
            if clean not in candidates:
                candidates.append(clean)

        _append(normalized_path)
        raw_parts = self._split_path_segments(normalized_path)
        for info in route_map.values():
            if not isinstance(info, dict):
                continue
            if str(info.get("method") or "").upper() != str(method or "").upper():
                continue
            route_path = str(info.get("path_pattern") or info.get("path") or "").strip()
            if not route_path:
                continue
            route_parts = self._split_path_segments(route_path)
            if self._path_segments_compatible(raw_parts, route_parts):
                _append(route_path)
                continue
            if raw_parts and len(route_parts) > len(raw_parts) and self._path_segments_compatible(raw_parts, route_parts[-len(raw_parts):]):
                _append(route_path)
        return candidates

    def _route_resource_keywords(self, route_map: dict | None) -> list[str]:
        if not isinstance(route_map, dict):
            return []
        counts: dict[str, int] = {}
        stopwords = {"api", "internal", "admin", "system", "public", "open"}
        for info in route_map.values():
            if not isinstance(info, dict):
                continue
            if str(info.get("method") or "").upper() != "GET":
                continue
            for part in self._split_path_segments(str(info.get("path_pattern") or "")):
                token = part.lower()
                if not token or token in stopwords or self._is_path_param(token):
                    continue
                if token.startswith("v") and token[1:].isdigit():
                    continue
                counts[token] = counts.get(token, 0) + 1
        return sorted(counts, key=lambda item: (-counts[item], -len(item), item))

    @staticmethod
    def _segment_similarity(a_parts: list[str], b_parts: list[str]) -> float:
        """Segment-level similarity with parameter-slot awareness.
        
        Scores per aligned segment pair:
        - 1.0: exact literal match (e.g. "materials" == "materials")
        - 0.8: both are {param} slots (e.g. {id} vs {materialId})
        - 0.5: one is a {param} slot, the other is a static segment
        - 0.0: literal mismatch
        
        Uses difflib.SequenceMatcher for alignment, which handles
        middle insertions/deletions (e.g. LLM adds /v1/ in the middle).
        """
        import re
        from difflib import SequenceMatcher

        def _is_param(s: str) -> bool:
            return bool(re.match(r'^\{[^}]+\}$', s))

        def _seg_score(a_seg: str, b_seg: str) -> float:
            if a_seg == b_seg:
                return 1.0
            if _is_param(a_seg) and _is_param(b_seg):
                return 0.8
            if _is_param(a_seg) or _is_param(b_seg):
                return 0.5
            return 0.0

        # Build a custom scoring matrix for difflib
        # Map segments to single chars for SequenceMatcher compatibility,
        # then use our segment-aware scoring on the matched blocks.
        matcher = SequenceMatcher(None, a_parts, b_parts)
        total_score = 0.0
        total_len = max(len(a_parts), len(b_parts))
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                # All segments in this block match exactly → score each
                for k in range(i2 - i1):
                    total_score += _seg_score(a_parts[i1 + k], b_parts[j1 + k])
            elif tag == 'replace':
                # Compare each pair in the replacement block
                block_len = max(i2 - i1, j2 - j1)
                for k in range(block_len):
                    a_seg = a_parts[i1 + k] if i1 + k < i2 else None
                    b_seg = b_parts[j1 + k] if j1 + k < j2 else None
                    if a_seg is not None and b_seg is not None:
                        total_score += _seg_score(a_seg, b_seg)
                    # else: insert/delete within replace block → score 0
            # insert/delete blocks score 0 (already accounted in total_len)

        return total_score / max(total_len, 1)

    @staticmethod
    def _literal_match_count(a_parts: list[str], b_parts: list[str]) -> int:
        """Count exact literal matches between two path segment lists.
        
        Only counts non-parameter, case-sensitive literal matches.
        Used as a hard constraint to prevent noise paths from matching.
        """
        import re
        return sum(1 for a in a_parts for b in b_parts
                   if a == b and not re.match(r'^\{[^}]+\}$', a))

    def _fetch_real_id(self, param_name: str, resolved: dict, route_map: dict) -> str:
        """Fetch a real entity ID from the target API for test data.
        
        Multi-strategy fallback (ordered by reliability):
        1. GET list endpoint → extract first entity ID
        2. GET list endpoint with pagination params → extract from page
        3. Probe parent resource → use parent's data to find child entity
        4. Return sentinel QUALIBUG_UNRESOLVED_ID so caller knows data is missing
           (instead of silent fallback to "1" which hits non-existent resources)
        """
        return resolve_real_id_from_documented_list(
            str(resolved.get("path_pattern") or ""),
            param_name,
            self._try_extract_id_from_list,
        )

    def _try_extract_id_from_list(self, list_path: str, param_name: str) -> str | None:
        """GET a list endpoint and extract the first entity's ID.

        Tries admin first, then other authenticated roles when admin gets
        401/403/empty — cross-user fixtures often live under non-admin actors.
        Returns the ID string on success, None if unreachable or no entities found.
        """
        roles: list[str] = []
        for role in ("admin", "viewer", "normal_user", "buyer", "doctor", "nurse", "operator"):
            if role not in roles:
                roles.append(role)
        for role in list(getattr(self, "_tokens", {}) or {}):
            if role and role not in roles:
                roles.append(str(role))
        for svc_roles in (getattr(self, "_service_tokens", {}) or {}).values():
            if not isinstance(svc_roles, dict):
                continue
            for role in svc_roles:
                if role and role not in roles:
                    roles.append(str(role))

        for role in roles:
            try:
                r = self._http("GET", list_path, role=role)
                status = int(r.get("_http", 0) or 0)
                if status in {401, 403}:
                    continue
                if status != 200:
                    continue
                body = {k: v for k, v in r.items() if not k.startswith("_")}
                entity_id = extract_first_entity_id(body, param_name)
                if entity_id:
                    return entity_id
            except Exception:
                continue
        return None

    def _generate_test_values(self, param_name: str, resolved: dict, route_map: dict) -> list[str]:
        """Generate test value variants: real ID + boundaries + edge cases.
        
        Returns list of values to try: [real_id, "0", "-1", "", "null", "undefined", "99999999"]
        For uuid-type params, uses valid uuid-format boundary values instead of integers.
        """
        values = []
        real_id = self._fetch_real_id(param_name, resolved, route_map)
        if real_id and real_id != "1" and real_id != QUALIBUG_UNRESOLVED_ID:
            values.append(real_id)
        
        # Detect if parameter is uuid-type (from OpenAPI schema format, param name, or real_id format)
        param_formats = resolved.get("path_param_formats", {}) if isinstance(resolved, dict) else {}
        param_format = str(param_formats.get(param_name, "")).lower()
        is_uuid_param = (
            param_format == "uuid"
            or "uuid" in param_name.lower()
            or bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', real_id or "", re.IGNORECASE))
        )
        
        if is_uuid_param:
            # UUID-type params: use valid-format uuid boundary values
            # (non-uuid values like "0" or "-1" cause PostgreSQL "invalid input syntax for type uuid")
            values.extend([
                "00000000-0000-0000-0000-000000000000",  # nil uuid
                "ffffffff-ffff-ffff-ffff-ffffffffffff",  # max uuid
                "00000000-0000-0000-0000-000000000001",  # near-nil
            ])
        else:
            # Integer/string params: standard boundary values
            values.extend(["0", "-1", "99999999", ""])
        
        # SQL injection / XSS probes (lightweight, safe for sandbox)
        values.append("' OR '1'='1")
        
        return values

    def _execute_verification(self, vm: dict, route_map: dict = None, async_delay: float = 0) -> dict:
        """Execute verification method using accurate route matching.
        
        Args:
            async_delay: seconds to wait between write operation and after-observer GET
                         (for eventually-consistent systems). Default 0 (no delay).
        """
        import re
        if route_map is None:
            route_map = {}
        
        calls = []
        # Try to get real IDs for path parameters from target API
        real_ids = {}
        for key in sorted(vm.keys()):
            value = str(vm[key])
            matches = re.findall(r'(GET|POST|PUT|DELETE)\s+(/(?:api|master|production|inventory|quality|planning|sales|purchase|warehouse|report|system|admin)/[\w\-\/{}]+)', value, re.IGNORECASE)
            for method, llm_path in matches:
                resolved = self._resolve_call(llm_path, method.upper(), route_map)
                if resolved:
                    actual_path = resolved["path_pattern"]
                    for param in resolved["path_params"]:
                        # Try to use a real ID from the target
                        if param not in real_ids:
                            real_ids[param] = self._fetch_real_id(param, resolved, route_map)
                        # If sentinel (unresolvable), fall back to "1" for the HTTP call
                        # but track that this is a synthetic ID → evidence quality degraded
                        # For uuid-type params, use nil uuid to avoid PostgreSQL type errors
                        rid = real_ids.get(param, "1")
                        if rid.startswith("QUALIBUG_"):
                            # Detect uuid-type param from OpenAPI schema format or param name
                            param_formats = resolved.get("path_param_formats", {}) if isinstance(resolved, dict) else {}
                            param_fmt = str(param_formats.get(param, "")).lower()
                            if param_fmt == "uuid" or "uuid" in param.lower():
                                rid = "00000000-0000-0000-0000-000000000000"
                            else:
                                rid = "1"
                        actual_path = actual_path.replace(f"{{{param}}}", rid)
                    synthetic_id = any(v.startswith("QUALIBUG_") for v in real_ids.values())
                    calls.append({"method": resolved["method"], "path": actual_path,
                        "llm_path": llm_path, "resolved": True, "source": key,
                        "synthetic_id": synthetic_id})
                else:
                    clean_path = re.sub(r'\{[^}]*\}', '1', llm_path)
                    calls.append({"method": method.upper(), "path": clean_path,
                        "llm_path": llm_path, "resolved": False, "source": key,
                        "synthetic_id": True, "unresolved_route": True})

        if not calls:
            for key in sorted(vm.keys()):
                value = str(vm[key])
                matches = re.findall(r'(/[A-Za-z0-9][\w\-{}]*(?:/[\w\-{}]+)+)', value)
                for path in matches:
                    resolved = self._resolve_call(path, "GET", route_map or {}) if route_map else None
                    resolved_path = str((resolved or {}).get("path_pattern") or self._candidate_route_paths(path, "GET", route_map or {})[0])
                    calls.append({"method": "GET", "path": resolved_path, "source": key,
                        "resolved": bool(resolved), "synthetic_id": not bool(resolved), "unresolved_route": not bool(resolved)})

        results = []
        prev_was_write = False
        for i, call in enumerate(calls[:10]):
            # Async delay: if previous call was a write and this is the after-observer GET
            if async_delay > 0 and prev_was_write and i >= 2:
                import time as _time
                _time.sleep(async_delay)
            
            path = call["path"]
            method = call["method"]
            prev_was_write = method in ("POST", "PUT", "DELETE", "PATCH")
            role_results = {}
            for role in ["admin", "viewer"]:
                r = self._http(call["method"], path, role=role)
                # Keep _error for evidence quality; _http captured as status, _request as request field
                body = {k: v for k, v in r.items() if k != "_request"}
                role_results[role] = {"status": r.get("_http", 0), "body": body,
                    "request": r.get("_request", {})}
            write_methods = {"POST", "PUT", "PATCH", "DELETE"}
            allow_unauth_write = str(os.environ.get("QUALIBUG_ALLOW_UNAUTH_WRITE_PROBES", "")).lower() in {"1", "true", "yes", "on"}
            if call["method"] in write_methods and not allow_unauth_write:
                # Never fire mutating unauthenticated probes by default.  If the
                # target has an auth bypass, such a probe could create/update/delete
                # real data.  Operators may opt in only for disposable sandboxes.
                role_results["no_auth"] = {
                    "status": 0,
                    "body": {
                        "_skipped_by_safety_gate": True,
                        "reason": "mutating unauthenticated probe requires QUALIBUG_ALLOW_UNAUTH_WRITE_PROBES=1",
                    },
                }
            else:
                r_noauth = self._http(call["method"], path, no_auth=True)
                body_noauth = {k: v for k, v in r_noauth.items() if k != "_request"}
                role_results["no_auth"] = {"status": r_noauth.get("_http", 0), "body": body_noauth,
                    "request": r_noauth.get("_request", {})}
            results.append({"call": f"{call['method']} {path}",
                "source_step": call["source"], "results": role_results,
                "resolved": call.get("resolved", False), "llm_path": call.get("llm_path", path),
                "synthetic_id": call.get("synthetic_id", False),
                "unresolved_route": call.get("unresolved_route", False)})

        return {"calls": results, "check_condition": vm.get("check", ""),
                "total_calls": len(results), "route_map_used": bool(route_map)}

    def stage_execute(self, hypotheses: list[dict], route_map: dict = None) -> list[dict]:
        """执行所有假设的验证方法 — P1: auto-inject before/after state observers + fixture construction"""
        import re
        results = []
        budget_settings = _get_execution_budget_settings()
        budget_settings["route_surface_size"] = len(route_map or {})
        budget_settings["drift_unlock_status"] = str(self._deployment_drift_unlock.get("status") or "not_required")
        budget_settings["drift_effective_unlock_level"] = str(self._deployment_drift_unlock.get("effective_unlock_level") or "normal")
        budget_settings["drift_severity"] = str(self._deployment_config_drift.get("severity") or "none")
        feedback_summary = self._budget_feedback_summary or _summarize_execution_feedback(self.findings)
        execution_plan, budget_summary = _plan_execution_budget(hypotheses, budget_settings, feedback_summary)
        deferred_count = sum(1 for item in execution_plan if item.get("budget_action") == "deferred")
        if budget_settings.get("enabled", True):
            tier_counts = {"A": 0, "B": 0, "C": 0, "DEFER": 0}
            for item in execution_plan:
                tier_counts[str(item.get("tier", "DEFER")).upper()] = tier_counts.get(str(item.get("tier", "DEFER")).upper(), 0) + 1
            print(
                f"  [OK] 动态预算: 总假设={budget_summary.get('total_hypotheses', 0)} "
                f"可执行={budget_summary.get('executable_count', 0)} 双来源={budget_summary.get('dual_source_count', 0)} "
                f"命中率={budget_summary.get('hit_rate', 0.0):.0%} "
                f"模式={self._deployment_config_snapshot.get('deployment_mode')}:{self._deployment_config_snapshot.get('learning_sync_mode')} "
                f"解锁={self._deployment_drift_unlock.get('status')}/{self._deployment_drift_unlock.get('effective_unlock_level')}",
                flush=True,
            )
            print(
                f"  [OK] 动态配额: A={tier_counts.get('A', 0)} B={tier_counts.get('B', 0)} "
                f"C={tier_counts.get('C', 0)} 延后={tier_counts.get('DEFER', 0)} "
                f"(执行比例={budget_summary.get('execution_ratio', 0.0):.0%}, A比例={budget_summary.get('tier_a_ratio', 0.0):.0%})",
                flush=True,
            )
        
        # Phase79+: Fixture Auto-Constructor for POST hypotheses
        _fixture_constructor = None
        try:
            from ai_test_asset_center.fixture_auto_constructor import FixtureAutoConstructor, FixtureObject
            _fixture_constructor = FixtureAutoConstructor()
        except ImportError as exc:
            logger.warning(
                "discovery: FixtureAutoConstructor不可用，POST假设将无法自动构造测试数据",
                extra={"error_code": "QB-D005", "context": {"error": str(exc)[:200]}},
            )
        
        from ai_test_asset_center.hypothesis_schema import validate_hypothesis
        for plan_item in execution_plan:
            raw_hypothesis = plan_item.get("hypothesis", {})
            budget_tier = str(plan_item.get("tier", "A")).upper()
            budget_action = str(plan_item.get("budget_action", "full"))
            # LLM output is untrusted.  One malformed hypothesis must never abort
            # the entire execution stage or be silently treated as a normal probe.
            validation = validate_hypothesis(raw_hypothesis)
            h = validation.normalized
            if not validation.valid:
                results.append({
                    "hypothesis_id": h.get("hypothesis_id", "?"),
                    "title": h.get("title", "invalid hypothesis"),
                    "verdict": validation.verdict,
                    "error": ";".join(validation.errors),
                    "budget_tier": budget_tier,
                    "budget_action": budget_action,
                    "evidence": {
                        "hypothesis_validation": {"verdict": validation.verdict, "errors": validation.errors},
                        "execution_budget": {"tier": budget_tier, "action": budget_action},
                    },
                })
                continue
            vm = h.get("verification_method", {})

            # ── P0: Auto-inject before/after GET observers for ALL write operations ──
            text = f"{h.get('title','')} {h.get('description','')} {h.get('expected_behavior','')}"
            # Detect HTTP method from vm or hypothesis text
            vm_method = str(vm.get("method", "")).upper()
            if not vm_method:
                step1 = str(vm.get("step1", "")).upper()
                if step1:
                    parts = step1.split(None, 1)
                    vm_method = parts[0] if parts else ""
            if not vm_method:
                if any(kw in text.lower() for kw in ("创建", "create", "新建", "post", "新增", "添加")):
                    vm_method = "POST"
                elif any(kw in text.lower() for kw in ("删除", "delete", "移除", "remove")):
                    vm_method = "DELETE"
                elif any(kw in text.lower() for kw in ("修改", "更新", "update", "编辑", "变更", "modify", "put", "patch")):
                    vm_method = "PUT"
            is_write = vm_method in ("POST", "PUT", "DELETE", "PATCH")
            
            if is_write and not vm.get("step2"):
                # Find a GET endpoint for this entity to use as before/after observer
                entity = str(h.get('entity') or h.get('source_entity') or '')
                normalized_entity = entity.lower().replace(' ', '_')
                get_path = None
                # First: try exact entity match from route_map
                for key in (route_map or {}):
                    if normalized_entity and 'GET' in key and normalized_entity in key.lower():
                        get_path = key
                        break
                # Second: try keyword match from hypothesis text
                if not get_path:
                    keywords = re.findall(r'[\u4e00-\u9fff\w]{2,}', text.lower()[:200])
                    for key in (route_map or {}):
                        if 'GET' in key and any(kw in key.lower() for kw in keywords[:10] if len(kw) >= 2):
                            get_path = key
                            break
                # Third: fallback to route-derived resource keywords instead of benchmark entities
                if not get_path:
                    for entity_name in self._route_resource_keywords(route_map):
                        for key in (route_map or {}):
                            if entity_name in key.lower() and "GET" in key:
                                get_path = key
                                break
                        if get_path:
                            break
                
                if get_path:
                    # Inject: step1=GET(before), step2=write_action, step3=GET(after)
                    new_vm = {"step1": get_path, "_before_observer": True}
                    for k, v in vm.items():
                        if not k.startswith("_"):
                            new_vm[f"step{len(new_vm)}"] = str(v) if k != "path" else v
                    new_vm[f"step{len(new_vm)}"] = get_path  # after observer
                    new_vm["_after_observer"] = True
                    # P2: Add cross-observer — fetch a related list endpoint for cross-validation
                    entity_parts = get_path.replace("GET ", "").strip("/").split("/")
                    if len(entity_parts) >= 2:
                        path_prefix = entity_parts[0] if entity_parts[0] else "api"
                        list_path = f"GET /{path_prefix}/{entity_parts[1]}"
                        if list_path != get_path:
                            for key in (route_map or {}):
                                if entity_parts[1] in key.lower() and "GET" in key and key != get_path:
                                    new_vm[f"step{len(new_vm)}"] = key
                                    new_vm["_cross_observer"] = True
                                    break
                    vm = new_vm
                    path_prefix = entity_parts[0] if len(entity_parts) >= 1 else ""
                    extra_observers = []
                    for key in (route_map or {}):
                        if "GET " not in key or key == get_path or key in str(new_vm):
                            continue
                        route_path = key.replace("GET ", "").strip()
                        route_parts = self._split_path_segments(route_path)
                        if path_prefix and route_parts and route_parts[0] == path_prefix:
                            extra_observers.append(key)
                    for extra_path in extra_observers[:2]:
                        new_vm[f"step{len(new_vm)}"] = extra_path
                        new_vm["_extra_observer"] = True

            # If no structured verification_method, try to extract paths from title + description
            if not (vm.get("path") or vm.get("step1")):
                text = f"{h.get('title','')} {h.get('description','')} {h.get('expected_behavior','')}"
                api_paths = re.findall(r'(/[A-Za-z0-9][\w\-{}]*(?:/[\w\-{}]+)+)', text)
                if api_paths:
                    first_path = api_paths[0]
                    resolved = self._resolve_call(first_path, "GET", route_map or {}) if route_map else None
                    first_path = str((resolved or {}).get("path_pattern") or self._candidate_route_paths(first_path, "GET", route_map or {})[0])
                    vm = {"step1": f"GET {first_path}"}
                else:
                    # Use entity-based path guess from route_map
                    entity = str(h.get('entity') or h.get('source_entity') or '')
                    normalized_entity = entity.lower().replace(' ', '_')
                    # Determine HTTP method from hypothesis wording
                    method = "GET"
                    if any(kw in text.lower() for kw in ("创建", "create", "新建", "post", "新增", "添加")):
                        method = "POST"
                    elif any(kw in text.lower() for kw in ("删除", "delete", "移除", "remove")):
                        method = "DELETE"
                    elif any(kw in text.lower() for kw in ("修改", "更新", "update", "编辑", "变更", "modify")):
                        method = "PUT"
                    # Try exact entity match first
                    for key in (route_map or {}):
                        if normalized_entity and normalized_entity in key.lower() and method in key:
                            vm = {"step1": key}
                            break
                    # Broader: match any route that contains keywords from the hypothesis
                    if not vm.get("step1"):
                        keywords = re.findall(r'[\u4e00-\u9fff\w]{2,}', text.lower()[:200])
                        for key in (route_map or {}):
                            key_lower = key.lower()
                            if method in key and any(kw in key_lower for kw in keywords[:10] if len(kw) >= 2):
                                vm = {"step1": key}
                                break
                    # Last resort: try any GET on a common entity
                    if not vm.get("step1"):
                        for entity_name in ("material", "bom", "order", "inventory", "routing", "work"):
                            for key in (route_map or {}):
                                if entity_name in key.lower() and "GET" in key:
                                    vm = {"step1": key}
                                    break
                            if vm.get("step1"):
                                break

            has_calls = bool(vm.get("path") or vm.get("step1"))
            if not has_calls:
                continue

            if budget_action == "deferred":
                results.append({
                    "hypothesis_id": h.get("hypothesis_id", "?"),
                    "title": h.get("title", "?")[:120],
                    "severity": h.get("severity", "P1"),
                    "expected_behavior": h.get("expected_behavior", ""),
                    "budget_tier": budget_tier,
                    "budget_action": budget_action,
                    "evidence": {
                        "calls": [],
                        "check_condition": vm.get("check", ""),
                        "total_calls": 0,
                        "route_map_used": bool(route_map),
                        "execution_budget": {"tier": budget_tier, "action": budget_action, "reason": "tier_quota_exhausted"},
                    },
                })
                continue

            execution_vm, async_delay = _apply_execution_budget_profile(vm, budget_tier, budget_settings)

            try:
                # P3: Enhanced fixture auto-construction for POST/PUT/PATCH/DELETE probes
                if _fixture_constructor:
                    method = execution_vm.get("method", "GET").upper()
                    # Detect method from step1 if not explicit
                    if method == "GET":
                        step1 = str(execution_vm.get("step1", ""))
                        parts = step1.split(None, 1)
                        if parts:
                            method = parts[0].upper()
                    
                    if method in ("POST", "PUT", "PATCH", "DELETE"):
                        entity = str(h.get("entity") or h.get("source_entity") or "")
                        path = str(execution_vm.get("path") or "")
                        entity_type = entity or path.strip("/").split("/")[-1].rstrip("s")
                        
                        # For DELETE: create a fixture first so we have something to delete
                        if method == "DELETE" and entity_type:
                            try:
                                # Find the POST endpoint for this entity type from route_map
                                fixture_path = None
                                for key in (route_map or {}):
                                    if "POST" in key and entity_type.lower() in key.lower():
                                        fixture_path = key
                                        break
                                if not fixture_path:
                                    fixture_path = f"/api/{entity_type}s" if not entity_type.endswith("s") else f"/api/{entity_type}"
                                
                                fixture_data = {"code": f"AUTO-DEL-{int(time.time())}", "name": f"Fixture for DELETE test", "spec": "auto"}
                                # Try creating via HTTP POST
                                self._http("POST", fixture_path.replace("POST ", "").strip(), fixture_data)
                            except Exception as exc:
                                logger.debug(
                                    f"discovery: DELETE fixture创建失败(best-effort) path={fixture_path}",
                                    extra={"context": {"path": fixture_path, "error": str(exc)[:150]}},
                                )
                        
                        if entity_type and method != "DELETE":
                            try:
                                from ai_test_asset_center.fixture_auto_constructor import SchemaAnalyzer
                                analyzer = SchemaAnalyzer()
                                dummy_schema = {"properties": {}, "required": []}
                                for k, v in h.items():
                                    if isinstance(v, str) and len(v) < 50:
                                        dummy_schema["properties"][k] = {"type": "string"}
                                fixture_obj = FixtureObject(
                                    entity_type=entity_type,
                                    object_id=f"AUTO-{entity_type}-{int(time.time())}",
                                    fields={},
                                    source="auto_constructed",
                                )
                                for fname in ["name", "code", "id", "status", "materialCode"]:
                                    fname_in_h = h.get(fname) or h.get(f"{entity_type}_{fname}")
                                    if fname_in_h:
                                        fixture_obj.fields[fname] = fname_in_h
                                    else:
                                        fixture_obj.fields[fname] = analyzer.generate_default_value(
                                            fname, dummy_schema.get("properties", {}).get(fname, {"type": "string"}))
                                fixture_path = f"/{entity_type}s" if not entity_type.endswith("s") else f"/{entity_type}"
                                self._http("POST", fixture_path, fixture_obj.fields)
                            except Exception as exc:
                                logger.debug(
                                    f"discovery: fixture构造失败(best-effort) entity={entity_type}",
                                    extra={"context": {"entity": entity_type, "error": str(exc)[:150]}},
                                )
                
                evidence = self._execute_verification(execution_vm, route_map, async_delay=async_delay)
                evidence["execution_budget"] = {
                    "tier": budget_tier,
                    "action": budget_action,
                    "async_delay": async_delay,
                    "deferred_total": deferred_count,
                }
                results.append({
                    "hypothesis_id": h.get("hypothesis_id", "?"),
                    "title": h.get("title", "?")[:120],
                    "severity": h.get("severity", "P1"),
                    "expected_behavior": h.get("expected_behavior", ""),
                    "budget_tier": budget_tier,
                    "budget_action": budget_action,
                    "evidence": evidence,
                })
            except Exception as e:
                results.append({
                    "hypothesis_id": h.get("hypothesis_id", "?"),
                    "title": h.get("title", "?")[:120],
                    "budget_tier": budget_tier,
                    "budget_action": budget_action,
                    "error": str(e),
                })

        return results

    # ==================================================================
    # Stage 4: Verifier — 判定假设
    # ==================================================================

    @staticmethod
    def _calls_use_synthetic_or_unresolved(calls: list) -> bool:
        """True when any probe call used a synthetic/fallback ID or unresolved route."""
        for call in calls or []:
            if not isinstance(call, dict):
                continue
            if bool(call.get("synthetic_id")) or bool(call.get("unresolved_route")):
                return True
            path = str(call.get("path") or call.get("call") or "")
            if "{" in path or re.search(r"/:[A-Za-z_]", path):
                return True
        return False

    @staticmethod
    def _hypothesis_expects_runtime_error(title: str, expected: str) -> bool:
        """Hypothesis must explicitly expect a server/runtime error before bare 5xx confirms."""
        text = f"{title} {expected}".lower()
        markers = (
            "500", "5xx", "server error", "runtime error", "exception", "crash", "traceback",
            "不应抛", "不得抛", "不能抛", "不应返回500", "不得返回500", "不能返回500",
            "must not return 500", "should not return 500", "must not throw", "should not throw",
            "服务端异常", "服务器异常", "内部错误", "internal server error",
        )
        return any(marker in text for marker in markers)

    def stage_verify(self, execution_results: list[dict]) -> list[DiscoveryFinding]:
        """Evidence-based verdict: compare API responses against hypothesis expectations"""
        import re
        findings = []

        for r in execution_results:
            evidence = r.get("evidence", {})
            if not isinstance(evidence, dict):
                evidence = {}
            budget_info = evidence.get("execution_budget", {})
            if not isinstance(budget_info, dict):
                budget_info = {}
            if r.get("budget_tier") and not budget_info.get("tier"):
                budget_info["tier"] = r.get("budget_tier")
            if r.get("budget_action") and not budget_info.get("action"):
                budget_info["action"] = r.get("budget_action")
            if budget_info:
                evidence["execution_budget"] = budget_info
            calls = evidence.get("calls", [])
            title = r.get("title", "").lower()
            expected = r.get("expected_behavior", "").lower()
            probe_degraded = self._calls_use_synthetic_or_unresolved(calls)
            expects_runtime_error = self._hypothesis_expects_runtime_error(title, expected)
            if probe_degraded:
                evidence["probe_quality"] = "synthetic_or_unresolved_id"
            
            verdict = "inconclusive"
            actual = ""
            confidence = 0.5
            
            # === P-1: RUNTIME ERROR DETECTION (universal) ===
            # Detect server errors, business failures, and error responses.
            # Synthetic/unresolved IDs and bare 5xx without an expected-error
            # hypothesis are probe artifacts, not confirmed product defects.
            p1_decided = False
            for call in calls:
                if p1_decided:
                    break
                for role in ("admin", "viewer", "no_auth"):
                    body = call.get("results", {}).get(role, {}).get("body", {})
                    status = call.get("results", {}).get(role, {}).get("status", 0)
                    if not isinstance(body, dict):
                        continue
                    # Server error (5xx)
                    if status >= 500:
                        if probe_degraded:
                            verdict = "inconclusive"
                            actual = (
                                f"探针使用合成/未解析实体ID，HTTP{status}视为探针伪影而非确认缺陷: "
                                f"{str(body)[:300]}"
                            )
                            confidence = 0.35
                            evidence["probe_quality_gate"] = "synthetic_or_unresolved_id"
                        elif expects_runtime_error:
                            verdict = "confirmed"
                            actual = f"服务端异常 HTTP{status}: {str(body)[:300]}"
                            confidence = 0.90
                        else:
                            verdict = "inconclusive"
                            actual = (
                                f"裸HTTP{status}缺少假设期望错误门禁，降级为 inconclusive: "
                                f"{str(body)[:300]}"
                            )
                            confidence = 0.50
                            evidence["probe_quality_gate"] = "bare_5xx_without_expected_error"
                        p1_decided = True
                        break
                    # Business logic failure
                    if body.get("ok") is False:
                        if probe_degraded:
                            verdict = "inconclusive"
                            actual = (
                                f"探针使用合成/未解析实体ID，业务失败响应视为探针伪影: "
                                f"{str(body)[:300]}"
                            )
                            confidence = 0.35
                            evidence["probe_quality_gate"] = "synthetic_or_unresolved_id"
                        else:
                            verdict = "confirmed"
                            actual = f"业务逻辑返回失败: {str(body)[:300]}"
                            confidence = 0.85
                        p1_decided = True
                        break
                    # Explicit error response
                    if body.get("error") and not (400 <= status < 500 and status not in (401, 403)):
                        if probe_degraded:
                            verdict = "inconclusive"
                            actual = (
                                f"探针使用合成/未解析实体ID，错误响应视为探针伪影: "
                                f"{str(body.get('error', ''))[:200]}"
                            )
                            confidence = 0.35
                            evidence["probe_quality_gate"] = "synthetic_or_unresolved_id"
                        else:
                            verdict = "confirmed"
                            actual = f"错误响应: {str(body.get('error',''))[:200]}"
                            confidence = 0.80
                        p1_decided = True
                        break
                    # Exception/stack trace in response (information leak)
                    if "exception" in str(body).lower() or "traceback" in str(body).lower():
                        if probe_degraded:
                            verdict = "inconclusive"
                            actual = (
                                f"探针使用合成/未解析实体ID，异常泄露响应无法确认真实资源缺陷: "
                                f"{str(body)[:300]}"
                            )
                            confidence = 0.40
                            evidence["probe_quality_gate"] = "synthetic_or_unresolved_id"
                        else:
                            verdict = "confirmed"
                            actual = f"响应体泄露异常信息: {str(body)[:300]}"
                            confidence = 0.85
                        p1_decided = True
                        break
            
            # === P0: BEFORE/AFTER STATE OBSERVER COMPARISON ===
            # If execution has 3+ steps (before GET → action → after GET),
            # use structured snapshot diff for higher-confidence verdicts.
            if len(calls) >= 3:
                before_body = calls[0].get("results", {}).get("admin", {}).get("body", {})
                action_result = calls[1]
                after_body = calls[-1].get("results", {}).get("admin", {}).get("body", {})
                
                if isinstance(before_body, dict) and isinstance(after_body, dict):
                    action_status = action_result.get("results", {}).get("admin", {}).get("status", 0)
                    action_ok = 200 <= action_status < 300
                    
                    if before_body != after_body:
                        # Exclude timestamp/metadata fields that naturally change
                        _skip_keys = {"created_at", "updated_at", "modified_at", "timestamp",
                                      "create_time", "update_time", "last_modified", "_links",
                                      "createdAt", "updatedAt", "modifiedAt"}
                        changed = {}
                        all_keys = set(list(before_body.keys())[:30]) | set(list(after_body.keys())[:30])
                        for k in all_keys - _skip_keys:
                            bv = before_body.get(k)
                            av = after_body.get(k)
                            if bv != av:
                                # Deep compare: serialize to JSON for nested objects
                                bv_str = json.dumps(bv, sort_keys=True, default=str) if isinstance(bv, (dict, list)) else str(bv)
                                av_str = json.dumps(av, sort_keys=True, default=str) if isinstance(av, (dict, list)) else str(av)
                                if bv_str != av_str:
                                    changed[k] = {
                                        "before": str(bv)[:200],
                                        "after": str(av)[:200],
                                    }
                        
                        if changed:
                            detail = "; ".join(f"{k}: {v['before']}→{v['after']}" for k, v in list(changed.items())[:5])
                            
                            invariant_violation_kw = (
                                "不应", "不得", "禁止", "must not", "should not", "unchanged",
                                "不可变", "不应变", "不能修改", "守恒", "conservation",
                                "总和不变", "余额不变", "库存不变", "reject", "block", "拒绝",
                            )
                            expected_change_kw = (
                                "应更新", "应该更新", "应变更", "应该变更", "应修改", "应该修改",
                                "expected to change", "should change", "should update", "transition",
                                "状态流转", "正常变更", "正常更新",
                            )
                            # Write succeeded and violated an explicit immutability/conservation expectation.
                            if action_ok and any(kw in title + expected for kw in invariant_violation_kw):
                                verdict = "confirmed"
                                actual = f"写操作成功并造成被禁止的状态变化: {detail}"
                                confidence = 0.90
                            # Expected/normal changes are evidence that the bug hypothesis is false, not a confirmed bug.
                            elif action_ok and any(kw in title + expected for kw in expected_change_kw):
                                verdict = "falsified"
                                actual = f"状态按预期发生变化: {detail}"
                                confidence = 0.82
                            # Numeric deltas alone are not bugs.  Confirm only when tied to an explicit invariant.
                            elif action_ok:
                                numeric_deltas = {}
                                for k in changed:
                                    try:
                                        bv = float(before_body.get(k, 0) or 0)
                                        av = float(after_body.get(k, 0) or 0)
                                        if bv != av:
                                            numeric_deltas[k] = av - bv
                                    except (ValueError, TypeError):
                                        pass
                                if numeric_deltas:
                                    invariant_kw = ("守恒", "conservation", "总和", "balance", "余额", "库存不得", "不得为负", "negative")
                                    if any(kw in title + expected for kw in invariant_kw):
                                        verdict = "confirmed"
                                        actual = f"数值不变量疑似被破坏: {detail}"
                                        confidence = 0.80
                                    else:
                                        verdict = "inconclusive"
                                        actual = f"检测到数值变化但缺少不变量约束: {detail}"
                                        confidence = max(confidence, 0.55)
                    else:
                        # before == after — no change detected
                        if action_ok and any(kw in title + expected for kw in ("变更", "更新", "修改", "change", "update", "modify", "delete", "删除")):
                            verdict = "confirmed"
                            actual = "操作返回成功但状态未变化 — 操作可能未实际执行"
                            confidence = 0.82
                        elif not action_ok:
                            verdict = "inconclusive"
                            actual = f"操作失败(HTTP {action_status})，状态未变化（预期行为）"
                            confidence = 0.60

            # === P2: MULTI-OBSERVER CROSS-VALIDATION ===
            if len(calls) >= 4 and verdict == "inconclusive":
                entity_obs = calls[2]
                cross_obs = calls[3]
                eb = entity_obs.get('results', {}).get('admin', {}).get('body', {})
                cb = cross_obs.get('results', {}).get('admin', {}).get('body', {})
                if isinstance(eb, dict) and isinstance(cb, dict):
                    cross_items = cb.get('data') or cb.get('items') or []
                    if isinstance(cross_items, list) and cross_items:
                        eid = eb.get('id') or eb.get('code') or ''
                        matched = None
                        for item in cross_items:
                            if isinstance(item, dict):
                                iid = item.get('id') or item.get('code') or ''
                                if eid and str(iid) == str(eid):
                                    matched = item; break
                        if not matched and eid and len(cross_items) == 1:
                            matched = cross_items[0]
                        if matched and isinstance(matched, dict):
                            mismatches = []
                            for k in ('status','name','quantity','qty','state','version'):
                                ev = eb.get(k); cv = matched.get(k)
                                if ev is not None and cv is not None and str(ev) != str(cv):
                                    mismatches.append(f'{k}: detail={ev}, list={cv}')
                            if mismatches:
                                verdict = 'confirmed'
                                actual = f'多视图数据不一致: {"; ".join(mismatches[:3])}'
                                confidence = 0.87
                            else:
                                confidence = max(confidence, 0.55)
                    elif isinstance(eb, dict) and len(eb) > 0:
                        diffs = []
                        for k in ('status','state','quantity','qty','completedQty'):
                            ev = eb.get(k); cv = cb.get(k)
                            if ev is not None and cv is not None and str(ev) != str(cv):
                                diffs.append(f'{k}: {ev} vs {cv}')
                        if diffs:
                            verdict = 'confirmed'
                            actual = f'详情视图与交叉视图不一致: {"; ".join(diffs[:3])}'
                            confidence = 0.85

            # Collect HTTP statuses AND response bodies for keyword matching
            unauth_ok = any(
                c.get("results", {}).get("no_auth", {}).get("status") == 200
                for c in calls
            )
            admin_ok = any(
                c.get("results", {}).get("admin", {}).get("status") == 200
                for c in calls
            )
            all_404 = calls and all(
                c.get("results", {}).get("admin", {}).get("status") == 404
                for c in calls
            )

            # Collect response body text and data-bearing signals.
            def _body_text(call_results, role):
                body = call_results.get("results", {}).get(role, {}).get("body", {})
                return json.dumps(body, ensure_ascii=False).lower() if body else ""

            def _has_business_data(body):
                """Return True only when the response contains non-empty business data.

                A bare HTTP 200 with an empty collection is not enough to confirm
                an authorization-bypass bug.  Earlier versions treated any
                unauthenticated 200 as confirmed, which inflated raw signals and
                pushed empty/healthy endpoints into needs_more_evidence.
                """
                if not body:
                    return False
                if isinstance(body, dict):
                    data = body.get("data", body)
                    if isinstance(data, list):
                        return len(data) > 0
                    if isinstance(data, dict):
                        return any(v not in (None, "", [], {}) for v in data.values())
                    return data not in (None, "", [], {})
                if isinstance(body, list):
                    return len(body) > 0
                return bool(str(body).strip())

            def _role_status(call_results, role):
                return int(call_results.get("results", {}).get(role, {}).get("status") or 0)

            def _role_has_business_data(call_results, role):
                body = call_results.get("results", {}).get(role, {}).get("body", {})
                return _has_business_data(body)

            all_bodies_admin = " ".join(_body_text(c, "admin") for c in calls)
            all_bodies_noauth = " ".join(_body_text(c, "no_auth") for c in calls)
            admin_body = " ".join(_body_text(c, "admin") for c in calls)
            viewer_body = " ".join(_body_text(c, "viewer") for c in calls)
            noauth_business_data = any(
                _role_status(c, "no_auth") == 200 and _role_has_business_data(c, "no_auth")
                for c in calls
            )
            viewer_business_data = any(
                _role_status(c, "viewer") == 200 and _role_has_business_data(c, "viewer")
                for c in calls
            )
            noauth_rejected = calls and all(_role_status(c, "no_auth") in {401, 403} for c in calls)
            viewer_rejected = calls and all(_role_status(c, "viewer") in {401, 403} for c in calls)

            # Evidence-based verdict rules (ordered by specificity)

            # === BODY-AWARE RULES (NEW) ===

            # Rule B0: Tenant/multi-tenant hypothesis — only falsify when project
            # config explicitly declares single-tenant mode.  Default: let evidence decide.
            is_single_tenant = str(os.environ.get("QUALIBUG_SINGLE_TENANT", "")).lower() in {"1", "true", "yes"}
            tenant_kw = ("租户", "tenant a", "tenant b", "tenant_id", "tenantid",
                         "多租户", "multi-tenant", "multi.tenant", "cross-tenant",
                         "跨租户", "tenant isolation", "租户隔离")
            if is_single_tenant and any(kw in title + expected for kw in tenant_kw):
                verdict = "falsified"
                actual = "项目配置为单租户模式 — 租户假设不适用"
                confidence = 0.95

            # Rule B1: Hypothesis says reject/block/error → body says success/ok
            if admin_ok and verdict == "inconclusive":
                reject_kw = ("拒绝", "reject", "block", "阻止", "禁止", "不应", "must not", "should not")
                success_kw = ("success", "ok", "成功", "created", "创建")
                auth_boundary_kw = ("auth", "认证", "permission", "权限", "anonymous", "匿名", "401", "403")
                is_auth_boundary_hypothesis = any(kw in title + expected for kw in auth_boundary_kw)
                if any(kw in title + expected for kw in reject_kw) and not is_auth_boundary_hypothesis:
                    if any(kw in all_bodies_admin for kw in success_kw):
                        verdict = "confirmed"
                        actual = "应被拒绝但API返回200且body含success — 校验缺失"
                        confidence = 0.92

            # Rule B2: Data not filtered — viewer sees same as admin
            if verdict == "inconclusive" and admin_body and viewer_body and len(admin_body) > 50:
                filter_kw = ("权限", "角色", "role", "限制", "restrict", "过滤", "filter", "隔离")
                if any(kw in title + expected for kw in filter_kw):
                    admin_nums = [w for w in admin_body.split('"') if w.isdigit() and len(w) >= 3]
                    viewer_nums = [w for w in viewer_body.split('"') if w.isdigit() and len(w) >= 3]
                    overlap = len(set(admin_nums[:20]) & set(viewer_nums[:20]))
                    if overlap >= 3:
                        verdict = "confirmed"
                        actual = f"viewer和admin返回相同数据(重叠{overlap}条) — 权限过滤缺失"
                        confidence = 0.88

            # Rule B3: Response body size — hypothesis expects error but body has real data
            reject_kw = ("reject", "拒绝", "block", "阻止", "error", "fail", "失败", "invalid")
            auth_boundary_kw = ("auth", "认证", "permission", "权限", "anonymous", "匿名", "401", "403")
            is_auth_boundary_hypothesis = any(kw in title + expected for kw in auth_boundary_kw)
            if verdict == "inconclusive" and admin_ok and any(kw in title + expected for kw in reject_kw) and not is_auth_boundary_hypothesis:
                # Count meaningful data fields in response body
                data_keys = sum(1 for c in calls 
                              for role in ["admin"]
                              for k, v in c.get("results", {}).get(role, {}).get("body", {}).items()
                              if v and str(v) not in ("", "null", "None", "[]", "{}"))
                if data_keys >= 3:
                    verdict = "confirmed"
                    actual = f"应报错/拒绝但返回{data_keys}个数据字段 — 错误处理缺失"
                    confidence = 0.82

            # Rule B4: Missing input validation — hypothesis says validate but API accepts invalid
            validation_kw = ("validate", "校验", "check", "检查", "verify", "核实", "constraint", "约束")
            invalid_kw = ("negative", "负数", "zero", "零", "null", "空", "empty", "exceed", "超过")
            if verdict == "inconclusive" and admin_ok and any(kw in title for kw in validation_kw) and any(kw in title + expected for kw in invalid_kw):
                verdict = "confirmed"
                actual = f"应校验输入但API接受无效值 — 输入校验缺失"
                confidence = 0.83

            # === Phase84: Multi-Observer Cross-Validation ===
            # Compare same entity from different endpoints (list vs detail vs inventory)
            # If same field has different values across observers → high-confidence Bug
            if verdict == "inconclusive" and len(calls) >= 3:
                try:
                    # Extract entity identifiers from hypothesis or calls
                    entity_code = None
                    entity_id = None
                    for call in calls:
                        path = call.get("call", "").split()[1] if " " in call.get("call", "") else call.get("call", "")
                        # Extract code/id from path like /api/materials/MAT001 or /api/orders/ORD001
                        import re as _re3
                        code_match = _re3.search(r'/([A-Z]{2,3}-?\d+|[A-Z]+\d+|\w+-\d+)(?:$|/|\?)', path.upper())
                        if code_match:
                            entity_code = code_match.group(1)
                            break
                    
                    # Collect all numeric/string fields from all GET observers
                    observer_data = {}
                    for i, call in enumerate(calls):
                        method = call.get("call", "").split()[0] if " " in call.get("call", "") else "GET"
                        if method == "GET":
                            body = call.get("results", {}).get("admin", {}).get("body", {})
                            if isinstance(body, dict):
                                # For list endpoints, find the matching entity
                                if "data" in body and isinstance(body["data"], list):
                                    for item in body["data"]:
                                        if entity_code and (item.get("code") == entity_code or item.get("materialCode") == entity_code):
                                            observer_data[f"observer_{i}"] = item
                                            break
                                else:
                                    # Detail endpoint — use whole body
                                    if entity_code and (body.get("code") == entity_code or body.get("materialCode") == entity_code or not entity_code):
                                        observer_data[f"observer_{i}"] = body
                    
                    # Cross-validate: find inconsistencies across observers
                    if len(observer_data) >= 2:
                        all_keys = set()
                        for obs_data in observer_data.values():
                            all_keys.update(k for k in obs_data.keys() if not k.startswith("_") and not k in ("created_at", "updated_at", "id"))
                        
                        inconsistencies = []
                        for key in sorted(all_keys):
                            values = {}
                            for obs_name, obs_data in observer_data.items():
                                if key in obs_data:
                                    val = obs_data[key]
                                    # Normalize for comparison
                                    if isinstance(val, float):
                                        val = round(val, 2)
                                    values[obs_name] = val
                            
                            # Check if values differ across observers
                            unique_values = set(str(v) for v in values.values())
                            if len(unique_values) > 1 and len(values) >= 2:
                                inconsistencies.append({
                                    "field": key,
                                    "values": values,
                                    "delta": f"{list(values.values())[0]} vs {list(values.values())[1]}"
                                })
                        
                        if inconsistencies:
                            verdict = "confirmed"
                            inc_detail = inconsistencies[0]
                            actual = f"多视图数据不一致: {inc_detail['field']} = {inc_detail['delta']}"
                            if len(inconsistencies) > 1:
                                actual += f" ({len(inconsistencies)}个字段不一致)"
                            confidence = 0.85
                            # Attach full inconsistency report
                            r["_cross_validation"] = {
                                "observers": list(observer_data.keys()),
                                "inconsistencies": inconsistencies[:5]
                            }
                except Exception as exc:
                    logger.debug(
                        "discovery: 跨视图一致性校验异常",
                        extra={"context": {"error": str(exc)[:200]}},
                    )

            # Rule B5: Missing cascade — hypothesis says cascade but no related entity evidence
            cascade_kw = ("级联", "cascade", "同步", "sync", "关联更新", "propagate")
            if verdict == "inconclusive" and admin_ok and any(kw in title + expected for kw in cascade_kw):
                # Cascade bugs need multi-step — flag as high-confidence inconclusive with action
                actual = f"级联效应需多步骤验证(当前单次HTTP无法确认) — 建议: 添加 before/after observer"
                confidence = 0.35  # Low confidence = needs more evidence

            # Rule B6: Time boundary — hypothesis says time constraint but API accepts reversed
            time_kw = ("时间", "time", "开始", "start", "结束", "end", "date", "日期", "计划")
            reverse_kw = ("早于", "before", "earlier", "晚于", "after", "later", "逆序", "reverse")
            if verdict == "inconclusive" and admin_ok and any(kw in title for kw in time_kw) and any(kw in title + expected for kw in reverse_kw):
                verdict = "confirmed"
                actual = f"时间约束未校验 — API可能接受逆序日期"
                confidence = 0.80
            
            # Rule 1: Auth bypass — hypothesis says auth required.
            # Confirm only if anonymous access returns HTTP 200 *and* non-empty
            # business data.  Empty 200 responses are retained as low-confidence
            # signals instead of customer-reportable bugs.
            auth_keywords = ("auth", "认证", "permission", "权限", "anonymous", "匿名", "role", "角色")
            is_auth_hypothesis = any(kw in title or kw in expected for kw in auth_keywords)
            if verdict == "inconclusive" and is_auth_hypothesis and noauth_business_data:
                verdict = "confirmed"
                actual = "无认证访问成功且响应包含业务数据 — 认证/权限边界未强制执行"
                confidence = 0.95
            elif verdict == "inconclusive" and is_auth_hypothesis and unauth_ok and not noauth_business_data:
                verdict = "inconclusive"
                actual = "无认证返回200但响应未包含业务数据；缺少可交付的数据泄露证据"
                confidence = 0.35
            elif verdict == "inconclusive" and is_auth_hypothesis and noauth_rejected and (viewer_rejected or not viewer_business_data) and admin_ok:
                verdict = "falsified"
                actual = "匿名/低权限访问被拒绝或未返回业务数据，认证边界表现正确"
                confidence = 0.86
            
            # Rule 2: Missing error contract — hypothesis says should reject, but returns 200
            elif verdict == "inconclusive" and unauth_ok and ("401" in expected or "403" in expected or "拒绝" in expected or "reject" in expected):
                if noauth_business_data:
                    verdict = "confirmed"
                    actual = "应返回401/403但无认证返回200且包含业务数据 — 错误处理/权限校验缺失"
                    confidence = 0.90
                else:
                    verdict = "inconclusive"
                    actual = "应拒绝但返回200；响应为空或无业务数据，需补充敏感性证据"
                    confidence = 0.35
            
            # Rule 3: Negative/allows-invalid — hypothesis says should reject, API succeeded
            elif verdict == "inconclusive" and admin_ok and any(kw in title+expected for kw in ("negative", "负数", "零", "zero", "null", "负", "空", "invalid", "无效", "exceed", "超过", "超")):
                # Check: does the hypothesis say this SHOULD be rejected?
                if any(kw in expected for kw in ("不应", "拒绝", "reject", "block", "阻止", "禁止", "must not", "should not", "should be rejected")):
                    verdict = "confirmed"
                    actual = f"应被拒绝的操作返回200 — 输入校验缺失"
                    confidence = 0.85
                else:
                    verdict = "inconclusive"
                    actual = "端点可访问, 需进一步验证业务逻辑"
                    confidence = 0.5
            
            # Rule 4: Missing side effect — hypothesis says state change should trigger something
            elif verdict == "inconclusive" and admin_ok and any(kw in title+expected for kw in ("未生成", "未触发", "未同步", "未更新", "not created", "not triggered", "missing", "缺失", "should trigger")):
                verdict = "inconclusive"
                actual = "端点可达,需验证副作用是否发生(需要状态编排)"
                confidence = 0.4
            
            # Rule 5: Conservation/integrity — cross-endpoint comparison
            elif verdict == "inconclusive" and admin_ok and any(kw in title+expected for kw in ("守恒", "不一致", "不等于", "≠", "总和", "sum", "total", "balance", "对账", "reconciliation")):
                if admin_body and len(admin_body) > 80:
                    import re as _re2
                    nums = _re2.findall(r'"(\w+)"\s*:\s*(-?\d+\.?\d*)', admin_body)
                    verdict = "inconclusive"
                    actual = f"端点可达,检测到{len(nums)}个数值字段,需跨端点对账 (body={len(admin_body)}chars)"
                    confidence = 0.45
                else:
                    verdict = "inconclusive"
                    actual = "端点可达,需对比多端点数据验证一致性"
                    confidence = 0.4
            
            # Rule 6: All 404 — paths don't match
            elif verdict == "inconclusive" and all_404:
                verdict = "inconclusive"
                actual = "所有API路径返回404 — 需要路径映射校正"
                confidence = 0.2
            
            # Rule 7: Properly rejected (401/403 on no-auth, 200 on auth) — NOT a bug
            elif verdict == "inconclusive" and not unauth_ok and admin_ok:
                verdict = "falsified"
                actual = "无认证被拒绝(正确),认证访问正常"
                confidence = 0.8
            
            # Rule 7.5: Cross-role body comparison (admin vs viewer).
            # A difference between admin and viewer is often expected.  It only
            # becomes a bug when the lower-privilege viewer receives business data
            # that should be denied, not merely because schemas differ.
            elif verdict == "inconclusive" and admin_ok and viewer_body and len(viewer_body) > 10:
                filter_kw = ("权限", "角色", "role", "限制", "restrict", "过滤", "filter", "隔离", "低权限")
                if any(kw in title + expected for kw in filter_kw) and viewer_business_data:
                    verdict = "inconclusive"
                    actual = "低权限角色返回了业务数据，但缺少与admin数据范围的确定性越权对比"
                    confidence = 0.45
            elif verdict == "inconclusive" and admin_ok and not unauth_ok:
                # A reachable authenticated endpoint is never proof of a business
                # defect.  Historical versions relaxed this branch based on keywords,
                # which inflated confirmed findings.  Keep the signal inconclusive
                # until a flow/evidence contract proves an invariant violation.
                verdict = "inconclusive"
                actual = "认证访问正常；缺少可复现的不变量或状态证据"
                confidence = 0.5
            
            else:
                if verdict == "inconclusive" and not evidence.get("probe_quality_gate"):
                    actual = "无法判定"
                    confidence = 0.3

            # === Enhanced: Extended Verification Rules (P1: 3.1) ===
            # These rules expand coverage for business logic, temporal consistency,
            # boundary values, idempotency, and batch operations.

            # Rule B7: Business Logic Verification — quantity/amount consistency
            if verdict == "inconclusive" and len(calls) >= 2:
                quantity_kw = ("数量", "quantity", "qty", "amount", "金额", "库存", "stock", "balance", "余额")
                if any(kw in title + expected for kw in quantity_kw):
                    try:
                        before_body = calls[0].get("results", {}).get("admin", {}).get("body", {})
                        after_body = calls[-1].get("results", {}).get("admin", {}).get("body", {})
                        if isinstance(before_body, dict) and isinstance(after_body, dict):
                            # Check for negative values (should never happen)
                            for key in ("quantity", "qty", "stock", "balance", "amount", "count"):
                                after_val = after_body.get(key)
                                if after_val is not None:
                                    try:
                                        if float(after_val) < 0:
                                            verdict = "confirmed"
                                            actual = f"业务逻辑错误: {key}={after_val} 为负值"
                                            confidence = 0.92
                                            break
                                    except (ValueError, TypeError):
                                        pass
                            # Check for zero when should be non-zero
                            if verdict == "inconclusive":
                                for key in ("quantity", "qty", "stock", "balance"):
                                    before_val = before_body.get(key)
                                    after_val = after_body.get(key)
                                    if before_val is not None and after_val is not None:
                                        try:
                                            bv = float(before_val)
                                            av = float(after_val)
                                            # Large unexpected change (>90% drop)
                                            if bv > 0 and av >= 0 and (bv - av) / bv > 0.90:
                                                if any(kw in title + expected for kw in ("守恒", "conservation", "不变", "unchanged")):
                                                    verdict = "confirmed"
                                                    actual = f"业务逻辑错误: {key} 从 {bv} 变为 {av}，降幅超过90%"
                                                    confidence = 0.85
                                                    break
                                        except (ValueError, TypeError):
                                            pass
                    except Exception:
                        pass

            # Rule B8: Temporal Consistency — async operation final state
            if verdict == "inconclusive" and len(calls) >= 3:
                temporal_kw = ("异步", "async", "最终一致", "eventual", "延迟", "delay", "pending", "处理中")
                if any(kw in title + expected for kw in temporal_kw):
                    try:
                        # Check if final state matches expected terminal state
                        final_body = calls[-1].get("results", {}).get("admin", {}).get("body", {})
                        if isinstance(final_body, dict):
                            status_val = str(final_body.get("status") or final_body.get("state") or "").lower()
                            # Stuck in pending/processing state
                            if status_val in ("pending", "processing", "queued", "waiting", "处理中", "等待"):
                                # Check if enough time has passed (multiple calls)
                                if len(calls) >= 4:
                                    verdict = "confirmed"
                                    actual = f"时序一致性错误: 状态停留在 '{status_val}'，未达终态"
                                    confidence = 0.78
                    except Exception:
                        pass

            # Rule B9: Boundary Value Verification — extreme inputs accepted
            if verdict == "inconclusive" and admin_ok:
                boundary_kw = ("边界", "boundary", "极值", "extreme", "溢出", "overflow", "最大", "最大", "minimum", "maximum")
                if any(kw in title + expected for kw in boundary_kw):
                    # Check if response indicates boundary violation
                    for call in calls:
                        body = call.get("results", {}).get("admin", {}).get("body", {})
                        status = call.get("results", {}).get("admin", {}).get("status", 0)
                        if isinstance(body, dict):
                            # Accepted invalid boundary value (should reject)
                            if status == 200 and body.get("ok") is not False:
                                # Check for overflow indicators
                                body_str = json.dumps(body, ensure_ascii=False).lower()
                                if any(ind in body_str for ind in ("99999999", "2147483647", "-1", "overflow", "溢出")):
                                    verdict = "confirmed"
                                    actual = "边界值验证缺失: 接受极端输入值"
                                    confidence = 0.80
                                    break

            # Rule B10: Idempotency Verification — repeated write produces different results
            if verdict == "inconclusive" and len(calls) >= 4:
                idempotent_kw = ("幂等", "idempotent", "重复", "repeat", "duplicate", "多次")
                if any(kw in title + expected for kw in idempotent_kw):
                    try:
                        # Compare results of repeated operations
                        results_bodies = []
                        for call in calls:
                            method = call.get("call", "").split()[0] if " " in call.get("call", "") else "GET"
                            if method in ("POST", "PUT", "PATCH"):
                                body = call.get("results", {}).get("admin", {}).get("body", {})
                                if isinstance(body, dict):
                                    results_bodies.append(body)
                        # If we have multiple write results, check for inconsistency
                        if len(results_bodies) >= 2:
                            first = results_bodies[0]
                            last = results_bodies[-1]
                            # Same operation should produce same result (idempotent)
                            first_id = first.get("id") or first.get("data", {}).get("id") if isinstance(first.get("data"), dict) else None
                            last_id = last.get("id") or last.get("data", {}).get("id") if isinstance(last.get("data"), dict) else None
                            if first_id and last_id and str(first_id) != str(last_id):
                                verdict = "confirmed"
                                actual = f"幂等性错误: 重复操作产生不同资源 ID ({first_id} vs {last_id})"
                                confidence = 0.88
                    except Exception:
                        pass

            # Rule B11: Batch Operation Verification — pagination/sorting consistency
            if verdict == "inconclusive" and len(calls) >= 2:
                batch_kw = ("分页", "pagination", "排序", "sort", "列表", "list", "批量", "batch")
                if any(kw in title + expected for kw in batch_kw):
                    try:
                        for call in calls:
                            body = call.get("results", {}).get("admin", {}).get("body", {})
                            if isinstance(body, dict):
                                data = body.get("data") or body.get("items") or body.get("list") or []
                                if isinstance(data, list) and len(data) > 0:
                                    # Check for duplicate IDs in list
                                    ids = [str(item.get("id") or item.get("code") or "") for item in data if isinstance(item, dict)]
                                    ids = [i for i in ids if i]
                                    if len(ids) != len(set(ids)):
                                        verdict = "confirmed"
                                        actual = f"批量操作错误: 列表包含重复 ID ({len(ids)} 项中 {len(ids) - len(set(ids))} 重复)"
                                        confidence = 0.85
                                        break
                                    # Check pagination metadata consistency
                                    total = body.get("total") or body.get("totalCount") or body.get("count")
                                    if total is not None:
                                        try:
                                            total_val = int(total)
                                            if total_val < len(data):
                                                verdict = "confirmed"
                                                actual = f"分页元数据不一致: total={total_val} 但返回 {len(data)} 条"
                                                confidence = 0.82
                                                break
                                        except (ValueError, TypeError):
                                            pass
                    except Exception:
                        pass

            # === Phase78B: Semantic State Verifier — last-resort for inconclusive ===
            if verdict == "inconclusive" and calls and isinstance(r.get("semantic_obligation"), dict):
                try:
                    from ai_test_asset_center.state_observer_registry import StateObserver
                    from ai_test_asset_center.business_invariant_evaluator import BusinessInvariantEvaluator, ProofObligation as EvalObl
                    observer = StateObserver(redact_sensitive=True)
                    evaluator = BusinessInvariantEvaluator()
                    # Build before/after snapshots from multi-step calls
                    if len(calls) >= 2:
                        body1 = calls[0].get("results", {}).get("admin", {}).get("body", {})
                        body2 = calls[-1].get("results", {}).get("admin", {}).get("body", {})
                        if isinstance(body1, dict) and isinstance(body2, dict) and body1 and body2:
                            s1 = observer.observe_from_http(body1, 200, "before", observer_id="before")
                            s2 = observer.observe_from_http(body2, 200, "after", observer_id="after")
                            # Try numeric_delta on all common keys
                            common_keys = [k for k in body1 if k in body2 and isinstance(body1[k], (int, float))]
                            if common_keys:
                                for key in common_keys[:3]:
                                    s1 = observer.apply_projection(s1, {"amounts": {"total": key}}, body1)
                                    s2 = observer.apply_projection(s2, {"amounts": {"total": key}}, body2)
                                    obl = EvalObl(obligation_id="", kind="numeric_delta",
                                                  title=title, severity="P1",
                                                  fields=["amounts.total"], expected_delta=0, tolerance=1e-6)
                                    result = evaluator.evaluate(obl, s1, s2)
                                    if not result.passed:
                                        verdict = "confirmed"
                                        actual = f"Semantic: {result.detail} (field={key})"
                                        confidence = 0.75
                                        break
                    # Try state_unchanged
                    if verdict == "inconclusive" and len(calls) >= 2:
                        body1 = calls[0].get("results", {}).get("admin", {}).get("body", {})
                        body2 = calls[-1].get("results", {}).get("admin", {}).get("body", {})
                        if isinstance(body1, dict) and isinstance(body2, dict):
                            status_keys = [k for k in body1 if "status" in str(k).lower() or "state" in str(k).lower()]
                            if status_keys:
                                s1 = observer.observe_from_http(body1, 200, "before")
                                s2 = observer.observe_from_http(body2, 200, "after")
                                s1 = observer.apply_projection(s1, {"lifecycle_state": status_keys[0]}, body1)
                                s2 = observer.apply_projection(s2, {"lifecycle_state": status_keys[0]}, body2)
                                obl = EvalObl(obligation_id="", kind="state_unchanged_after_rejection",
                                              title=title, severity="P0", fields=["lifecycle_state"])
                                result = evaluator.evaluate(obl, s1, s2)
                                if not result.passed:
                                    verdict = "confirmed"
                                    actual = f"Semantic state change: {result.detail}"
                                    confidence = 0.75
                except Exception:
                    pass  # Semantic verifier is best-effort

            # === Enhanced: Probe Quality Gate with Evidence Accumulation (P1: 3.2) ===
            # Instead of hard downgrade, use a softer approach:
            # 1. Mark as "needs_secondary_confirmation" instead of direct inconclusive
            # 2. Allow evidence accumulation from multiple weak signals
            # 3. Preserve high-confidence findings even with synthetic IDs
            if probe_degraded and verdict == "confirmed":
                # ── Evidence accumulation: check if multiple signals support this finding ──
                signal_count = 0
                signal_sources = []
                # Count distinct evidence signals
                if evidence.get("before_after_diff"):
                    signal_count += 1
                    signal_sources.append("state_change")
                if evidence.get("cross_validation_mismatch"):
                    signal_count += 1
                    signal_sources.append("cross_validation")
                if evidence.get("business_logic_violation"):
                    signal_count += 1
                    signal_sources.append("business_logic")
                if confidence >= 0.85:
                    signal_count += 1
                    signal_sources.append("high_confidence")

                # If we have multiple strong signals, preserve the finding with a flag
                if signal_count >= 2 and confidence >= 0.80:
                    # Keep confirmed but mark for secondary verification
                    evidence["probe_quality_gate"] = "needs_secondary_confirmation"
                    evidence["accumulated_signals"] = signal_sources
                    evidence["signal_count"] = signal_count
                    actual = f"[待二次确认] {actual}"
                    # Slightly reduce confidence but keep confirmed status
                    confidence = max(0.70, confidence - 0.10)
                elif confidence >= 0.90:
                    # Very high confidence findings are preserved with warning
                    evidence["probe_quality_gate"] = "high_confidence_synthetic"
                    actual = f"[高置信度-合成ID] {actual}"
                    confidence = max(0.75, confidence - 0.08)
                else:
                    # Standard downgrade for low-signal findings
                    verdict = "needs_secondary_confirmation"
                    actual = f"探针ID未真实解析，需二次确认: {actual}"
                    confidence = min(float(confidence or 0.0), 0.55)
                    evidence["probe_quality_gate"] = "synthetic_or_unresolved_id_soft_downgrade"

            # === Enhanced: Pending Observation for INCONCLUSIVE ===
            # Instead of discarding inconclusive results, mark high-potential ones
            # for future observation rounds
            if verdict == "inconclusive" and confidence >= 0.50:
                # Check if this has potential for future confirmation
                potential_indicators = (
                    "疑似", "可能", "suspected", "possible", "partial",
                    "部分", "异常", "unexpected", "mismatch"
                )
                if any(ind in actual.lower() for ind in potential_indicators):
                    evidence["pending_observation"] = True
                    evidence["observation_priority"] = "high" if confidence >= 0.60 else "medium"
                    verdict = "pending_observation"
                    actual = f"[待观察] {actual}"

            findings.append(DiscoveryFinding(
                hypothesis_id=r.get("hypothesis_id", "?"),
                title=r.get("title", "?"),
                severity=r.get("severity", "P1"),
                verdict=verdict,
                expected=r.get("expected_behavior", ""),
                actual=actual,
                evidence=evidence,
                confidence=confidence,
            ))

        self.findings = findings
        self._budget_feedback_summary = _summarize_execution_feedback(findings)
        try:
            persist_budget_feedback_profile(self._budget_learning_context, self._budget_feedback_summary)
        except Exception as e:
            print(f"  [WARN] Budget feedback persistence failed (non-fatal): {e}", flush=True)
        return findings

    # ==================================================================
    # 全自动流水线
    # ==================================================================

    def discover(self, prd_text: str, api_spec_text: str, prior_findings: list[dict] = None) -> dict:
        """一站式自主发现 — with closed-loop feedback from prior rounds"""
        t0 = time.time()
        stage_failures: list[str] = []
        self._emit_progress("starting", "Discovery engine started")

        print("=" * 60)
        print("QualiBug Autonomous Discovery Engine")
        print("=" * 60)

        # Stage 0: ProjectContext compiler.  Phase91 keeps the complete typed
        # compiler output so the Cognitive Memory Graph can be incrementally
        # updated from entities, APIs, invariants, observers and lifecycle facts.
        self._emit_progress("context", "Compiling project context")
        project_context = {}
        try:
            from ai_test_asset_center.project_context_compiler import ProjectContextCompiler
            compiler = ProjectContextCompiler()
            try:
                openapi_spec = json.loads(api_spec_text) if str(api_spec_text or "").lstrip().startswith("{") else {}
            except (TypeError, ValueError):
                openapi_spec = {}
            ctx = compiler.compile(prd_text, openapi_spec, api_docs_text=api_spec_text)
            project_context = compiler.to_dict(ctx)
        except Exception as context_error:
            # Context compilation is allowed to degrade, but never to abort the
            # discovery loop.  The artifact cache and GraphContextComposer will
            # use whichever deterministic facts remain available.
            project_context = {"entities": [], "apis": [], "candidate_invariants": [], "observers": []}
            stage_failures.append(f"context: {context_error}")

        # Stage 1: Read — Phase83A: Artifact Cache (no repeated API calls)
        self._emit_progress("reader", "Loading project context artifact")
        print("\n[Stage 1] Reader — 提取业务事实 (artifact cache)...")
        entities = []; rules = []; artifact_id = ""; artifact_status = "none"
        
        try:
            from ai_test_asset_center.project_context_artifact import get_artifact_cache
            cache = get_artifact_cache()
            # Reader compilation is intentionally decoupled from the Discovery
            # critical path.  Existing/stale artifacts are returned immediately;
            # a cache miss starts one background build rather than blocking every
            # Reasoner on a slow external API.
            reader_profile = str(getattr(self.client.config, "model", "") or "default")
            artifact = cache.get_or_build(
                os.environ.get("QUALIBUG_PROJECT", "real_project_demo"),
                prd_text,
                api_spec_text,
                reader_fn=lambda p, a: self.stage_read(p, a, project_context),
                project_config={"reader_model_profile": reader_profile},
                background_refresh=True,
            )
            artifact_id = artifact.artifact_id
            artifact_status = artifact.artifact_status

            if artifact_status == "READY":
                entities = artifact.entities
                rules = artifact.candidate_lifecycles or artifact.coverage.get("documented_rules", [])
                print(f"  [OK] Reader artifact READY: {len(entities)} entities", flush=True)
            elif artifact_status in ("STALE", "DEGRADED_CONTEXT"):
                entities = artifact.entities
                rules = artifact.candidate_lifecycles or artifact.coverage.get("documented_rules", [])
                print(f"  [WARN] {artifact_status}: using reusable artifact ({len(entities)} entities)", flush=True)
            elif artifact_status == "CONTEXT_PENDING":
                # Continue using deterministic local ProjectContext compilation.
                # No external Reader failure can now terminate the loop.
                entities = list(project_context.get("entities") or [])
                rules = []
                print("  [WARN] CONTEXT_PENDING: background Reader compiling; using local context", flush=True)
            else:
                entities = list(project_context.get("entities") or [])
                rules = []
                print(f"  [WARN] {artifact_status}: using limited local context", flush=True)
        except Exception as e:
            # Cache failures must not reintroduce a synchronous Reader call or
            # turn an external provider problem into a Discovery crash.
            artifact_status = "DEGRADED_CONTEXT"
            entities = list(project_context.get("entities") or [])
            rules = []
            print(f"  [WARN] Artifact cache error: {e}; using limited local context", flush=True)

        # Phase91: cognitive memory graph.  This is a private SQLite fact and
        # evidence graph, never a Markdown/Obsidian source.  It is wired before
        # reasoning so the Frontier selects an explainable, non-duplicate target
        # and the Context Composer can operate in shadow or active mode.
        graph = None
        graph_context_pack: dict[str, Any] = {}
        frontier_selection: dict[str, Any] | None = None
        graph_sync: dict[str, Any] = {}
        graph_ab_report: dict[str, Any] = {}
        graph_mode = (
            os.environ.get("QUALIBUG_GRAPH_CONTEXT_MODE")
            or os.environ.get("GRAPH_CONTEXT_MODE")
            or "shadow"
        ).strip().lower() or "shadow"
        project_id = os.environ.get("QUALIBUG_PROJECT", "real_project_demo")
        environment_id = os.environ.get("QUALIBUG_ENVIRONMENT", "test")
        discovery_run_id = f"discovery-{int(t0 * 1000)}"
        policy_version = "baseline"
        try:
            from ai_test_asset_center.policy_registry import get_policy_registry
            active_record = get_policy_registry().get_active()
            policy_version = active_record.policy_version if active_record else "baseline"
        except Exception:
            active_record = None
        try:
            from ai_test_asset_center.cognitive_memory_graph import CognitiveMemoryGraph, GraphContextComposer, Phase91ABEvaluator, RiskFrontierPlanner
            graph = CognitiveMemoryGraph(project_id, environment_id)
            graph_sync = graph.sync_context(
                project_context,
                artifact=locals().get("artifact"),
                prd_source_ref="prd",
                api_source_ref="api_contract",
                run_id=discovery_run_id,
                policy_version=policy_version,
            )
            planner = RiskFrontierPlanner(graph)
            frontier = planner.rank(active_policy={"policy_version": policy_version})
            frontier_selection = next((item for item in frontier if item.get("execution_allowed")), None)
            if frontier_selection:
                planner.record_selection(frontier_selection, run_id=discovery_run_id, policy_version=policy_version)
                graph_context_pack = GraphContextComposer(graph).compose(
                    frontier_selection.get("target") or {},
                    high_risk_write=False,
                )
            else:
                graph_context_pack = GraphContextComposer(graph).compose({})
            graph_context_pack["graph_mode"] = graph_mode
            graph_context_pack["frontier_selection"] = frontier_selection or {}
            graph_ab_report = Phase91ABEvaluator().evaluate(
                baseline_prompt=(prd_text[:2000] + "\n" + api_spec_text[:3000]),
                graph_pack=graph_context_pack,
                baseline_metrics={"production_http_requests": 0, "cleanup_failures": 0, "safety_violations": 0},
                challenger_metrics={"production_http_requests": 0, "cleanup_failures": 0, "safety_violations": 0},
            )
            print(f"  [OK] Cognitive graph: {graph_sync.get('node_count', 0)} nodes, frontier={len(frontier)} mode={graph_mode}", flush=True)
        except Exception as graph_error:
            # Graph is a progressive enhancement; its safe failure mode is the
            # Phase90 context path, never an uncontrolled prompt fallback.
            stage_failures.append(f"cognitive_graph: {graph_error}")
            graph_context_pack = {"graph_ready": False, "graph_mode": "off", "degradation_reason": str(graph_error)[:300]}
            print(f"  [WARN] Cognitive graph degraded: {graph_error}", flush=True)

        # Stage 2: Reason — 跑全部引擎 (with prior findings for closed loop)
        self._emit_progress("reasoner", "Running reasoner engines")
        print("\n[Stage 2] Reasoner — 9 engines, generating hypotheses...")
        if prior_findings:
            print(f"  Prior findings: {len(prior_findings)} confirmed bugs from previous rounds")
        hypotheses = []
        # Build world dict from cached entities+rules (or fallback reader output)
        # Use reader output if available, otherwise build from cached entities
        _world = dict(locals().get(
            "_reader_world",
            {"entities": entities or list(project_context.get("entities") or []), "documented_rules": rules},
        ))
        # These fields are consumed by stage_reason_all_v2.  In shadow mode the
        # pack is measured and carried through the mainline but does not alter
        # the LLM prompt; active mode switches the prompt to bounded graph facts.
        _world["_graph_evidence_pack"] = graph_context_pack
        _world["_risk_frontier"] = frontier_selection or {}
        _world["_graph_mode"] = graph_mode
        try:
            hypotheses = self.stage_reason_all(_world, prd_text, api_spec_text, prior_findings)
            print(f"  [OK] 生成了 {len(hypotheses)} 条假设 (across 9 engines)")
            for h in hypotheses[:5]:
                print(f"    - [{h.get('severity','?')}] {h.get('title','?')[:80]}")
        except Exception as e:
            stage_failures.append(f"reasoner: {e}")
            print(f"  [FAIL] Reasoner 失败: {e}")

        # Stage 3: Execute — with route map
        self._emit_progress("executor", "Building route map and executing probes")
        print("\n[Stage 3] Executor — building route map + executing probes...")
        route_map = self._build_route_map(api_spec_text)
        print(f"  Route map: {len(route_map)} routes")
        execution_results: list[dict] = []
        stage_status: dict[str, Any] = {
            "reasoner": "FAILED_SAFE" if any(str(item).startswith("reasoner:") for item in stage_failures) else ("ok" if hypotheses else "empty"),
            "executor": "pending",
            "verifier": "pending",
        }
        try:
            execution_results = self.stage_execute(hypotheses, route_map)
            stage_status["executor"] = "ok"
            print(f"  [OK] 执行了 {len(execution_results)} 个探针")
        except Exception as e:
            execution_results = []
            stage_failures.append(f"executor: {e}")
            stage_status["executor"] = "FAILED_SAFE"
            print(f"  [FAIL] Executor 失败: {e}")

        # Stage 4: Verify
        self._emit_progress("verifier", "Verifying execution evidence")
        print("\n[Stage 4] Verifier — 判定假设...")
        # Pre-initialize so a verifier crash cannot leave confirmed/findings unbound
        # (previously caused UnboundLocalError in round2 `confirmed += r2_confirmed`).
        findings: list[DiscoveryFinding] = []
        confirmed = 0
        try:
            findings = self.stage_verify(execution_results)
            self.findings = findings  # ← 关键修复：确保 crash 后 self.findings 有值
            confirmed = sum(1 for f in findings if f.verdict == "confirmed")
            falsified = sum(1 for f in findings if f.verdict == "falsified")
            inconclusive = sum(1 for f in findings if f.verdict == "inconclusive")
            stage_status["verifier"] = "ok"
            print(f"  [OK] {len(findings)} 条判定完成")
            print(f"     confirmed: {confirmed}, falsified: {falsified}, inconclusive: {inconclusive}")
            for f in findings:
                if f.verdict == "confirmed":
                    print(f"     [BUG] [{f.severity}] {f.title[:80]}")
        except Exception as e:
            findings = []
            confirmed = 0
            stage_failures.append(f"verifier: {e}")
            stage_status["verifier"] = "FAILED_SAFE"
            print(f"  [FAIL] Verifier 失败: {e}")

        # Stage 4.5: Round 2 — feed confirmed findings back for deeper discovery
        round2_hypotheses = []
        confirmed = sum(1 for f in self.findings if f.verdict == "confirmed")
        if confirmed > 0:
            print(f"\n[Stage 4.5] Round 2 — feeding {confirmed} confirmed findings back...")
            try:
                confirmed_json = json.dumps([
                    {"title": f.title, "severity": f.severity, "verdict": f.verdict}
                    for f in self.findings if f.verdict == "confirmed"
                ], ensure_ascii=False, default=str)[:3000]
                round2_hypotheses = self.stage_reason_all(_world, prd_text, api_spec_text,
                    prior_findings=[
                        {"title": f.title, "severity": f.severity, "verdict": f.verdict}
                        for f in self.findings if f.verdict == "confirmed"
                    ])
                # Replace the empty observed_data with confirmed findings
                for i, h in enumerate(round2_hypotheses):
                    h["_round"] = 2
                print(f"  Round 2: {len(round2_hypotheses)} additional hypotheses")
            except Exception as e:
                print(f"  Round 2 failed: {e}")

        if round2_hypotheses:
            hypotheses.extend(round2_hypotheses)
            # Guard round 2 so a secondary executor/verifier crash cannot abort the
            # whole discover() run (previously surfaced as `confirmed` UnboundLocalError).
            try:
                r2_exec = self.stage_execute(round2_hypotheses, route_map)
                r2_findings = self.stage_verify(r2_exec)
                findings.extend(r2_findings)
                r2_confirmed = sum(1 for f in r2_findings if f.verdict == "confirmed")
                print(f"  Round 2 Verifier: {r2_confirmed} additional confirmed")
                confirmed += r2_confirmed
            except Exception as e:
                stage_failures.append(f"round2: {e}")
                print(f"  [FAIL] Round 2 execution/verify failed: {e}")

        # The verifier produces raw *signals*.  A signal can never become a
        # formal enterprise bug by itself.  Route every direct DiscoveryEngine
        # finding through the same adversarial/schema gate used by Flow discovery.
        # Phase92A: Evidence Bridge — normalize runtime probes → enrich → gate,
        # preserving four-layer state (raw_runtime / semantic / business_evidence / final_review).
        raw_confirmed_signals = sum(1 for f in findings if f.verdict == "confirmed")
        finding_gate_report: dict[str, Any] = {"status": "NOT_RUN"}

        # ── Phase92A: Normalize + Enrich all findings before gate ──
        try:
            from ai_test_asset_center.evidence_normalizer import normalize_finding_evidence
            from ai_test_asset_center.business_evidence_enricher import enrich_finding_evidence
            _bridge_available = True
        except ImportError:
            _bridge_available = False

        # Pre-enrich: attach enriched evidence to each finding before gate
        for finding in findings:
            raw_verdict = finding.verdict  # preserve stage_verify original
            raw_evidence = finding.evidence or {}
            calls = raw_evidence.get("calls", [])

            if _bridge_available and calls:
                try:
                    norm = normalize_finding_evidence(finding, calls)
                    enriched = enrich_finding_evidence(
                        finding, calls,
                        normalized=norm.get("runtime", {}),
                        semantic=norm.get("semantic", {}),
                        project_id=os.environ.get("QUALIBUG_PROJECT", "real_project_demo"),
                        policy_version=policy_version,
                    )
                    # Merge enriched fields into evidence without overwriting calls
                    for key in ("before_snapshot_ref", "after_snapshot_ref", "action_evidence_ref",
                                "entity_binding", "observer_refs", "cleanup", "flow_id",
                                "invariant_evidence_ref", "raw_evidence_refs", "missing_requirements"):
                        if enriched.get(key) and not raw_evidence.get(key):
                            raw_evidence[key] = enriched[key]
                    finding.evidence = raw_evidence
                    
                    # ── Phase92A: Four-layer state from enricher ──
                    finding._raw_runtime_verdict = enriched.get("raw_runtime_verdict", raw_verdict)
                    finding._semantic_verdict = enriched.get("semantic_verdict", raw_verdict)
                    finding._business_evidence_status = enriched.get("business_evidence_status", "NOT_ENRICHED")
                    finding._final_review_status = enriched.get("final_review_status", "NEEDS_MORE_EVIDENCE")
                    finding._compound_status = enriched.get("compound_status", "")
                    finding._enrichment_trace = enriched.get("enrichment_trace", [])
                except Exception:
                    finding._raw_runtime_verdict = raw_verdict
                    finding._semantic_verdict = raw_verdict
                    finding._business_evidence_status = "ENRICHMENT_FAILED"
                    finding._final_review_status = "NEEDS_MORE_EVIDENCE"
            else:
                finding._raw_runtime_verdict = raw_verdict
                finding._semantic_verdict = raw_verdict
                finding._business_evidence_status = "NOT_ENRICHED"
                finding._final_review_status = "NEEDS_MORE_EVIDENCE"

        try:
            from ai_test_asset_center.discovery_finding_gate import GATED_VERDICTS, gate_discovery_findings
            from ai_test_asset_center.policy_registry import get_policy_registry

            active_record = get_policy_registry().get_active()
            policy_version = active_record.policy_version if active_record else "baseline"
            gated_contracts, finding_gate_report = gate_discovery_findings(
                findings,
                project_id=os.environ.get("QUALIBUG_PROJECT", "real_project_demo"),
                policy_version=policy_version,
                context_artifact_id=artifact_id,
                enable_llm_disprover=False,
            )
            by_hypothesis = {
                str(row.get("hypothesis_id") or row.get("finding_id")): row
                for row in gated_contracts
            }
            for finding in findings:
                raw_verdict = getattr(finding, "_raw_runtime_verdict", finding.verdict)
                semantic_verdict = getattr(finding, "_semantic_verdict", finding.verdict)
                business_status = getattr(finding, "_business_evidence_status", "NOT_ENRICHED")
                contract = by_hypothesis.get(str(finding.hypothesis_id))
                if not contract:
                    # Phase92A: Preserve semantic verdict — gate missing does NOT invalidate it
                    finding._business_evidence_status = "PENDING_EVIDENCE"
                    finding._final_review_status = "NEEDS_MORE_EVIDENCE"
                    finding._raw_runtime_verdict = raw_verdict
                    finding._semantic_verdict = semantic_verdict
                    finding.evidence = {
                        **(finding.evidence or {}),
                        "finding_gate": {"verdict": "NEEDS_MORE_EVIDENCE", "reason": "missing gate result"},
                        "raw_runtime_verdict": raw_verdict,
                        "semantic_verdict": semantic_verdict,
                        "business_evidence_status": "PENDING_EVIDENCE",
                        "final_review_status": "NEEDS_MORE_EVIDENCE",
                    }
                    # PHASE92A: semantic confirmed → preserve, mark as needs_more_evidence for UI.
                    # Non-confirmed stage verdicts are not bugs and must not be
                    # inflated into needs_more_evidence just because the customer
                    # evidence gate did not produce a contract.
                    if raw_verdict == "confirmed":
                        finding.verdict = "needs_more_evidence"  # UI-level: needs more business evidence
                    else:
                        finding.verdict = raw_verdict or "inconclusive"
                    continue
                gate_verdict = str(contract.get("verdict") or "NEEDS_MORE_EVIDENCE").upper()
                # Phase92A: Do NOT blindly overwrite semantic verdict
                gated_mapped = GATED_VERDICTS.get(gate_verdict, "needs_more_evidence")
                contract_semantic = contract.get("semantic_verdict", "")
                contract_business = contract.get("business_evidence_status", "")
                contract_final = contract.get("final_review_status", "")
                contract_rt_gate = contract.get("runtime_gate_status", "")
                contract_biz_gate = contract.get("business_gate_status", "")
                
                # PHASE92A: Use four-layer state from gate.
                if raw_verdict == "confirmed" and gated_mapped in ("needs_more_evidence", "blocked"):
                    # Semantic confirmed but gate wants more evidence → PRESERVE semantic truth
                    finding._business_evidence_status = contract_business or ("PENDING_" + gate_verdict)
                    finding._final_review_status = "NEEDS_MORE_EVIDENCE"
                    # Finding verdict: needs_more_evidence for UI (semantic truth preserved in evidence)
                    finding.verdict = "needs_more_evidence"
                elif gated_mapped == "validated_candidate":
                    finding._business_evidence_status = "VALIDATED"
                    finding._final_review_status = "PENDING_REVIEW"
                    finding.verdict = "validated_candidate"
                elif raw_verdict in {"falsified", "inconclusive", "execution_error"}:
                    # A negative or indeterminate verifier result is not a pending
                    # customer bug.  Preserve it so healthy endpoints and empty
                    # read-only responses do not pollute needs_more_evidence.
                    finding._business_evidence_status = contract_business or gate_verdict
                    finding._final_review_status = contract_final or raw_verdict
                    finding.verdict = raw_verdict
                else:
                    finding._business_evidence_status = contract_business or gate_verdict
                    finding._final_review_status = contract_final or gated_mapped
                    finding.verdict = gated_mapped
                finding.evidence = {
                    **(finding.evidence or {}),
                    "business_finding": contract,
                    "finding_gate": {
                        "verdict": gate_verdict,
                        "runtime_gate_status": contract_rt_gate,
                        "business_gate_status": contract_biz_gate,
                        "business_gate_missing": contract.get("business_gate_missing", []),
                        "schema": contract.get("_schema_validation", {}),
                        "evidence_verification": contract.get("_evidence_verification", {}),
                    },
                    "raw_runtime_verdict": raw_verdict,
                    "semantic_verdict": semantic_verdict,
                    "business_evidence_status": finding._business_evidence_status,
                    "final_review_status": finding._final_review_status,
                }
            self._last_finding_gate_report = finding_gate_report
        except Exception as gate_error:
            stage_failures.append(f"finding_gate: {gate_error}")
            finding_gate_report = {"status": "FAILED_SAFE", "error": str(gate_error)[:300]}
            for finding in findings:
                finding._raw_runtime_verdict = getattr(finding, "_raw_runtime_verdict", finding.verdict)
                finding._semantic_verdict = getattr(finding, "_semantic_verdict", finding.verdict)
                finding._business_evidence_status = "GATE_ERROR"
                finding._final_review_status = "BLOCKED"
                finding.evidence = {
                    **(finding.evidence or {}),
                    "finding_gate": finding_gate_report,
                    "raw_runtime_verdict": finding._raw_runtime_verdict,
                    "semantic_verdict": finding._semantic_verdict,
                    "business_evidence_status": "GATE_ERROR",
                    "final_review_status": "BLOCKED",
                }

        self.findings = findings

        graph_finding_update: dict[str, Any] = {}
        if graph is not None:
            try:
                graph_finding_update = graph.record_findings(
                    self.findings,
                    run_id=discovery_run_id,
                    policy_version=policy_version,
                )
            except Exception as graph_finding_error:
                # Preserve gate results and make the graph write failure visible.
                stage_failures.append(f"cognitive_graph_findings: {graph_finding_error}")
                graph_finding_update = {"status": "FAILED_SAFE", "error": str(graph_finding_error)[:300]}

        elapsed = time.time() - t0
        validated_candidates = sum(1 for f in self.findings if f.verdict == "validated_candidate")
        try:
            persist_deployment_config_snapshot(self._deployment_config_snapshot)
        except Exception as e:
            print(f"  [WARN] Deployment config persistence failed (non-fatal): {e}", flush=True)

        # ── P3: Bug Ontology + Invariant Engine Integration ──────────
        # Generate behavior slices, evaluate invariants, and compute coverage.
        ontology_summary: dict[str, Any] = {}
        invariant_results: dict[str, Any] = {}
        coverage_matrix_data: dict[str, Any] = {}
        evidence_classification: dict[str, Any] = {"confirmed": 0, "candidate": 0, "clue": 0}

        try:
            from ai_test_asset_center.context_extractor import extract_context
            from ai_test_asset_center.bug_ontology_registry import get_ontology_registry
            from ai_test_asset_center.behavior_slice_gen import BehaviorSliceGenerator
            from ai_test_asset_center.invariant_engine import evaluate_all_invariants, invariant_coverage_report
            from ai_test_asset_center.coverage_matrix import compute_coverage_matrix

            # Extract context from available data
            ctx = extract_context(prd_text, api_spec_text)
            registry = get_ontology_registry()

            # Generate behavior slices
            gen = BehaviorSliceGenerator(ctx, registry)
            slices = gen.generate()
            print(f"  [OK] Ontology: generated {gen.count()} behavior slices", flush=True)

            # Evaluate invariants on findings' evidence
            for finding in self.findings:
                evidence = finding.evidence or {}
                if evidence:
                    inv_result = evaluate_all_invariants(evidence)
                    evidence["_invariant_results"] = {
                        k: v.to_dict() for k, v in inv_result.items()
                    }

            # Classify findings by evidence completeness
            for finding in self.findings:
                evidence = finding.evidence or {}
                has_execution = bool(evidence.get("calls") or evidence.get("has_before_snapshot"))
                has_req_resp = has_execution and bool(
                    evidence.get("calls") and any(
                        c.get("results", {}).get("admin", {}).get("status")
                        for c in evidence.get("calls", []) if isinstance(c, dict)
                    )
                )
                has_full_evidence = has_req_resp and bool(
                    evidence.get("before_snapshot_ref") or evidence.get("after_snapshot_ref") or
                    evidence.get("entity_binding")
                )

                if finding.verdict in ("confirmed", "validated_candidate") and has_full_evidence:
                    evidence_classification["confirmed"] += 1
                elif has_req_resp:
                    evidence_classification["candidate"] += 1
                else:
                    evidence_classification["clue"] += 1

            # Compute coverage matrix
            block_list = []
            matrix = compute_coverage_matrix(
                [s.to_dict() for s in slices],
                project_id=os.environ.get("QUALIBUG_PROJECT", "real_project_demo"),
                scan_id=discovery_run_id,
                findings=[{
                    "hypothesis_id": f.hypothesis_id,
                    "verdict": f.verdict,
                    "customer_delivery_status": getattr(f, "customer_delivery_status", "clue"),
                } for f in self.findings],
                blocked_paths=block_list,
            )
            coverage_matrix_data = matrix.to_dict()

            ontology_summary = {
                "total_families": registry.count_families(),
                "total_entries": registry.count_entries(),
                "total_slices": gen.count(),
                "coverage_by_family": registry.coverage_summary(),
            }
            print(f"  [OK] Coverage: {matrix.executed_slices}/{matrix.total_slices} slices executed, "
                  f"confirmed={evidence_classification['confirmed']}, "
                  f"candidate={evidence_classification['candidate']}, "
                  f"clue={evidence_classification['clue']}", flush=True)
        except Exception as onto_err:
            print(f"  [WARN] Ontology integration degraded: {onto_err}", flush=True)
            ontology_summary = {"error": str(onto_err)[:300]}

        return {
            "pipeline": "autonomous_discovery_v1",
            "runtime_status": "FAILED" if stage_failures else "OK",
            "stage_failures": stage_failures,
            "stage_status": stage_status,
            "operator_note": (
                "发现链路关键阶段失败：空 findings 表示执行/验证中断，不是「系统无缺陷」。"
                if any(str(stage_status.get(key) or "") == "FAILED_SAFE" for key in ("reasoner", "executor", "verifier"))
                else ""
            ),
            "duration_seconds": round(elapsed, 1),
            "deployment_config": dict(self._deployment_config_snapshot),
            "deployment_config_drift": dict(self._deployment_config_drift),
            "deployment_drift_unlock": dict(self._deployment_drift_unlock),
            "stages": {
                "reader": {
                    "entities": len(entities) if 'entities' in dir() else 0,
                    "artifact_id": artifact_id,
                    "artifact_status": artifact_status,
                },
                "cognitive_graph": {
                    "mode": graph_mode,
                    "stats": graph_sync,
                    "frontier_selection": frontier_selection or {},
                    "context_refs": len(graph_context_pack.get("context_refs") or []),
                    "graph_ready": bool(graph_context_pack.get("graph_ready")),
                    "finding_update": graph_finding_update,
                    "ab": graph_ab_report,
                },
                "reasoner": {"hypotheses": len(hypotheses), "status": stage_status.get("reasoner", "ok")},
                "executor": {"probes": len(execution_results), "status": stage_status.get("executor", "ok")},
                "verifier": {
                    "total": len(findings),
                    "status": stage_status.get("verifier", "ok"),
                    "raw_confirmed_signals": raw_confirmed_signals,
                    "validated_candidates": validated_candidates,
                    "rejected": sum(1 for f in self.findings if f.verdict == "rejected"),
                    "needs_more_evidence": sum(1 for f in self.findings if f.verdict == "needs_more_evidence"),
                },
                "finding_gate": finding_gate_report.get("meta", finding_gate_report),
            },
            "findings": [{
                "id": f.hypothesis_id,
                "title": f.title,
                "verdict": (
                    "risk_clue" if (not self._tokens_authentic and f.verdict in ("confirmed", "validated_candidate"))
                    else f.verdict
                ),
                "severity": f.severity,
                "finding_gate": (f.evidence or {}).get("finding_gate", {}),
                "raw_runtime_verdict": (f.evidence or {}).get("raw_runtime_verdict", f.verdict),
                "semantic_verdict": (f.evidence or {}).get("semantic_verdict", f.verdict),
                "business_evidence_status": (f.evidence or {}).get("business_evidence_status", "NOT_ENRICHED"),
                "final_review_status": (f.evidence or {}).get("final_review_status", "NOT_GATED"),
                "evidence_class": "reproducible_bug" if self._tokens_authentic else "risk_clue",
                "auth_status": "real" if self._tokens_authentic else "unauthenticated",
                "evidence": {
                    "calls_count": len((f.evidence or {}).get("calls", [])),
                    "has_before_snapshot": bool((f.evidence or {}).get("before_snapshot_ref")),
                    "has_after_snapshot": bool((f.evidence or {}).get("after_snapshot_ref")),
                    "has_entity_binding": bool((f.evidence or {}).get("entity_binding", {}).get("entity_id")),
                    "cleanup_status": (f.evidence or {}).get("cleanup", {}).get("status", "?"),
                },
            } for f in self.findings],
            "auto_har": self._auto_har_report(),
            # ── P3: Bug Ontology + Coverage Matrix ──
            "ontology": ontology_summary,
            "coverage_matrix": coverage_matrix_data,
            "evidence_classification": evidence_classification,
        }

    def _auto_har_report(self) -> dict[str, Any]:
        """Generate an automatic HAR analysis report from captured probe traffic.

        Called automatically at the end of every discovery run — the user
        never needs to provide a HAR file or log file.
        """
        entries = self._har_entries
        error_patterns = self._har_error_patterns

        if not entries:
            return {"status": "no_traffic", "entries": 0, "log_analysis": {}}

        # Deduplicate endpoints
        endpoint_counts: dict[tuple[str, str], int] = {}
        error_by_endpoint: dict[tuple[str, str], list[dict]] = {}
        for ep in error_patterns:
            key = (ep["method"], ep["endpoint"])
            error_by_endpoint.setdefault(key, []).append(ep)
        for entry in entries:
            key = (entry["request"]["method"], entry["request"]["url"])
            endpoint_counts[key] = endpoint_counts.get(key, 0) + 1

        total_errors = len(error_patterns)
        error_by_status: dict[int, int] = {}
        for ep in error_patterns:
            s = ep["status"]
            error_by_status[s] = error_by_status.get(s, 0) + 1

        # Feed error patterns through the log analyzer inline
        log_analysis = self._analyze_captured_errors(error_patterns)

        return {
            "status": "captured",
            "total_http_calls": len(entries),
            "unique_endpoints": len(endpoint_counts),
            "total_error_responses": total_errors,
            "error_by_status": error_by_status,
            "error_endpoints": [
                {"method": k[0], "path": k[1], "errors": len(v)}
                for k, v in sorted(error_by_endpoint.items(), key=lambda x: -len(x[1]))[:20]
            ],
            "log_analysis": log_analysis,
        }

    def _analyze_captured_errors(self, error_patterns: list[dict]) -> dict[str, Any]:
        """Feed auto-captured probe errors through the log analyzer inline.

        Produces error clusters and candidates from real probe traffic,
        without requiring any user-provided log files.
        """
        if not error_patterns:
            return {"error_clusters": [], "candidates_from_log": []}

        # Build a synthetic log from error patterns for the log analyzer
        log_lines: list[str] = []
        for ep in error_patterns:
            status = ep.get("status", 0)
            level = "ERROR" if status >= 500 else "WARN"
            body = ep.get("response_body", "")[:200]
            # Try to extract error message from JSON response
            err_msg = ""
            if body:
                try:
                    data = json.loads(body)
                    for key in ("message", "error", "detail"):
                        if key in data and isinstance(data[key], str):
                            err_msg = data[key]
                            break
                except (json.JSONDecodeError, TypeError):
                    pass
            if not err_msg:
                err_msg = body.replace("\n", " ")[:100]
            log_lines.append(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} {level} "
                f"{ep.get('method', 'GET')}:{ep.get('endpoint', '/')} "
                f"HTTP{status} {err_msg}"
            )

        if not log_lines:
            return {"error_clusters": [], "candidates_from_log": []}

        # Write to temp file and run through log analyzer
        import tempfile as _tmp
        tmp_path = ""
        try:
            with _tmp.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as tf:
                tf.write("\n".join(log_lines))
                tmp_path = tf.name

            from ai_test_asset_center.log_analyzer import analyze_logs, log_errors_to_candidates
            result = analyze_logs(tmp_path)
            clusters = [
                {"error_type": c.error_type, "count": c.count,
                 "message": c.message_pattern[:120], "severity": c.severity}
                for c in result.get("error_clusters", [])[:20]
            ]
            candidates = log_errors_to_candidates(tmp_path)
            return {
                "error_clusters": clusters,
                "candidates_from_log": candidates[:20],
            }
        except Exception as e:
            return {"error_clusters": [], "candidates_from_log": [], "error": str(e)[:200]}
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink()
                except OSError:
                    pass

