from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from ai_test_asset_center.private_pilot_authority_decision_http_patch import (
    install_authority_decision_http_patch,
)
from ai_test_asset_center.private_pilot_http_routing import HttpRoutingMixin


class _Handler(HttpRoutingMixin):
    def __init__(
        self,
        root: Path,
        *,
        path: str,
        body: dict[str, Any] | None = None,
        role: str = "qa_lead",
    ) -> None:
        self._test_root = root
        self.path = path
        self.command = "POST" if body is not None else "GET"
        self.actor = {"name": "alice", "role": role}
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.responses: list[tuple[int, Any]] = []
        self.context_initialized = False

    def _init_request_context(self) -> None:
        self.context_initialized = True

    def _root(self) -> Path:
        return self._test_root

    def _require_actor(self) -> dict[str, str] | None:
        return dict(self.actor)

    def _require_tenant(self, root: Path) -> str | None:
        return "tenant-1"

    def _require_project_scope(self, project: str) -> bool:
        return True

    def _require_known_project(self, project: str, root: Path) -> bool:
        return True

    def _require_role(
        self,
        actor: dict[str, str],
        allowed: set[str],
        action: str,
    ) -> bool:
        if actor.get("role") in allowed:
            return True
        self._json({"ok": False, "error": "FORBIDDEN"}, 403)
        return False

    def _json(
        self,
        body: Any,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.responses.append((status, body))
        return {"status": status, "body": body}


def _route(project: str = "auth-project") -> str:
    return f"/api/v1/projects/{project}/authority-decisions"


def test_http_lists_authority_decisions(tmp_path: Path) -> None:
    install_authority_decision_http_patch()
    with patch(
        "ai_test_asset_center.private_pilot_authority_decision_http_patch.list_operator_authority_decisions",
        return_value={
            "ok": True,
            "schema": "qualibug.operator-authority-decision-ledger.v1",
            "project_id": "auth-project",
            "decisions": [],
            "audit_receipts": [],
        },
    ):
        handler = _Handler(tmp_path, path=_route())
        handler.do_GET()
    assert handler.context_initialized is True
    status, body = handler.responses[-1]
    assert status == 200
    assert body["ok"] is True


def test_http_records_select_fact_decision(tmp_path: Path) -> None:
    install_authority_decision_http_patch()
    fake = {
        "ok": True,
        "decision": {
            "decision_id": "decision:1",
            "conflict_id": "conflict:1",
            "action": "SELECT_FACT",
            "selected_fact_id": "fact-a",
            "actor": {"name": "alice", "role": "qa_lead"},
        },
        "audit_receipt": {"audit_receipt_id": "audit:1"},
    }
    with patch(
        "ai_test_asset_center.private_pilot_authority_decision_http_patch.record_operator_authority_decision",
        return_value=fake,
    ) as mocked:
        handler = _Handler(
            tmp_path,
            path=_route(),
            body={
                "action": "SELECT_FACT",
                "conflict_id": "conflict:1",
                "selected_fact_id": "fact-a",
                "rationale": "operator chose fact-a",
            },
        )
        handler.do_POST()
    assert mocked.called
    status, body = handler.responses[-1]
    assert status == 201
    assert body["ok"] is True
    assert body["action"] == "SELECT_FACT"
    assert body["data"]["decision"]["selected_fact_id"] == "fact-a"


def test_http_rejects_unknown_action(tmp_path: Path) -> None:
    install_authority_decision_http_patch()
    handler = _Handler(
        tmp_path,
        path=_route(),
        body={"action": "AUTO_PICK_NEWEST", "conflict_id": "conflict:1"},
    )
    handler.do_POST()
    status, body = handler.responses[-1]
    assert status == 400
    assert body["ok"] is False


def test_http_forbids_unprivileged_role(tmp_path: Path) -> None:
    install_authority_decision_http_patch()
    handler = _Handler(
        tmp_path,
        path=_route(),
        body={
            "action": "SELECT_FACT",
            "conflict_id": "conflict:1",
            "selected_fact_id": "fact-a",
        },
        role="viewer",
    )
    handler.do_POST()
    status, body = handler.responses[-1]
    assert status == 403
    assert body["ok"] is False
