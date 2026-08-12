from __future__ import annotations

import json

from ai_test_asset_center.actor_exploration_runtime import build_executable_candidates
from ai_test_asset_center.enterprise_knowledge_center import _parsing
from ai_test_asset_center.openapi_security_authority import (
    openapi_operation_security_facts,
    operation_has_source_declared_anonymous_access,
    project_operation_security_provenance,
)
from ai_test_asset_center.universal_api_parser import build_api_operations_from_text


def _doc() -> dict:
    return {
        "openapi": "3.0.0",
        "info": {"title": "security provenance", "version": "1"},
        "security": [{"bearerAuth": []}],
        "paths": {
            "/callback": {"post": {"security": [], "responses": {"200": {"description": "ok"}}}},
            "/me": {"get": {"responses": {"200": {"description": "ok"}}}},
            "/optional": {"get": {"security": [{"bearerAuth": []}, {}], "responses": {"200": {"description": "ok"}}}},
        },
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
    }


def _by_path(rows: list[dict]) -> dict[str, dict]:
    return {row["path"]: row for row in rows}


def test_security_facts_distinguish_explicit_empty_from_inherited() -> None:
    doc = _doc()
    callback = openapi_operation_security_facts(doc, doc["paths"]["/callback"]["post"])
    assert callback["security_operation_declaration_present"] is True
    assert callback["security_operation_anonymous_override"] is True
    assert callback["security_effective_anonymous"] is True
    assert callback["security_declaration_scope"] == "operation"

    me = openapi_operation_security_facts(doc, doc["paths"]["/me"]["get"])
    assert me["security_operation_declaration_present"] is False
    assert me["security_inherited_from_document"] is True
    assert me["security_effective_anonymous"] is False
    assert me["security_effective_mode"] == "authenticated"

    optional = openapi_operation_security_facts(doc, doc["paths"]["/optional"]["get"])
    assert optional["security_effective_anonymous"] is True
    assert optional["security_effective_mode"] == "anonymous"


def test_missing_security_everywhere_is_unknown_not_anonymous() -> None:
    doc = {"openapi": "3.0.0", "paths": {"/x": {"get": {}}}}
    facts = openapi_operation_security_facts(doc, doc["paths"]["/x"]["get"])
    assert facts["security_effective_mode"] == "unknown"
    assert facts["security_effective_anonymous"] is False
    assert operation_has_source_declared_anonymous_access(facts) is False


def test_universal_parser_preserves_operation_security_provenance() -> None:
    rows = _by_path(build_api_operations_from_text(json.dumps(_doc())))
    assert rows["/callback"]["security_operation_declaration_present"] is True
    assert rows["/callback"]["security_effective_anonymous"] is True
    assert rows["/callback"]["security_source_pointer"].endswith("/post/security")
    assert rows["/me"]["security_inherited_from_document"] is True
    assert rows["/me"]["security_effective_mode"] == "authenticated"
    assert rows["/me"]["security_source_pointer"] == "/security"


def test_knowledge_openapi_parser_preserves_same_provenance() -> None:
    rows = _by_path(_parsing._openapi_operations(_doc(), "src-api"))
    assert rows["/callback"]["security_effective_anonymous"] is True
    assert rows["/callback"]["security_provenance_authority"] == "OPENAPI_OPERATION_SECURITY"
    assert rows["/me"]["security_effective_mode"] == "authenticated"
    assert rows["/me"]["security_provenance_authority"] == "OPENAPI_DOCUMENT_SECURITY_INHERITED"


def test_behavior_projection_requires_source_consensus() -> None:
    doc = _doc()
    source_rows = _parsing._openapi_operations(doc, "src-a")
    model = {"operations": [
        {"id": "op1", "method": "POST", "path": "/callback", "security": []},
        {"id": "op2", "method": "GET", "path": "/me", "security": []},
    ]}
    project_operation_security_provenance(model, api_operations=source_rows)
    assert model["operations"][0]["security_source_declared_anonymous"] is True
    assert model["operations"][0]["security_provenance_conflict"] is False
    assert model["operations"][1]["security_source_declared_anonymous"] is False
    assert operation_has_source_declared_anonymous_access(model["operations"][0]) is True
    assert operation_has_source_declared_anonymous_access(model["operations"][1]) is False

    conflicting = _parsing._openapi_operations(
        {"openapi": "3.0.0", "paths": {"/callback": {"post": {"security": [{"bearerAuth": []}]}}}},
        "src-b",
    )
    model2 = {"operations": [{"id": "op1", "method": "POST", "path": "/callback", "security": []}]}
    project_operation_security_provenance(model2, api_operations=[*source_rows, *conflicting])
    assert model2["operations"][0]["security_provenance_conflict"] is True
    assert model2["operations"][0]["security_source_declared_anonymous"] is False
    assert operation_has_source_declared_anonymous_access(model2["operations"][0]) is False


def test_actor_exploration_never_treats_normalized_empty_security_as_public() -> None:
    normalized_only = {"id": "op", "method": "POST", "path": "/callback", "security": []}
    assert build_executable_candidates({}, operation=normalized_only) == []

    source_declared = {**normalized_only, "security_source_declared_anonymous": True, "security_provenance_conflict": False}
    candidates = build_executable_candidates({}, operation=source_declared)
    assert [candidate.actor_id for candidate in candidates] == ["anonymous"]


def _accepted_residue_experiment() -> dict:
    return {
        "cleanup_plan": [{"action": "accepted_residue", "mode": "accepted_residue_no_cleanup", "residue": True}],
        "write_reversibility_proof": {
            "proof_status": "PROVEN",
            "proof_kind": "accepted_residue",
            "reversibility": "none",
            "cleanup_authority": {"kind": "accepted_residue"},
        },
    }


def test_accepted_residue_anonymous_exception_requires_provenance() -> None:
    from ai_test_asset_center.actor_exploration_execution import exploration_execution_policy

    normalized_only = {"method": "POST", "path": "/callback", "security": []}
    assert exploration_execution_policy(
        operation=normalized_only,
        experiment=_accepted_residue_experiment(),
        requested_max_attempts=2,
    ) == (False, 0, "accepted_residue_is_not_reversible")

    source_declared = {**normalized_only, "security_source_declared_anonymous": True, "security_provenance_conflict": False}
    assert exploration_execution_policy(
        operation=source_declared,
        experiment=_accepted_residue_experiment(),
        requested_max_attempts=2,
    ) == (True, 1, "anonymous_accepted_residue_write")


def test_behavior_ir_receives_security_provenance_from_universal_parser() -> None:
    from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset

    operations = build_api_operations_from_text(json.dumps(_doc()))
    model = build_behavior_ir_from_knowledge_asset(
        {"sources": [], "operations": [], "interfaces": [], "actors": [], "relations": [], "entities": []},
        api_operations=operations,
    )
    rows = {(row["method"], row["path"]): row for row in model["operations"]}
    callback = rows[("POST", "/callback")]
    me = rows[("GET", "/me")]
    assert callback["security_source_declared_anonymous"] is True
    assert callback["security_provenance_conflict"] is False
    assert me["security_source_declared_anonymous"] is False
    assert me["security_inherited_from_document"] is True
