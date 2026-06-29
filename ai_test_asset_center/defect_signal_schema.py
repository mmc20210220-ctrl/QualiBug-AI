from __future__ import annotations

"""Normalization helpers for full-spectrum defect signals."""

import hashlib
from typing import Any

from .defect_family_registry import resolve_defect_family


def build_signal_id(payload: dict[str, Any]) -> str:
    base = "|".join(
        [
            str(payload.get("defect_family") or ""),
            str(payload.get("risk_type") or ""),
            str(payload.get("method") or ""),
            str(payload.get("path") or ""),
            str(payload.get("title") or ""),
            str(payload.get("source") or ""),
        ]
    )
    return hashlib.md5(base.encode("utf-8")).hexdigest()[:12]


def normalize_defect_signal(
    payload: dict[str, Any] | None,
    *,
    signal_kind: str,
    default_source: str = "",
    default_status: str = "needs_human_review",
    default_confidence: float = 0.5,
) -> dict[str, Any]:
    source = dict(payload or {})
    family = resolve_defect_family(source)
    defect_family = str(source.get("defect_family") or family.get("family_id") or "scenario_flow")
    method = str(source.get("method") or ((source.get("request") or {}).get("method") if isinstance(source.get("request"), dict) else "") or "GET").upper()
    path = str(source.get("path") or ((source.get("request") or {}).get("url") if isinstance(source.get("request"), dict) else "") or "")
    evidence = source.get("evidence") if isinstance(source.get("evidence"), dict) else {}
    signal = {
        "signal_id": str(source.get("signal_id") or build_signal_id({**source, "defect_family": defect_family, "method": method, "path": path, "source": source.get("source") or default_source})),
        "signal_kind": signal_kind,
        "title": str(source.get("title") or family.get("display_name") or "未命名缺陷信号"),
        "defect_family": defect_family,
        "family_display_name": str(family.get("display_name") or defect_family),
        "risk_type": str(source.get("risk_type") or defect_family),
        "severity": str(source.get("severity") or "P2"),
        "confidence": float(source.get("confidence") or default_confidence),
        "status": str(source.get("status") or default_status),
        "source": str(source.get("source") or default_source),
        "method": method,
        "path": path,
        "route": str(source.get("route") or path),
        "expected": source.get("expected"),
        "actual": source.get("actual"),
        "description": str(source.get("description") or ""),
        "evidence": evidence,
        "required_evidence": list(source.get("required_evidence") or family.get("required_evidence") or []),
        "allowed_execution_modes": list(source.get("allowed_execution_modes") or family.get("allowed_execution_modes") or []),
        "reporting_bucket": str(source.get("reporting_bucket") or family.get("reporting_bucket") or ""),
        "probe_id": source.get("probe_id"),
        "issue_id": source.get("issue_id"),
    }
    return signal

