from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import ai_test_asset_center.git_connector_adapter as git
from ai_test_asset_center.connector_sync_authority import (
    list_connector_instances,
    register_connector_instance,
)
from ai_test_asset_center.enterprise_knowledge_center import (
    list_enterprise_knowledge_sources,
)
from ai_test_asset_center.local_runner_connector import (
    LocalRunnerError,
    accept_local_runner_result,
    acknowledge_local_runner_result,
    execute_local_runner_task,
    initialize_local_runner,
    issue_local_runner_task,
    local_runner_status,
    register_local_runner,
)
from ai_test_asset_center.private_pilot_connector_handlers import (
    KnowledgeConnectorHandlersMixin,
)


ACTOR = {"name": "qa-owner", "role": "qa_lead"}


class _Handler(KnowledgeConnectorHandlersMixin):
    def _json(self, body, status=200, extra_headers=None):
        return {"status": status, "body": body, "headers": extra_headers or {}}


def _blob(value: bytes) -> dict[str, object]:
    return {
        "content": base64.b64encode(value).decode("ascii"),
        "encoding": "base64",
        "size": len(value),
    }


def _transport(method, url, headers, body, timeout, max_bytes):
    path = urlsplit(url).path
    if path == "/api/v4/projects/acme%2Fdemo":
        return git.GitHttpResponse(200, {}, b'{"default_branch":"main"}', url)
    if path == "/api/v4/projects/acme%2Fdemo/repository/branches/main":
        payload = {
            "name": "main",
            "commit": {
                "id": "commit-1",
                "tree_id": "tree-1",
                "committed_date": "2026-08-02T00:00:00Z",
            },
        }
        return git.GitHttpResponse(200, {}, json.dumps(payload).encode(), url)
    if path == "/api/v4/projects/acme%2Fdemo/repository/tree":
        payload = [{"path": "docs/readme.md", "type": "blob", "id": "blob-1", "size": 12}]
        return git.GitHttpResponse(200, {}, json.dumps(payload).encode(), url)
    if path == "/api/v4/projects/acme%2Fdemo/repository/files/docs%2Freadme.md":
        return git.GitHttpResponse(
            200,
            {},
            json.dumps(_blob(b"hello world\n")).encode(),
            url,
        )
    return git.GitHttpResponse(404, {}, b"{}", url)


def _setup(tmp_path: Path, monkeypatch, *, scope_host: str = "gitlab.internal"):
    monkeypatch.setenv("QUALIBUG_CRED_ENC_KEY", "local-runner-test-master-key")
    control_root = tmp_path / "control"
    runner_root = tmp_path / "runner"
    registration = register_local_runner(
        "enterprise-project",
        runner_id="runner-1",
        allowed_hosts=["gitlab.internal"],
        supported_connector_types=["gitlab"],
        root=control_root,
        actor=ACTOR,
    )
    initialize_local_runner(
        runner_root,
        bootstrap=registration["bootstrap"],
        source_profiles={
            "gitlab-main": {
                "connector_type": "gitlab",
                "auth_mode": "personal_access_token",
                "token": "runner-only-token",
            }
        },
    )
    scope = json.dumps(
        {
            "provider": "gitlab",
            "repository_url": f"https://{scope_host}/acme/demo",
            "api_base_url": f"https://{scope_host}/api/v4",
            "branch": "main",
            "max_files": 20,
        },
        separators=(",", ":"),
    )
    register_connector_instance(
        "enterprise-project",
        root=control_root,
        actor=ACTOR,
        connector_instance_id="gitlab-main",
        connector_type="gitlab",
        resource_scope=scope,
    )
    return control_root, runner_root


def _issue(control_root: Path, *, result_mode: str = "SANITIZED_SNAPSHOT"):
    return issue_local_runner_task(
        "enterprise-project",
        connector_instance_id="gitlab-main",
        runner_id="runner-1",
        root=control_root,
        actor=ACTOR,
        result_mode=result_mode,
    )


