"""Render a unified validation summary report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center.validation_summary import build_validation_summary_report
from tools.render_behavior_coverage_report import render_behavior_coverage_report
from tools.render_behavior_registry_report import render_behavior_registry_report
from tools.render_behavior_traceability_report import render_behavior_traceability_report
from tools.render_evidence_package_report import render_evidence_package_report
from tools.render_regression_asset_report import render_regression_asset_report


REPORT_KEYS = {
    "behavior_registry",
    "evidence_packages",
    "regression_assets",
    "behavior_traceability",
    "behavior_coverage",
}


def _artifact_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("artifacts"), list):
        return payload["artifacts"]
    return payload


def _violation_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("artifacts"), list):
        return {"violations": payload["artifacts"]}
    return payload


def _regression_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("artifacts"), list):
        result = {"violations": payload["artifacts"]}
        if isinstance(payload.get("regression_results"), list):
            result["regression_results"] = payload["regression_results"]
        return result
    return payload


def _prebuilt_reports(payload: dict[str, Any]) -> dict[str, Any] | None:
    reports = payload.get("reports") if isinstance(payload.get("reports"), dict) else payload
    if not isinstance(reports, dict):
        return None
    selected = {key: reports[key] for key in REPORT_KEYS if isinstance(reports.get(key), dict)}
    return selected if selected else None


def render_validation_summary_report(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        prebuilt = _prebuilt_reports(payload)
        if prebuilt:
            return build_validation_summary_report(prebuilt)

    reports = {
        "behavior_registry": render_behavior_registry_report(_artifact_payload(payload)),
        "evidence_packages": render_evidence_package_report(_violation_payload(payload)),
        "regression_assets": render_regression_asset_report(_regression_payload(payload)),
        "behavior_traceability": render_behavior_traceability_report(payload),
        "behavior_coverage": render_behavior_coverage_report(payload),
    }
    return build_validation_summary_report(reports)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render validation summary report")
    parser.add_argument("--input", required=True, help="Path to source artifact JSON or prebuilt reports JSON")
    parser.add_argument("--output", required=True, help="Path to write validation summary report JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_validation_summary_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Validation summary report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
