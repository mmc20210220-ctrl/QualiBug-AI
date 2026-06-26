from __future__ import annotations

from typing import Any, Dict, Optional

from demo_system.api_service import ApiService


class BuggyApiService(ApiService):
    """Intentional buggy service for V5 failure triage demo.

    Bug: normal users can access /admin/users and receive 200 instead of 403.
    This simulates a high-value enterprise failure: permission bypass.
    """

    def request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        method = method.upper()
        headers = headers or {}
        json = json or {}

        if method == "GET" and path == "/admin/users":
            # BUG: should check X-Role == admin. It does not.
            return self._response(200, {"items": self.users, "warning": "permission check bypassed"})

        return super().request(method, path, json=json, headers=headers)
