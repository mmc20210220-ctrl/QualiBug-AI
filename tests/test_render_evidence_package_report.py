import json
import subprocess
import sys

from tools.render_evidence_package_report import extract_violation_artifacts, render_evidence_package_report


def test_extract_violation_artifacts_from_violations_container():
    payload = {"violations": [{"violation_id": "VIO-1"}]}

    assert extract_violation_artifacts(payload) == [{"violation_id": "VIO-1"}]


def test_extract_violation_artifacts_from_confirmed_bugs_container():
    payload = {"confirmed_bugs": [{"bug_id": "BUG-1", "confirmed_bug": True}]}

    assert extract_violation_artifacts(payload) == [{"bug_id": "BUG-1", "confirmed_bug": True}]


def test_extract_violation_artifacts_falls_back_to_single_payload():
    payload = {"violation_id": "VIO-2"}

    assert extract_violation_artifacts(payload) == [payload]


def test_render_evidence_package_report_outputs_customer_grade_package():
    report = render_evidence_package_report(
        {
            "findings": [
                {
                    "violation_id": "VIO-ORDER-001",
                    "behavior_id": "BEH-ORDER-CREATE",
                    "confirmed": True,
                    "runtime_evidence": {"status": 500},
                    "severity": "P1",
                }
            ]
        }
    )

    assert report["total_packages"] == 1
    assert report["confirmed_packages"] == 1
    assert report["packages"][0]["package_id"] == "EP-VIO-ORDER-001"
    assert report["packages"][0]["risk_context"]["severity"] == "P1"


def test_render_evidence_package_report_cli_writes_output(tmp_path):
    input_path = tmp_path / "violations.json"
    output_path = tmp_path / "evidence_packages.json"
    input_path.write_text(
        json.dumps(
            {
                "violations": [
                    {
                        "violation_id": "VIO-PAYMENT-001",
                        "behavior_id": "BEH-PAYMENT",
                        "confirmed": True,
                        "runtime_evidence": {"status": 409},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            "tools/render_evidence_package_report.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["total_packages"] == 1
    assert report["evidence_complete_packages"] == 1
    assert report["packages"][0]["violation"]["behavior_id"] == "BEH-PAYMENT"


def test_render_evidence_package_report_does_not_emit_repair_language():
    report = render_evidence_package_report(
        {"violations": [{"violation_id": "VIO-1", "runtime_evidence": {"status": 500}}]}
    )

    serialized = str(report).lower()
    assert "fix" not in serialized
    assert "repair" not in serialized
    assert "recommendation" not in serialized
    assert "remediation" not in serialized
