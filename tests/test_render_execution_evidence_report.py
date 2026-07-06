import json
import subprocess
import sys

from tools.render_execution_evidence_report import render_execution_evidence_report


def test_render_execution_evidence_report_from_results_by_engine():
    report = render_execution_evidence_report(
        {
            "results_by_engine": {
                "causality": [
                    {"request": "POST /api/order/create", "status_code": 500},
                    {"summary": "No runtime probe captured"},
                ],
                "temporal": [
                    {"probe": {"observed": {"status": 200}}},
                ],
            }
        },
        engine_names=["causality", "temporal", "boundary"],
    )

    assert report["evidence_backed_items"] == 2
    assert report["non_evidence_backed_items"] == 1
    assert report["evidence_backed_ratio"] == 2 / 3
    assert report["per_engine_evidence_backed_ratio"]["causality"] == 0.5
    assert report["per_engine_evidence_backed_ratio"]["temporal"] == 1.0
    assert report["per_engine_evidence_backed_ratio"]["boundary"] == 0.0
    assert report["engines_with_no_evidence_backed_output"] == ["boundary"]


def test_render_execution_evidence_cli_writes_json(tmp_path):
    input_path = tmp_path / "raw_verification.json"
    output_path = tmp_path / "evidence_quality.json"
    input_path.write_text(
        json.dumps(
            {
                "verification_results": {
                    "causality": [
                        {"request": "GET /api/user/1", "response": {"status_code": 200}},
                    ],
                    "invariant": [
                        {"verification_method": {"path": "/api/inventory"}},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/render_execution_evidence_report.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--engine",
            "causality",
            "--engine",
            "invariant",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    stdout_payload = json.loads(completed.stdout)
    file_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert file_payload["evidence_backed_ratio"] == 0.5
    assert file_payload["engines_with_no_evidence_backed_output"] == ["invariant"]
