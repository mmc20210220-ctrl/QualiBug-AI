from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.private_pilot_connector_handlers import (
    _error_status,
    _public_connector_instance,
    _sanitize_sync_response,
)


def test_connector_inventory_projection_hides_fencing_and_checkpoint_internals():
    projected = _public_connector_instance(
        {
            "connector_instance_id": "feishu-main",
            "active_sync_epoch_id": "sync_live",
            "fencing_generation": 9,
            "last_fencing_token_issued_at_utc": "2026-07-31T00:00:00Z",
            "last_fencing_token_issued_by": {
                "name": "system",
                "role": "knowledge_admin",
            },
            "fencing_takeover_pending": True,
            "last_committed_cursor_fingerprint": "secret-internal-fingerprint",
        }
    )
    assert projected["connector_instance_id"] == "feishu-main"
    assert projected["active_sync_epoch_id"] == "sync_live"
    assert projected["fencing_token_returned_to_client"] is False
    assert "fencing_generation" not in projected
    assert "last_fencing_token_issued_at_utc" not in projected
    assert "last_fencing_token_issued_by" not in projected
    assert "fencing_takeover_pending" not in projected
    assert "last_committed_cursor_fingerprint" not in projected


def test_sync_response_never_returns_raw_fencing_or_cursor_values():
    result = _sanitize_sync_response(
        {
            "status": "COMPLETE",
            "next_cursor": "raw-cursor",
            "fencing_token": 12,
            "previous_fencing_token": 11,
            "takeover_attempt_id": "fence_internal",
            "success_count": 3,
        }
    )
    assert result["status"] == "COMPLETE"
    assert result["success_count"] == 3
    assert result["next_cursor_returned_to_client"] is False
    assert result["fencing_token_returned_to_client"] is False
    assert "next_cursor" not in result
    assert "fencing_token" not in result
    assert "previous_fencing_token" not in result
    assert "takeover_attempt_id" not in result


def test_fence_conflicts_are_http_409():
    assert _error_status(RuntimeError("connector_sync_fence_revoked")) == 409
    assert _error_status(RuntimeError("connector_sync_fence_transaction_busy")) == 409
    assert _error_status(RuntimeError("connector_sync_already_running_owner_active")) == 409


def test_public_handler_does_not_expose_private_fencing_fields_directly():
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center"
        / "private_pilot_connector_handlers.py"
    ).read_text(encoding="utf-8")
    assert '"fencing_tokens_returned_to_frontend": False' in source
    assert '"checkpoint_fingerprints_returned_to_frontend": False' in source
    assert "_public_connector_instance(raw)" in source
    assert "_sanitize_sync_response(run)" in source


def test_http_surface_has_no_unfenced_abort_path():
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center"
        / "private_pilot_connector_handlers.py"
    ).read_text(encoding="utf-8")
    # No abort capability may exist anywhere in the connector HTTP surface:
    # not as a route segment, not as an action, not as a handler.
    assert "abort_connector_sync_run" not in source
    assert '"abort"' not in source
    assert "action == 'abort'" not in source
    assert 'action == "abort"' not in source
    # The connector action fence must keep dispatching the managed sync
    # authority; sync stays first-class inside the fenced action set.
    assert "tail[1] in {" in source
    assert '"sync",' in source
    assert 'if action == "sync"' in source
