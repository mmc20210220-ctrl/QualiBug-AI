from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

import ai_test_asset_center.git_connector_adapter as git
from ai_test_asset_center.connector_sync_authority import register_connector_instance
from ai_test_asset_center.enterprise_knowledge_center import list_enterprise_knowledge_sources


ACTOR = {"name": "qa-owner", "role": "qa_lead"}


def _scope(**overrides: object) -> str:
    value: dict[str, object] = {
        "repository_url": "https://github.com/acme/demo",
        "branch": "main",
        "max_files": 20,
    }
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _blob(value: bytes) -> dict[str, object]:
    return {
        "content": base64.b64encode(value).decode("ascii"),
        "encoding": "base64",
        "size": len(value),
    }


def _github_transport(
    *,
    tree: list[dict[str, object]],
    commit_sha: str = "commit-1",
    tree_sha: str = "tree-1",
    blobs: dict[str, bytes] | None = None,
    calls: list[tuple[str, str, dict[str, str], bytes | None]] | None = None,
):
    payloads = blobs or {}

    def transport(method, url, headers, body, timeout, max_bytes):
        if calls is not None:
            calls.append((method, url, dict(headers), body))
        path = urlsplit(url).path
        if path == "/repos/acme/demo":
            return git.GitHttpResponse(
                200,
                {},
                b'{"default_branch":"main","visibility":"private"}',
                url,
            )
        if path == "/repos/acme/demo/branches/main":
            data = {
                "name": "main",
                "commit": {
                    "sha": commit_sha,
                    "commit": {
                        "committer": {"date": "2026-08-02T00:00:00Z"},
                        "tree": {"sha": tree_sha},
                    },
                },
            }
            return git.GitHttpResponse(200, {}, json.dumps(data).encode(), url)
        if path == f"/repos/acme/demo/git/trees/{tree_sha}":
            return git.GitHttpResponse(
                200,
                {},
                json.dumps({"tree": tree, "truncated": False}).encode(),
                url,
            )
        if path.startswith("/repos/acme/demo/git/blobs/"):
            sha = path.rsplit("/", 1)[-1]
            if sha not in payloads:
                return git.GitHttpResponse(404, {}, b"{}", url)
            return git.GitHttpResponse(200, {}, json.dumps(_blob(payloads[sha])).encode(), url)
        if path.startswith("/repos/acme/demo/git/commits/"):
            sha = path.rsplit("/", 1)[-1]
            return git.GitHttpResponse(
                200,
                {},
                json.dumps({"sha": sha, "tree": {"sha": tree_sha}}).encode(),
                url,
            )
        return git.GitHttpResponse(404, {}, b"{}", url)

    return transport


@pytest.fixture
def bypass_test_dns(monkeypatch):
    monkeypatch.setattr(git, "validate_url", lambda url, **_: url)


def test_manifest_and_default_registry_are_provider_manifest_driven() -> None:
    from ai_test_asset_center.connector_registry import build_default_connector_registry

    registry = build_default_connector_registry()
    assert {row["connector_type"] for row in registry.catalog()["connector_types"]} == {
        "feishu",
        "git",
        "gitee",
        "github",
        "gitlab",
        "openapi",
        "website",
    }
    for connector_type in ("gitee", "gitlab", "github", "git"):
        manifest = registry.manifest(connector_type)
        assert manifest.category == "source_code"
        assert manifest.read_only is True
        assert manifest.sync_modes == ("FULL", "INCREMENTAL")
        assert manifest.scope_schema["required"] == ["repository_url"]
        assert {field.name for field in manifest.credential_fields} == {"token"}


