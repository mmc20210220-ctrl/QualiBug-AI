from __future__ import annotations

"""Phase92W: link generated report/repro artifacts back to each finding."""

from typing import Any


OUTPUT_LABELS = {
    "execution_report": "machine_readable_execution_report",
    "execution_report_md": "customer_readable_markdown_report",
    "repro_ps1": "powershell_reproduction_asset",
    "regression_pytest": "pytest_regression_asset",
    "remediation_verification_json": "developer_remediation_verification_json",
    "remediation_verification_md": "developer_remediation_verification_markdown",
}


def _artifact_entries(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for key, label in OUTPUT_LABELS.items():
        path = outputs.get(key)
        if path:
            entries.append({"kind": key, "label": label, "path": str(path)})
    return entries


def link_reproduction_assets(report: dict[str, Any]) -> dict[str, Any]:
    """Mutate and return report with per-finding artifact backlinks.

    The executor only knows output paths after report/repro files are written.
    Phase92W fills those concrete paths into each evidence package so a customer
    can move from a finding to exact generated assets without manual searching.
    """
    outputs = report.get("outputs") if isinstance(report.get("outputs"), dict) else {}
    entries = _artifact_entries(outputs)
    for finding in report.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        package = finding.get("evidence_package") if isinstance(finding.get("evidence_package"), dict) else {}
        if not package:
            continue
        repro = package.get("reproduction_assets") if isinstance(package.get("reproduction_assets"), dict) else {}
        repro["artifact_links"] = entries
        repro["artifact_link_count"] = len(entries)
        repro["primary_repro_asset"] = next((e for e in entries if e.get("kind") == "repro_ps1"), entries[0] if entries else None)
        package["reproduction_assets"] = repro
        finding["evidence_package"] = package
        finding["reproduction_artifact_links"] = entries
    report["reproduction_artifact_index"] = {
        "engine": "runtime_reproduction_asset_linker_v1_phase92w",
        "artifact_count": len(entries),
        "finding_link_count": sum(1 for f in (report.get("findings") or []) if isinstance(f, dict) and f.get("reproduction_artifact_links")),
        "artifacts": entries,
    }
    return report
