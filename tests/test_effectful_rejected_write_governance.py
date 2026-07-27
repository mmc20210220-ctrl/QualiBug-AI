from __future__ import annotations

from pathlib import Path

import ai_test_asset_center.sandbox_write_executor as governed
from ai_test_asset_center.experiment_executor import (
    _governed_write_changed_state,
)


def _receipt(
    *,
    status: int,
    before: object,
    after: object,
    accepted: bool = False,
) -> dict:
    return {
        "status": "executed" if accepted else "failed",
        "reason": "accepted" if accepted else "control_write_not_accepted",
        "accepted": accepted,
        "method": "POST",
        "path": "/resources",
        "before": {"status": 200, "body": before},
        "write": {"status": status, "body": {"error": "invalid"}},
        "after": {"status": 200, "body": after},
        "before_ref": "before",
        "after_ref": "after",
        "audit_path": "/tmp/original-audit.jsonl",
        "audit_record": {
            "operation_accepted": accepted,
            "http_status": status,
        },
        "http_attempt_count": 3,
        "production_http_requests": 0,
    }


def _execute(monkeypatch, tmp_path: Path, receipt: dict) -> dict:
    monkeypatch.setattr(
        governed._base,
        "execute_governed_control_write",
        lambda **kwargs: receipt,
    )
    appended: list[dict] = []

    def append_audit(root: Path, project: str, record: dict) -> Path:
        appended.append(dict(record))
        return tmp_path / "sandbox_write_audit.jsonl"

    monkeypatch.setattr(governed._base, "_append_audit", append_audit)
    result = governed.execute_governed_control_write(
        root=tmp_path,
        project="benchmark",
        base_url="http://localhost:8080",
        runtime_contract={},
        campaign_id="campaign-1",
        operation_phase="experiment_treatment",
        actor_identity="buyer",
        actor_token="token",
        method="POST",
        path="/resources",
        body={"name": "invalid"},
        observation_path="/resources",
    )
    result["_appended_records"] = appended
    return result


def test_rejected_transport_with_business_side_effect_requires_cleanup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    result = _execute(
        monkeypatch,
        tmp_path,
        _receipt(
            status=422,
            before={"data": []},
            after={"data": [{"id": "dirty-1", "name": "invalid"}]},
        ),
    )

    assert result["write"]["status"] == 422
    assert result["transport_accepted"] is False
    # A rejected write must never be laundered into an "accepted" one just
    # because a side effect was observed underneath it -- that would let a
    # technically-failed request satisfy the delivery gate's accepted-write
    # bookkeeping. The anomaly stays fail-visible via a distinct status and
    # audit trail instead of a false acceptance.
    assert result["effectively_accepted"] is False
    assert result["accepted"] is False
    assert result["indeterminate_side_effect_detected"] is True
    assert result["status"] == "rejected_with_indeterminate_side_effect"
    assert (
        result["reason"]
        == "rejected_transport_but_business_state_changed"
    )
    assert result["audit_record"]["cleanup_status"] == "required"
    assert result["audit_record"]["operation_accepted"] is False
    assert len(result["_appended_records"]) == 1
    # Because it is correctly not "accepted", this write is excluded from the
    # accepted-write cleanup path and instead falls into the dedicated
    # rejected-but-effectful fail-closed branch downstream.
    assert _governed_write_changed_state(result) is False


def test_rejected_transport_without_side_effect_stays_rejected(
    monkeypatch,
    tmp_path: Path,
) -> None:
    result = _execute(
        monkeypatch,
        tmp_path,
        _receipt(
            status=422,
            before={"data": []},
            after={"data": []},
        ),
    )

    assert result["transport_accepted"] is False
    assert result["effectively_accepted"] is False
    assert result["accepted"] is False
    assert result["_appended_records"] == []


def test_server_managed_timestamp_change_does_not_fake_a_side_effect(
    monkeypatch,
    tmp_path: Path,
) -> None:
    result = _execute(
        monkeypatch,
        tmp_path,
        _receipt(
            status=400,
            before={
                "data": [{
                    "id": "resource-1",
                    "quantity": 1,
                    "updatedAt": "2026-07-13T00:00:00Z",
                }]
            },
            after={
                "data": [{
                    "id": "resource-1",
                    "quantity": 1,
                    "updatedAt": "2026-07-13T00:00:01Z",
                }]
            },
        ),
    )

    assert result["accepted"] is False
    assert result["effectively_accepted"] is False
    assert result["_appended_records"] == []


def test_normal_2xx_acceptance_preserves_transport_semantics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    result = _execute(
        monkeypatch,
        tmp_path,
        _receipt(
            status=201,
            before={"data": []},
            after={"data": [{"id": "resource-1"}]},
            accepted=True,
        ),
    )

    assert result["transport_accepted"] is True
    assert result["effectively_accepted"] is True
    assert result["accepted"] is True
    assert "accepted_due_to_observed_effect" not in result
    assert result["_appended_records"] == []