def test_github_full_discovery_and_materialization_are_read_only_and_provenance_bound(
    bypass_test_dns,
):
    tree = [
        {"path": "src/app.py", "type": "blob", "sha": "blob-app", "size": 15},
        {"path": "docs/api.json", "type": "blob", "sha": "blob-api", "size": 20},
        {"path": ".env", "type": "blob", "sha": "blob-env", "size": 20},
        {
            "path": "vendor/lib",
            "type": "commit",
            "sha": "submodule-1",
            "mode": "160000",
        },
    ]
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    transport = _github_transport(
        tree=tree,
        blobs={
            "blob-app": b"print('ready')\n",
            "blob-api": b'{"openapi":"3.0.3"}',
            "blob-env": b"TOKEN=should-not-enter",
        },
        calls=calls,
    )
    adapter = git.GitRepositoryConnectorAdapter("github")
    result = adapter.discover(
        {
            "resource_scope": _scope(),
            "connection_profile": {
                "auth_mode": "personal_access_token",
                "token": "opaque-token",
            },
            "transport": transport,
            "sleeper": lambda _: None,
        }
    )

    assert result["complete"] is True
    assert result["current_commit_sha"] == "commit-1"
    assert result["current_tree_hash"] == "tree-1"
    assert result["cursor_state"]["branch_ref"] == "refs/heads/main"
    assert result["cursor_state"]["platform_event_id"] == "NOT_PROVIDED"
    descriptors = result["descriptors"]
    assert {row["display_title"] for row in descriptors} == {
        "src/app.py",
        "docs/api.json",
        ".env",
        "vendor/lib",
    }
    assert all("opaque-token" not in json.dumps(row, ensure_ascii=False) for row in descriptors)
    assert all("Authorization" not in json.dumps(row, ensure_ascii=False) for row in descriptors)

    app = next(row for row in descriptors if row["display_title"] == "src/app.py")
    materialized = adapter.materialize(
        {
            "resource_scope": _scope(),
            "connection_profile": {
                "auth_mode": "personal_access_token",
                "token": "opaque-token",
            },
            "transport": transport,
            "sleeper": lambda _: None,
        },
        app,
    )
    assert materialized["content"] == b"print('ready')\n"
    assert materialized["source_type"] == "other_document"
    assert all(method == "GET" and body is None for method, _, _, body in calls)
    assert any(headers.get("Authorization") == "Bearer opaque-token" for _, _, headers, _ in calls)


