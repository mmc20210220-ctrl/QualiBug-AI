"""Render an evidence package report from validation artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.render_behavior_registry_report import extract_behavior_records


def _has_runtime_evidence(item: dict[str, Any]) -> bool:
    value = item.get("runtime_evidence") or item.get("raw_evidence") or item.get("evidence_package")
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return value is not None and str(value).strip() != ""


def _is_confirmed(item: dict[str, Any]) -> bool:
    if item.get("confirmed") is True:
        return True
    status = str(item.get("bug_status") or item.get("status") or "").strip().lower()
    return status in {"confirmed", "reproduced", "validated"}


def _package_id(item: dict[str, Any], index: int) -> str:
    for key in ("package_id", "evidence_package_id", "evidence_bundle_id", "violation_id", "finding_id", "id"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return f"EVIDENCE-PACKAGE-{index:04d}"


def render_evidence_package_report(payload: Any) -> dict[str, Any]:
    records = extract_behavior_records(payload)
    packages: list[dict[str, Any]] = []
    for index, item in enumerate(records, start=1):
        confirmed = _is_confirmed(item)
        complete = confirmed and _has_runtime_evidence(item)
        packages.append(
            {
                "package_id": _package_id(item, index),
                "confirmed": confirmed,
                "evidence_complete": complete,
            }
        )

    total = len(packages)
    confirmed_count = sum(1 for item in packages if item["confirmed"])
    complete_count = sum(1 for item in packages if item["evidence_complete"])
    return {
        "total_packages": total,
        "confirmed_packages": confirmed_count,
        "evidence_complete_packages": complete_count,
        "evidence_completeness_percent": round((complete_count / total) * 100, 2) if total else 0.0,
        "packages": packages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render evidence package report")
    parser.add_argument("--input", required=True, help="Path to validation artifact JSON")
    parser.add_argument("--output", required=True, help="Path to write evidence package report JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_evidence_package_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Evidence package report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
