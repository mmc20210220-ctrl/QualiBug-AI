from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import inspect

import pytest

from ai_test_asset_center.discovery_mainline_contract import (
    MainlineContractError,
    build_mainline_run_contract,
)
from ai_test_asset_center.operational_receipts import (
    build_execution_operational_receipt,
)


API_SPEC = """{
  "openapi": "3.0.0",
  "paths": {
    "/resources": {
      "get": {"operationId": "listResources"}
    }
  }
}"""


def _inputs(authority: str):
    from ai_test_asset_center.discovery_mainline import DiscoveryMainlineInputs

    return DiscoveryMainlineInputs(
        project="PROJECT-1",
        root=Path("."),
        prd_text="requirement",
        api_spec_text="GET /resources",
        db_schema_text="",
        approved_base_url="http://127.0.0.1:8080",
        campaign_context={
            "mainline_authority": authority,
            "run_id": "RUN-1",
            "campaign_id": "CMP-1",
            "target_id": "TARGET-1",
            "environment_id": "ENV-1",
            "policy_id": "policy-1",
            "policy_version": "v1" if authority == "legacy_champion" else "v2",
            "strategy_fingerprint": "a" * 64,
            "evaluation_mode": "replay",
        },
    )


def _contract(authority: str, *, campaign_id: str = "CMP-1") -> dict:
    return build_mainline_run_contract(
        mainline_authority=authority,
        run_id="RUN-1",
        campaign_id=campaign_id,
        target_id="TARGET-1",
        environment_id="ENV-1",
        policy_version="v1" if authority == "legacy_champion" else "v2",
        evaluation_mode="replay",
    )