def test_gitlab_incremental_compare_returns_only_changed_files_and_visible_rename_delete(
    bypass_test_dns,
):
    scope = json.dumps(
        {
            "repository_url": "https://gitlab.com/acme/demo",
            "provider": "gitlab",
            "branch": "main",
            "max_files": 20,
        },
        separators=(",", ":"),
    )

    def transport(method, url, headers, body, timeout, max_bytes):
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        if path == "/api/v4/projects/acme%2Fdemo":
            return git.GitHttpResponse(200, {}, b'{"default_branch":"main"}', url)
        if path == "/api/v4/projects/acme%2Fdemo/repository/branches/main":
            return git.GitHttpResponse(
                200,
                {},
                json.dumps(
                    {
                        "name": "main",
                        "commit": {
                            "id": "commit-2",
                            "tree_id": "tree-2",
                            "committed_date": "2026-08-02T00:00:00Z",
                        },
                    }
                ).encode(),
                url,
            )
        if path == "/api/v4/projects/acme%2Fdemo/repository/compare":
            assert query["from"] == ["commit-1"]
            assert query["to"] == ["commit-2"]
            return git.GitHttpResponse(
                200,
                {},
                json.dumps(
                    {
                        "diffs": [
                            {
                                "old_path": "src/old.py",
                                "new_path": "src/new.py",
                                "renamed_file": True,
                            },
                            {
                                "old_path": "gone.py",
                                "new_path": "gone.py",
                                "deleted_file": True,
                            },
                            {
                                "old_path": "changed.py",
                                "new_path": "changed.py",
                            },
                        ],
                        "compare_timeout": False,
                    }
                ).encode(),
                url,
            )
        return git.GitHttpResponse(404, {}, b"{}", url)

    scope_context = {"resource_scope": scope}
    normalized_scope = git._scope_from_context(scope_context, connector_type="gitlab")
    first_cursor = git._encode_cursor(
        {
            "schema": "qualibug.git-cursor.v1",
            "provider": "gitlab",
            "repository_url": "https://gitlab.com/acme/demo",
            "repository_path": "acme/demo",
            "branch_ref": "refs/heads/main",
            "branch": "main",
            "commit_sha": "commit-1",
            "tree_hash": "tree-1",
            "platform_event_id": "event-1",
            "scope_fingerprint": git._scope_fingerprint(normalized_scope, "main"),
            "sync_mode": "FULL",
        }
    )
    result = git.GitRepositoryConnectorAdapter("gitlab").discover(
        {
            "resource_scope": scope,
            "transport": transport,
            "sleeper": lambda _: None,
            "previous_observations": {
                git._resource_id(normalized_scope, "file", "src/old.py"): {
                    "source_metadata": {"path": "src/old.py"}
                }
            },
        },
        first_cursor,
    )
    assert result["sync_mode"] == "INCREMENTAL"
    assert {row["display_title"] for row in result["descriptors"]} == {
        "src/new.py",
        "changed.py",
    }
    changed = next(row for row in result["descriptors"] if row["display_title"] == "src/new.py")
    assert changed["metadata"]["revision_kind"] == "commit_path"
    old_remote_id = git._resource_id(normalized_scope, "file", "src/old.py")
    assert changed["remote_resource_id"] == old_remote_id

    def materialize_transport(method, url, headers, body, timeout, max_bytes):
        if urlsplit(url).path == "/api/v4/projects/acme%2Fdemo/repository/files/src%2Fnew.py":
            return git.GitHttpResponse(
                200,
                {},
                json.dumps(_blob(b"renamed = True\n")).encode(),
                url,
            )
        return transport(method, url, headers, body, timeout, max_bytes)

    materialized = git.GitRepositoryConnectorAdapter("gitlab").materialize(
        {"resource_scope": scope, "transport": materialize_transport, "sleeper": lambda _: None},
        changed,
    )
    assert materialized["content"] == b"renamed = True\n"
    events = {row["event"] for row in result["lifecycle"]}
    assert {"GIT_FILE_RENAMED", "GIT_FILE_DELETED"}.issubset(events)
    rename_event = next(row for row in result["lifecycle"] if row["event"] == "GIT_FILE_RENAMED")
    assert rename_event["remote_resource_id"] == old_remote_id


def test_force_push_falls_back_to_a_bounded_full_tree_scan(bypass_test_dns):
    calls: list[str] = []
    scope = json.dumps(
        {"repository_url": "https://github.com/acme/demo", "branch": "main"},
        separators=(",", ":"),
    )

    def transport(method, url, headers, body, timeout, max_bytes):
        path = urlsplit(url).path
        calls.append(path)
        if path == "/repos/acme/demo":
            return git.GitHttpResponse(200, {}, b'{"default_branch":"main"}', url)
        if path == "/repos/acme/demo/branches/main":
            return git.GitHttpResponse(
                200,
                {},
                b'{"commit":{"sha":"commit-new","commit":{"tree":{"sha":"tree-new"}}}}',
                url,
            )
        if path.startswith("/repos/acme/demo/compare/"):
            return git.GitHttpResponse(404, {}, b"{}", url)
        if path == "/repos/acme/demo/git/trees/tree-new":
            return git.GitHttpResponse(200, {}, b'{"tree":[],"truncated":false}', url)
        return git.GitHttpResponse(404, {}, b"{}", url)

    normalized_scope = git._scope_from_context({"resource_scope": scope}, connector_type="github")
    prior = git._encode_cursor(
        {
            "schema": "qualibug.git-cursor.v1",
            "provider": "github",
            "repository_url": "https://github.com/acme/demo",
            "repository_path": "acme/demo",
            "branch_ref": "refs/heads/main",
            "branch": "main",
            "commit_sha": "commit-old",
            "tree_hash": "tree-old",
            "platform_event_id": "NOT_PROVIDED",
            "scope_fingerprint": git._scope_fingerprint(normalized_scope, "main"),
            "sync_mode": "FULL",
        }
    )
    result = git.GitRepositoryConnectorAdapter("github").discover(
        {"resource_scope": scope, "transport": transport, "sleeper": lambda _: None},
        prior,
    )
    assert result["sync_mode"] == "FULL"
    assert any(row["event"] == "GIT_HISTORY_REWRITTEN" for row in result["lifecycle"])
    assert "/repos/acme/demo/git/trees/tree-new" in calls


