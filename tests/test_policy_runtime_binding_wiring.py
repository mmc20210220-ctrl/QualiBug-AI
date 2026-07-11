from __future__ import annotations

import json

from ai_test_asset_center.auto_test_data_factory import build_source_grounded_request_body
from ai_test_asset_center.policy_registry import StrategyBundle
from ai_test_asset_center.policy_wiring import policy_strategy_override
from ai_test_asset_center.sandbox_write_executor import resolve_environment_kind
from ai_test_asset_center.semantic_scenario_generator import (
    ExecutableScenario,
    ScenarioStep,
    SemanticScenarioGenerator,
)
from ai_test_asset_center.supplementary_behavior_slices import generate_permission_slices
from ai_test_asset_center.test_data_receipt_bootstrap import bootstrap_test_data_receipts_for_campaign
from ai_test_asset_center.v12_pipeline import _execute_scenario


OPENAPI = """
openapi: 3.0.3
servers:
  - url: https://example.invalid/service/v3
components:
  schemas:
    Command:
      type: object
      required: [object_id]
      properties:
        object_id: {type: string}
        tenant_id: {type: string}
paths:
  /objects/{id}:
    post:
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Command'
      responses:
        '200': {description: OK}
"""


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return json.dumps({"ok": True}).encode("utf-8")

    @property
    def headers(self):
        return {}


def test_openapi_server_base_path_still_resolves_documented_request_schema() -> None:
    result = build_source_grounded_request_body(
        OPENAPI,
        "POST",
        "/service/v3/objects/{id}",
    )

    assert result["provenance"] == "documented_schema_generated"
    assert result["body"]["object_id"].startswith("qb_auto_")


def test_documented_schema_binding_candidate_changes_real_http_execution(monkeypatch) -> None:
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        assert timeout == 10
        return _Response()

    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.urllib.request.urlopen", fake_urlopen)
    scenario = ExecutableScenario(
        id="scenario-1",
        title="source-grounded write",
        steps=[ScenarioStep(
            order=1,
            action="write_object",
            api_method="POST",
            api_path="/objects/{id}",
            body_template={"object_id": "runtime-object-1", "tenant_id": "tenant-a"},
            body_provenance="documented_schema_generated",
            expected_status=200,
        )],
    )
    baseline = StrategyBundle()
    challenger = StrategyBundle()
    challenger.execution.runtime_binding_sources.append("documented_schema_generated_value")

    with policy_strategy_override(baseline):
        baseline_trace = _execute_scenario(scenario, "http://example.invalid", max_retries=0)
    assert baseline_trace["errors"] == ["missing_runtime_path_binding:id"]
    assert calls == []

    with policy_strategy_override(challenger):
        challenger_trace = _execute_scenario(scenario, "http://example.invalid", max_retries=0)
    assert challenger_trace["errors"] == []
    assert calls == ["http://example.invalid/objects/runtime-object-1"]
    assert challenger_trace["runtime_binding_summary"] == {
        "event_count": 1,
        "sources": ["documented_schema_generated_value"],
        "bound_path_params": ["id"],
    }


def test_precondition_resolution_attempts_changes_generated_resolver_steps(monkeypatch) -> None:
    # Isolate policy wiring from the current path's finite resolver catalog: an
    # attempt limit is an upper bound, so the fixture must expose at least five
    # distinct candidates to prove that the challenger value takes effect.
    monkeypatch.setattr(
        "ai_test_asset_center.semantic_scenario_generator.alternate_collection_paths",
        lambda _path: [
            "/api/objects",
            "/api/object",
            "/api/entities",
            "/api/entity",
            "/api/resources",
        ],
    )
    baseline = StrategyBundle()
    challenger = StrategyBundle()
    challenger.execution.precondition_resolution_attempts = 5
    resolver_catalog = "\n".join(
        f"### GET {path}"
        for path in ("/api/objects", "/api/object", "/api/entities", "/api/entity", "/api/resources")
    )

    with policy_strategy_override(baseline):
        baseline_steps, _ = SemanticScenarioGenerator._resolve_entity_steps(
            "/api/work-orders/{thing_id}/transition",
            actor="operator",
            api_doc=resolver_catalog,
        )
    with policy_strategy_override(challenger):
        challenger_steps, _ = SemanticScenarioGenerator._resolve_entity_steps(
            "/api/work-orders/{thing_id}/transition",
            actor="operator",
            api_doc=resolver_catalog,
        )

    assert len(baseline_steps) == 4
    assert len(challenger_steps) == 5


def test_environment_url_is_identity_not_a_safety_class(tmp_path) -> None:
    assert resolve_environment_kind(
        tmp_path,
        "missing-project",
        {"environment_ref": "http://127.0.0.1:8011"},
    ) == ""
    assert resolve_environment_kind(
        tmp_path,
        "missing-project",
        {"environment_ref": "http://127.0.0.1:8011", "environment_type": "sandbox"},
    ) == "sandbox"


def test_test_data_bootstrap_uses_explicit_environment_type_not_target_url(tmp_path) -> None:
    result = bootstrap_test_data_receipts_for_campaign(
        project="project-a",
        root=tmp_path,
        base_url="http://127.0.0.1:8011",
        api_doc_text="openapi: 3.0.3\npaths: {}\n",
        campaign={
            "campaign_id": "campaign-a",
            "scope_id": "scope-a",
            "environment_ref": "http://127.0.0.1:8011",
        },
        selected_slices=[],
        contract={"strategy": "create_disposable", "write_approved": True},
        environment_kind="sandbox",
    )

    assert result["reason"] == "bootstrap_probe_not_found"


def test_permission_generation_does_not_assume_every_non_admin_role_is_denied() -> None:
    endpoint = {"method": "POST", "path": "/api/records", "entity": "records"}
    actor = {"role": "normal_user", "email": "normal@example.test"}

    assert generate_permission_slices([endpoint], [actor]) == []
    assert generate_permission_slices(
        [endpoint],
        [actor],
        permission_matrix=[{
            "role": "normal_user",
            "resource": "/api/records",
            "actions": ["GET"],
        }],
    )


def test_permission_generation_skips_irreversible_identity_mutations() -> None:
    endpoint = {
        "method": "POST",
        "path": "/api/auth/password/reset",
        "entity": "auth",
        "action": "reset",
        "summary": "admin only password reset",
    }
    actor = {"role": "buyer", "email": "buyer@example.test", "password": "Test@123456"}

    assert generate_permission_slices(
        [endpoint],
        [actor],
        permission_matrix=[{
            "role": "admin",
            "resource": "/api/auth/password/reset",
            "actions": ["POST"],
        }],
    ) == []