def _write_runtime_test_accounts(root: Path, project: str) -> None:
    account_dir = root / "platform_inputs" / project
    account_dir.mkdir(parents=True, exist_ok=True)
    (account_dir / "test_accounts.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "role": "reader",
                        "account_ref": "reader_a",
                        "token": "reader-token",
                    },
                    {
                        "role": "restricted",
                        "account_ref": "restricted_a",
                        "token": "restricted-token",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def test_runtime_actor_uses_authenticated_role_for_source_lineage(tmp_path: Path) -> None:
    from ai_test_asset_center.discovery_runtime import _runtime_actors

    _write_runtime_test_accounts(tmp_path, "PROJECT-1")
    account_path = tmp_path / "platform_inputs" / "PROJECT-1" / "test_accounts.json"
    account_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "role": "localized display label",
                        "authenticated_role": "source-role",
                        "account_ref": "account-1",
                        "status": "active",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    actors = _runtime_actors(tmp_path, "PROJECT-1", {})

    assert actors == [
        {
            "role": "source-role",
            "account_ref": "account-1",
            "tenant": None,
            "secret_ref": "secret_ref:test_accounts:account-1",
            "status": "active",
        }
    ]


def test_runtime_actor_uses_declared_enterprise_credential_identity(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.discovery_runtime import _runtime_actors

    project = "PROJECT-1"
    config_path = tmp_path / "platform_workspace" / project / "multi_service_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "name": "gateway",
                        "base_url": "http://127.0.0.1:8080",
                        "auth": {
                            "type": "password_login",
                            "login_api": "/api/auth/login",
                            "username_field": "email",
                            "buyer": {
                                "username": "buyer@example.com",
                                "password": "declared-only-in-test",
                            },
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    actors = _runtime_actors(tmp_path, project, {})

    assert actors == [
        {
            "role": "buyer",
            "account_ref": "buyer@example.com",
            "tenant": None,
            "secret_ref": "secret_ref:test_accounts:buyer@example.com",
            "status": "active",
        }
    ]


def test_runtime_actor_uses_source_accounts_when_credential_config_cannot_decrypt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center import discovery_runtime_planning_actors as planning
    from ai_test_asset_center.credential_crypto import CredentialDecryptionError
    from ai_test_asset_center.discovery_runtime import _runtime_actors

    project = "PROJECT-1"

    def raise_decryption_error(root: Path, project_id: str) -> list[dict[str, str]]:
        raise CredentialDecryptionError("stale_project_credential_key")

    monkeypatch.setattr(planning, "configured_runtime_accounts", raise_decryption_error)
    monkeypatch.setattr(
        planning,
        "_parse_test_accounts_md",
        lambda root, project_id: [
            {
                "role": "buyer",
                "email": "buyer@example.com",
                "password": "source-only-secret",
            }
        ],
    )
    context: dict[str, object] = {}

    actors = _runtime_actors(tmp_path, project, context)

    assert actors == [
        {
            "role": "buyer",
            "account_ref": "buyer@example.com",
            "tenant": None,
            "secret_ref": "secret_ref:test_accounts:buyer@example.com",
            "status": "active",
        }
    ]
    assert context["runtime_credential_resolution"] == {
        "status": "source_backed_fallback",
        "configured_status": "decryption_failed",
        "source": "registered_test_data_or_TEST_ACCOUNTS.md",
        "error_type": "CredentialDecryptionError",
        "account_count": 1,
    }


def test_runtime_actor_does_not_hide_credential_decryption_without_source_accounts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``_runtime_actors`` resolves these symbols in the extracted planning
    # actors module; ``discovery_runtime_planning`` keeps compatibility re-exports.
    from ai_test_asset_center import discovery_runtime_planning_actors as planning
    from ai_test_asset_center.credential_crypto import CredentialDecryptionError
    from ai_test_asset_center.discovery_runtime import _runtime_actors

    def raise_decryption_error(root: Path, project_id: str) -> list[dict[str, str]]:
        raise CredentialDecryptionError("stale_project_credential_key")

    monkeypatch.setattr(planning, "configured_runtime_accounts", raise_decryption_error)
    monkeypatch.setattr(planning, "_parse_test_accounts_md", lambda root, project_id: [])

    with pytest.raises(CredentialDecryptionError, match="stale_project_credential_key"):
        _runtime_actors(tmp_path, "PROJECT-1", {})


def test_registered_test_data_source_supplies_exact_account_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center import experiment_runtime_credentials as runtime_support
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    project = "PROJECT-1"
    register_source_asset(
        project,
        "test-accounts",
        "| role | email | password |\n"
        "| --- | --- | --- |\n"
        "| buyer | buyer@example.com | Test@123 |\n",
        source_type="test_data",
        root=tmp_path,
    )
    monkeypatch.setattr(
        runtime_support,
        "_http_request",
        lambda *args, **kwargs: {
            "status": 200,
            "body": {"token": "buyer-token"},
        },
    )

    tokens = runtime_support.load_actor_tokens(
        tmp_path,
        project,
        base_url="http://127.0.0.1:8080",
    )

    assert tokens["buyer@example.com"] == "buyer-token"
    assert tokens["secret_ref:test_accounts:buyer@example.com"] == "buyer-token"


def test_registered_database_schema_source_reaches_scan_preparation(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.scan_source_runtime import _load_schema_assets

    register_source_asset(
        "PROJECT-1",
        "db-schema",
        "# Database schema\n\n- orders.id: integer\n",
        source_type="database_schema",
        root=tmp_path,
    )

    schema_text = _load_schema_assets(tmp_path, "PROJECT-1")

    assert "orders.id" in schema_text


def test_platform_inputs_sql_ddl_reaches_schema_load_despite_weak_md(
    tmp_path: Path,
) -> None:
    """CREATE TABLE DDL in platform_inputs must reach db_schema_text.

    Weak DB_SCHEMA.md inventory alone previously left runtime overlay without
    columns; SQL materials must win through for entity field projection.
    """
    from ai_test_asset_center.scan_source_runtime import _load_schema_assets

    project = "PROJECT-DDL"
    inputs = tmp_path / "platform_inputs" / project
    inputs.mkdir(parents=True)
    (inputs / "DB_SCHEMA.md").write_text(
        "# Schema\n\n| table | note |\n|---|---|\n| accounts | roles |\n\n- `accounts.role`: role\n",
        encoding="utf-8",
    )
    (inputs / "schema.sql").write_text(
        "CREATE TABLE accounts (\n"
        "  id UUID PRIMARY KEY,\n"
        "  email TEXT UNIQUE NOT NULL,\n"
        "  name TEXT NOT NULL,\n"
        "  phone TEXT,\n"
        "  role TEXT NOT NULL\n"
        ");\n",
        encoding="utf-8",
    )
    # Seed dumps must not be treated as schema authority.
    (inputs / "seed.sql").write_text(
        "INSERT INTO accounts(email) VALUES ('seed@example.com');\n",
        encoding="utf-8",
    )

    schema_text = _load_schema_assets(tmp_path, project)
    assert "CREATE TABLE accounts" in schema_text
    assert "email" in schema_text
    assert "seed@example.com" not in schema_text


def test_misclassified_registered_create_table_reaches_schema_load(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.scan_source_runtime import _load_schema_assets

    register_source_asset(
        "PROJECT-2",
        "legacy-ddl.md",
        "Notes\n\nCREATE TABLE widgets (id TEXT PRIMARY KEY, sku TEXT NOT NULL);\n",
        source_type="collaboration_document",
        root=tmp_path,
    )

    schema_text = _load_schema_assets(tmp_path, "PROJECT-2")
    assert "CREATE TABLE widgets" in schema_text
    assert "sku" in schema_text


def test_api_operation_extraction_does_not_invent_post_write_side_effect() -> None:
    from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
    from ai_test_asset_center.discovery_runtime import _api_operations
    from ai_test_asset_center.v12_pipeline import _extract_api_operations_for_ir

    api_spec = json.dumps({
        "openapi": "3.0.0",
        "paths": {
            "/api/discounts/validate": {
                "post": {
                    "operationId": "validateDiscount",
                    "summary": "Validate a discount and calculate eligibility.",
                }
            },
            "/api/discounts/redeem": {
                "post": {
                    "operationId": "redeemDiscount",
                    "summary": "Redeem and consume a discount.",
                }
            },
        },
    })

    for extractor in (_api_operations, _extract_api_operations_for_ir):
        operations = (
            extractor(api_spec, submitted_source_text="")
            if extractor is _api_operations
            else extractor(api_spec)
        )
        by_path = {operation["path"]: operation for operation in operations}

        assert "side_effect_class" not in by_path["/api/discounts/validate"]
        assert "side_effect_class" not in by_path["/api/discounts/redeem"]

        ir = build_behavior_ir_from_knowledge_asset(
            {},
            api_operations=operations,
            project_id="api-extraction-side-effect-semantics",
        )
        by_operation = {operation["operation_id"]: operation for operation in ir["operations"]}

        assert by_operation["validateDiscount"]["read_write"] == "read"
        assert by_operation["validateDiscount"]["side_effect_class"] == "read"
        assert by_operation["redeemDiscount"]["read_write"] == "write"
        assert by_operation["redeemDiscount"]["side_effect_class"] == "write"


def test_v12_wrapper_delegates_once_and_has_no_runtime_fallback() -> None:
    source = Path("ai_test_asset_center/v12_pipeline.py").read_text(encoding="utf-8")

    assert "effective_execution_status" not in source
    assert "fallback_to_legacy" not in source
    assert source.count("run_discovery_mainline(") == 1


def test_legacy_champion_domain_does_not_compile_or_execute_candidate_vertical_slice() -> None:
    from ai_test_asset_center.v12_pipeline import _run_legacy_champion_domain

    source = inspect.getsource(_run_legacy_champion_domain)

    assert "build_behavior_ir_from_knowledge_asset" not in source
    assert "compile_obligations_from_behavior_ir" not in source
    assert "compile_experiments" not in source
    assert "plan_obligation_round" not in source
    assert "execute_selected_experiments" not in source


def test_v12_rejects_missing_immutable_run_identity(tmp_path: Path) -> None:
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    with pytest.raises(MainlineContractError, match="mainline_authority_missing"):
        run_v12_pipeline(
            "project-missing-identity",
            tmp_path,
            api_spec_text=API_SPEC,
            campaign_context={},
        )


def test_experiment_candidate_returns_attempt_authoritative_result(tmp_path: Path) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    manifest = register_source_asset(
        "project-candidate",
        "api-contract",
        API_SPEC,
        source_type="openapi",
        root=tmp_path,
    )
    result = run_v12_pipeline(
        "project-candidate",
        tmp_path,
        api_spec_text=API_SPEC,
        campaign_context={
            "mainline_authority": "experiment_candidate",
            "run_id": "RUN-CANDIDATE",
            "target_id": "TARGET-CANDIDATE",
            "environment_id": "ENV-CANDIDATE",
            "environment_ref": "ENV-CANDIDATE",
            "environment_type": "test",
            "scope_id": "scope-candidate",
            "policy_version": "policy-candidate",
            "evaluation_mode": "operational",
            "source_manifest": manifest,
        },
    )

    assert result["mainline_run"]["mainline_authority"] == "experiment_candidate"
    assert result["obligation_attempt_ledger"]["complete"] is True
    assert result["discovery_funnel"]["receipt_authority"] == "obligation_attempt_ledger"
    assert result["formal_count_projection"]["canonical_defect_ids"] == []


def test_candidate_plans_source_derived_runtime_interface_discovery(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    manifest = register_source_asset(
        "project-runtime-surface-plan",
        "api-contract",
        API_SPEC,
        source_type="openapi",
        root=tmp_path,
    )
    result = run_v12_pipeline(
        "project-runtime-surface-plan",
        tmp_path,
        api_spec_text=API_SPEC,
        campaign_context={
            "mainline_authority": "experiment_candidate",
            "run_id": "RUN-RUNTIME-SURFACE-PLAN",
            "target_id": "TARGET-RUNTIME-SURFACE-PLAN",
            "environment_id": "ENV-RUNTIME-SURFACE-PLAN",
            "environment_ref": "ENV-RUNTIME-SURFACE-PLAN",
            "environment_type": "test",
            "scope_id": "scope-runtime-surface-plan",
            "policy_version": "policy-runtime-surface-plan",
            "evaluation_mode": "operational",
            "source_manifest": manifest,
            "runtime_interface_discovery_enabled": True,
        },
    )

    surface = result["runtime_interface_discovery"]
    assert surface["status"] == "PLANNED"
    assert surface["plan"]["candidate_count"] > 0
    assert surface["execution"]["selected_count"] == 0
    serialized = json.dumps(result, ensure_ascii=False)
    assert "_knowledge_asset" not in serialized
    assert "_documented_operations" not in serialized
    assert "_runtime_actors" not in serialized


def test_v12_validates_primary_source_before_using_enriched_api_document(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    primary = API_SPEC
    enriched = json.dumps({
        "openapi": "3.0.0",
        "paths": {
            "/resources": {"get": {"operationId": "listResources"}},
            "/resources/{id}": {"get": {"operationId": "getResource"}},
        },
    })
    manifest = register_source_asset(
        "project-enriched-source",
        "primary-api-contract",
        primary,
        source_type="openapi",
        root=tmp_path,
    )

    result = run_v12_pipeline(
        "project-enriched-source",
        tmp_path,
        api_spec_text=enriched,
        campaign_context={
            "mainline_authority": "experiment_candidate",
            "run_id": "RUN-ENRICHED-SOURCE",
            "target_id": "TARGET-ENRICHED-SOURCE",
            "environment_id": "ENV-ENRICHED-SOURCE",
            "environment_ref": "ENV-ENRICHED-SOURCE",
            "environment_type": "test",
            "scope_id": "scope-enriched-source",
            "policy_version": "policy-enriched-source",
            "evaluation_mode": "operational",
            "source_manifest": manifest,
            "_source_verification_text": primary,
        },
    )

    assert result["mainline_run"]["run_id"] == "RUN-ENRICHED-SOURCE"


def test_candidate_operation_catalog_preserves_primary_request_evidence() -> None:
    from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
    from ai_test_asset_center.discovery_runtime import _api_operations

    primary = """# API

### POST /resources

Request:

```json
{"name":"source-declared","count":2,"enabled":true,"labels":["a"]}
```
"""
    enriched = """# Merged API catalog

### POST /resources
"""

    operations = _api_operations(
        enriched,
        submitted_source_text=primary,
    )
    behavior_ir = build_behavior_ir_from_knowledge_asset(
        {},
        project_id="primary-request-evidence",
        api_operations=operations,
    )

    assert len(behavior_ir["operations"]) == 1
    request_schema = behavior_ir["operations"][0]["request_schema"]
    media = request_schema["content"]["application/json"]
    assert media["example"] == {
        "name": "source-declared",
        "count": 2,
        "enabled": True,
        "labels": ["a"],
    }
    properties = media["schema"]["properties"]
    assert properties["name"]["type"] == "string"
    assert properties["count"]["type"] == "integer"
    assert properties["enabled"]["type"] == "boolean"
    assert properties["labels"]["type"] == "array"


def test_manual_candidate_terminal_receipt_preserves_compile_detail() -> None:
    from ai_test_asset_center.discovery_mainline import DiscoveryPlanningBundle
    from ai_test_asset_center.discovery_runtime import _manual_terminal_receipts

    plan = DiscoveryPlanningBundle(
        mainline_run={},
        behavior_ir={},
        obligations={},
        experiments={
            "by_obligation": {
                "obl-1": {
                    "experiment_id": "exp-1",
                    "compile_receipt": {
                        "status": "BLOCKED",
                        "reason_code": "BLOCKED_MISSING_OBSERVER",
                        "detail": "write_observer",
                    },
                }
            },
            "obligation_plan": {"selected": [], "pending_next_round": []},
        },
    )
    compile_results: dict[str, dict] = {}

    _manual_terminal_receipts(
        selected_rows=[{"obligation_id": "obl-1"}],
        experiments_by_obligation=dict(plan.experiments["by_obligation"]),
        obligation_plan=dict(plan.experiments["obligation_plan"]),
        runtime_contract={},
        compile_results=compile_results,
        execution_results={},
    )

    assert compile_results["obl-1"]["detail"] == "write_observer"


def test_candidate_accounts_for_compiled_obligation_when_runtime_is_plan_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    manifest = register_source_asset(
        "project-plan-only",
        "api-contract",
        API_SPEC,
        source_type="openapi",
        root=tmp_path,
    )
    _write_runtime_test_accounts(tmp_path, "project-plan-only")
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_knowledge_center.build_enterprise_business_knowledge_asset",
        lambda *_args, **_kwargs: {
            "source_inventory": [
                {
                    "source_id": "api-contract",
                    "source_type": "openapi",
                    "original_name": "api.json",
                    "content_hash": manifest["source_hash"],
                }
            ],
            "permission_matrix": [
                {
                    "role": "reader",
                    "resource": "/resources",
                    "actions": ["read"],
                    "scope": "own",
                    "source_id": "permission-source",
                },
                {
                    "role": "restricted",
                    "resource": "/resources",
                    "actions": ["read"],
                    "decision": "deny",
                    "scope": "own",
                    "source_id": "permission-source",
                },
            ],
        },
    )
    result = run_v12_pipeline(
        "project-plan-only",
        tmp_path,
        api_spec_text=API_SPEC,
        campaign_context={
            "mainline_authority": "experiment_candidate",
            "run_id": "RUN-PLAN-ONLY",
            "target_id": "TARGET-PLAN-ONLY",
            "environment_id": "ENV-PLAN-ONLY",
            "environment_ref": "ENV-PLAN-ONLY",
            "environment_type": "test",
            "scope_id": "scope-plan-only",
            "policy_version": "policy-plan-only",
            "evaluation_mode": "operational",
            "source_manifest": manifest,
        },
    )

    attempts = result["obligation_attempt_ledger"]["attempts"]
    assert attempts
    assert all(row["terminal_status"] in {"BLOCKED", "DEFERRED"} for row in attempts)
    # Plan-only runs block all experiments. Core obligations from the main
    # compiler fail at the runtime-target gate, while coverage-driven
    # obligations may fail earlier (missing fixture, missing actor, etc.)
    # because the plan-only test environment is intentionally minimal.
    reason_codes = {row["reason_code"] for row in attempts}
    assert "BLOCKED_RUNTIME_TARGET" in reason_codes, (
        f"Expected at least one BLOCKED_RUNTIME_TARGET, got {reason_codes}"
    )
    # Non-selected formal obligations remain explicitly deferred and are not
    # misreported as runtime blocks or successful execution.
    deferred = [
        row for row in attempts
        if row["selection_status"] == "DEFERRED_NOT_SELECTED"
    ]
    assert deferred
    assert all(row["terminal_status"] == "DEFERRED" for row in deferred)
    assert all(row["reason_code"] == "OBLIGATION_NOT_IN_PLAN" for row in deferred)
    assert all(
        row["reason_code"].startswith("BLOCKED_")
        for row in attempts
        if row["selection_status"] == "SELECTED"
    )
    assert result["discovery_funnel"]["pipeline_health"]["status"] == "BLOCKED"


def test_candidate_keeps_runtime_interface_discovery_inside_attempt_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    manifest = register_source_asset(
        "project-execution",
        "api-contract",
        API_SPEC,
        source_type="openapi",
        root=tmp_path,
    )
    _write_runtime_test_accounts(tmp_path, "project-execution")
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_knowledge_center.build_enterprise_business_knowledge_asset",
        lambda *_args, **_kwargs: {
            "source_inventory": [
                {
                    "source_id": "api-contract",
                    "source_type": "openapi",
                    "original_name": "api.json",
                    "content_hash": manifest["source_hash"],
                }
            ],
            "permission_matrix": [
                {
                    "role": "reader",
                    "resource": "/resources",
                    "actions": ["read"],
                    "scope": "own",
                    "source_id": "permission-source",
                },
                {
                    "role": "restricted",
                    "resource": "/resources",
                    "actions": ["read"],
                    "decision": "deny",
                    "scope": "own",
                    "source_id": "permission-source",
                },
            ],
        },
    )
    calls: list[str] = []

    def fake_execute(selected, **_kwargs):
        calls.append("experiment")
        obligation_id = selected[0]["obligation_id"]
        return {
            "selected_count": 1,
            "executed_count": 1,
            "blocked_count": 0,
            "harness_failure_count": 0,
            "cleanup_failures": 0,
            "findings": [],
            "results": [],
            "compile_results": {
                obligation_id: {
                    "status": "COMPILED",
                    "cost_coverage_status": "MEASURED",
                }
            },
            "execution_results": {
                obligation_id: {
                    "status": "EXECUTED",
                    "observation_receipt_ids": ["obs-approved"],
                    "oracle_receipt_id": "oracle-approved",
                    "cost_coverage_status": "MEASURED",
                        "operational_receipt": build_execution_operational_receipt(
                            receipt_id=f"operational-approved-{obligation_id}",
                            execution_status="EXECUTED",
                            steps=[{
                                "method": "GET",
                                "path": "/resources",
                                "status_code": 200,
                            }],
                            cleanup_failures=0,
                        ),
                }
            },
            "gate_results": {
                obligation_id: {
                    "status": "REJECTED",
                    "reason_code": "ORACLE_NOT_VIOLATED",
                    "gate_receipt_id": "gate-approved",
                    "cost_coverage_status": "MEASURED",
                }
            },
            "every_experiment_has_receipt": True,
        }

    monkeypatch.setattr(
        "ai_test_asset_center.discovery_runtime_execution.execute_selected_experiments",
        fake_execute,
    )
    pending_round_calls: list[set[str]] = []
    pending_round_id = "obl-runtime-round-2-pending"

    def fake_consume_pending_rounds(**kwargs):
        obligation_ids = {
            str(row.get("obligation_id") or "")
            for row in kwargs.get("obligations", [])
            if isinstance(row, dict)
        }
        if pending_round_id not in obligation_ids:
            return [], kwargs["obligation_plan"]
        pending_round_calls.append(obligation_ids)
        return [
            {
                "selected_count": 1,
                "executed_count": 1,
                "blocked_count": 0,
                "harness_failure_count": 0,
                "cleanup_failures": 0,
                "findings": [],
                "results": [],
                "compile_results": {
                    pending_round_id: {
                        "status": "COMPILED",
                        "experiment_id": "exp-runtime-round-2-pending",
                        "cost_coverage_status": "MEASURED",
                    }
                },
                "execution_results": {
                    pending_round_id: {
                        "status": "EXECUTED",
                        "execution_id": "exec-runtime-round-2-pending",
                        "observation_receipt_ids": ["obs-runtime-round-2-pending"],
                        "oracle_receipt_id": "oracle-runtime-round-2-pending",
                        "cost_coverage_status": "MEASURED",
                        "operational_receipt": build_execution_operational_receipt(
                            receipt_id="operational-runtime-round-2-pending",
                            execution_status="EXECUTED",
                            steps=[{
                                "method": "GET",
                                "path": "/resources",
                                "status_code": 200,
                            }],
                            cleanup_failures=0,
                        ),
                    }
                },
                "gate_results": {
                    pending_round_id: {
                        "status": "REJECTED",
                        "reason_code": "ORACLE_NOT_VIOLATED",
                        "gate_receipt_id": "gate-runtime-round-2-pending",
                        "cost_coverage_status": "MEASURED",
                    }
                },
                "every_experiment_has_receipt": True,
            }
        ], {
            **kwargs["obligation_plan"],
            "pending_next_round": [],
            "pending_count": 0,
            "follow_on_round_receipts": [{
                "planning_round": 3,
                "selected_count": 1,
                "pending_count": 0,
                "executed_count": 1,
            }],
        }

    monkeypatch.setattr(
        "ai_test_asset_center.discovery_runtime_execution._consume_pending_obligation_rounds",
        fake_consume_pending_rounds,
    )

    def fake_surface_execute(*_args, **_kwargs):
        calls.append("surface")
        obligation_id = "surfobl-test"
        return {
            "selected_count": 1,
            "executed_count": 1,
            "blocked_count": 0,
            "harness_failure_count": 0,
            "cleanup_failures": 0,
            "selected_rows": [{
                "obligation_id": obligation_id,
                "candidate_id": "surface-test",
                "risk_family": "interface_discovery",
                "source_refs": [{"source_id": "api-contract"}],
                "required_operations": [],
                "required_actors": [],
                "relation_refs": [],
                "operation_refs": [],
                "actor_refs": [],
                "behavior_ir_refs": [],
                "adapter": "http_api_discovery",
                "planning_round": 0,
                "experiment_id": "surfexp-test",
            }],
            "compile_results": {
                obligation_id: {
                    "status": "COMPILED",
                    "experiment_id": "surfexp-test",
                    "cost_coverage_status": "MEASURED",
                }
            },
            "execution_results": {
                obligation_id: {
                    "status": "EXECUTED",
                    "execution_id": "surfexec-test",
                    "experiment_id": "surfexp-test",
                    "observation_receipt_ids": ["surface-observation"],
                    "cost_coverage_status": "MEASURED",
                    "operational_receipt": build_execution_operational_receipt(
                        receipt_id="surface-operational",
                        execution_status="EXECUTED",
                        steps=[{
                            "method": "GET",
                            "path": "/resources/export",
                            "status_code": 404,
                        }],
                        cleanup_failures=0,
                    ),
                }
            },
            "gate_results": {
                obligation_id: {
                    "status": "REJECTED",
                    "reason_code": "SURFACE_DISCOVERY_OBSERVATION_ONLY",
                    "gate_receipt_id": "surface-gate",
                    "cost_coverage_status": "MEASURED",
                }
            },
            "observation_receipts": [],
            "discovered_operations": [],
            "findings": [],
        }

    monkeypatch.setattr(
        "ai_test_asset_center.discovery_runtime_execution.execute_runtime_interface_discovery",
        fake_surface_execute,
    )

    def fake_expand(**kwargs):
        calls.append("expand")
        delta_obligation = {
            "obligation_id": "obl-runtime-round-2",
            "candidate_id": "obl-runtime-round-2",
            "risk_family": "authorization",
            "source_refs": [{"source_id": "surface-observation"}],
            "required_operations": [],
            "required_actors": [],
            "relation_refs": [],
            "operation_refs": [],
            "actor_refs": [],
            "behavior_ir_refs": [],
            "adapter": "http_api",
            "execution_adapters": ["http_api"],
            "planning_round": 2,
            "experiment_id": "exp-runtime-round-2",
        }
        pending_obligation = {
            **delta_obligation,
            "obligation_id": pending_round_id,
            "candidate_id": pending_round_id,
            "experiment_id": "exp-runtime-round-2-pending",
        }
        return {
            "status": "EXPANDED",
            "behavior_ir": kwargs["initial_behavior_ir"],
            "delta_obligations": [delta_obligation, pending_obligation],
            "experiment_pack": {},
            "all_experiments": [],
            "by_obligation": {
                "obl-runtime-round-2": {
                    "obligation_id": "obl-runtime-round-2",
                    "experiment_id": "exp-runtime-round-2",
                    "compile_receipt": {"status": "COMPILED"},
                },
                pending_round_id: {
                    "obligation_id": pending_round_id,
                    "experiment_id": "exp-runtime-round-2-pending",
                    "compile_receipt": {"status": "COMPILED"},
                },
            },
            "obligation_plan": {
                "selected": [{"obligation_id": "obl-runtime-round-2"}],
                "pending_next_round": [{"obligation_id": pending_round_id}],
                "budget": 1,
                "selected_count": 1,
                "pending_count": 1,
            },
            "agent_intent_plan": {
                "intents": [{"obligation_id": "obl-runtime-round-2"}],
            },
            "selected_rows": [delta_obligation, pending_obligation],
            "round_receipt": {
                "schema_version": "qualibug.behavior-ir-expansion-round.v1",
                "planning_round": 2,
                "new_obligation_count": 2,
                "receipt_fingerprint": "a" * 64,
            },
        }

    monkeypatch.setattr(
        "ai_test_asset_center.discovery_runtime_execution.expand_behavior_ir_from_runtime_observations",
        fake_expand,
    )
    result = run_v12_pipeline(
        "project-execution",
        tmp_path,
        api_spec_text=API_SPEC,
        base_url="http://127.0.0.1:8080",
        campaign_context={
            "mainline_authority": "experiment_candidate",
            "run_id": "RUN-EXECUTION",
            "target_id": "TARGET-EXECUTION",
            "environment_id": "ENV-EXECUTION",
            "environment_ref": "ENV-EXECUTION",
            "environment_type": "test",
            "scope_id": "scope-execution",
            "execution_mode": "safe_read_only",
            "policy_version": "policy-execution",
            "evaluation_mode": "operational",
            "source_manifest": manifest,
            "runtime_interface_discovery_enabled": True,
        },
    )

    assert calls == ["surface", "expand", "experiment", "experiment"]
    assert pending_round_calls == [{
        "obl-runtime-round-2",
        pending_round_id,
    }]
    # Core obligations (3) + coverage-driven obligations (variable).
    # Coverage obligations may be BLOCKED when the minimal test setup lacks
    # fixtures/observers; this is expected and does not affect the runtime
    # interface discovery authority under test.
    attempts = result["obligation_attempt_ledger"]["attempts"]
    assert len(attempts) >= 3, f"Expected at least 3 attempts, got {len(attempts)}"
    assert not any(
        row.get("risk_family") == "interface_discovery"
        for row in attempts
    )
    separation = result["business_discovery_separation"]
    assert separation["business_obligation_summary"]["total"] == len(attempts)
    assert separation["discovery_task_summary"]["generated_discovery_tasks"] == 1
    assert result["experiment_execution"]["surface_discovery_selected_count"] == 1
    assert result["behavior_ir_expansion"]["follow_on_batch_count"] == 1
    assert result["behavior_ir_expansion"]["obligation_plan"]["pending_count"] == 0
    assert any(
        row["obligation_id"] == pending_round_id
        and row["terminal_status"] == "REJECTED"
        for row in attempts
    )
    terminal_statuses = {row["terminal_status"] for row in attempts}
    assert terminal_statuses.issubset({"REJECTED", "BLOCKED", "DEFERRED"}), (
        f"Unexpected terminal statuses: {terminal_statuses}"
    )
    assert all(
        row["reason_code"] == "OBLIGATION_NOT_IN_PLAN"
        for row in attempts
        if row["terminal_status"] == "DEFERRED"
    )
    assert result["phases"]["execution"]["observed_http_request_count"] >= 3
    assert result["phases"]["execution"]["production_http_requests"] == 0
    assert result["phases"]["execution"]["scenario_attempts"] >= 3
    assert result["phases"]["execution"]["accepted_write_count"] == 0
    assert result["discovery_funnel"]["pipeline_health"]["status"] in {"OK", "DEGRADED"}
    identity_receipt = result["test_obligations"][
        "obligation_identity_receipt"
    ]
    assert identity_receipt["status"] == "PASS"
    assert identity_receipt["duplicate_count"] == 0
    assert identity_receipt["expansion_overlap_ids"] == []
    assert result["behavior_ir_expansion"]["status"] == "EXPANDED"
    assert result["behavior_ir_expansion"]["round_receipt"]["planning_round"] == 2
    consistency = result["defect_identity_consistency"]
    assert set(consistency["occurrence_scopes"]) >= {
        "delivery_gate_ids",
        "formal_authority_occurrence_ids",
        "registry_occurrence_ids",
        "formal_projection_occurrence_ids",
        "evaluator_submission_occurrence_ids",
    }
    assert set(consistency["canonical_scopes"]) >= {
        "canonical_registry_ids",
        "formal_projection_ids",
        "product_projection_ids",
        "evaluator_submission_ids",
    }


def test_shadow_projection_never_mutates_semantic_gate_receipt() -> None:
    from ai_test_asset_center.discovery_runtime import _project_gate_results_for_authority

    projected = _project_gate_results_for_authority(
        gate_results={
            "obl-1": {
                "status": "DELIVERABLE",
                "finding_id": "finding-1",
                "gate_receipt_id": "gate-1",
            }
        },
        contract=_contract("experiment_candidate"),
    )

    assert projected["obl-1"] == {
        "status": "DELIVERABLE",
        "finding_id": "finding-1",
        "gate_receipt_id": "gate-1",
    }


def test_shadow_finding_retains_semantic_gate_status_for_private_evaluator() -> None:
    from ai_test_asset_center.discovery_runtime import (
        _authority_findings,
        _project_gate_results_for_authority,
    )

    contract = _contract("experiment_candidate")
    gate_results = _project_gate_results_for_authority(
        gate_results={
            "obl-1": {
                "status": "DELIVERABLE",
                "finding_id": "finding-1",
                "gate_receipt_id": "gate-1",
            }
        },
        contract=contract,
    )

    deliverable, candidates, shadow = _authority_findings(
        raw_findings=[{"finding_id": "finding-1", "obligation_id": "obl-1"}],
        gate_results=gate_results,
        contract=contract,
    )

    assert deliverable == []
    assert candidates == []
    assert shadow[0]["semantic_delivery_gate_status"] == "DELIVERABLE"
    assert shadow[0]["delivery_gate_receipt_id"] == "gate-1"


def test_shadow_finding_resolves_selected_key_with_exact_variant_gate_identity() -> None:
    from ai_test_asset_center.discovery_runtime import _authority_findings

    deliverable, candidates, shadow = _authority_findings(
        raw_findings=[{
            "finding_id": "finding-variant",
            "selected_obligation_id": "obl-selected",
            "obligation_id": "obl-selected__v_abcd",
        }],
        gate_results={
            "obl-selected": {
                "status": "DELIVERABLE",
                "gate_receipt_id": "gate-variant",
                "identity": {
                    "finding_id": "finding-variant",
                    "obligation_id": "obl-selected__v_abcd",
                },
            }
        },
        contract=_contract("experiment_candidate"),
    )

    assert deliverable == []
    assert candidates == []
    assert shadow[0]["obligation_id"] == "obl-selected__v_abcd"
    assert shadow[0]["delivery_gate_receipt_id"] == "gate-variant"


def test_campaign_identity_exists_before_planning_and_execution() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    events: list[str] = []
    contract = _contract("experiment_candidate")

    result = run_discovery_mainline(
        _inputs("experiment_candidate"),
        build_campaign=lambda _: events.append("campaign") or SimpleNamespace(campaign_id="CMP-1"),
        build_plan=lambda *_: events.append("plan") or SimpleNamespace(mainline_run=contract),
        experiment_runner=lambda *_: events.append("experiment") or {"mainline_run": contract},
    )

    assert events == ["campaign", "plan", "experiment"]
    assert result["mainline_run"]["campaign_id"] == "CMP-1"


@pytest.mark.parametrize("authority", ["legacy_champion", "experiment_candidate"])
def test_frozen_authority_invokes_only_its_matching_runner(authority: str) -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    calls = {"legacy": 0, "experiment": 0}
    contract = _contract(authority)

    def legacy(*_):
        calls["legacy"] += 1
        return {"mainline_run": contract}

    def experiment(*_):
        calls["experiment"] += 1
        return {"mainline_run": contract}

    run_discovery_mainline(
        _inputs(authority),
        build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
        build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
        legacy_runner=legacy,
        experiment_runner=experiment,
    )

    assert calls == (
        {"legacy": 1, "experiment": 0}
        if authority == "legacy_champion"
        else {"legacy": 0, "experiment": 1}
    )


def test_missing_selected_runner_fails_before_campaign_creation() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    events: list[str] = []
    with pytest.raises(
        MainlineContractError,
        match="mainline_runner_unavailable:legacy_champion",
    ):
        run_discovery_mainline(
            _inputs("legacy_champion"),
            build_campaign=lambda _: events.append("campaign"),
            build_plan=lambda *_: events.append("plan"),
            legacy_runner=None,
            experiment_runner=lambda *_: events.append("candidate") or {},
        )

    assert events == []


def test_runner_binding_receipt_preserves_policy_authority_and_code_identity() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    contract = _contract("experiment_candidate")

    def candidate(*_):
        return {"mainline_run": contract}

    result = run_discovery_mainline(
        _inputs("experiment_candidate"),
        build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
        build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
        experiment_runner=candidate,
    )

    receipt = result["mainline_runner_receipt"]
    assert receipt["schema_version"] == "qualibug.discovery-mainline-runner.v1"
    assert receipt["mainline_authority"] == "experiment_candidate"
    assert receipt["policy_id"] == "policy-1"
    assert receipt["policy_version"] == "v2"
    assert receipt["strategy_fingerprint"] == "a" * 64
    assert receipt["mainline_contract_fingerprint"] == contract["contract_fingerprint"]
    assert len(receipt["runner_fingerprint"]) == 64
    assert len(receipt["receipt_fingerprint"]) == 64


def test_runner_fingerprint_mismatch_fails_before_campaign_creation() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    inputs = _inputs("experiment_candidate")
    inputs.campaign_context["mainline_runner_fingerprint"] = "0" * 64
    events: list[str] = []

    with pytest.raises(
        MainlineContractError,
        match="mainline_runner_fingerprint_mismatch:experiment_candidate",
    ):
        run_discovery_mainline(
            inputs,
            build_campaign=lambda _: events.append("campaign"),
            build_plan=lambda *_: events.append("plan"),
            experiment_runner=lambda *_: events.append("candidate") or {},
        )

    assert events == []


def test_runner_failure_never_falls_back_to_other_authority() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    calls = {"legacy": 0, "experiment": 0}
    contract = _contract("experiment_candidate")

    def fail_experiment(*_):
        calls["experiment"] += 1
        raise RuntimeError("candidate failed")

    with pytest.raises(RuntimeError, match="candidate failed"):
        run_discovery_mainline(
            _inputs("experiment_candidate"),
            build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
            build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
            experiment_runner=fail_experiment,
        )

    assert calls == {"legacy": 0, "experiment": 1}


def test_legacy_policy_block_is_not_misclassified_as_harness_failure() -> None:
    from ai_test_asset_center.discovery_runtime import _legacy_execution_terminal

    status, reason = _legacy_execution_terminal(
        cleanup_failed=False,
        observation_receipt_ids=[],
        trace_errors=["write_cleanup_operation_not_declared"],
        skipped_reasons=["write_cleanup_operation_not_declared"],
        trace_present=True,
    )

    assert status == "BLOCKED"
    assert reason == "BLOCKED_WRITE_CLEANUP_OPERATION_NOT_DECLARED"


def test_legacy_cleanup_failure_with_observations_stays_executed() -> None:
    from ai_test_asset_center.discovery_runtime import _legacy_execution_terminal

    status, reason = _legacy_execution_terminal(
        cleanup_failed=True,
        observation_receipt_ids=["observation_1"],
        trace_errors=[],
        skipped_reasons=[],
        trace_present=True,
    )

    assert status == "EXECUTED"
    assert reason == "CLEANUP_COMPENSATION_FAILED"

    status, reason = _legacy_execution_terminal(
        cleanup_failed=True,
        observation_receipt_ids=[],
        trace_errors=[],
        skipped_reasons=[],
        trace_present=True,
    )
    assert status == "HARNESS_FAILED"
    assert reason == "CLEANUP_COMPENSATION_FAILED"


def test_coordinator_rejects_campaign_or_result_identity_mismatch() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    contract = _contract("experiment_candidate")
    with pytest.raises(MainlineContractError, match="mainline_campaign_identity_mismatch"):
        run_discovery_mainline(
            _inputs("experiment_candidate"),
            build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-OTHER"),
            build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
            experiment_runner=lambda *_: {"mainline_run": contract},
        )

    wrong_result = _contract("experiment_candidate", campaign_id="CMP-OTHER")
    with pytest.raises(MainlineContractError, match="mainline_result_authority_mismatch"):
        run_discovery_mainline(
            _inputs("experiment_candidate"),
            build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
            build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
            experiment_runner=lambda *_: {"mainline_run": wrong_result},
        )


def test_v12_establishes_identity_and_runtime_contract_before_coordinator() -> None:
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    source = inspect.getsource(run_v12_pipeline)
    identity_index = source.index("_require_mainline_identity(")
    normalization_index = source.index("_normalize_executable_api_document(")
    runtime_index = source.index("_runtime_contract(")
    coordinator_index = source.index("run_discovery_mainline(")

    assert identity_index < normalization_index < runtime_index < coordinator_index
    assert "_run_legacy_champion_domain(" not in source