def test_deleted_branch_is_a_visible_gap_and_preserves_the_previous_cursor(bypass_test_dns):
    scope = _scope()
    calls: list[str] = []

    def transport(method, url, headers, body, timeout, max_bytes):
        path = urlsplit(url).path
        calls.append(path)
        if path == "/repos/acme/demo":
            return git.GitHttpResponse(200, {}, b'{"default_branch":"main"}', url)
        if path == "/repos/acme/demo/branches/main":
            return git.GitHttpResponse(404, {}, b"{}", url)
        return git.GitHttpResponse(500, {}, b"{}", url)

    prior = "git-snapshot-v1:" + json.dumps(
        {
            "schema": "qualibug.git-cursor.v1",
            "provider": "github",
            "repository_url": "https://github.com/acme/demo",
            "repository_path": "acme/demo",
            "branch_ref": "refs/heads/main",
            "branch": "main",
            "commit_sha": "commit-old",
            "tree_hash": "tree-old",
            "platform_event_id": "NOT_PROVIDED",
            "scope_fingerprint": "old-scope",
            "sync_mode": "FULL",
        },
        separators=(",", ":"),
    )
    result = git.GitRepositoryConnectorAdapter("github").discover(
        {"resource_scope": scope, "transport": transport, "sleeper": lambda _: None},
        prior,
    )
    assert result["complete"] is False
    assert result["next_cursor"] == prior
    assert result["coverage"]["observations"][0]["reason_code"] == "GIT_BRANCH_NOT_FOUND"
    assert any(row["event"] == "GIT_BRANCH_DELETED_OR_UNAVAILABLE" for row in result["lifecycle"])
    assert "/repos/acme/demo/git/trees/tree-old" not in calls


def test_lfs_and_suspected_secret_materialization_fail_closed(bypass_test_dns):
    tree = [
        {"path": "assets/model.bin", "type": "blob", "sha": "lfs", "size": 80},
        {"path": "config/.env", "type": "blob", "sha": "env", "size": 20},
    ]
    transport = _github_transport(
        tree=tree,
        blobs={
            "lfs": b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 123\n",
            "env": b"TOKEN=should-not-enter\n",
        },
    )
    adapter = git.GitRepositoryConnectorAdapter("github")
    result = adapter.discover({"resource_scope": _scope(), "transport": transport})
    lfs = next(row for row in result["descriptors"] if row["display_title"] == "assets/model.bin")
    secret = next(row for row in result["descriptors"] if row["display_title"] == "config/.env")
    with pytest.raises(git.GitUnsupportedResource, match="lfs_pointer"):
        adapter.materialize({"resource_scope": _scope(), "transport": transport}, lfs)
    with pytest.raises(git.GitUnsupportedResource, match="SENSITIVE_PATH"):
        adapter.materialize({"resource_scope": _scope(), "transport": transport}, secret)


def test_yaml_api_documents_reuse_the_existing_api_source_type_classifier() -> None:
    assert git._source_type_for_blob(
        "docs/openapi.yaml",
        b"openapi: 3.0.3\ninfo:\n  title: Orders\n  version: '1'\npaths: {}\n",
    ) == "openapi"
    assert git._source_type_for_blob(
        "docs/postman.yaml",
        b"info:\n  name: Orders\nitem:\n  - name: list\n",
    ) == "postman"
    assert git._source_type_for_blob("docs/readme.yaml", b"title: Readme\n") == "other_document"


