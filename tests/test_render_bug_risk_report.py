import json
import subprocess
import sys

from tools.render_bug_risk_report import extract_findings, render_bug_risk_report


def test_extract_findings_from_findings_container():
    payload = {"findings": [{"title": "Payment security data loss"}]}

    assert extract_findings(payload) == [{"title": "Payment security data loss"}]


def test_extract_findings_falls_back_to_single_payload():
    payload = {"title": "Validation missing"}

    assert extract_findings(payload) == [payload]


def test_render_bug_risk_report_outputs_severity_counts():
    report = render_bug_risk_report(
        {
            "bugs": [
                {"title": "Payment security data loss", "confirmed_bug": True, "response": {"status": 500}},
                {"title": "Button typo"},
            ]
        }
    )

    assert report["total_findings"] == 2
    assert report["severity_counts"]["P0"] == 1
    assert report["severity_counts"]["P3"] == 1
    assert report["highest_risk_finding"]["severity"] == "P0"


def test_render_bug_risk_report_cli_writes_output(tmp_path):
    input_path = tmp_path / "discovery.json"
    output_path = tmp_path / "risk.json"
    input_path.write_text(
        json.dumps({"findings": [{"title": "Order API crashes with 500 exception", "confirmed": True}]}),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            "tools/render_bug_risk_report.py",
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
    assert report["total_findings"] == 1
    assert report["findings"][0]["severity"] == "P1"
