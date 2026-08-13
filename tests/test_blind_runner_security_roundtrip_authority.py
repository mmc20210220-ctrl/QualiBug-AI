from __future__ import annotations

from ai_test_asset_center.blind_project_runner import _merge_openapi_with_knowledge_asset


def _root_bearer() -> dict:
    return {
        "openapi": "3.0.0",
        "security": [{"bearerAuth": []}],
        "paths": {
            "/callback": {
                "post": {
                    "security": [],
                    "summary": "token callback",
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/me": {
                "get": {
                    "summary": "authorization profile",
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
    }


def test_explicit_anonymous_source_cannot_be_overwritten_by_auth_keyword() -> None:
    merged = _merge_openapi_with_knowledge_asset(
        _root_bearer(),
        {"interfaces": [{"method": "POST", "path": "/callback", "summary": "token callback"}]},
    )
    assert merged["paths"]["/callback"]["post"]["security"] == []
    assert "x-qualibug-security-hint" not in merged["paths"]["/callback"]["post"]


def test_inherited_root_security_remains_inherited_not_operation_heuristic() -> None:
    merged = _merge_openapi_with_knowledge_asset(
        _root_bearer(),
        {"interfaces": [{"method": "GET", "path": "/me", "summary": "authorization profile"}]},
    )
    assert merged["security"] == [{"bearerAuth": []}]
    assert "security" not in merged["paths"]["/me"]["get"]


def test_unknown_markdown_auth_keyword_is_hint_not_execution_contract() -> None:
    merged = _merge_openapi_with_knowledge_asset(
        {},
        {"interfaces": [{"method": "POST", "path": "/login", "summary": "token 登录"}]},
    )
    operation = merged["paths"]["/login"]["post"]
    assert "security" not in operation
    assert operation["x-qualibug-security-hint"] == {
        "kind": "auth_keyword",
        "authority": "heuristic_only",
        "execution_authority": False,
    }


def test_asset_operation_security_provenance_restores_explicit_anonymous() -> None:
    merged = _merge_openapi_with_knowledge_asset(
        {},
        {
            "interfaces": [{
                "method": "POST",
                "path": "/callback",
                "summary": "token callback",
                "security": [],
                "security_provenance_valid": True,
                "security_provenance_authority": "OPENAPI_OPERATION_SECURITY",
                "security_declaration_scope": "operation",
                "security_effective_mode": "anonymous",
                "source_id": "api-a",
            }]
        },
    )
    assert merged["paths"]["/callback"]["post"]["security"] == []


def test_asset_security_conflict_never_selects_one_source_by_order() -> None:
    merged = _merge_openapi_with_knowledge_asset(
        {},
        {
            "interfaces": [
                {
                    "method": "POST",
                    "path": "/callback",
                    "summary": "token callback",
                    "security": [],
                    "security_provenance_valid": True,
                    "security_provenance_authority": "OPENAPI_OPERATION_SECURITY",
                    "security_declaration_scope": "operation",
                    "security_effective_mode": "anonymous",
                    "source_id": "a",
                },
                {
                    "method": "POST",
                    "path": "/callback",
                    "summary": "token callback",
                    "security": [{"bearerAuth": []}],
                    "security_provenance_valid": True,
                    "security_provenance_authority": "OPENAPI_OPERATION_SECURITY",
                    "security_declaration_scope": "operation",
                    "security_effective_mode": "authenticated",
                    "source_id": "b",
                },
            ]
        },
    )
    operation = merged["paths"]["/callback"]["post"]
    assert "security" not in operation
    assert operation["x-qualibug-security-provenance-conflict"] is True
