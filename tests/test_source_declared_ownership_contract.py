"""Source-owned collection reads enter the existing isolation mainline."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

import ai_test_asset_center.experiment_executor as experiment_executor
import ai_test_asset_center.experiment_plan_executor as plan_executor
import ai_test_asset_center.experiment_runtime_credentials as runtime_credentials
import ai_test_asset_center.experiment_runtime_support as runtime_support
import ai_test_asset_center.sandbox_write_executor as sandbox_write_executor
import ai_test_asset_center.sandbox_write_executor_base as sandbox_write_executor_base
import ai_test_asset_center._experiment_runtime_support_mechanics as runtime_support_mechanics
from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.experiment_compiler_base import (
    compile_experiment_for_obligation,
)
from ai_test_asset_center.fixture_dag import build_fixture_dag_for_experiment
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)


def _ir() -> dict:
    ir = empty_behavior_ir(project_id="unfamiliar-owned-documents")
    ir.update({
        "operations": [
            {
                "id": "op-documents",
                "method": "GET",
                "path": "/api/documents",
                "read_write": "read",
                "summary": (
                    "Callers can only query their own documents; cross-account "
                    "access through accountId must be rejected"
                ),
                "tags": ["api", "accountId"],
                "source_refs": [{
                    "source_id": "api_spec",
                    "locator": "GET /api/documents",
                    "kind": "api_operation",
                }],
            },
            {
                "id": "op-current-principal",
                "method": "GET",
                "path": "/api/session/me",
                "read_write": "read",
                "tags": ["api"],
                "source_refs": [{
                    "source_id": "api_spec",
                    "locator": "GET /api/session/me",
                    "kind": "api_operation",
                }],
            },
        ],
        "actors": [
            {
                "id": "actor-member-a",
                "role": "member",
                "account_ref": "member_a",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:member_a",
            },
            {
                "id": "actor-member-b",
                "role": "member",
                "account_ref": "member_b",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:member_b",
            },
        ],
        # Deliberately no permission-matrix ``owns`` relation. The operation's
        # own source contract is the authority under test.
        "relations": [],
    })
    return ir


def _patch_http(monkeypatch: pytest.MonkeyPatch, fake_http) -> None:
    for module in (
        experiment_executor,
        plan_executor,
        runtime_support,
        runtime_support_mechanics,
        runtime_credentials,
        sandbox_write_executor,
        sandbox_write_executor_base,
    ):
        monkeypatch.setattr(module, "_http_request", fake_http)


def test_source_owned_read_reaches_existing_finding_mainline_without_owns_edge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    ir = _ir()
    compiled = compile_obligations_from_behavior_ir(ir)
    obligations = [
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-documents"
    ]

    assert len(obligations) == 2
    assert {
        (row["property"]["owner_actor_ref"], row["property"]["viewer_actor_ref"])
        for row in obligations
    } == {
        ("actor-member-a", "actor-member-b"),
        ("actor-member-b", "actor-member-a"),
    }
    assert all(len(row["relation_refs"]) == 2 for row in obligations)
    assert all(
        all(ref.startswith("rel_") for ref in row["relation_refs"])
        for row in obligations
    )

    obligation = next(
        row
        for row in obligations
        if row["property"]["owner_actor_ref"] == "actor-member-a"
    )
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    experiment["fixture_dag"] = build_fixture_dag_for_experiment(
        experiment,
        behavior_ir=ir,
    )

    calls: list[tuple[str, str, dict[str, list[str]], str]] = []

    def fake_http(method: str, url: str, **kwargs):
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        token = str(kwargs.get("token") or "")
        calls.append((method, parsed.path, query, token))

        if parsed.path == "/api/session/me":
            principal_id = "account-a" if token == "owner-token" else "account-b"
            return {
                "status": 200,
                "body": {"id": principal_id},
                "headers": {"content-type": "application/json"},
            }
        if parsed.path == "/api/documents":
            target_account = (
                (query.get("accountId") or [None])[0]
                or ("account-a" if token == "owner-token" else "account-b")
            )
            # Broken SUT behavior: a viewer may select another account and gets
            # that account's document collection.
            return {
                "status": 200,
                "body": [{"id": "doc-1", "account_id": target_account}],
                "headers": {"content-type": "application/json"},
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    _patch_http(monkeypatch, fake_http)
    result = experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=ir,
        root=tmp_path,
        project="unfamiliar-owned-documents",
        base_url="http://target.invalid",
        runtime_contract={
            "environment_type": "test",
            "environment_ref": "test-env",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": "http://target.invalid",
            "status": "approved",
        },
        campaign_id="campaign-owned-documents",
        execution_id="execution-owned-documents",
        actor_tokens={
            "secret_ref:test_accounts:member_a": "owner-token",
            "secret_ref:test_accounts:member_b": "viewer-token",
        },
    )

    assert calls == [
        ("GET", "/api/session/me", {}, "owner-token"),
        ("GET", "/api/documents", {}, "owner-token"),
        (
            "GET",
            "/api/documents",
            {"accountId": ["account-a"]},
            "viewer-token",
        ),
    ]
    assert result["status"] == "EXECUTED"
    assert result["oracle_verdict"]["status"] == "VIOLATION"
    finding = result["finding"]
    assert finding is not None
    assert finding["risk_family"] == "isolation"
    assert finding["expected"] == {
        "owner_can_access": True,
        "viewer_can_access": False,
        "leak_detected": False,
    }
    assert finding["actual"] == {
        "owner_can_access": True,
        "viewer_can_access": True,
        "leak_detected": True,
    }
    assert finding["reproduction_steps"] == [
        "GET /api/session/me -> HTTP 200",
        "GET /api/documents -> HTTP 200",
        "GET /api/documents?accountId=account-a -> HTTP 200",
    ]


def test_identity_parameter_without_ownership_contract_does_not_create_isolation() -> None:
    ir = _ir()
    ir["operations"][0]["summary"] = "Filter documents by accountId"

    compiled = compile_obligations_from_behavior_ir(ir)

    assert not [
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-documents"
    ]


def _entity_relation(
    relation_id: str,
    relation_type: str,
    operation_ref: str,
    entity_ref: str,
) -> dict:
    return {
        "id": relation_id,
        "relation_type": relation_type,
        "from_ref": operation_ref,
        "to_ref": entity_ref,
        "operation_ref": operation_ref,
        "actor_ref": "",
        "preconditions": [],
        "effects": [],
        "status": "accepted",
        "confidence": 0.9,
        "source_refs": [{
            "source_id": "api_spec",
            "locator": relation_id,
            "kind": "entity_relation",
        }],
    }


def _write_ir(*, declare_owner_input: bool = False, shared_entity: bool = True) -> dict:
    ir = _ir()
    properties = {"title": {"type": "string"}}
    request_example = {"title": "Quarterly plan"}
    if declare_owner_input:
        properties["accountId"] = {"type": "string"}
        request_example["accountId"] = "delegated-account"
    ir["operations"].extend([
        {
            "id": "op-create-document",
            "method": "POST",
            "path": "/api/documents",
            "read_write": "write",
            "summary": "Create a document",
            "request_schema": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["title"],
                            "properties": properties,
                        },
                        "example": request_example,
                    }
                }
            },
            "request_example": request_example,
            "source_refs": [{
                "source_id": "api_spec",
                "locator": "POST /api/documents",
                "kind": "api_operation",
            }],
        },
        {
            "id": "op-delete-document",
            "method": "DELETE",
            "path": "/api/documents/{id}",
            "read_write": "write",
            "source_refs": [{
                "source_id": "api_spec",
                "locator": "DELETE /api/documents/{id}",
                "kind": "api_operation",
            }],
        },
    ])
    ir["entities"] = [
        {
            "id": "entity-document",
            "name": "document",
            "fields": ["id", "account_id", "title"],
            "status": "accepted",
            "confidence": 0.9,
        },
        {
            "id": "entity-unrelated",
            "name": "unrelated",
            "fields": ["id", "title"],
            "status": "accepted",
            "confidence": 0.9,
        },
    ]
    produced_entity = "entity-document" if shared_entity else "entity-unrelated"
    ir["relations"] = [
        _entity_relation(
            "rel-read-document",
            "observes",
            "op-documents",
            "entity-document",
        ),
        _entity_relation(
            "rel-create-document",
            "produces",
            "op-create-document",
            produced_entity,
        ),
        {
            "id": "rel-delete-compensates-create",
            "relation_type": "compensates",
            "from_ref": "op-delete-document",
            "to_ref": "op-create-document",
            "operation_ref": "op-delete-document",
            "actor_ref": "",
            "preconditions": [],
            "effects": [],
            "status": "accepted",
            "confidence": 0.9,
            "source_refs": [{
                "source_id": "api_spec",
                "locator": "DELETE /api/documents/{id} compensates POST /api/documents",
                "kind": "compensation_relation",
            }],
        },
    ]
    return ir


def _create_document_isolation(ir: dict) -> list[dict]:
    return [
        row
        for row in compile_obligations_from_behavior_ir(ir)["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-create-document"
    ]


def test_owned_entity_write_reuses_existing_isolation_and_cleanup_mainline() -> None:
    ir = _write_ir()
    obligations = _create_document_isolation(ir)

    assert len(obligations) == 2
    assert {
        (row["property"]["owner_actor_ref"], row["property"]["viewer_actor_ref"])
        for row in obligations
    } == {
        ("actor-member-a", "actor-member-b"),
        ("actor-member-b", "actor-member-a"),
    }
    obligation = obligations[0]
    assert obligation["property"]["ownership_param"] == "accountId"
    assert obligation["property"]["ownership_param_location"] == "body"
    assert obligation["cleanup_requirement"]["operation_ref"] == "op-delete-document"

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["control_plan"][0]["body"] == {"title": "Quarterly plan"}
    assert experiment["treatment_plan"][0]["body"] == {
        "title": "Quarterly plan",
        "accountId": "{user_id}",
    }
    assert {
        row.get("operation_ref")
        for row in experiment.get("cleanup_plan") or []
        if isinstance(row, dict)
    } == {"op-delete-document"}


def test_owned_entity_write_reaches_existing_oracle_and_finding_mainline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    ir = _write_ir()
    obligation = next(
        row
        for row in _create_document_isolation(ir)
        if row["property"]["owner_actor_ref"] == "actor-member-a"
    )
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    experiment["fixture_dag"] = build_fixture_dag_for_experiment(
        experiment,
        behavior_ir=ir,
    )

    calls: list[tuple[str, str, dict | None, str]] = []
    created: dict[str, dict] = {}

    def fake_http(method: str, url: str, **kwargs):
        parsed = urlsplit(url)
        token = str(kwargs.get("token") or "")
        body = kwargs.get("body")
        calls.append((method, parsed.path, body, token))
        if parsed.path == "/api/session/me":
            principal_id = "account-a" if token == "owner-token" else "account-b"
            return {
                "status": 200,
                "body": {"id": principal_id},
                "headers": {"content-type": "application/json"},
            }
        if method == "GET" and parsed.path == "/api/documents":
            principal_id = "account-a" if token == "owner-token" else "account-b"
            return {
                "status": 200,
                "body": [
                    dict(row)
                    for row in created.values()
                    if row.get("account_id") == principal_id
                ],
                "headers": {"content-type": "application/json"},
            }
        if method == "POST" and parsed.path == "/api/documents":
            principal_id = "account-a" if token == "owner-token" else "account-b"
            target_account = str((body or {}).get("accountId") or principal_id)
            document_id = f"doc-{len(created) + 1}"
            row = {
                "id": document_id,
                "account_id": target_account,
                "title": str((body or {}).get("title") or ""),
            }
            created[document_id] = row
            return {
                "status": 201,
                "body": row,
                "headers": {"content-type": "application/json"},
            }
        if method == "DELETE" and parsed.path.startswith("/api/documents/"):
            created.pop(parsed.path.rsplit("/", 1)[-1], None)
            return {
                "status": 200,
                "body": {"ok": True},
                "headers": {"content-type": "application/json"},
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    _patch_http(monkeypatch, fake_http)
    result = experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=ir,
        root=tmp_path,
        project="unfamiliar-owned-documents",
        base_url="http://target.invalid",
        runtime_contract={
            "environment_type": "test",
            "environment_ref": "test-env",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": "http://target.invalid",
            "status": "approved",
        },
        campaign_id="campaign-owned-document-write",
        execution_id="execution-owned-document-write",
        actor_tokens={
            "secret_ref:test_accounts:member_a": "owner-token",
            "secret_ref:test_accounts:member_b": "viewer-token",
        },
    )

    post_calls = [row for row in calls if row[0] == "POST"]
    assert post_calls == [
        ("POST", "/api/documents", {"title": "Quarterly plan"}, "owner-token"),
        (
            "POST",
            "/api/documents",
            {"title": "Quarterly plan", "accountId": "account-a"},
            "viewer-token",
        ),
    ]
    assert [row for row in calls if row[0] == "DELETE"] == [
        ("DELETE", "/api/documents/doc-2", None, "viewer-token"),
        ("DELETE", "/api/documents/doc-1", None, "owner-token"),
    ]
    assert created == {}
    assert result["status"] == "EXECUTED"
    assert result["oracle_verdict"]["status"] == "VIOLATION"
    assert result["finding"] is not None
    assert result["finding"]["risk_family"] == "isolation"
    assert result["finding"]["actual"]["leak_detected"] is True


def test_explicit_owner_assignment_contract_is_not_reinterpreted_as_own_scope() -> None:
    assert _create_document_isolation(_write_ir(declare_owner_input=True)) == []


def test_ownership_does_not_propagate_to_unrelated_entity_write() -> None:
    assert _create_document_isolation(_write_ir(shared_entity=False)) == []
