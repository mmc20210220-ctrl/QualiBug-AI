from __future__ import annotations

from pathlib import Path
from typing import Any

from .enterprise_test_data_receipts import issue_test_data_receipt
from .ui_execution_adapter import execute_ui_execution_requests


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _verify_created_data(candidate: dict[str, Any], created_data: dict[str, Any]) -> dict[str, Any]:
    current_url = _text(candidate.get("current_url"), 2000)
    object_id = _text(created_data.get("object_id"))
    data_scope_ref = _text(created_data.get("data_scope_ref"))
    object_url = _text(created_data.get("object_url"), 2000)
    signals: list[str] = []
    if object_id and current_url and object_id in current_url:
        signals.append("current_url_contains_object_id")
    if object_url and current_url and object_url == current_url:
        signals.append("current_url_matches_object_url")
    if data_scope_ref and (":" in data_scope_ref or "/" in data_scope_ref):
        signals.append("structured_data_scope_ref")
    if object_id and data_scope_ref and object_id in data_scope_ref:
        signals.append("data_scope_ref_contains_object_id")
    verified = bool(signals)
    return {
        "verified": verified,
        "signals": signals,
        "current_url": current_url,
        "object_url": object_url,
    }


def bootstrap_ui_test_data_receipts_for_campaign(
    *,
    project: str,
    root: Path,
    campaign: dict[str, Any] | None,
    contract: dict[str, Any] | None,
    runtime_contract: dict[str, Any] | None,
    requests: list[dict[str, Any]] | None,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = dict(contract or {})
    strategy = _text(current.get("strategy"))
    if strategy != "create_disposable":
        return {"status": "skipped", "reason": "strategy_not_create_disposable", "contract": current}
    if current.get("write_approved") is not True:
        return {"status": "skipped", "reason": "write_not_approved", "contract": current}
    if not isinstance(requests, list) or not requests:
        return {"status": "skipped", "reason": "ui_test_data_requests_missing", "contract": current}

    campaign = dict(campaign or {})
    runtime_contract = dict(runtime_contract or {})
    campaign_id = _text(campaign.get("campaign_id") or current.get("campaign_id"))
    scope_id = _text(campaign.get("scope_id") or current.get("scope_id"))
    environment_ref = _text(campaign.get("environment_ref") or current.get("environment_ref"))
    if not campaign_id or not scope_id or not environment_ref:
        return {"status": "skipped", "reason": "campaign_identity_incomplete", "contract": current}

    ui_execution = execute_ui_execution_requests(
        project,
        requests,
        runtime_contract,
        root=root,
        run_id=f"{campaign_id}_ui_bootstrap",
        execution_context=execution_context,
    )
    candidate = next(
        (
            item for item in _as_list(ui_execution.get("results"))
            if isinstance(item, dict)
            and str(item.get("status") or "") == "executed"
            and isinstance(item.get("created_data"), dict)
            and (
                _text(_as_dict(item.get("created_data")).get("data_scope_ref"))
                or (
                    _text(_as_dict(item.get("created_data")).get("object_type"))
                    and _text(_as_dict(item.get("created_data")).get("object_id"))
                )
            )
        ),
        {},
    )
    if not candidate:
        return {
            "status": "blocked",
            "reason": "ui_bootstrap_created_data_missing",
            "contract": current,
            "ui_execution": ui_execution,
        }

    created_data = _as_dict(candidate.get("created_data"))
    object_type = _text(created_data.get("object_type"))
    object_id = _text(created_data.get("object_id"))
    data_scope_ref = _text(created_data.get("data_scope_ref")) or _text(f"{object_type}:{object_id}" if object_type and object_id else "")
    if not data_scope_ref:
        return {
            "status": "blocked",
            "reason": "ui_bootstrap_data_scope_missing",
            "contract": current,
            "ui_execution": ui_execution,
        }
    verification = _verify_created_data(candidate, {**created_data, "data_scope_ref": data_scope_ref})
    if not verification["verified"]:
        return {
            "status": "blocked",
            "reason": "ui_bootstrap_verification_failed",
            "contract": current,
            "ui_execution": ui_execution,
            "created_data": created_data,
            "verification": verification,
        }

    actor = {"name": "QualiBug", "role": "sandbox_operator"}
    creation = issue_test_data_receipt(
        project,
        root=root,
        kind="creation",
        campaign_id=campaign_id,
        scope_id=scope_id,
        environment_ref=environment_ref,
        actor=actor,
        data_scope_ref=data_scope_ref,
        operation_ref=_text(created_data.get("operation_ref") or candidate.get("request_id") or "ui_bootstrap_create"),
    )
    cleanup = issue_test_data_receipt(
        project,
        root=root,
        kind="cleanup",
        campaign_id=campaign_id,
        scope_id=scope_id,
        environment_ref=environment_ref,
        actor=actor,
        operation_ref=_text(created_data.get("cleanup_operation_ref") or candidate.get("request_id") or "ui_bootstrap_cleanup"),
    )
    merged = dict(current)
    merged.update(
        {
            "strategy": "create_disposable",
            "write_approved": True,
            "campaign_id": campaign_id,
            "scope_id": scope_id,
            "environment_ref": environment_ref,
            "disposable_scope_ref": data_scope_ref,
            "creation_receipt_ref": creation["receipt_id"],
            "cleanup_receipt_ref": cleanup["receipt_id"],
            "ui_bootstrap_source": str(candidate.get("provider") or "page_agent"),
            "ui_bootstrap_request_id": _text(candidate.get("request_id")),
        }
    )
    return {
        "status": "ready",
        "reason": "ui_bootstrap_receipts_issued",
        "contract": merged,
        "ui_execution": ui_execution,
        "creation_receipt": creation,
        "cleanup_receipt": cleanup,
        "created_data": created_data,
        "verification": verification,
    }
