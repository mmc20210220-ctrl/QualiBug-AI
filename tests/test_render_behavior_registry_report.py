import json
import subprocess
import sys

from tools.render_behavior_registry_report import extract_behavior_records, render_behavior_registry_report


def test_extract_behavior_records_from_behaviors_container():
    payload = {"behaviors": [{"behavior_id": "BEH-1", "evidence_id": "EVID-1"}]}

    assert extract_behavior_records(payload) == [{"behavior_id": "BEH-1", "evidence_id": "EVID-1"}]


def test_extract_behavior_records_from_violations_container():
    payload = {"violations": [{"behavior_id": "BEH-2", "violation_id": "VIO-2"}]}

    assert extract_behavior_records(payload) == [{"behavior_id": "BEH-2", "violation_id": "VIO-2"}]


def test_extract_behavior_records_falls_back_to_single_payload():
    payload = {"behavior_id": "BEH-3"}

    assert extract_behavior_records(payload) == [payload]


def test_render_behavior_registry_report_outputs_status_counts():
    report = render_behavior_registry_report(
        {
            "behavior_records": [
                {"behavior_id": "BEH-1", "violation_id": "VIO-1"},
                {"behavior_id": "BEH-2", "evidence_id": "EVID-2"},
                {"behavior_id": "BEH-3"},
            ]
        }
    )

    assert report["total_behaviors"] == 3
    assert report["status_counts"] == {
        "violated": 1,
        "validated": 1,
        "observed": 0,
        "untested": 1,
    }
    assert report["behavior_coverage_percent"] == 66.67


def test_render_behavior_registry_report_cli_writes_output(tmp_path):
    input_path = tmp_path / "behaviors.json"
    output_path = tmp_path / "behavior_registry.json"
    input_path.write_text(
        json.dumps(
            {
                "violations": [
                    {
                        "behavior_id": "BEH-ORDER-CREATE",
                        "behavior_name": "Create Order",
                        "violation_id": "VIO-001",
                        "evidence_id": "EVID-001",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            "tools/render_behavior_registry_report.py",
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
    assert report["total_behaviors"] == 1
    assert report["behaviors"][0]["behavior_name"] == "Create Order"
    assert report["behaviors"][0]["status"] == "violated"


def test_render_behavior_registry_report_does_not_emit_repair_language():
    report = render_behavior_registry_report(
        {"behaviors": [{"behavior_id": "BEH-PAYMENT", "violation_id": "VIO-PAYMENT-001"}]}
    )

    serialized = str(report).lower()
    assert "fix" not in serialized
    assert "repair" not in serialized
    assert "recommendation" not in serialized
