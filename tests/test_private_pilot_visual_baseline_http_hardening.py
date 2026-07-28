from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_visual_baseline_http_patch as patch


class _Header(dict[str, str]):
    def get(self, key: str, default: str | None = None) -> str | None:
        return super().get(key, default)


class _Handler:
    def __init__(self, root: Path, *, path: str, content_length: int = 0) -> None:
        self._test_root = root
        self.path = path
        self.headers = _Header({"Content-Length": str(content_length)})
        self.rfile = io.BytesIO(b"")
        self.responses: list[tuple[int, Any]] = []

    def _root(self) -> Path:
        return self._test_root

    def _require_actor(self) -> dict[str, str]:
        return {"name": "alice", "role": "qa_lead"}

    def _require_tenant(self, root: Path) -> str:
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
        return True

    def _json(self, body: Any, status: int = 200) -> dict[str, Any]:
        self.responses.append((status, body))
        return {"status": status, "body": body}


def test_route_project_rejects_encoded_slash_and_sanitized_identity() -> None:
    assert patch._route_project(
        "/api/v1/projects/acme%2Fother/visual-baselines"
    ) == ""
    assert patch._route_project(
        "/api/v1/projects/acme%20corp/visual-baselines"
    ) == ""
    assert patch._route_project(
        "/api/v1/projects/acme-corp/visual-baselines"
    ) == "acme-corp"


def test_oversized_visual_baseline_request_returns_413(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("QUALIBUG_MAX_REQUEST_BODY", "100")
    handler = _Handler(
        tmp_path,
        path="/api/v1/projects/acme/visual-baselines",
        content_length=101,
    )

    response = patch._handle_post(handler, "acme")

    assert response["status"] == 413
    assert response["body"]["error"] == "PAYLOAD_TOO_LARGE"
    assert handler.rfile.tell() == 0


def test_body_project_identity_must_match_exact_route_identity(
    tmp_path: Path,
) -> None:
    raw = b'{"action":"approve","project_id":"acme corp"}'
    handler = _Handler(
        tmp_path,
        path="/api/v1/projects/acmecorp/visual-baselines",
        content_length=len(raw),
    )
    handler.rfile = io.BytesIO(raw)

    response = patch._handle_post(handler, "acmecorp")

    assert response["status"] == 400
    assert response["body"]["message"] == (
        "visual_baseline_project_id_mismatch"
    )