def test_managed_sync_uses_source_occurrence_authority_and_commits_cursor_for_clean_snapshot(
    bypass_test_dns,
    tmp_path: Path,
):
    project = "git-project"
    connector = "github-main"
    scope = _scope(file_types=["*.py"])
    register_connector_instance(
        project,
        root=tmp_path,
        actor=ACTOR,
        connector_instance_id=connector,
        connector_type="github",
        resource_scope=scope,
    )
    transport = _github_transport(
        tree=[{"path": "src/app.py", "type": "blob", "sha": "blob-app", "size": 15}],
        blobs={"blob-app": b"print('ready')\n"},
    )
    result = git.sync_git_connector(
        project,
        connector_instance_id=connector,
        connector_type="github",
        root=tmp_path,
        actor=ACTOR,
        transport=transport,
        sleeper=lambda _: None,
    )
    assert result["status"] == "COMPLETE"
    assert result["materialized_resource_count"] == 1
    assert result["cursor_checkpoint_committed"] is True
    assert result["repository_code_executed"] is False
    assert result["build_or_test_scripts_executed"] is False
    assert result["remote_lifecycle_status"] == "COMPLETE"


def test_managed_rename_reuses_the_existing_source_occurrence_identity(
    bypass_test_dns,
    tmp_path: Path,
):
    project = "git-rename-project"
    connector = "github-rename"
    register_connector_instance(
        project,
        root=tmp_path,
        actor=ACTOR,
        connector_instance_id=connector,
        connector_type="github",
        resource_scope=_scope(),
    )
    phase = {"value": 1}

    def transport(method, url, headers, body, timeout, max_bytes):
        path = urlsplit(url).path
        if path == "/repos/acme/demo":
            return git.GitHttpResponse(200, {}, b'{"default_branch":"main"}', url)
        if path == "/repos/acme/demo/branches/main":
            if phase["value"] == 1:
                payload = {"commit": {"sha": "commit-1", "commit": {"tree": {"sha": "tree-1"}}}}
            else:
                payload = {"commit": {"sha": "commit-2", "commit": {"tree": {"sha": "tree-2"}}}}
            return git.GitHttpResponse(200, {}, json.dumps(payload).encode(), url)
        if path == "/repos/acme/demo/git/trees/tree-1":
            return git.GitHttpResponse(
                200,
                {},
                b'{"tree":[{"path":"src/old.py","type":"blob","sha":"blob-old","size":16}],"truncated":false}',
                url,
            )
        if path.startswith("/repos/acme/demo/git/blobs/"):
            blob = b"old = True\n" if path.endswith("blob-old") else b"new = True\n"
            return git.GitHttpResponse(200, {}, json.dumps(_blob(blob)).encode(), url)
        if path.startswith("/repos/acme/demo/compare/"):
            return git.GitHttpResponse(
                200,
                {},
                b'{"files":[{"filename":"src/new.py","previous_filename":"src/old.py","status":"renamed","sha":"blob-new","size":16}],"truncated":false}',
                url,
            )
        return git.GitHttpResponse(404, {}, b"{}", url)

    first = git.sync_git_connector(
        project,
        connector_instance_id=connector,
        connector_type="github",
        root=tmp_path,
        actor=ACTOR,
        transport=transport,
        sleeper=lambda _: None,
    )
    phase["value"] = 2
    second = git.sync_git_connector(
        project,
        connector_instance_id=connector,
        connector_type="github",
        root=tmp_path,
        actor=ACTOR,
        previous_cursor=first["next_cursor"],
        transport=transport,
        sleeper=lambda _: None,
    )

    assert first["status"] == "COMPLETE"
    assert second["status"] == "COMPLETE"
    assert second["renamed_resource_count"] == 1
    assert second["lifecycle_events"][0]["event"] == "GIT_FILE_RENAMED"
    assert second["lifecycle_events"][0]["remote_resource_id"] == first["successful_items"][0]["remote_resource_id"]
    inventory = list_enterprise_knowledge_sources(project, root=tmp_path, include_deleted=True)
    lineage = [row for row in inventory["sources"] if row["source_ref"] == first["successful_items"][0]["source_ref"]]
    assert sorted(row["occurrence_version"] for row in lineage) == [1, 2]
    assert sorted(row["status"] for row in lineage) == ["active", "superseded"]
