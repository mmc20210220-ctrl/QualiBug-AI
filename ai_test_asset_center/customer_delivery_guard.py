from __future__ import annotations

"""Machine-readable customer delivery guard.

The HTML report explains release status to humans.  This module produces the
same decision as a durable JSON contract so delivery packages, tracker payloads,
and external handoff automation cannot bypass the latest release gate or a
missing commercial handoff approval.
"""

import json
import time
from pathlib import Path
from typing import Any

from ai_test_asset_center.customer_safe_report import (
    as_dict,
    commercial_handoff_label,
    load_customer_commercial_assets,
    load_customer_release_gate,
    read_json_file,
    safe_bool,
)

GUARD_VERSION = "customer_delivery_guard.v1"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _report_from_disk(project: str, root: Path) -> dict[str, Any]:
    path = root / "platform_outputs" / project / "pipeline_reports" / "latest_pipeline_report.json"
    payload = read_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def _delivery_status(overall: str, safe_for_customer: bool) -> str:
    if overall == "fail":
        return "blocked_by_release_gate"
    if overall == "pending":
        return "hold_for_validation"
    if overall == "pass" and safe_for_customer:
        return "customer_deliverable"
    if overall == "pass":
        return "hold_for_commercial_handoff"
    return "blocked_missing_release_gate"


def _block_reasons(release_gate: dict[str, Any], commercial_assets: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    overall = str(release_gate.get("overall_status") or "")
    handoff = as_dict(commercial_assets.get("commercial_handoff"))
    if not release_gate:
        reasons.append("missing_release_gate")
    elif overall == "fail":
        reasons.append("release_gate_failed")
    elif overall == "pending":
        reasons.append("release_gate_pending")
    if not safe_bool(handoff.get("safe_for_customer")):
        reasons.append("commercial_handoff_not_safe")
    if not reasons:
        reasons.append("customer_delivery_allowed")
    return reasons


def build_customer_delivery_guard(project: str, root: Path, report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the durable delivery decision for the current customer report."""
    report_payload = report if isinstance(report, dict) else _report_from_disk(project, root)
    release_gate = load_customer_release_gate(project, root, report_payload)
    commercial_assets = load_customer_commercial_assets(project, root, report_payload, release_gate)
    handoff = as_dict(commercial_assets.get("commercial_handoff"))
    tracker = as_dict(commercial_assets.get("tracker_sync"))
    delivery_package = as_dict(commercial_assets.get("delivery_package"))
    overall = str(release_gate.get("overall_status") or "")
    safe_for_customer = safe_bool(handoff.get("safe_for_customer"))
    status = _delivery_status(overall, safe_for_customer)
    customer_deliverable = status == "customer_deliverable"
    if not customer_deliverable:
        handoff["safe_for_customer"] = False
        if status == "blocked_by_release_gate":
            handoff["acceptance_status"] = "blocked_by_release_gate"
            tracker["payload_status"] = "blocked_by_release_gate"
        elif status == "hold_for_validation":
            handoff["acceptance_status"] = "hold_for_validation"
            tracker["payload_status"] = "hold_for_validation"
        elif status == "hold_for_commercial_handoff":
            handoff["acceptance_status"] = str(handoff.get("acceptance_status") or "hold_for_commercial_handoff")
            tracker["payload_status"] = str(tracker.get("payload_status") or "hold_for_commercial_handoff")
        else:
            handoff["acceptance_status"] = "blocked_missing_release_gate"
            tracker["payload_status"] = "blocked_missing_release_gate"
        delivery_package["release_gate_blocked"] = True
        delivery_package["release_gate_block_reason"] = str(
            delivery_package.get("release_gate_block_reason")
            or release_gate.get("honesty_rule")
            or "Customer delivery is blocked until release gate passes and commercial_handoff.safe_for_customer is true."
        )
    delivery_package["release_verdict"] = overall or "missing_release_gate"
    delivery_package["customer_deliverable"] = customer_deliverable
    commercial_assets["commercial_handoff"] = handoff
    commercial_assets["tracker_sync"] = tracker
    commercial_assets["delivery_package"] = delivery_package
    return {
        "guard_version": GUARD_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_id": project,
        "status": status,
        "customer_deliverable": customer_deliverable,
        "release_gate_overall_status": overall or "missing",
        "release_recommendation": str(release_gate.get("release_recommendation") or "hold_for_validation"),
        "commercial_handoff_label": commercial_handoff_label(commercial_assets),
        "safe_for_customer": safe_bool(handoff.get("safe_for_customer")),
        "tracker_payload_status": str(tracker.get("payload_status") or ""),
        "delivery_package_release_verdict": str(delivery_package.get("release_verdict") or ""),
        "block_reasons": _block_reasons(release_gate, commercial_assets),
        "release_gate": release_gate,
        "commercial_assets": commercial_assets,
        "honesty_rule": "Customer delivery is allowed only when the latest release gate passes and commercial_handoff.safe_for_customer is explicitly true. A passed release gate alone is not a customer-delivery approval.",
    }


def persist_customer_delivery_guard(project: str, root: Path, report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist the customer delivery guard beside customer-facing artifacts."""
    payload = build_customer_delivery_guard(project, root, report)
    output_root = root / "platform_outputs" / project
    _write_json(output_root / "customer_delivery_guard.json", payload)
    _write_json(output_root / "pipeline_reports" / "customer_delivery_guard.json", payload)
    return payload
