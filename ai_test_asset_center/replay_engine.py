"""Replay engine for evidence-backed defect re-verification.

Replay is constrained to the project's exact approved target. A result is
tri-state: reproduced, not_reproduced, or inconclusive. Only an explicit,
evaluable replay oracle may produce ``not_reproduced`` and therefore permit a
defect to be closed by the HTTP lifecycle authority.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .credential_crypto import (
    decrypt as _decrypt_credential,
    is_encrypted as _is_encrypted_credential,
)
from .ssrf_guard import safe_urlopen
from .target_policy import (
    approved_target_authority,
    build_target_policy_decision,
    normalize_base_url,
)


_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SENSITIVE_HEADER_PARTS = (
    "authorization",
    "cookie",
    "token",
    "api-key",
    "apikey",
    "secret",
    "password",
)
_SAFE_RESPONSE_HEADERS = frozenset(
    {"content-type", "content-length", "etag", "last-modified", "retry-after"}
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _effective_port(url: str) -> int:
    parsed = urlsplit(url)
    return parsed.port or (443 if parsed.scheme.lower() == "https" else 80)


def _json_path(value: Any, path: str) -> Any:
    current = value
    for part in [item for item in str(path or "").split(".") if item]:
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return None
    return current


def _normalized_body(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except Exception:
        return " ".join(text.split())
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ReplayEngine:
    """Re-execute one finding against its exact approved project target."""

    def __init__(self, root: Path, project_id: str):
        self.root = Path(root).resolve()
        self.project_id = str(project_id)
        self._config: dict[str, Any] | None = None
        self._connector_config: dict[str, Any] | None = None
        self._target_config: dict[str, Any] | None = None

    def _load_json_object(self, path: Path, label: str) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid {label}: {path}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{label} must be an object: {path}")
        return parsed

    def _load_service_config(self) -> dict[str, Any]:
        if self._config is None:
            path = self.root / "platform_workspace" / self.project_id / "multi_service_config.json"
            parsed = self._load_json_object(path, "replay service config")
            services = parsed.get("services", [])
            if services and (
                not isinstance(services, list)
                or any(not isinstance(item, dict) for item in services)
            ):
                raise ValueError(
                    f"replay service config services must be a list of objects: {path}"
                )
            self._config = parsed
        return dict(self._config)

    def _load_connector_config(self) -> dict[str, Any]:
        if self._connector_config is None:
            path = (
                self.root
                / "platform_workspace"
                / self.project_id
                / "enterprise_pilot_runtime"
                / "connector_registry.json"
            )
            parsed = self._load_json_object(path, "replay connector config")
            connectors = parsed.get("connectors", [])
            if connectors and (
                not isinstance(connectors, list)
                or any(not isinstance(item, dict) for item in connectors)
            ):
                raise ValueError(
                    f"replay connector config connectors must be a list of objects: {path}"
                )
            self._connector_config = parsed
        return dict(self._connector_config)

    def _load_target_config(self) -> dict[str, Any]:
        if self._target_config is None:
            path = (
                self.root
                / "platform_inputs"
                / self.project_id
                / "real_project_config.json"
            )
            self._target_config = self._load_json_object(path, "project target config")
        return dict(self._target_config)

    def _target_policy(self) -> dict[str, Any]:
        config = self._load_target_config()
        return build_target_policy_decision(
            requested_base_url=config.get("base_url"),
            approved_base_url=config.get("approved_base_url"),
            environment_type=config.get("environment_type"),
            environment_ref=config.get("environment_ref"),
            execution_mode=config.get("execution_mode") or "safe_read_only",
            runtime_status=config.get("runtime_status") or "approved",
        )

    def _approved_target(self) -> dict[str, Any]:
        grant = approved_target_authority(self.project_id, self.root)
        if not isinstance(grant, dict) or grant.get("approved") is not True:
            reason = _text((grant or {}).get("reason_code")) or "PROJECT_TARGET_NOT_APPROVED"
            raise ValueError(reason)
        base_url = normalize_base_url(grant.get("base_url"))
        host = _text(grant.get("host")).lower()
        if not base_url or not host:
            raise ValueError("PROJECT_TARGET_NOT_APPROVED")
        return {**grant, "base_url": base_url, "host": host}

    def _url_within_approved_base(self, url: str, approved_base_url: str) -> bool:
        try:
            candidate = urlsplit(url)
            approved = urlsplit(approved_base_url)
        except ValueError:
            return False
        if candidate.scheme.lower() != approved.scheme.lower():
            return False
        if (candidate.hostname or "").lower() != (approved.hostname or "").lower():
            return False
        if _effective_port(url) != _effective_port(approved_base_url):
            return False
        approved_path = (approved.path or "").rstrip("/")
        candidate_path = candidate.path or "/"
        if not approved_path:
            return True
        return candidate_path == approved_path or candidate_path.startswith(approved_path + "/")

    def _resolve_replay_url(self, path: str, base_url_override: str = "") -> tuple[str, dict[str, Any]]:
        grant = self._approved_target()
        approved_base = str(grant["base_url"])
        override = normalize_base_url(base_url_override) if _text(base_url_override) else ""
        if override and override != approved_base:
            raise ValueError("REPLAY_TARGET_OVERRIDE_NOT_APPROVED")
        raw_path = _text(path)
        if not raw_path:
            raise ValueError("REPLAY_PATH_MISSING")
        if raw_path.startswith(("http://", "https://")):
            full_url = raw_path
        else:
            full_url = approved_base.rstrip("/") + "/" + raw_path.lstrip("/")
        if not self._url_within_approved_base(full_url, approved_base):
            raise ValueError("REPLAY_URL_OUTSIDE_APPROVED_TARGET")
        return full_url, grant

    def _get_auth_header(self) -> tuple[str, str]:
        config = self._load_service_config()
        for service in config.get("services") or []:
            auth = service.get("auth") if isinstance(service.get("auth"), dict) else {}
            bearer = _text(auth.get("bearer_token"))
            if bearer:
                if _is_encrypted_credential(bearer):
                    bearer = _decrypt_credential(bearer)
                return "Authorization", f"Bearer {bearer}"
            api_key = _text(auth.get("api_key"))
            if api_key:
                if _is_encrypted_credential(api_key):
                    api_key = _decrypt_credential(api_key)
                return "X-API-Key", api_key
        connector_data = self._load_connector_config()
        for connector in connector_data.get("connectors") or []:
            if connector.get("enabled", True) is not True:
                continue
            credential = _text(connector.get("credential_ref"))
            if credential and _is_encrypted_credential(credential):
                return "Authorization", f"Bearer {_decrypt_credential(credential)}"
        return "", ""

    def _load_test_credentials(self) -> dict[str, Any]:
        profile = self._load_connector_config().get("test_profile")
        if not isinstance(profile, dict):
            return {}
        credentials = profile.get("test_credentials")
        return dict(credentials) if isinstance(credentials, dict) else {}

    def _open_approved(
        self,
        request: urllib.request.Request,
        *,
        grant: dict[str, Any],
        timeout: float,
    ) -> Any:
        if not self._url_within_approved_base(request.full_url, str(grant["base_url"])):
            raise ValueError("REPLAY_URL_OUTSIDE_APPROVED_TARGET")
        return safe_urlopen(
            request,
            timeout=timeout,
            allow_internal=False,
            approved_host=str(grant["host"]),
        )

    def _auto_login(self, grant: dict[str, Any]) -> tuple[str, str]:
        credentials = self._load_test_credentials()
        buyer = credentials.get("buyer") if isinstance(credentials.get("buyer"), dict) else {}
        email = _text(buyer.get("email"))
        password = _text(buyer.get("password"))
        if not email or not password:
            return "", ""
        login_url = str(grant["base_url"]).rstrip("/") + "/api/auth/login"
        body = json.dumps({"email": email, "password": password}).encode("utf-8")
        request = urllib.request.Request(
            login_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._open_approved(request, grant=grant, timeout=10) as response:
                parsed = json.loads(response.read(200_000).decode("utf-8"))
        except Exception:
            return "", ""
        if not isinstance(parsed, dict):
            return "", ""
        token = _text(parsed.get("token") or parsed.get("access_token"))
        return ("Authorization", f"Bearer {token}") if token else ("", "")

    def _find_finding(self, finding_id: str, risks: list[dict[str, Any]]) -> dict[str, Any] | None:
        for risk in risks:
            if not isinstance(risk, dict):
                continue
            identity = _text(
                risk.get("id")
                or risk.get("risk_id")
                or risk.get("finding_id")
                or risk.get("bug_id")
            )
            if identity == finding_id:
                return risk
        return None

    def _write_replay_allowed(self, finding: dict[str, Any]) -> tuple[bool, str]:
        contract = finding.get("replay_contract")
        if not isinstance(contract, dict):
            return False, "REPLAY_WRITE_CONTRACT_MISSING"
        if contract.get("approved_write") is not True:
            return False, "REPLAY_WRITE_NOT_APPROVED"
        policy = self._target_policy()
        if policy.get("write_allowed") is not True:
            return False, "REPLAY_TARGET_POLICY_BLOCKED"
        return True, "APPROVED"

    @staticmethod
    def _redacted_headers(headers: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in headers.items():
            lower = key.lower()
            result[key] = "***" if any(part in lower for part in _SENSITIVE_HEADER_PARTS) else value
        return result

    @staticmethod
    def _response_headers(headers: Any) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in dict(headers or {}).items()
            if str(key).lower() in _SAFE_RESPONSE_HEADERS
        }

    @staticmethod
    def _request_body(finding: dict[str, Any], method: str) -> bytes | None:
        if method not in _WRITE_METHODS:
            return None
        har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
        reproduction = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
        value = har.get("request_body")
        if value in (None, ""):
            value = reproduction.get("request_body")
        if value in (None, ""):
            return None
        if isinstance(value, bytes):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False).encode("utf-8")
        return str(value).encode("utf-8")

    def replay(
        self,
        finding_id: str,
        risks: list[dict[str, Any]],
        base_url_override: str = "",
    ) -> dict[str, Any]:
        finding = self._find_finding(finding_id, risks)
        if finding is None:
            return {"ok": False, "finding_id": finding_id, "error": "FINDING_NOT_FOUND"}

        method = _text(
            finding.get("repro_method")
            or finding.get("_api_method")
            or finding.get("method")
            or "GET"
        ).upper()
        if method not in _READ_ONLY_METHODS | _WRITE_METHODS:
            return {"ok": False, "finding_id": finding_id, "error": "REPLAY_METHOD_UNSUPPORTED"}
        path = _text(
            finding.get("repro_path")
            or finding.get("_api_path")
            or finding.get("path")
        )
        if not path:
            return {"ok": False, "finding_id": finding_id, "error": "REPLAY_PATH_MISSING"}
        if "{" in path or "}" in path:
            return {
                "ok": False,
                "finding_id": finding_id,
                "error": "REPLAY_PATH_PLACEHOLDER_UNRESOLVED",
            }
        if method in _WRITE_METHODS:
            allowed, reason = self._write_replay_allowed(finding)
            if not allowed:
                return {"ok": False, "finding_id": finding_id, "error": reason}

        try:
            full_url, grant = self._resolve_replay_url(path, base_url_override)
        except Exception as exc:
            return {"ok": False, "finding_id": finding_id, "error": str(exc)}

        auth_name, auth_value = self._get_auth_header()
        if not auth_value:
            auth_name, auth_value = self._auto_login(grant)
        headers: dict[str, str] = {}
        if auth_name and auth_value:
            headers[auth_name] = auth_value
        body = self._request_body(finding, method)
        if body is not None:
            headers["Content-Type"] = "application/json"

        request_info = {
            "method": method,
            "url": full_url,
            "headers": self._redacted_headers(headers),
            "body_present": body is not None,
            "body_bytes": len(body or b""),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        started = time.perf_counter()
        try:
            request = urllib.request.Request(
                full_url,
                data=body,
                headers=headers,
                method=method,
            )
            with self._open_approved(request, grant=grant, timeout=30) as response:
                status = int(response.status)
                response_headers = self._response_headers(response.headers)
                response_body = response.read(500_000).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            response_headers = self._response_headers(exc.headers)
            response_body = exc.read(500_000).decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            return {
                "ok": False,
                "finding_id": finding_id,
                "error": f"REPLAY_CONNECTION_FAILED:{exc.reason}",
                "request": request_info,
            }
        except Exception as exc:
            return {
                "ok": False,
                "finding_id": finding_id,
                "error": f"REPLAY_EXECUTION_FAILED:{type(exc).__name__}:{exc}",
                "request": request_info,
            }

        response_info = {
            "status_code": status,
            "headers": response_headers,
            "body": response_body[:5000],
            "body_truncated": len(response_body) > 5000,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        original = self._extract_original_evidence(finding)
        verdict, oracle = self._evaluate_replay(finding, original, response_info)
        return {
            "ok": True,
            "finding_id": finding_id,
            "request": request_info,
            "response": response_info,
            "success": True if verdict == "reproduced" else False if verdict == "not_reproduced" else None,
            "verdict": verdict,
            "oracle": oracle,
            "original_evidence": original,
            "diff": self._compute_diff(original, response_info),
        }

    def replay_batch(
        self,
        finding_ids: list[str],
        risks: list[dict[str, Any]],
        max_workers: int = 4,
    ) -> list[dict[str, Any]]:
        workers = max(1, min(int(max_workers or 1), 8))
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self.replay, identity, risks): identity for identity in finding_ids}
            try:
                for future in as_completed(futures, timeout=120):
                    identity = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        results.append(
                            {
                                "ok": False,
                                "finding_id": identity,
                                "error": f"REPLAY_BATCH_FAILED:{type(exc).__name__}:{exc}",
                            }
                        )
            except TimeoutError:
                completed = {row.get("finding_id") for row in results if isinstance(row, dict)}
                for identity in finding_ids:
                    if identity not in completed:
                        results.append(
                            {"ok": False, "finding_id": identity, "error": "REPLAY_BATCH_TIMEOUT"}
                        )
        return results

    def _extract_original_evidence(self, finding: dict[str, Any]) -> dict[str, Any]:
        har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
        raw_status = har.get("status_code") or evidence.get("status_code") or evidence.get("response_status")
        try:
            status = int(raw_status or 0)
        except (TypeError, ValueError):
            status = 0
        return {
            "status_code": status,
            "response_body_excerpt": _text(
                har.get("response_body")
                or evidence.get("response")
                or evidence.get("actual")
            )[:1000],
            "har_actor": _text(har.get("actor")),
        }

    def _explicit_oracle(self, finding: dict[str, Any]) -> dict[str, Any]:
        for value in (
            finding.get("replay_oracle"),
            finding.get("oracle"),
            (finding.get("reproduction") or {}).get("oracle")
            if isinstance(finding.get("reproduction"), dict)
            else None,
        ):
            if isinstance(value, dict) and value:
                return dict(value)
        return {}

    def _evaluate_explicit_oracle(
        self,
        oracle: dict[str, Any],
        response: dict[str, Any],
    ) -> tuple[bool | None, list[dict[str, Any]]]:
        checks: list[dict[str, Any]] = []
        status = int(response.get("status_code") or 0)
        body = str(response.get("body") or "")
        expected_status = oracle.get("expected_status")
        if expected_status is not None:
            values = expected_status if isinstance(expected_status, list) else [expected_status]
            expected: set[int] = set()
            for value in values:
                try:
                    expected.add(int(value))
                except (TypeError, ValueError):
                    return None, [{"check": "expected_status", "evaluable": False}]
            checks.append(
                {
                    "check": "expected_status",
                    "expected": sorted(expected),
                    "actual": status,
                    "passed": status in expected,
                }
            )
        contains = oracle.get("expected_body_contains")
        if contains is not None:
            values = contains if isinstance(contains, list) else [contains]
            tokens = [str(value) for value in values if str(value)]
            if not tokens:
                return None, [{"check": "expected_body_contains", "evaluable": False}]
            checks.append(
                {
                    "check": "expected_body_contains",
                    "expected": tokens,
                    "passed": all(token in body for token in tokens),
                }
            )
        excludes = oracle.get("expected_body_not_contains")
        if excludes is not None:
            values = excludes if isinstance(excludes, list) else [excludes]
            tokens = [str(value) for value in values if str(value)]
            if not tokens:
                return None, [{"check": "expected_body_not_contains", "evaluable": False}]
            checks.append(
                {
                    "check": "expected_body_not_contains",
                    "expected": tokens,
                    "passed": all(token not in body for token in tokens),
                }
            )
        expected_fields = oracle.get("expected_json_fields")
        if expected_fields is not None:
            if not isinstance(expected_fields, dict) or not expected_fields:
                return None, [{"check": "expected_json_fields", "evaluable": False}]
            try:
                parsed_body = json.loads(body)
            except Exception:
                checks.append(
                    {
                        "check": "expected_json_fields",
                        "expected": expected_fields,
                        "passed": False,
                        "reason": "response_not_json",
                    }
                )
            else:
                checks.append(
                    {
                        "check": "expected_json_fields",
                        "expected": expected_fields,
                        "passed": all(
                            _json_path(parsed_body, path) == expected
                            for path, expected in expected_fields.items()
                        ),
                    }
                )
        if not checks:
            return None, []
        return all(bool(check.get("passed")) for check in checks), checks

    def _evaluate_replay(
        self,
        finding: dict[str, Any],
        original: dict[str, Any],
        response: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        explicit = self._explicit_oracle(finding)
        if explicit:
            matched, checks = self._evaluate_explicit_oracle(explicit, response)
            if matched is True:
                return "reproduced", {"basis": "explicit_replay_oracle", "checks": checks}
            if matched is False:
                return "not_reproduced", {"basis": "explicit_replay_oracle", "checks": checks}
            return "inconclusive", {
                "basis": "explicit_replay_oracle",
                "checks": checks,
                "reason": "oracle_not_evaluable",
            }

        original_status = int(original.get("status_code") or 0)
        original_body = _normalized_body(str(original.get("response_body_excerpt") or ""))
        replay_status = int(response.get("status_code") or 0)
        replay_body = _normalized_body(str(response.get("body") or ""))
        if original_status and original_body and original_status == replay_status and original_body == replay_body:
            return "reproduced", {
                "basis": "exact_original_evidence_match",
                "checks": [
                    {"check": "status", "passed": True},
                    {"check": "body", "passed": True},
                ],
            }
        return "inconclusive", {
            "basis": "insufficient_replay_oracle",
            "reason": "status_only_or_non_exact_evidence_cannot_close_or_confirm_defect",
        }

    @staticmethod
    def _compute_diff(original: dict[str, Any], replay_response: dict[str, Any]) -> dict[str, Any]:
        original_status = int(original.get("status_code") or 0)
        replay_status = int(replay_response.get("status_code") or 0)
        original_body = _normalized_body(str(original.get("response_body_excerpt") or ""))
        replay_body = _normalized_body(str(replay_response.get("body") or ""))
        return {
            "status_match": bool(original_status and original_status == replay_status),
            "body_match": bool(original_body and replay_body and original_body == replay_body),
            "original_status_available": bool(original_status),
            "original_body_available": bool(original_body),
        }


__all__ = ["ReplayEngine"]
