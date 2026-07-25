"""MES HTTP Client with full request/response evidence recording."""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any


BASE_URL = "http://localhost:8020"

# Account tokens from MES SUT (public test accounts documented in TEST_ACCOUNTS.md)
ACCOUNTS = {
    "planner_acme": {"token": "planner-pat-token", "role": "PLANNER", "org": "acme", "factory": "fac-001"},
    "operator_acme": {"token": "operator-oli-token", "role": "OPERATOR", "org": "acme", "factory": "fac-001"},
    "operator_acme_f2": {"token": "operator-ole-token", "role": "OPERATOR", "org": "acme", "factory": "fac-002"},
    "inspector_acme": {"token": "inspector-iris-token", "role": "INSPECTOR", "org": "acme", "factory": "fac-001"},
    "manager_acme": {"token": "manager-marcus-token", "role": "MANAGER", "org": "acme", "factory": "fac-001"},
    "warehouse_acme": {"token": "warehouse-will-token", "role": "WAREHOUSE", "org": "acme", "factory": "fac-001"},
    "admin_acme": {"token": "admin-arthur-token", "role": "ADMIN", "org": "acme", "factory": None},
    "planner_globex": {"token": "planner-pam-token", "role": "PLANNER", "org": "globex", "factory": "fac-003"},
    "operator_globex": {"token": "operator-ova-token", "role": "OPERATOR", "org": "globex", "factory": "fac-003"},
    "inspector_globex": {"token": "inspector-ivan-token", "role": "INSPECTOR", "org": "globex", "factory": "fac-003"},
    "manager_globex": {"token": "manager-mona-token", "role": "MANAGER", "org": "globex", "factory": "fac-003"},
    "warehouse_globex": {"token": "warehouse-wanda-token", "role": "WAREHOUSE", "org": "globex", "factory": "fac-003"},
}


@dataclass
class HttpEvidence:
    """Complete evidence of one HTTP request/response pair."""
    method: str
    url: str
    request_headers: dict
    request_body: Any
    status_code: int
    response_body: Any
    timestamp: float
    actor: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "url": self.url,
            "actor": self.actor,
            "request_body": self.request_body,
            "status_code": self.status_code,
            "response_body": self.response_body,
            "timestamp": self.timestamp,
            "duration_ms": round(self.duration_ms, 2),
        }


class MESClient:
    """HTTP client for MES SUT with evidence capture."""

    def __init__(self, base_url: str = BASE_URL, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.evidence_log: list[HttpEvidence] = []

    def _request(self, method: str, path: str, actor: str = "admin_acme",
                 body: dict | None = None, params: str = "") -> HttpEvidence:
        """Execute HTTP request and record evidence."""
        url = f"{self.base_url}{path}"
        if params:
            url += f"?{params}"

        account = ACCOUNTS.get(actor, ACCOUNTS["admin_acme"])
        headers = {
            "Authorization": f"Bearer {account['token']}",
            "Content-Type": "application/json",
        }

        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        ts = time.time()
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                resp_body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                resp_body = json.loads(e.read().decode())
            except Exception:
                resp_body = {"error": str(e)}
        except Exception as e:
            status = 0
            resp_body = {"error": f"{type(e).__name__}: {e}"}

        duration = (time.perf_counter() - t0) * 1000

        evidence = HttpEvidence(
            method=method,
            url=url,
            request_headers={"Authorization": f"Bearer {account['token'][:12]}..."},
            request_body=body,
            status_code=status,
            response_body=resp_body,
            timestamp=ts,
            actor=actor,
            duration_ms=duration,
        )
        self.evidence_log.append(evidence)
        return evidence

    # === Convenience methods ===

    def get(self, path: str, actor: str = "admin_acme", params: str = "") -> HttpEvidence:
        return self._request("GET", path, actor=actor, params=params)

    def post(self, path: str, body: dict | None = None, actor: str = "admin_acme") -> HttpEvidence:
        return self._request("POST", path, actor=actor, body=body or {})

    def put(self, path: str, body: dict | None = None, actor: str = "admin_acme") -> HttpEvidence:
        return self._request("PUT", path, actor=actor, body=body or {})

    def delete(self, path: str, actor: str = "admin_acme") -> HttpEvidence:
        return self._request("DELETE", path, actor=actor)

    def reset(self) -> HttpEvidence:
        """Reset SUT state."""
        url = f"{self.base_url}/reset"
        # Use admin token for reset (server requires auth)
        admin_token = ACCOUNTS["admin_acme"]["token"]
        req = urllib.request.Request(url, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {admin_token}"},
                                     data=b"{}")
        ts = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                resp_body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                resp_body = json.loads(e.read().decode())
            except Exception:
                resp_body = {"error": str(e)}
        except Exception as e:
            status = 0
            resp_body = {"error": str(e)}
        ev = HttpEvidence(method="POST", url=url, request_headers={},
                          request_body={}, status_code=status,
                          response_body=resp_body, timestamp=ts, actor="system")
        self.evidence_log.append(ev)
        return ev

    def health(self) -> bool:
        """Check if SUT is reachable."""
        try:
            url = f"{self.base_url}/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def clear_evidence(self):
        """Clear evidence log (call between experiments)."""
        self.evidence_log = []

    def get_evidence(self) -> list[dict]:
        """Get all recorded evidence as dicts."""
        return [e.to_dict() for e in self.evidence_log]
