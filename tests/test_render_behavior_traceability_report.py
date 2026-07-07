import json
import subprocess
import sys
from pathlib import Path

from tools.render_behavior_traceability_report import (
    extract_traceability_sources,
    render_behavior_traceability_report,
)


def test_extract_traceability_sources_collects_supported_containers():
    payload = {
        "behaviors": [{"behavior_id": "BEH-1", "validation_run_id": "VAL-1"}],
        "evidence_packages": [{"package_id": "EP-1", "traceability": {"behavior_id": "BEH-1"}}],
        "regression_results": [{"behavior_id": "BEH-1", "result_id": "RES-1"}],
    }

    sources = extract_traceability_sources(payload)

    assert len(sources) == 3
    assert sources[0]["behavior_id"] == "BEH-1"
    assert sources[1]["package_id"] == "EP-1"
    assert sources[2]["result_id"] == "RES-1"


def test_extract_traceability_sources_falls_back_to_payload_record():
    payload = {"behavior_id": "BEH-single", "validation_run_id": "VAL-1"}

    assert extract_traceability_sources(payload) == [payload]


def test_render_behavior_traceability_report_builds_complete_chain():
    payload = {
        "artifacts": [
            {
                "behavior_id": "BEH-1",
                "validation_run_id": "VAL-1",
                "package_id": "EP-1",
                "violation_id": "VIO-1",
                "regression_asset_id": "REG-1",
                "result_id": "RES-1",
            }
        ]
    }

    report = render_behavior_traceability_report(payload)

    assert report["total_traces"] == 1
    assert report["status_counts"]["complete"] == 1
    assert report["complete_traceability_percent"] == 100.0


def test_cli_writes_behavior_traceability_report(tmp_path: Path):
    source = tmp_path / "source.json"
    output = tmp_path / "traceability.json"
    source.write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "behavior_id": "BEH-1",
                        "validation_run_id": "VAL-1",
                        "package_id": "EP-1",
                        "violation_id": "VIO-1",
                        "regression_asset_id": "REG-1",
                        "result_id": "RES-1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/render_behavior_traceability_report.py",
            "--input",
            str(source),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Behavior traceability report written" in completed.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status_counts"]["complete"] == 1
    assert report["traces"][0]["status"] == "complete"
