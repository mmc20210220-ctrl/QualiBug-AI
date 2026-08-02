from __future__ import annotations

import json

import pytest

import ai_test_asset_center.connector_source_preflight as preflight


def _response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "application/json",
    final_url: str = "https://source.example.test/entry",
) -> preflight.SourcePreflightHttpResponse:
    return preflight.SourcePreflightHttpResponse(
        status=status,
        headers={"Content-Type": content_type},
        body=body,
        final_url=final_url,
    )


def test_openapi_entrypoint_is_selected_from_structural_evidence(monkeypatch):
    monkeypatch.setattr(preflight, "validate_url", lambda url, **_kwargs: url)
    captured: dict[str, object] = {}

    def transport(url, headers, timeout, max_bytes):
        captured.update(
            {
                "url": url,
                "headers": dict(headers),
                "timeout": timeout,
                "max_bytes": max_bytes,
            }
        )
        return _response(b'{"openapi":"3.0.0","paths":{}}')

    result = preflight.preflight_source_entry(
        "https://source.example.test/entry",
        transport=transport,
    )

    assert result["status"] == "READY"
    assert result["recommended_connector_type"] == "openapi"
    assert result["candidates"][0]["connector_type"] == "openapi"
    assert "document_shape:openapi_document" in result["candidates"][0]["evidence"]
    assert captured["headers"]["User-Agent"].startswith("QualiBug-Connector-Source-Preflight/")
    assert "Authorization" not in captured["headers"]
    assert result["governance"]["request_method"] == "GET"
    assert result["governance"]["request_body_sent"] is False
    assert result["governance"]["source_content_returned"] is False
    serialized = json.dumps(result, ensure_ascii=False)
    assert '"paths"' not in serialized
    assert "3.0.0" not in serialized


def test_html_entrypoint_recommends_website_without_returning_body(monkeypatch):
    monkeypatch.setattr(preflight, "validate_url", lambda url, **_kwargs: url)
    result = preflight.preflight_source_entry(
        "https://source.example.test/docs",
        transport=lambda *_args: _response(
            b"<html><title>Private handbook</title></html>",
            content_type="text/html; charset=utf-8",
        ),
    )

    assert result["status"] == "READY"
    assert result["recommended_connector_type"] == "website"
    serialized = json.dumps(result, ensure_ascii=False)
    assert "Private handbook" not in serialized
    assert "<html>" not in serialized
    assert result["governance"]["response_body_persisted"] is False


def test_authorized_entrypoint_remains_explicitly_unresolved(monkeypatch):
    monkeypatch.setattr(preflight, "validate_url", lambda url, **_kwargs: url)
    result = preflight.preflight_source_entry(
        "https://source.example.test/private",
        transport=lambda *_args: _response(
            b"authorization required",
            status=401,
            content_type="text/plain",
        ),
    )

    assert result["status"] == "AUTHORIZATION_REQUIRED"
    assert result["recommended_connector_type"] == ""
    assert result["candidates"]
    assert all(row["match_status"] == "REVIEW_REQUIRED" for row in result["candidates"])


def test_non_success_remote_response_never_becomes_a_recommendation(monkeypatch):
    monkeypatch.setattr(preflight, "validate_url", lambda url, **_kwargs: url)
    result = preflight.preflight_source_entry(
        "https://source.example.test/missing",
        transport=lambda *_args: _response(
            b"not found",
            status=404,
            content_type="text/html",
        ),
    )

    assert result["status"] == "REMOTE_ERROR"
    assert result["recommended_connector_type"] == ""
    assert result["candidates"]
    assert all(row["match_status"] == "REVIEW_REQUIRED" for row in result["candidates"])


def test_secret_query_is_rejected_before_transport(monkeypatch):
    monkeypatch.setattr(preflight, "validate_url", lambda url, **_kwargs: url)
    called = False

    def transport(*_args):
        nonlocal called
        called = True
        return _response(b"{}")

    with pytest.raises(preflight.SourcePreflightError, match="credential_query_not_allowed"):
        preflight.preflight_source_entry(
            "https://source.example.test/docs?api_key=should-not-be-sent",
            transport=transport,
        )
    assert called is False
