from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ai_test_asset_center import private_pilot_http_routing as routing
from ai_test_asset_center.private_pilot_upload_fixture_health_patch import (
    upload_fixture_health_status,
)
from ai_test_asset_center.private_pilot_upload_fixture_routes import (
    install_private_pilot_upload_fixture_routes,
)
from ai_test_asset_center.ui_upload_fixture_ingest import (
    MAX_HTTP_FIXTURE_BYTES,
    stage_and_register_upload_fixture,
)
from ai_test_asset_center.ui_upload_fixture_runtime_binding import (
    install_ui_upload_fixture_runtime_binding,
)


_PROJECT = "fixture-project"
_ACTOR = {"name": "qa-owner", "role": "qa_lead"}


class _Handler:
    def __init__(
        self,
        root: Path,
        *,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        role_allowed: bool = True,
    ) -> None:
        self.path = path
        self.command = "POST"
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Length", str(len(body)))
        self.rfile = io.BytesIO(body)
        self.server = SimpleNamespace(server_address=("127.0.0.1", 8088))
        self._test_root = root
        self.role_allowed = role_allowed
        self.responses: list[tuple[int, Any]] = []

    def _init_request_context(self) -> None:
        return None

    def _root(self) -> Path:
        return self._test_root

    def _require_actor(self) -> dict[str, str]:
        return dict(_ACTOR)

    def _require_tenant(self, _root: Path) -> str:
        return "tenant-1"

    def _require_project_scope(self, project: str) -> bool:
        return project == _PROJECT

    def _require_role(
        self,
        _actor: dict[str, str],
        _allowed: set[str],
        _action: str,
    ) -> bool:
        if self.role_allowed:
            return True
        self._json({"ok": False, "error": "FORBIDDEN"}, 403)
        return False

    def _body(self) -> dict[str, Any]:
        raw = self.rfile.read()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _json(self, payload: Any, status: int = 200, **_kwargs: Any) -> None:
        self.responses.append((status, payload))


@pytest.fixture(autouse=True)
def _install_routes() -> None:
    install_ui_upload_fixture_runtime_binding()
    install_private_pilot_upload_fixture_routes()


def test_binary_upload_route_registers_candidate_without_base64(tmp_path: Path) -> None:
    data = b"id,name\n1,Alice\n"
    handler = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-fixtures/upload",
        body=data,
        headers={
            "Content-Type": "text/csv",
            "X-QualiBug-Filename": "customer-import.csv",
            "X-QualiBug-Fixture-Name": "customer-import-valid",
        },
    )

    routing.HttpRoutingMixin.do_POST(handler)

    status, payload = handler.responses[-1]
    assert status == 201
    assert payload["ok"] is True
    assert payload["status"] == "REGISTERED"
    assert payload["fixture"]["authority"] == "source_registered"
    assert payload["transport"]["base64_used"] is False
    assert payload["transport"]["size_bytes"] == len(data)
    assert (
        tmp_path
        / "platform_inputs"
        / _PROJECT
        / "ui_upload_inbox"
    ).is_dir()


def test_binary_upload_then_approve_list_and_revoke_through_routes(tmp_path: Path) -> None:
    data = b'{"customer_id": 1}'
    upload = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-fixtures/upload",
        body=data,
        headers={
            "Content-Type": "application/json",
            "X-QualiBug-Filename": "customer.json",
            "X-QualiBug-Fixture-Name": "customer-json",
        },
    )
    routing.HttpRoutingMixin.do_POST(upload)
    candidate = upload.responses[-1][1]["fixture"]

    approve_body = json.dumps({
        "action": "approve",
        "fixture_id": candidate["fixture_id"],
    }).encode("utf-8")
    approve = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-fixtures",
        body=approve_body,
        headers={"Content-Type": "application/json"},
    )
    routing.HttpRoutingMixin.do_POST(approve)
    approved = approve.responses[-1][1]["fixture"]

    assert approve.responses[-1][0] == 200
    assert approved["authority"] == "approved_copy"
    assert approved["binding_ref"].startswith("uifb_")

    listing = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-fixtures",
    )
    listing.command = "GET"
    routing.HttpRoutingMixin.do_GET(listing)
    fixtures = listing.responses[-1][1]["fixtures"]
    assert {row["authority"] for row in fixtures} == {
        "source_registered",
        "approved_copy",
    }

    revoke_body = json.dumps({
        "action": "revoke",
        "fixture_id": candidate["fixture_id"],
        "reason": "fixture replaced",
    }).encode("utf-8")
    revoke = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-fixtures",
        body=revoke_body,
        headers={"Content-Type": "application/json"},
    )
    routing.HttpRoutingMixin.do_POST(revoke)
    assert revoke.responses[-1][1]["status"] == "REVOKED"
    assert len(revoke.responses[-1][1]["revoked_records"]) == 2


def test_binary_upload_route_requires_configuration_role(tmp_path: Path) -> None:
    handler = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-fixtures/upload",
        body=b"fixture",
        headers={
            "Content-Type": "application/octet-stream",
            "X-QualiBug-Filename": "fixture.bin",
        },
        role_allowed=False,
    )

    routing.HttpRoutingMixin.do_POST(handler)

    assert handler.responses == [(403, {"ok": False, "error": "FORBIDDEN"})]
    assert not (
        tmp_path
        / "platform_inputs"
        / _PROJECT
        / "ui_upload_inbox"
    ).exists()


def test_binary_upload_route_rejects_missing_filename_and_oversize(tmp_path: Path) -> None:
    missing_name = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-fixtures/upload",
        body=b"fixture",
        headers={"Content-Type": "application/octet-stream"},
    )
    routing.HttpRoutingMixin.do_POST(missing_name)
    assert missing_name.responses[-1][0] == 400
    assert "filename_required" in missing_name.responses[-1][1]["message"]

    with pytest.raises(ValueError, match="http_size_invalid"):
        stage_and_register_upload_fixture(
            _PROJECT,
            data=b"x" * (MAX_HTTP_FIXTURE_BYTES + 1),
            filename="too-large.bin",
            fixture_name="too-large",
            root=tmp_path,
            actor=_ACTOR,
        )


def test_upload_fixture_health_is_code_ready_and_non_executing() -> None:
    status = upload_fixture_health_status()

    assert status["schema_version"] == "qualibug.ui-upload-fixture-health.v1"
    assert status["ready"] is True
    assert status["checks"]["registry_api_available"] is True
    assert status["checks"]["project_routes_installed"] is True
    assert status["checks"]["scan_prepare_binding_installed"] is True
    assert status["governance"]["explicit_approval_required"] is True
    assert status["governance"]["caller_authored_absolute_paths_supported"] is False
    assert status["governance"]["browser_execution_verified_by_health"] is False
