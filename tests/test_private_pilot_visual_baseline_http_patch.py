from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image

from ai_test_asset_center.private_pilot_http_routing import HttpRoutingMixin
from ai_test_asset_center.private_pilot_visual_baseline_http_patch import (
    install_visual_baseline_http_patch,
)


def _png() -> bytes:
    image = Image.new("RGBA", (12, 8), (255, 255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


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


def _route(project: str = "visual-project") -> str:
    return f"/api/v1/projects/{project}/visual-baselines"


def test_http_register_and_list_visual_baseline_metadata(tmp_path: Path) -> None:
    install_visual_baseline_http_patch()
    register = _Handler(
        tmp_path,
        path=_route(),
        body={
            "action": "register",
            "filename": "orders.png",
            "content": base64.b64encode(_png()).decode("ascii"),
            "baseline_name": "orders",
            "viewport_width": 1280,
            "viewport_height": 720,
            "full_page": False,
        },
    )

    response = register.do_POST()

    assert register.context_initialized is True
    assert response["status"] == 201
    result = response["body"]["data"]
    assert result["status"] == "REGISTERED"
    baseline = result["baseline"]
    assert baseline["authority"] == "source_registered"
    assert baseline["created_by"] == "alice:qa_lead"
    assert "content" not in baseline
    staging = (
        tmp_path
        / "platform_workspace"
        / "visual-project"
        / "visual_baseline_uploads"
    )
    assert list(staging.glob("*")) == []

    listing = _Handler(
        tmp_path,
        path=_route() + "?include_revoked=true",
    )
    list_response = listing.do_GET()

    assert list_response["status"] == 200
    inventory = list_response["body"]["data"]
    assert inventory["summary"]["active_count"] == 1
    assert inventory["raw_pixels_embedded"] is False
    assert inventory["baselines"][0]["baseline_id"] == baseline["baseline_id"]


def test_http_registration_never_consumes_client_server_file_path(
    tmp_path: Path,
) -> None:
    install_visual_baseline_http_patch()
    handler = _Handler(
        tmp_path,
        path=_route(),
        body={
            "action": "register",
            "file_path": str(tmp_path / "outside.png"),
            "baseline_name": "outside",
            "viewport_width": 1280,
            "viewport_height": 720,
            "full_page": False,
        },
    )

    response = handler.do_POST()

    assert response["status"] == 400
    assert response["body"]["error"] == "VISUAL_BASELINE_BAD_REQUEST"
    assert response["body"]["message"] == (
        "visual_baseline_base64_content_required"
    )


def test_http_mutation_role_gate_runs_before_request_body_read(tmp_path: Path) -> None:
    install_visual_baseline_http_patch()
    handler = _Handler(
        tmp_path,
        path=_route(),
        body={
            "action": "register",
            "content": base64.b64encode(_png()).decode("ascii"),
        },
        role="viewer",
    )

    response = handler.do_POST()

    assert response is None
    assert handler.responses == [(403, {"ok": False, "error": "FORBIDDEN"})]
    assert handler.rfile.tell() == 0


def test_unrelated_routes_still_delegate_to_original_router(tmp_path: Path) -> None:
    install_visual_baseline_http_patch()
    handler = _Handler(tmp_path, path="/api/not-a-visual-route")

    # The original router reaches its own auth/routing logic. This assertion only
    # proves the visual patch did not claim the unrelated URL.
    assert handler.context_initialized is False
    try:
        handler.do_GET()
    except AttributeError:
        pass
    assert handler.context_initialized is True
