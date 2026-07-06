import json
import subprocess
import sys

from tools.render_confirmed_bug_evidence_report import render_confirmed_bug_evidence_report


def test_render_confirmed_bug_evidence_report_from_findings_container():
    report = render_confirmed_bug_evidence_report(
        {
            "findings": [
                {"id": "bug-1", "status": "confirmed", "request": "GET /api/a", "status_code": 500},
                {"id": "bug-2", "status": "confirmed", "summary": "No runtime artifact"},
                {"id": "bug-3", "status": "suspected", "request": "GET /api/b", "status_code": 400},
            ]
        }
    )

    assert report["confirmed_bug_candidates"] == 2
    assert report["evidence_backed_confirmed_bugs"] == 1
    assert report["non_evidence_backed_confirmed_bugs"] == 1
    assert report["confirmed_bug_evidence_ratio"] == 0.5
    assert report["confirmed_bug_promotion_blocked"] == 1


def test_render_confirmed_bug_evidence_cli_writes_json(tmp_path):
    input_path = tmp_path / "discovery_report.json"
    output_path = tmp_path / "confirmed_bug_evidence.json"
    input_path.write_text(
        json.dumps(
            {
                "confirmed_bugs": [
                    {"id": "bug-1", "confirmed_bug": True, "request": "POST /api/pay", "response": {"status_code": 500}},
                    {"id": "bug-2", "confirmed_bug": True, "summary": "No runtime artifact"},
                    {"id": "bug-3", "finding_status": "reproduced", "probe": {"observed": {"status": 409}}},
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/render_confirmed_bug_evidence_report.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    stdout_payload = json.loads(completed.stdout)
    file_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert file_payload["confirmed_bug_candidates"] == 3
    assert file_payload["evidence_backed_confirmed_bugs"] == 2
    assert file_payload["confirmed_bug_promotion_blocked"] == 1
