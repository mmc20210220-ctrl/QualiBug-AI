"""Target system profiling for adaptive probe strategy.

This module automatically detects target system characteristics and adjusts
probe strategies accordingly. Profiles are cached for reuse across scans.

Key features:
- Authentication mechanism detection
- Response format analysis
- Error handling style detection
- API convention detection (REST, GraphQL, RPC)
- Adaptive probe strategy recommendation
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

# ── Default profile storage path ──
_DEFAULT_PROFILE_DIR = Path.home() / ".qualibug" / "profiles"


class TargetProfiler:
    """Profiles target systems for adaptive probe strategy."""

    def __init__(self, base_url: str = "", profile_dir: Path | None = None):
        self.base_url = base_url.rstrip("/")
        self.profile_dir = profile_dir or _DEFAULT_PROFILE_DIR
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._profile = self._load_profile()

    def _profile_key(self) -> str:
        """Generate a stable key for this target."""
        return hashlib.sha256(self.base_url.encode()).hexdigest()[:16]

    def _profile_file(self) -> Path:
        return self.profile_dir / f"target_profile_{self._profile_key()}.json"

    def _load_profile(self) -> dict[str, Any]:
        """Load existing profile or create new one."""
        path = self._profile_file()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "base_url": self.base_url,
            "created_at": time.time(),
            "updated_at": time.time(),
            "auth_type": "unknown",
            "response_format": "unknown",
            "error_style": "unknown",
            "api_style": "unknown",
            "conventions": {},
            "probe_adjustments": {},
            "observations": [],
        }

    def _save_profile(self) -> None:
        """Persist profile."""
        self._profile["updated_at"] = time.time()
        path = self._profile_file()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._profile, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def observe_response(
        self,
        method: str,
        path: str,
        status: int,
        headers: dict[str, str],
        body: Any,
    ) -> None:
        """Record an observation from a probe response."""
        observation = {
            "timestamp": time.time(),
            "method": method.upper(),
            "path": path,
            "status": status,
        }

        # Detect auth type from headers/status
        self._detect_auth_type(status, headers, body)

        # Detect response format
        self._detect_response_format(headers, body)

        # Detect error handling style
        if status >= 400:
            self._detect_error_style(status, body)

        # Detect API style
        self._detect_api_style(method, path, headers)

        # Store observation (keep last 100)
        self._profile["observations"].append(observation)
        if len(self._profile["observations"]) > 100:
            self._profile["observations"] = self._profile["observations"][-100:]

        self._save_profile()

    def _detect_auth_type(self, status: int, headers: dict[str, str], body: Any) -> None:
        """Detect authentication mechanism."""
        if self._profile["auth_type"] != "unknown":
            return

        headers_lower = {k.lower(): v for k, v in headers.items()}

        # Check for common auth headers
        if "authorization" in headers_lower:
            auth_value = headers_lower["authorization"].lower()
            if auth_value.startswith("bearer"):
                self._profile["auth_type"] = "bearer_token"
            elif auth_value.startswith("basic"):
                self._profile["auth_type"] = "basic_auth"
            else:
                self._profile["auth_type"] = "custom_header"
        elif "x-api-key" in headers_lower or "api-key" in headers_lower:
            self._profile["auth_type"] = "api_key"
        elif "set-cookie" in headers_lower:
            self._profile["auth_type"] = "cookie_session"
        elif status in (401, 403):
            # Check response body for hints
            body_str = json.dumps(body, ensure_ascii=False).lower() if body else ""
            if "token" in body_str:
                self._profile["auth_type"] = "bearer_token"
            elif "api_key" in body_str or "apikey" in body_str:
                self._profile["auth_type"] = "api_key"
            else:
                self._profile["auth_type"] = "unknown_auth_required"

    def _detect_response_format(self, headers: dict[str, str], body: Any) -> None:
        """Detect response format convention."""
        if self._profile["response_format"] != "unknown":
            return

        headers_lower = {k.lower(): v for k, v in headers.items()}
        content_type = headers_lower.get("content-type", "")

        if "application/json" in content_type:
            self._profile["response_format"] = "json"
            # Detect JSON structure convention
            if isinstance(body, dict):
                if "data" in body and "code" in body:
                    self._profile["conventions"]["json_wrapper"] = "data_code"
                elif "data" in body and "success" in body:
                    self._profile["conventions"]["json_wrapper"] = "data_success"
                elif "result" in body:
                    self._profile["conventions"]["json_wrapper"] = "result"
                elif "items" in body or "list" in body:
                    self._profile["conventions"]["json_wrapper"] = "list_direct"
        elif "text/html" in content_type:
            self._profile["response_format"] = "html"
        elif "application/xml" in content_type or "text/xml" in content_type:
            self._profile["response_format"] = "xml"

    def _detect_error_style(self, status: int, body: Any) -> None:
        """Detect error handling style."""
        if isinstance(body, dict):
            # Check for structured error response
            if "error" in body:
                self._profile["error_style"] = "error_field"
            elif "message" in body and "code" in body:
                self._profile["error_style"] = "message_code"
            elif "errors" in body and isinstance(body["errors"], list):
                self._profile["error_style"] = "errors_array"
            elif "detail" in body:
                self._profile["error_style"] = "detail_field"
            elif status == 200 and body.get("ok") is False:
                # Business error wrapped in 200
                self._profile["error_style"] = "business_error_200"
                self._profile["conventions"]["business_error_in_200"] = True
        elif isinstance(body, str):
            if "exception" in body.lower() or "traceback" in body.lower():
                self._profile["error_style"] = "stack_trace_leak"

    def _detect_api_style(self, method: str, path: str, headers: dict[str, str]) -> None:
        """Detect API architectural style."""
        if self._profile["api_style"] != "unknown":
            return

        path_lower = path.lower()

        if "/graphql" in path_lower:
            self._profile["api_style"] = "graphql"
        elif path_lower.startswith("/rpc") or path_lower.startswith("/jsonrpc"):
            self._profile["api_style"] = "rpc"
        elif any(seg in path_lower for seg in ("/api/", "/v1/", "/v2/", "/v3/")):
            self._profile["api_style"] = "rest"
        elif method.upper() == "GET" and "?" in path:
            self._profile["api_style"] = "rest"

    def get_probe_adjustments(self) -> dict[str, Any]:
        """Get probe strategy adjustments based on profile."""
        adjustments: dict[str, Any] = {}

        # Auth adjustments
        auth_type = self._profile.get("auth_type", "unknown")
        if auth_type == "bearer_token":
            adjustments["auth_header"] = "Authorization: Bearer {token}"
        elif auth_type == "api_key":
            adjustments["auth_header"] = "X-API-Key: {token}"
        elif auth_type == "cookie_session":
            adjustments["use_cookies"] = True

        # Error detection adjustments
        error_style = self._profile.get("error_style", "unknown")
        if error_style == "business_error_200":
            adjustments["check_business_error_in_200"] = True
            adjustments["business_error_fields"] = ["ok", "success", "code"]
        elif error_style == "message_code":
            adjustments["error_fields"] = ["message", "code"]

        # Response format adjustments
        conventions = self._profile.get("conventions", {})
        if conventions.get("json_wrapper") == "data_code":
            adjustments["data_field"] = "data"
            adjustments["success_field"] = "code"
        elif conventions.get("json_wrapper") == "data_success":
            adjustments["data_field"] = "data"
            adjustments["success_field"] = "success"

        return adjustments

    def get_profile_summary(self) -> dict[str, Any]:
        """Get a summary of the target profile."""
        return {
            "base_url": self._profile.get("base_url"),
            "auth_type": self._profile.get("auth_type"),
            "response_format": self._profile.get("response_format"),
            "error_style": self._profile.get("error_style"),
            "api_style": self._profile.get("api_style"),
            "conventions": self._profile.get("conventions", {}),
            "observation_count": len(self._profile.get("observations", [])),
            "probe_adjustments": self.get_probe_adjustments(),
        }

    def should_adjust_verdict(self, verdict: str, evidence: dict[str, Any]) -> tuple[str, str]:
        """Adjust verdict based on target profile.

        Returns (adjusted_verdict, reason).
        """
        adjustments = self.get_probe_adjustments()

        # If target uses business errors in 200 responses
        if adjustments.get("check_business_error_in_200"):
            calls = evidence.get("calls", [])
            for call in calls:
                body = call.get("results", {}).get("admin", {}).get("body", {})
                status = call.get("results", {}).get("admin", {}).get("status", 0)
                if status == 200 and isinstance(body, dict):
                    # Check business error fields
                    for field in adjustments.get("business_error_fields", []):
                        if body.get(field) is False or body.get(field) == 0:
                            if verdict == "inconclusive":
                                return "confirmed", f"business_error_in_200:{field}"

        return verdict, ""


# ── Module-level singleton ──
_global_profiler: TargetProfiler | None = None


def get_target_profiler(base_url: str = "") -> TargetProfiler:
    """Get or create the global target profiler instance."""
    global _global_profiler
    if _global_profiler is None or (base_url and _global_profiler.base_url != base_url.rstrip("/")):
        _global_profiler = TargetProfiler(base_url=base_url)
    return _global_profiler


def profile_target_response(
    base_url: str,
    method: str,
    path: str,
    status: int,
    headers: dict[str, str],
    body: Any,
) -> None:
    """Convenience function to record a target response observation."""
    profiler = get_target_profiler(base_url)
    profiler.observe_response(method, path, status, headers, body)
