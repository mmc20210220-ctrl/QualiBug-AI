"""Source-owned collection reads enter the existing isolation mainline."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

import ai_test_asset_center.experiment_executor as experiment_executor
import ai_test_asset_center.experiment_plan_executor as plan_executor
import ai_test_asset_center.experiment_runtime_credentials as runtime_credentials
import ai_test_asset_center.experiment_runtime_support as runtime_support
from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.experiment_compiler_obligation import (
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
        runtime_credentials,
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
    assert all(row["relation_refs"] == [] for row in obligations)

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
