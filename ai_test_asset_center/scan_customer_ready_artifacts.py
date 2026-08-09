"""Customer-ready static artifact projection for product scans.

Extracted from ``__main__``. Symbols are re-exported for compatibility.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .product_scan_mainline import _as_dict, _safe_project
import time


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist JSON only after unified recursive redaction + secret scan."""
    from .artifact_redactor import ArtifactSecretLeakError, write_json_redacted

    try:
        write_json_redacted(path, payload)
    except ArtifactSecretLeakError as exc:
        import sys as _sys

        print(
            f"[scan] FAILED_SAFE artifact secret scan blocked write to {path}: {exc}",
            file=_sys.stderr,
        )
        raise


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}

def _customer_ready_static_snapshot(project: str, root: Path) -> dict[str, Any]:
    try:
        from .private_pilot_service import PrivatePilotHandler
    except Exception:
        return {}
    try:
        handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
        handler.headers = {}
        envelope = handler._build_command_center(project, root)
    except Exception:
        return {}
    if not isinstance(envelope, dict):
        return {}
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    if not isinstance(data, dict):
        return {}
    defects = [dict(item) for item in data.get("defects", []) if isinstance(item, dict)]
    clues = [dict(item) for item in data.get("clues", []) if isinstance(item, dict)]
    snapshot = {
        "project": project,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "defects": defects,
        "clues": clues,
        "risks": defects,
        "value_metrics": dict(data.get("value_metrics") or {}) if isinstance(data.get("value_metrics"), dict) else {},
        "executive_summary": dict(data.get("executive_summary") or {}) if isinstance(data.get("executive_summary"), dict) else {},
        "scan_meta": dict(data.get("scan_meta") or {}) if isinstance(data.get("scan_meta"), dict) else {},
        "data_contract": dict(data.get("data_contract") or {}) if isinstance(data.get("data_contract"), dict) else {},
    }
    if isinstance(data.get("current_campaign_scope"), dict):
        snapshot["current_campaign_scope"] = dict(data.get("current_campaign_scope") or {})
    if isinstance(data.get("defect_grouped_summary"), dict):
        snapshot["defect_grouped_summary"] = dict(data.get("defect_grouped_summary") or {})
    if isinstance(data.get("defect_priority_summary"), dict):
        snapshot["defect_priority_summary"] = dict(data.get("defect_priority_summary") or {})
    if isinstance(data.get("defect_repro_summary"), dict):
        snapshot["defect_repro_summary"] = dict(data.get("defect_repro_summary") or {})
    if isinstance(data.get("defect_delivery_cards"), dict):
        snapshot["defect_delivery_cards"] = dict(data.get("defect_delivery_cards") or {})
    if isinstance(data.get("commercial_assets"), dict):
        snapshot["commercial_assets"] = dict(data.get("commercial_assets") or {})
    if isinstance(data.get("continuous_discovery_campaign"), dict):
        snapshot["continuous_discovery_campaign"] = dict(data.get("continuous_discovery_campaign") or {})
    return snapshot