def test_registration_and_runner_state_keep_credentials_local_and_encrypted(
    tmp_path: Path, monkeypatch
):
    control_root, runner_root = _setup(tmp_path, monkeypatch)

    control_text = (
        control_root
        / "platform_workspace"
        / "enterprise-project"
        / "enterprise_knowledge_center"
        / "local_runner_registry.json"
    ).read_text(encoding="utf-8")
    state_text = (
        runner_root
        / "platform_workspace"
        / ".local_runner"
        / "runner-1"
        / "state.json"
    ).read_text(encoding="utf-8")
    assert "runner-only-token" not in control_text
    assert "runner-only-token" not in state_text
    assert "enc$v1$" in state_text
    assert "source_credentials_persisted" in control_text
    status = local_runner_status(runner_root, runner_id="runner-1")
    assert status["source_profile_count"] == 1
    assert status["credentials_returned"] is False


def test_control_handler_exposes_runner_registration_and_private_projection(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("QUALIBUG_CRED_ENC_KEY", "local-runner-handler-key")
    handler = _Handler()
    registration = handler._handle_local_runner_register(
        "enterprise-project",
        {
            "runner_id": "runner-http",
            "allowed_hosts": ["gitlab.internal"],
            "supported_connector_types": ["gitlab"],
            "runner_version": "1.0.0",
        },
        tmp_path,
        ACTOR,
    )
    assert registration["status"] == 201
    assert registration["body"]["data"]["bootstrap_key_returned"] is True
    projection = handler._handle_knowledge_connector_get(
        "enterprise-project", ["runners"], tmp_path
    )
    assert projection["status"] == 200
    row = projection["body"]["data"]["runners"][0]
    assert row["runner_id"] == "runner-http"
    assert "task_key_ciphertext" not in json.dumps(projection["body"])
    assert projection["body"]["data"]["raw_cursor_returned"] is False


def test_signed_result_enters_existing_source_occurrence_mainline_and_is_idempotent(
    tmp_path: Path, monkeypatch
):
    control_root, runner_root = _setup(tmp_path, monkeypatch)
    task_response = _issue(control_root)
    task = task_response["task"]
    result = execute_local_runner_task(
        task,
        runner_root=runner_root,
        transport=_transport,
        sleeper=lambda _: None,
    )
    assert result["source_content_returned"] is True
    assert result["items"][0]["content_encoding"] == "base64"
    assert "runner-only-token" not in json.dumps(result, ensure_ascii=False)

    receipt = accept_local_runner_result(
        "enterprise-project", result=result, root=control_root, actor=ACTOR
    )
    assert receipt["accepted"] is True
    assert receipt["materialized_success_count"] == 1
    assert receipt["cursor_checkpoint_committed"] is True
    control_registry_path = (
        control_root
        / "platform_workspace"
        / "enterprise-project"
        / "enterprise_knowledge_center"
        / "local_runner_registry.json"
    )
    assert result["next_cursor"] not in control_registry_path.read_text(encoding="utf-8")
    replay = accept_local_runner_result(
        "enterprise-project", result=result, root=control_root, actor=ACTOR
    )
    assert replay["idempotent_replay"] is True
    assert replay["result_fingerprint"] == receipt["result_fingerprint"]

    sources = list_enterprise_knowledge_sources(
        "enterprise-project", root=control_root, include_deleted=True
    )["sources"]
    assert len([row for row in sources if row["source_ref"].startswith("connector://gitlab-main/")]) == 1
    ack = acknowledge_local_runner_result(
        runner_root,
        runner_id="runner-1",
        task_id=result["task_id"],
        acceptance=receipt,
    )
    assert ack["outbox_removed"] is True

    # The control-plane cursor is encrypted in the runner ledger, while the sync registry keeps
    # only its fingerprint.  A second task therefore exercises the same incremental boundary.
    second = _issue(control_root)["task"]
    assert second["previous_cursor"]
    assert second["previous_cursor_fingerprint"]
    second_result = execute_local_runner_task(
        second,
        runner_root=runner_root,
        transport=_transport,
        sleeper=lambda _: None,
    )
    second_receipt = accept_local_runner_result(
        "enterprise-project",
        result=second_result,
        root=control_root,
        actor=ACTOR,
    )
    assert second_receipt["materialized_success_count"] == 0
    assert second_receipt["unchanged_success_count"] == 1
    instance = list_connector_instances("enterprise-project", root=control_root)[
        "connector_instances"
    ][0]
    assert instance["last_committed_cursor_fingerprint"] == hashlib.sha256(
        second_result["next_cursor"].encode("utf-8")
    ).hexdigest()


def test_structured_only_is_visible_incomplete_and_does_not_advance_cursor(
    tmp_path: Path, monkeypatch
):
    control_root, runner_root = _setup(tmp_path, monkeypatch)
    task = _issue(control_root, result_mode="STRUCTURED_ONLY")["task"]
    result = execute_local_runner_task(
        task,
        runner_root=runner_root,
        transport=_transport,
        sleeper=lambda _: None,
    )
    assert result["items"] == []
    assert result["next_cursor"] == ""
    assert result["structured_only_content_omitted"] is True
    receipt = accept_local_runner_result(
        "enterprise-project", result=result, root=control_root, actor=ACTOR
    )
    assert receipt["accepted"] is True
    assert receipt["cursor_checkpoint_committed"] is False
    assert receipt["knowledge_coverage_complete"] is False
    assert receipt["retry_required"] is True


def test_tampered_task_and_unallowlisted_scope_fail_before_network_access(
    tmp_path: Path, monkeypatch
):
    control_root, runner_root = _setup(tmp_path, monkeypatch)
    task = _issue(control_root)["task"]
    tampered = dict(task)
    tampered["resource_scope"] = tampered["resource_scope"].replace(
        "gitlab.internal", "other.internal"
    )
    with pytest.raises(LocalRunnerError, match="signature_invalid"):
        execute_local_runner_task(tampered, runner_root=runner_root, transport=_transport)

    unallowlisted_control, _ = _setup(
        tmp_path / "unallowlisted", monkeypatch, scope_host="other.internal"
    )
    with pytest.raises(LocalRunnerError, match="HOST_NOT_ALLOWLISTED"):
        _issue(unallowlisted_control)


def test_outbox_is_not_deleted_without_matching_acceptance_and_version_is_controlled(
    tmp_path: Path, monkeypatch
):
    control_root, runner_root = _setup(tmp_path, monkeypatch)
    task = _issue(control_root)["task"]
    result = execute_local_runner_task(
        task,
        runner_root=runner_root,
        transport=_transport,
        sleeper=lambda _: None,
    )
    with pytest.raises(LocalRunnerError, match="control_acceptance"):
        acknowledge_local_runner_result(
            runner_root,
            runner_id="runner-1",
            task_id=result["task_id"],
            acceptance={"accepted": False},
        )
    assert local_runner_status(runner_root, runner_id="runner-1")["outbox_result_count"] == 1

    receipt = accept_local_runner_result(
        "enterprise-project", result=result, root=control_root, actor=ACTOR
    )
    acknowledge_local_runner_result(
        runner_root,
        runner_id="runner-1",
        task_id=result["task_id"],
        acceptance=receipt,
    )

    register_local_runner(
        "enterprise-project",
        runner_id="runner-1",
        allowed_hosts=["gitlab.internal"],
        supported_connector_types=["gitlab"],
        runner_version="2.0.0",
        root=control_root,
        actor=ACTOR,
    )
    newer_task = _issue(control_root)["task"]
    with pytest.raises(LocalRunnerError, match="VERSION_TOO_OLD"):
        execute_local_runner_task(newer_task, runner_root=runner_root, transport=_transport)
