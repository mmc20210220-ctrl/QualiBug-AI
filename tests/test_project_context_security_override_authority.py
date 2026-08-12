from __future__ import annotations

from ai_test_asset_center.project_context_compiler import ProjectContextCompiler


def _spec() -> dict:
    return {
        "openapi": "3.0.0",
        "info": {"title": "security override", "version": "1"},
        "security": [{"bearerAuth": []}],
        "paths": {
            "/callback": {
                "post": {
                    "security": [],
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/me": {
                "get": {
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/optional": {
                "get": {
                    "security": [{"bearerAuth": []}, {}],
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
    }


def _by_path(rows):
    return {row.path: row for row in rows}


def test_explicit_empty_security_overrides_root_authentication() -> None:
    rows = _by_path(ProjectContextCompiler()._extract_api_capabilities(_spec(), []))
    assert rows["/callback"].security == []
    receipt = next(item for item in rows["/callback"].evidence if item.get("kind") == "openapi_security_provenance")
    assert receipt["authority"] == "OPENAPI_OPERATION_SECURITY"
    assert receipt["effective_mode"] == "anonymous"
    assert receipt["operation_declaration_present"] is True


def test_missing_operation_security_inherits_root_security() -> None:
    rows = _by_path(ProjectContextCompiler()._extract_api_capabilities(_spec(), []))
    assert rows["/me"].security == [{"bearerAuth": []}]
    receipt = next(item for item in rows["/me"].evidence if item.get("kind") == "openapi_security_provenance")
    assert receipt["authority"] == "OPENAPI_DOCUMENT_SECURITY_INHERITED"
    assert receipt["effective_mode"] == "authenticated"
    assert receipt["inherited_from_document"] is True


def test_empty_security_requirement_object_allows_anonymous_alternative() -> None:
    rows = _by_path(ProjectContextCompiler()._extract_api_capabilities(_spec(), []))
    assert rows["/optional"].security == [{"bearerAuth": []}, {}]
    receipt = next(item for item in rows["/optional"].evidence if item.get("kind") == "openapi_security_provenance")
    assert receipt["effective_mode"] == "anonymous"
    assert receipt["effective_anonymous"] is True
