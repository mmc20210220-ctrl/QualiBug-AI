from __future__ import annotations

"""Expose risk/invariant coverage matrix in the customer command center.

The scanner persists benchmark metrics after each run.  When seeded ground truth
is missing, those metrics still contain an honest ``coverage_matrix`` derived
from real findings/candidates.  This patch lifts that matrix into the command
center's top-level data contract so the frontend and customer API can render it
without treating it as benchmark recall.
"""

import json
from pathlib import Path
from typing import Any

PATCH_SOURCE = "ai_test_asset_center.private_pilot_coverage_matrix_patch"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_project(value: str) -> str:
    return str(value or "").replace("/", "_").strip() or "unscoped"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _project_from_payload(payload: dict[str, Any]) -> str:
    data = _as_dict(payload.get("data"))
    for value in (
        data.get("project_id"),
        data.get("project"),
        payload.get("project_id"),
        payload.get("project"),
    ):
        text = str(value or "").strip()
        if text:
            return _safe_project(text)
    return ""


def _coverage_summary(matrix: dict[str, Any]) -> dict[str, Any]:
    if not matrix:
        return {}
    families = matrix.get("families") if isinstance(matrix.get("families"), list) else []
    invariants = matrix.get("invariants") if isinstance(matrix.get("invariants"), list) else []
    gap_families = [row for row in families if isinstance(row, dict) and row.get("coverage_status") == "gap"]
    candidate_only = [row for row in families if isinstance(row, dict) and row.get("coverage_status") == "candidate_only"]
    confirmed = [row for row in families if isinstance(row, dict) and str(row.get("coverage_status") or "").startswith("confirmed")]
    return {
        "schema_version": str(matrix.get("schema_version") or ""),
        "ontology_family_count": int(matrix.get("ontology_family_count") or len(families) or 0),
        "ontology_invariant_count": int(matrix.get("ontology_invariant_count") or len(invariants) or 0),
        "covered_family_count": int(matrix.get("covered_family_count") or 0),
        "confirmed_family_count": int(matrix.get("confirmed_family_count") or len(confirmed) or 0),
        "family_coverage_rate": float(matrix.get("family_coverage_rate") or 0.0),
        "confirmed_family_rate": float(matrix.get("confirmed_family_rate") or 0.0),
        "candidate_only_family_count": len(candidate_only),
        "gap_family_count": len(gap_families),
        "unclassified_signal_count": int(matrix.get("unclassified_signal_count") or 0),
        "honesty_note": str(matrix.get("honesty_note") or "Coverage matrix is not bug recall unless benchmark ground truth is available."),
    }


def _evidence_classification(data: dict[str, Any]) -> dict[str, int]:
    defects = data.get("defects") if isinstance(data.get("defects"), list) else []
    clues = data.get("clues") if isinstance(data.get("clues"), list) else []
    risks = data.get("risks") if isinstance(data.get("risks"), list) else []
    return {
        "confirmed": len([item for item in defects if isinstance(item, dict)]),
        "candidate": len([item for item in risks if isinstance(item, dict)]) + len([item for item in clues if isinstance(item, dict)]),
        "clue": len([item for item in clues if isinstance(item, dict)]),
    }


def _load_benchmark_metrics(project: str, root: Path) -> dict[str, Any]:
    if not project:
        return {}
    return _read_json(root / "platform_outputs" / _safe_project(project) / "benchmark" / "benchmark_metrics.json")


def inject_coverage_matrix(payload: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if data is None:
        return payload

    project = _project_from_payload(payload)
    if not project:
        project = _project_from_payload({"data": data})
    resolved_root = Path(root or Path.cwd())

    scan_meta = _as_dict(data.get("scan_meta"))
    benchmark_metrics = _as_dict(scan_meta.get("benchmark_metrics"))
    if not benchmark_metrics:
        benchmark_metrics = _load_benchmark_metrics(project, resolved_root)
        if benchmark_metrics:
            scan_meta["benchmark_metrics"] = benchmark_metrics
            data["scan_meta"] = scan_meta

    matrix = _as_dict(data.get("coverage_matrix")) or _as_dict(benchmark_metrics.get("coverage_matrix"))
    if not matrix:
        return payload

    summary = _coverage_summary(matrix)
    data["coverage_matrix"] = matrix
    data["coverage_matrix_summary"] = summary
    data["evidence_classification"] = _evidence_classification(data)

    value_metrics = _as_dict(data.get("value_metrics"))
    value_metrics["coverage_matrix_summary"] = summary
    value_metrics["risk_invariant_coverage_rate"] = summary.get("family_coverage_rate", 0.0)
    value_metrics["risk_invariant_confirmed_rate"] = summary.get("confirmed_family_rate", 0.0)
    data["value_metrics"] = value_metrics

    executive = _as_dict(data.get("executive_summary"))
    executive["coverage_matrix_summary"] = summary
    executive["risk_invariant_coverage_label"] = (
        f"风险家族覆盖 {round(float(summary.get('family_coverage_rate') or 0) * 100)}%，"
        f"确认覆盖 {round(float(summary.get('confirmed_family_rate') or 0) * 100)}%"
    )
    data["executive_summary"] = executive

    contract = _as_dict(data.get("data_contract"))
    contract["coverage_matrix"] = {
        "display_key": "coverage_matrix",
        "summary_key": "coverage_matrix_summary",
        "source": "platform_outputs/<project>/benchmark/benchmark_metrics.json:coverage_matrix",
        "honesty_rule": "Coverage matrix is risk/invariant coverage from scan outputs; it is not recall unless benchmark_active and ground_truth_available are true.",
    }
    data["data_contract"] = contract
    payload["data"] = data
    return payload


def install_coverage_matrix_patch(*, patch_source: str = PATCH_SOURCE, root: Path | None = None) -> None:
    from ai_test_asset_center import private_pilot_service as service

    if getattr(service, "_COVERAGE_MATRIX_PATCHED", False):
        return

    original_normalizer = getattr(service, "_normalize_command_center_envelope", None)

    def _normalize_with_coverage_matrix(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = original_normalizer(payload) if callable(original_normalizer) else payload
        try:
            return inject_coverage_matrix(normalized, root=root or service._root())
        except Exception:
            return normalized

    service._ORIGINAL_COVERAGE_MATRIX_NORMALIZER = original_normalizer  # type: ignore[attr-defined]
    service._normalize_command_center_envelope = _normalize_with_coverage_matrix  # type: ignore[attr-defined]
    service._COVERAGE_MATRIX_PATCHED = True  # type: ignore[attr-defined]
    service._COVERAGE_MATRIX_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_coverage_matrix_patch() -> None:
    from ai_test_asset_center import private_pilot_service as service

    original = getattr(service, "_ORIGINAL_COVERAGE_MATRIX_NORMALIZER", None)
    if callable(original):
        service._normalize_command_center_envelope = original  # type: ignore[attr-defined]
    service._COVERAGE_MATRIX_PATCHED = False  # type: ignore[attr-defined]