def _persist_customer_ready_static_artifacts(project: str, root: Path, result: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    snapshot = _customer_ready_static_snapshot(project, root)
    if not snapshot:
        return {}
    project_key = _safe_project(project)
    defect_count = len(snapshot.get("defects") or [])
    clue_count = len(snapshot.get("clues") or [])

    scan_result_path = root / "platform_outputs" / project_key / "scan_result.json"
    from .scan_result_store import is_sharded_scan_result, update_scan_result_index

    if is_sharded_scan_result(scan_result_path):
        # 分片 store：只重写索引新增小键，分片文件不动 —— O(索引) 成本，
        # 不再整读 4GB 级产物回读-重写。
        update_scan_result_index(scan_result_path, {
            "customer_ready_snapshot": snapshot,
            "customer_ready_defect_count": defect_count,
            "customer_ready_clue_count": clue_count,
        })
    else:
        scan_payload = _read_json(scan_result_path) or (dict(result) if isinstance(result, dict) else {})
        scan_payload["customer_ready_snapshot"] = snapshot
        scan_payload["customer_ready_defect_count"] = defect_count
        scan_payload["customer_ready_clue_count"] = clue_count
        _write_json(scan_result_path, scan_payload)

    real_project_path = root / "platform_outputs" / project_key / "real_project" / "real_project_defect_data.json"
    real_project_payload = _read_json(real_project_path)
    if not isinstance(real_project_payload, dict):
        real_project_payload = {}

    customer_ready_family_shelf = {
        "project": project,
        "generated_at_utc": snapshot.get("generated_at_utc"),
        "defects": snapshot.get("defects", []) or (
            [dict(f, is_reproducible=True) for f in (result.get("findings") or [])
             if isinstance(f, dict) and f.get("customer_delivery_status") == "defect"]
            if isinstance(result, dict) else []
        ),
        "clues": snapshot.get("clues", []),
        "value_metrics": snapshot.get("value_metrics", {}),
        "executive_summary": snapshot.get("executive_summary", {}),
        "scan_meta": snapshot.get("scan_meta", {}),
        "data_contract": snapshot.get("data_contract", {}),
    }
    if isinstance(snapshot.get("current_campaign_scope"), dict):
        customer_ready_family_shelf["current_campaign_scope"] = dict(snapshot.get("current_campaign_scope") or {})
    if isinstance(snapshot.get("continuous_discovery_campaign"), dict):
        customer_ready_family_shelf["continuous_discovery_campaign"] = dict(snapshot.get("continuous_discovery_campaign") or {})
    if isinstance(snapshot.get("defect_grouped_summary"), dict):
        customer_ready_family_shelf["defect_grouped_summary"] = dict(snapshot.get("defect_grouped_summary") or {})
    if isinstance(snapshot.get("defect_priority_summary"), dict):
        customer_ready_family_shelf["defect_priority_summary"] = dict(snapshot.get("defect_priority_summary") or {})
    if isinstance(snapshot.get("defect_repro_summary"), dict):
        customer_ready_family_shelf["defect_repro_summary"] = dict(snapshot.get("defect_repro_summary") or {})
    if isinstance(snapshot.get("defect_delivery_cards"), dict):
        customer_ready_family_shelf["defect_delivery_cards"] = dict(snapshot.get("defect_delivery_cards") or {})
    if isinstance(snapshot.get("commercial_assets"), dict):
        customer_ready_family_shelf["commercial_assets"] = dict(snapshot.get("commercial_assets") or {})

    discovery_owned_markers = (
        "metrics",
        "summary",
        "probes",
        "risk_distribution",
        "issue_count",
        "validated_bug_count",
        "candidate_issue_count",
        "pending_finding_count",
        "network_requests",
    )
    preserve_discovery_top_level = any(
        key in real_project_payload and real_project_payload.get(key) not in (None, "", [], {})
        for key in discovery_owned_markers
    )

    real_project_payload["customer_ready_snapshot"] = snapshot
    real_project_payload["customer_ready_family_shelf"] = customer_ready_family_shelf
    real_project_payload["customer_ready_defect_count"] = defect_count
    real_project_payload["customer_ready_clue_count"] = clue_count
    real_project_payload["customer_ready_projection_basis"] = "command_center_snapshot"
    if isinstance(snapshot.get("commercial_assets"), dict):
        real_project_payload["customer_ready_commercial_assets"] = dict(snapshot.get("commercial_assets") or {})
    if isinstance(snapshot.get("current_campaign_scope"), dict):
        real_project_payload["customer_ready_current_campaign_scope"] = dict(snapshot.get("current_campaign_scope") or {})
    if isinstance(snapshot.get("continuous_discovery_campaign"), dict):
        real_project_payload["customer_ready_continuous_discovery_campaign"] = dict(snapshot.get("continuous_discovery_campaign") or {})

    if not preserve_discovery_top_level:
        real_project_payload.update(customer_ready_family_shelf)
    _write_json(real_project_path, real_project_payload)

    if isinstance(result, dict):
        result["customer_ready_snapshot"] = snapshot
        result["customer_ready_defect_count"] = defect_count
        result["customer_ready_clue_count"] = clue_count
    return snapshot


