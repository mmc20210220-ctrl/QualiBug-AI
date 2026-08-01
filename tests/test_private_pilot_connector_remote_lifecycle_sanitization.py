from __future__ import annotations

import json

from ai_test_asset_center.private_pilot_connector_handlers import (
    _sanitize_sync_response,
)


def test_immediate_sync_response_uses_safe_lifecycle_projection() -> None:
    response = _sanitize_sync_response(
        {
            "status": "COMPLETE",
            "next_cursor": "SECRET-CURSOR",
            "fencing_token": "SECRET-FENCE",
            "remote_lifecycle": {
                "status": "COMPLETE",
                "authoritative_snapshot_complete": True,
                "present_count": 8,
                "absent_count": 1,
                "unconfirmed_missing_count": 1,
                "retirement_eligible_count": 0,
                "retired_count": 0,
                "renamed_resource_count": 1,
                "moved_resource_count": 0,
                "reappeared_resource_count": 0,
                "retire_after_complete_snapshots": 2,
                "requested_deletion_policy": "RETAIN",
                "effective_deletion_policy": "RETAIN",
                "sync_receipt_persisted": True,
                "evidence_persistence_status": "COMPLETE",
                "retired_source_occurrences": [
                    {
                        "remote_resource_id": "SECRET-REMOTE-ID",
                        "source_ref": "connector://secret/source",
                        "display_title": "Customer confidential title",
                    }
                ],
                "errors": [
                    {
                        "source_ref": "connector://secret/error",
                        "detail": "Customer confidential diagnostic",
                    }
                ],
            },
        }
    )

    encoded = json.dumps(response, ensure_ascii=False)
    assert response["remote_lifecycle"]["status"] == "COMPLETE"
    assert response["remote_lifecycle"]["absent_count"] == 1
    assert response["remote_lifecycle"]["remote_resource_identities_returned"] is False
    assert response["remote_lifecycle"]["source_refs_returned"] is False
    assert response["remote_lifecycle_remote_resource_identities_returned"] is False
    assert response["remote_lifecycle_source_refs_returned"] is False
    assert response["next_cursor_returned_to_client"] is False
    assert response["fencing_token_returned_to_client"] is False
    assert "SECRET-CURSOR" not in encoded
    assert "SECRET-FENCE" not in encoded
    assert "SECRET-REMOTE-ID" not in encoded
    assert "connector://secret" not in encoded
    assert "Customer confidential" not in encoded
    assert "retired_source_occurrences" not in encoded
