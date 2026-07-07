from __future__ import annotations

from ai_test_asset_center.display_ready_formatter import _build_raw_evidence
from ai_test_asset_center.phase103_enterprise_command_center import build_evidence_bundle
from ai_test_asset_center import private_pilot_service


def test_raw_evidence_preserves_db_snapshot_before_after_and_assertion() -> None:
    finding = {
        "title": "order write mutated db unexpectedly",
        "db_evidence": {
            "table": "orders",
            "before_db_snapshot": {"table": "orders", "row_count": 1},
            "after_db_snapshot": {"table": "orders", "row_count": 2},
            "db_assertion": "orders row count changed 1->2",
            "business_operation": "POST /api/v1/orders",
        },
        "evidence": {
            "status_code": 200,
            "payload_summary": {"ok": True},
        },
        "_api_method": "POST",
        "_api_path": "/api/v1/orders",
        "last_verified_at": "2026-07-07T15:00:00Z",
    }

    raw = _build_raw_evidence(finding, {})

    assert raw["db_snapshot"]["table"] == "orders"
    assert raw["db_snapshot"]["assertion"] == "orders row count changed 1->2"
    assert raw["db_snapshot"]["business_operation"] == "POST /api/v1/orders"
    assert raw["db_snapshot"]["before"]["row_count"] == 1
    assert raw["db_snapshot"]["after"]["row_count"] == 2
    assert raw["has_real_evidence"] is True


def test_evidence_bundle_carries_database_snapshot_block() -> None:
    risk = {"risk_id": "risk-1", "title": "db evidence risk", "risk_type": "data_integrity"}
    raw_evidence = {
        "request_summary": {"method": "POST", "path": "/api/v1/orders"},
        "response_summary": {"status_code": 200, "observed_issue": "write accepted"},
        "db_snapshot": {
            "table": "orders",
            "assertion": "orders row count changed 1->2",
            "before": {"row_count": 1},
            "after": {"row_count": 2},
        },
    }

    bundle = build_evidence_bundle(risk, raw_evidence)

    assert bundle["snapshots"]["database"]["table"] == "orders"
    assert bundle["snapshots"]["database"]["before"]["row_count"] == 1
    assert bundle["snapshots"]["database"]["after"]["row_count"] == 2


def test_private_pilot_debug_env_uses_configured_url(monkeypatch) -> None:
    monkeypatch.setenv("QUALIBUG_DEBUG_SERVER_URL", "https://debug.example.test/event")
    monkeypatch.setenv("QUALIBUG_DEBUG_SESSION_ID", "session-a")
    private_pilot_service._DBG_ENV_CACHE = None

    url, session_id = private_pilot_service._dbg_env()

    assert url == "https://debug.example.test/event"
    assert session_id == "session-a"
