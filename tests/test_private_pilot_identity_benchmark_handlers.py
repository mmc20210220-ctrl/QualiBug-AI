from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.private_pilot_identity_benchmark_handlers import (
    IdentityBenchmarkHttpMixin,
    _route,
)


class _Fallback:
    def do_GET(self):
        self.fallback_get = True

    def do_POST(self):
        self.fallback_post = True


class _Handler(IdentityBenchmarkHttpMixin, _Fallback):
    def __init__(self, path: str, root: Path, body: dict | None = None):
        self.path = path
        self._test_root = root
        self._test_body = body or {}
        self.response = None
        self.fallback_get = False
        self.fallback_post = False
        self._qualibug_corr_id = "test"

    def _init_request_context(self):
        self.initialized = True

    def _root(self):
        return self._test_root

    def _require_actor(self):
        return {"name": "qa", "role": "qa_lead", "tenant_id": "tenant-a"}

    def _require_tenant(self, root):
        return "tenant-a"

    def _require_project_scope(self, project):
        return True

    def _require_known_project(self, project, root):
        return True

    def _require_role(self, actor, roles, operation):
        return True

    def _body(self):
        return self._test_body

    def _json(self, payload, status=200, **kwargs):
        self.response = (status, payload)
        return self.response


def test_route_matches_only_project_identity_benchmark_paths() -> None:
    assert _route("/api/v1/projects/acme/identity-benchmark") == (
        "acme",
        "workspace",
    )
    assert _route("/api/v1/projects/acme/identity-benchmark/manifest") == (
        "acme",
        "manifest",
    )
    assert _route("/api/v1/projects/acme/identity-benchmark/ground-truth") == (
        "acme",
        "ground-truth",
    )
    assert _route("/api/v1/projects/acme/authority-decisions") == ("", "")


def test_unmatched_requests_continue_to_existing_router(tmp_path) -> None:
    handler = _Handler("/api/knowledge/asset", tmp_path)
    handler.do_GET()
    assert handler.fallback_get is True

    post = _Handler("/api/knowledge/ingest", tmp_path)
    post.do_POST()
    assert post.fallback_post is True


def test_manifest_route_returns_blind_manifest(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
        identity_benchmark_workflow as workflow,
    )

    monkeypatch.setattr(
        workflow,
        "get_identity_benchmark_workspace",
        lambda project, root: {
            "manifest": {
                "manifest_id": "manifest:1",
                "contains_predicted_entity_ids": False,
            }
        },
    )
    handler = _Handler(
        "/api/v1/projects/acme/identity-benchmark/manifest", tmp_path
    )

    handler.do_GET()

    assert handler.response[0] == 200
    assert handler.response[1]["data"]["manifest_id"] == "manifest:1"
    assert handler.response[1]["data"]["contains_predicted_entity_ids"] is False


def test_ground_truth_post_delegates_to_transactional_workflow(
    tmp_path, monkeypatch
) -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
        identity_benchmark_workflow as workflow,
    )

    captured = {}

    def fake_import(project, payload, **kwargs):
        captured.update({"project": project, "payload": payload, **kwargs})
        return {"benchmark": {"status": "MEASURED"}}

    monkeypatch.setattr(workflow, "import_identity_ground_truth", fake_import)
    body = {
        "manifest_id": "manifest:1",
        "ground_truth": {
            "schema": "qualibug.enterprise-identity-ground-truth.v1",
            "clusters": [],
        },
    }
    handler = _Handler(
        "/api/v1/projects/acme/identity-benchmark/ground-truth",
        tmp_path,
        body,
    )

    handler.do_POST()

    assert handler.response[0] == 201
    assert captured["project"] == "acme"
    assert captured["manifest_id"] == "manifest:1"
    assert captured["rebuild"] is True
    assert captured["actor"]["name"] == "qa"


def test_value_error_is_bounded_bad_request(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
        identity_benchmark_workflow as workflow,
    )

    def reject(*args, **kwargs):
        raise ValueError("identity_ground_truth_manifest_stale")

    monkeypatch.setattr(workflow, "import_identity_ground_truth", reject)
    handler = _Handler(
        "/api/v1/projects/acme/identity-benchmark/ground-truth",
        tmp_path,
        {"manifest_id": "old", "ground_truth": {}},
    )

    handler.do_POST()

    assert handler.response[0] == 400
    assert handler.response[1]["error"] == "IDENTITY_BENCHMARK_BAD_REQUEST"
    assert "manifest_stale" in handler.response[1]["message"]
