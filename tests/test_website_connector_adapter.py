from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import ai_test_asset_center.website_connector_adapter as website
from ai_test_asset_center.connector_sync_authority import (
    connector_snapshot_observation_index,
    register_connector_instance,
)
from ai_test_asset_center.enterprise_knowledge_center import (
    list_enterprise_knowledge_sources,
)


BASE = "https://example.com"
PROJECT = "website-project"
CONNECTOR = "website-main"
ACTOR = {"name": "qa-owner", "role": "qa_lead"}


def _scope(**overrides: object) -> str:
    value: dict[str, object] = {
        "seed_urls": [f"{BASE}/docs/start"],
        "max_depth": 1,
        "max_pages": 20,
    }
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _pages(
    *,
    robots: bytes = b"User-agent: *\nAllow: /\n",
    start: bytes | None = None,
) -> dict[str, website.WebsiteHttpResponse]:
    return {
        "/robots.txt": website.WebsiteHttpResponse(
            200,
            {"Content-Type": "text/plain"},
            robots,
            f"{BASE}/robots.txt",
        ),
        "/docs/start": website.WebsiteHttpResponse(
            200,
            {"Content-Type": "text/html", "ETag": '"start-v1"'},
            start
            or b'<html><head><title>Start</title></head><body><nav><a href="/docs/next">Next</a></nav><form action="/write" method="post"></form></body></html>',
            f"{BASE}/docs/start",
        ),
        "/docs/next": website.WebsiteHttpResponse(
            200,
            {"Content-Type": "text/html", "ETag": '"next-v1"'},
            b"<html><head><title>Next</title></head><body>Next</body></html>",
            f"{BASE}/docs/next",
        ),
        "/manual.pdf": website.WebsiteHttpResponse(
            200,
            {"Content-Type": "application/pdf", "ETag": '"pdf-v1"'},
            b"%PDF-website-fixture",
            f"{BASE}/manual.pdf",
        ),
    }


def _transport_for(
    responses: dict[str, website.WebsiteHttpResponse],
    calls: list[tuple[str, str, dict[str, str], bytes | None]],
):
    def transport(method, url, headers, body, timeout, max_bytes):
        calls.append((method, url, dict(headers), body))
        path = urlsplit(url).path
        response = responses.get(
            path,
            website.WebsiteHttpResponse(404, {}, b"", url),
        )
        if path in {"/docs/start", "/docs/next", "/manual.pdf"}:
            etag = response.headers.get("ETag")
            if etag and headers.get("If-None-Match") == etag:
                return website.WebsiteHttpResponse(
                    304,
                    dict(response.headers),
                    b"",
                    url,
                )
        return response

    return transport


@pytest.fixture
def bypass_test_dns(monkeypatch):
    """Keep transport fixtures offline while preserving the adapter URL-policy call boundary."""
    monkeypatch.setattr(website, "validate_url", lambda url, **_: url)


def test_manifest_is_generic_read_only_and_exposes_website_scope() -> None:
    manifest = website.website_connector_manifest()

    assert manifest.connector_type == "website"
    assert manifest.category == "website"
    assert manifest.read_only is True
    assert manifest.supported_resource_types == ("html_page", "attachment")
    assert set(manifest.auth_modes) == {"anonymous", "cookie_session"}
    assert manifest.scope_schema["required"] == ["seed_urls"]
    assert manifest.credential_fields[0].secret is True


def test_discovery_preserves_title_parent_relationship_and_never_submits_form(
    bypass_test_dns,
) -> None:
    responses = _pages()
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    result = website.WebsiteConnectorAdapter().discover(
        {
            "resource_scope": _scope(),
            "transport": _transport_for(responses, calls),
            "sleeper": lambda _: None,
        }
    )

    assert result["complete"] is True
    assert [row["display_title"] for row in result["descriptors"]] == ["Next", "Start"]
    start = next(row for row in result["descriptors"] if row["display_title"] == "Start")
    next_page = next(row for row in result["descriptors"] if row["display_title"] == "Next")
    assert next_page["parent_remote_id"] == start["remote_resource_id"]
    relationships = json.loads(start["source_relationships_json"])
    assert relationships[0]["target_url"] == f"{BASE}/docs/next"
    assert all(method == "GET" and body is None for method, _, _, body in calls)
    assert all("Cookie" not in headers for _, _, headers, _ in calls)


def test_ssrf_block_stops_seed_before_transport(monkeypatch) -> None:
    def reject(url: str, **_: object):
        raise website.SsrfBlockedError(url)

    monkeypatch.setattr(website, "validate_url", reject)
    calls: list[str] = []

    result = website.WebsiteConnectorAdapter().discover(
        {
            "resource_scope": _scope(seed_urls=["http://127.0.0.1:8088/"]),
            "transport": lambda method, url, headers, body, timeout, max_bytes: calls.append(url),
        }
    )

    assert result["descriptors"] == []
    assert calls == []
    assert any(
        row["reason_code"] == "WEBSITE_SEED_OUT_OF_SCOPE"
        for row in result["coverage"]["observations"]
    )


