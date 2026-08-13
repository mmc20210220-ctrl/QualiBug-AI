from __future__ import annotations

import json

from ai_test_asset_center.input_grounded_candidate_compiler import parse_openapi_endpoints


def _write(tmp_path, spec: dict) -> None:
    (tmp_path / "openapi.json").write_text(json.dumps(spec), encoding="utf-8")


def _by_path(rows):
    return {row.path: row for row in rows}


def _spec() -> dict:
    return {
        "openapi": "3.0.0",
        "security": [{"bearerAuth": []}],
        "paths": {
            "/inherited": {"get": {"responses": {"200": {"description": "ok"}}}},
            "/anonymous": {"post": {"security": [], "responses": {"200": {"description": "ok"}}}},
            "/optional": {"get": {"security": [{"bearerAuth": []}, {}], "responses": {"200": {"description": "ok"}}}},
        },
    }


def test_root_security_is_inherited_as_auth_check(tmp_path) -> None:
    _write(tmp_path, _spec())
    rows = _by_path(parse_openapi_endpoints(tmp_path))
    assert "auth" in rows["/inherited"].checks


def test_explicit_anonymous_override_removes_auth_check(tmp_path) -> None:
    _write(tmp_path, _spec())
    rows = _by_path(parse_openapi_endpoints(tmp_path))
    assert "auth" not in rows["/anonymous"].checks


def test_optional_auth_allows_anonymous_candidate_check_surface(tmp_path) -> None:
    _write(tmp_path, _spec())
    rows = _by_path(parse_openapi_endpoints(tmp_path))
    assert "auth" not in rows["/optional"].checks


def test_missing_security_is_unknown_and_not_guessed_as_auth(tmp_path) -> None:
    spec = {"openapi": "3.0.0", "paths": {"/unknown": {"get": {"responses": {"200": {"description": "ok"}}}}}}
    _write(tmp_path, spec)
    rows = _by_path(parse_openapi_endpoints(tmp_path))
    assert "auth" not in rows["/unknown"].checks
