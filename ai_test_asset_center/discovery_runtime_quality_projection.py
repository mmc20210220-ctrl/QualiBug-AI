"""Public execution wrapper for formal evidence and loss-funnel projection."""
from __future__ import annotations

from typing import Any

from .discovery_event_loss_projection import attach_formal_event_loss_funnel
from .discovery_funnel import build_funnel_report
from .discovery_loss_funnel import build_discovery_loss_funnel
from .discovery_performance_loss_projection import (
    attach_formal_performance_loss_funnel,
)
from .discovery_ui_loss_projection import attach_formal_ui_loss_funnel
from .formal_event_binding_evidence_projection import (
    project_formal_event_binding_evidence,
)
from .formal_evidence_projection import (
    run_experiment_candidate as _run_with_formal_evidence,
)


def project_discovery_quality(result: dict[str, Any]) -> dict[str, Any]:
    """Attach durable identity evidence and receipt-backed conversion measurement."""

    if not isinstance(result, dict):
        raise TypeError("discovery_result_not_object")
    projected = project_formal_event_binding_evidence(dict(result))
    generic_funnel = build_discovery_loss_funnel(projected)
    with_ui = attach_formal_ui_loss_funnel(projected, generic_funnel)
    with_event = attach_formal_event_loss_funnel(projected, with_ui)
    projected["discovery_loss_funnel"] = (
        attach_formal_performance_loss_funnel(projected, with_event)
    )
    raw_ledger = projected.get("obligation_attempt_ledger")
    if (
        isinstance(raw_ledger, dict)
        and raw_ledger.get("schema_version")
        == "qualibug.obligation-attempt-ledger.v1"
    ):
        projected["discovery_funnel_report"] = build_funnel_report(
            projected,
            funnel=(
                projected.get("discovery_funnel")
                if isinstance(projected.get("discovery_funnel"), dict)
                else None
            ),
        )
    elif isinstance(raw_ledger, dict):
        # This projection is also used by legacy diagnostic fixtures that do
        # not carry the authoritative ledger schema. Keep the missing authority
        # explicit instead of manufacturing a funnel from partial rows.
        projected["discovery_funnel_report"] = {
            "schema_version": "qualibug.discovery-funnel-report.v1",
            "report_status": "NOT_AVAILABLE",
            "reason": "obligation_attempt_ledger_schema_invalid",
            "quality": {
                "status": "NOT_MEASURED",
                "recall": "NOT_MEASURED",
                "precision": "NOT_MEASURED",
            },
            "receipt_authority": "qualibug.obligation-attempt-ledger.v1",
        }
    return projected


def run_experiment_candidate(inputs: Any, campaign_handle: Any, plan: Any) -> dict[str, Any]:
    """Execute, project formal evidence, then build the honest loss funnel."""

    result = _run_with_formal_evidence(inputs, campaign_handle, plan)
    return project_discovery_quality(result)


__all__ = [
    "project_discovery_quality",
    "run_experiment_candidate",
]