def test_robots_disallow_is_a_visible_gap_and_does_not_fetch_disallowed_page(
    bypass_test_dns,
) -> None:
    responses = _pages(robots=b"User-agent: *\nDisallow: /docs/next\n")
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    result = website.WebsiteConnectorAdapter().discover(
        {
            "resource_scope": _scope(),
            "transport": _transport_for(responses, calls),
            "sleeper": lambda _: None,
        }
    )

    assert [row["canonical_url"] for row in result["descriptors"]] == [
        f"{BASE}/docs/start"
    ]
    assert any(
        row["reason_code"] == "WEBSITE_ROBOTS_DISALLOWED"
        and row["remote_resource_id"] == f"{BASE}/docs/next"
        for row in result["coverage"]["observations"]
    )
    assert not any(url.endswith("/docs/next") for _, url, _, _ in calls)


def test_canonical_url_deduplicates_aliases_without_title_based_merging(
    bypass_test_dns,
) -> None:
    responses = _pages(
        start=b'<html><head><title>Canonical page</title><link rel="canonical" href="/docs/canonical"></head><body><a href="/docs/canonical">same page</a></body></html>'
    )
    responses["/docs/canonical"] = website.WebsiteHttpResponse(
        200,
        {"Content-Type": "text/html"},
        b'<html><head><title>Canonical page</title><link rel="canonical" href="/docs/canonical"></head><body>canonical</body></html>',
        f"{BASE}/docs/canonical",
    )
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    result = website.WebsiteConnectorAdapter().discover(
        {
            "resource_scope": _scope(),
            "transport": _transport_for(responses, calls),
            "sleeper": lambda _: None,
        }
    )

    assert len(result["descriptors"]) == 1
    assert result["descriptors"][0]["remote_resource_id"] == f"{BASE}/docs/canonical"
    assert json.loads(result["descriptors"][0]["aliases_json"]) == [
        f"{BASE}/docs/canonical",
        f"{BASE}/docs/start",
    ]


def test_attachment_is_discovered_and_materialized_through_same_adapter(
    bypass_test_dns,
) -> None:
    responses = _pages(
        start=b'<html><head><title>Docs</title></head><body><a href="/manual.pdf">Manual</a></body></html>'
    )
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    adapter = website.WebsiteConnectorAdapter()
    result = adapter.discover(
        {
            "resource_scope": _scope(),
            "transport": _transport_for(responses, calls),
            "sleeper": lambda _: None,
        }
    )
    attachment = next(row for row in result["descriptors"] if row["obj_type"] == "attachment")

    capability = adapter.classify_resource(attachment)
    materialized = adapter.materialize(
        {
            "resource_scope": _scope(),
            "transport": _transport_for(responses, calls),
            "sleeper": lambda _: None,
        },
        attachment,
    )

    assert capability.materializable is True
    assert materialized["content"] == b"%PDF-website-fixture"
    assert materialized["filename"] == "manual.pdf"
    assert materialized["source_type"] == "other_document"


def test_incremental_etag_304_reuses_both_page_snapshots_without_reingestion(
    bypass_test_dns,
    tmp_path: Path,
) -> None:
    register_connector_instance(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
        connector_instance_id=CONNECTOR,
        connector_type="website",
        resource_scope=_scope(),
    )
    responses = _pages()
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    transport = _transport_for(responses, calls)

    first = website.sync_website_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        transport=transport,
        sleeper=lambda _: None,
    )
    second = website.sync_website_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        previous_cursor=first["next_cursor"],
        transport=transport,
        sleeper=lambda _: None,
    )

    assert first["status"] == "COMPLETE"
    assert first["materialized_resource_count"] == 2
    assert second["status"] == "COMPLETE"
    assert second["materialized_resource_count"] == 0
    assert second["unchanged_resource_count"] == 2
    assert sum(
        1
        for _, url, headers, _ in calls
        if "/docs/" in url and headers.get("If-None-Match")
    ) == 2


def test_permission_loss_does_not_retire_previous_source_snapshot(
    bypass_test_dns,
    tmp_path: Path,
) -> None:
    register_connector_instance(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
        connector_instance_id=CONNECTOR,
        connector_type="website",
        resource_scope=_scope(),
    )
    responses = _pages()
    first = website.sync_website_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        transport=_transport_for(responses, []),
        sleeper=lambda _: None,
    )
    denied = dict(responses)
    denied["/robots.txt"] = website.WebsiteHttpResponse(
        403,
        {},
        b"",
        f"{BASE}/robots.txt",
    )
    second = website.sync_website_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        previous_cursor=first["next_cursor"],
        deletion_policy="RETIRE_MISSING",
        transport=_transport_for(denied, []),
        sleeper=lambda _: None,
    )

    assert second["status"] == "COMPLETE"
    assert second["snapshot_complete"] is False
    assert second["deletion_policy_requested"] == "RETIRE_MISSING"
    assert second["deletion_policy_effective"] == "RETAIN"
    assert second["retirement_skip_reason"] == "INCOMPLETE_SNAPSHOT_ACCESS_OR_SCOPE_GAP"
    assert any(
        row["reason_code"] == "WEBSITE_ROBOTS_ACCESS_DENIED"
        for row in second["coverage_observations"]
    )
    assert (
        list_enterprise_knowledge_sources(PROJECT, root=tmp_path)["summary"][
            "active_source_count"
        ]
        == 2
    )
    observation_index = connector_snapshot_observation_index(
        PROJECT,
        connector_instance_id=CONNECTOR,
        root=tmp_path,
    )
    assert len(observation_index) == 2
