from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from ai_test_asset_center.display_ready_formatter import (
    _build_repro_steps_display,
    format_findings_display_ready,
)
from ai_test_asset_center.evidence_enricher_v3 import enrich_finding, load_enterprise_context
from ai_test_asset_center.har_bridge import enrich_finding_with_har, load_playwright_har


ROOT = Path(__file__).resolve().parents[1]


def _write_finding_without_request_body() -> dict:
    return {
        "title": "POST /api/resources returned HTTP 500",
        "description": "The observed write request returned an internal error.",
        "_api_method": "POST",
        "_api_path": "/api/resources",
        "har_evidence": {
            "method": "POST",
            "path": "/api/resources",
            "status_code": 500,
            "response_body": '{"error":"internal"}',
            "request_body_observed": False,
        },
    }


def test_enrichment_never_turns_missing_write_inputs_into_executed_steps(tmp_path: Path) -> None:
    enriched = enrich_finding(_write_finding_without_request_body(), {})
    serialized = json.dumps(enriched, ensure_ascii=False)

    assert enriched.get("reproduction_steps") in (None, [])
    assert enriched["reproduction_guidance"]["is_synthetic"] is True
    assert enriched["reproduction_guidance"]["request_body_status"] == "missing"
    assert enriched["reproduction_guidance"]["can_execute"] is False
    assert " -d " not in "\n".join(enriched["reproduction_guidance"]["steps"])
    assert "SKU-PHONE-001" not in serialized
    assert "buyer01@example.com" not in serialized
    assert "localhost:8080" not in serialized

    assert load_enterprise_context("missing-project", tmp_path) == {}


def test_malformed_enterprise_context_fails_fast(tmp_path: Path) -> None:
    registry = (
        tmp_path
        / "platform_workspace"
        / "broken-project"
        / "enterprise_pilot_runtime"
        / "connector_registry.json"
    )
    registry.parent.mkdir(parents=True)
    registry.write_text('{"connectors":', encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        load_enterprise_context("broken-project", tmp_path)


def test_har_bridge_preserves_only_observed_request_body_with_provenance(tmp_path: Path) -> None:
    har_path = tmp_path / "captured.har"
    har_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "startedDateTime": "2026-07-16T08:00:00Z",
                            "request": {
                                "method": "POST",
                                "url": "https://target.example/api/resources",
                                "postData": {
                                    "mimeType": "application/json",
                                    "text": '{"asset_id":"A-42","count":2,"password":"secret value"}',
                                },
                            },
                            "response": {
                                "status": 500,
                                "content": {"text": '{"error":"internal"}'},
                            },
                            "time": 12,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    entries = load_playwright_har(har_path)
    enriched = enrich_finding_with_har(_write_finding_without_request_body(), entries)
    evidence = enriched["har_evidence"]
    request_body = json.loads(evidence["request_body"])

    assert evidence["request_body_observed"] is True
    assert evidence["request_body_source"] == "har.request.postData.text"
    assert request_body == {"asset_id": "A-42", "count": 2, "password": "<REDACTED>"}


def test_malformed_har_fails_fast(tmp_path: Path) -> None:
    har_path = tmp_path / "broken.har"
    har_path.write_text('{"log":', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid HAR JSON"):
        load_playwright_har(har_path)


def test_display_keeps_generated_steps_synthetic_and_blocks_placeholder_write_curl() -> None:
    finding = {
        **_write_finding_without_request_body(),
        "reproduction_steps": ["Generated guidance"],
        "reproduction_steps_provenance": {
            "status": "generated_guidance",
            "source": "evidence_enricher_v3",
            "is_synthetic": True,
        },
    }

    reproduction = _build_repro_steps_display(finding, {})

    assert reproduction["is_synthetic"] is True
    assert reproduction["curl_command"] == ""
    assert reproduction["request_body_status"] == "missing"


def test_display_does_not_invent_business_impact_for_unenriched_input() -> None:
    findings, _metrics = format_findings_display_ready([
        {
            "title": "GET /api/resources returned HTTP 500",
            "_api_method": "GET",
            "_api_path": "/api/resources",
        }
    ])

    impact = findings[0]["business_impact"]
    assert impact == {
        "summary": "缺少企业资料支持的业务影响结论。",
        "urgency": "待评估",
        "module": "未绑定业务域",
    }


def test_service_core_uses_the_canonical_customer_delivery_gate() -> None:
    from ai_test_asset_center import private_pilot_service
    from ai_test_asset_center.customer_delivery_gate import split_customer_delivery_tracks

    assert private_pilot_service._partition_delivery_tracks is split_customer_delivery_tracks

    candidate = {
        "title": "A runtime response without a traceable evidence identity",
        "bug_status": "reproduced",
        "gate_passed": True,
        "repro_method": "GET",
        "repro_path": "/api/resources",
        "reproduction": {
            "method": "GET",
            "path": "/api/resources",
            "is_synthetic": False,
            "har_evidence": {"status_code": 500},
        },
        "raw_evidence": {"has_real_evidence": True},
    }

    defects, clues = private_pilot_service._partition_delivery_tracks([candidate])

    assert defects == []
    assert clues[0]["customer_delivery_gate_reasons"]


def test_packaging_has_one_server_entrypoint() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setup_source = (ROOT / "setup.py").read_text(encoding="utf-8")
    expected = "ai_test_asset_center.private_pilot_entrypoint:run_server"

    assert pyproject["project"]["scripts"]["qualibug-server"] == expected
    assert f"qualibug-server={expected}" in setup_source
    legacy_command = "python -m ai_test_asset_center.private_pilot_service"
    for path in (
        ROOT / "deploy" / "README.md",
        ROOT / "frontend" / "README.md",
        ROOT / "docs" / "PHASE60_ENTERPRISE_PILOT_RUNTIME.md",
        ROOT / "docs" / "PHASE61_PRODUCT_UI.md",
        ROOT / "docs" / "commercial_handoff" / "QUALIBUG_FULL_PACKAGE_README.md",
        ROOT / "ai_test_asset_center" / "loop_watchdog.py",
    ):
        assert legacy_command not in path.read_text(encoding="utf-8")
