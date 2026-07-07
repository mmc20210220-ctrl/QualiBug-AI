import json
import subprocess
import sys
from pathlib import Path

from tools.render_validation_summary_report import render_validation_summary_report


def _prebuilt_reports():
    return {
        "reports": {
            "behavior_registry": {
                "total_behaviors": 2,
                "status_counts": {"violated": 1, "validated": 1, "observed": 0, "untested": 0},
            },
            "evidence_packages": {
                "total_packages": 2,
                "confirmed_packages": 2,
                "evidence_complete_packages": 2,
                "evidence_completeness_percent": 100.0,
            },
            "regression_assets": {
                "total_assets": 2,
                "confirmed_violation_assets": 2,
                "comparison_counts": {"ready": 0, "validated": 2, "failed": 0, "blocked": 0},
            },
            "behavior_traceability": {
                "total_traces": 2,
                "status_counts": {"complete": 2, "partial": 0, "unlinked": 0},
                "complete_traceability_percent": 100.0,
            },
            "behavior_coverage": {
                "total_behaviors": 2,
                "coverage_bucket_counts": {"covered": 2, "partially_covered": 0, "uncovered": 0},
                "covered_behavior_percent": 100.0,
                "observed_or_covered_behavior_percent": 100.0,
            },
        }
    }


def test_render_validation_summary_report_uses_prebuilt_reports():
    report = render_validation_summary_report(_prebuilt_reports())

    assert report["assurance_level"] == "strong"
    assert report["north_star"]["confirmed_violation_rate"] == 100.0
    assert report["attention_items"] == []


def test_render_validation_summary_report_builds_from_artifacts():
    report = render_validation_summary_report(
        {
            "artifacts": [
                {
                    "behavior_id": "BEH-ORDER",
                    "behavior_name": "Create Order",
                    "violation_id": "VIO-ORDER-1",
                    "confirmed": True,
                    "runtime_evidence": {"status": 500},
                    "validation_run_id": "VAL-1",
                    "package_id": "EP-VIO-ORDER-1",
                    "regression_asset_id": "REG-ORDER-1",
                    "result_id": "RES-1",
                }
            ],
            "regression_results": [
                {"asset_id": "REG-ORDER-1", "result_id": "RES-1", "passed": True}
            ],
        }
    )

    assert report["north_star"]["confirmed_violations"] == 1
    assert report["behavior_state"]["total_behaviors"] == 1
    assert report["evidence_state"]["evidence_complete_packages"] == 1
    assert report["traceability_state"]["complete_traceability_percent"] == 100.0
    assert report["regression_state"]["comparison_counts"]["validated"] == 1


def test_cli_writes_validation_summary_report(tmp_path: Path):
    source = tmp_path / "reports.json"
    output = tmp_path / "summary.json"
    source.write_text(json.dumps(_prebuilt_reports()), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "tools/render_validation_summary_report.py",
            "--input",
            str(source),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Validation summary report written" in completed.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["assurance_level"] == "strong"
    assert report["north_star"]["confirmed_violation_rate"] == 100.0


def test_render_validation_summary_report_does_not_emit_out_of_boundary_language():
    report = render_validation_summary_report(_prebuilt_reports())
    rendered = str(report).lower()
    forbidden = ("repair", "recommendation", "auto fix", "pull request", "patch")
    assert not any(term in rendered for term in forbidden)
