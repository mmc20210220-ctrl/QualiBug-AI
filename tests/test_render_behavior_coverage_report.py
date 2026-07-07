import json
import subprocess
import sys
from pathlib import Path

from tools.render_behavior_coverage_report import (
    extract_coverage_sources,
    render_behavior_coverage_report,
)


def test_extract_coverage_sources_collects_supported_containers():
    payload = {
        "behaviors": [{"behavior_id": "BEH-1", "validation_run_id": "VAL-1"}],
        "evidence_packages": [{"package_id": "EP-1", "traceability": {"behavior_id": "BEH-1"}}],
        "regression_assets": [{"asset_id": "REG-1", "behavior": {"behavior_id": "BEH-1"}}],
    }

    sources = extract_coverage_sources(payload)

    assert len(sources) == 3
    assert sources[0]["behavior_id"] == "BEH-1"
    assert sources[1]["package_id"] == "EP-1"
    assert sources[2]["asset_id"] == "REG-1"


def test_extract_coverage_sources_expands_behavior_registry_payload():
    payload = {
        "behavior_registry": {
            "behaviors": [
                {"behavior_id": "BEH-1", "evidence": ["EP-1"]},
                {"behavior_id": "BEH-2", "validation_runs": ["VAL-2"]},
            ]
        }
    }

    sources = extract_coverage_sources(payload)

    assert [source["behavior_id"] for source in sources] == ["BEH-1", "BEH-2"]


def test_render_behavior_coverage_report_builds_metrics():
    payload = {
        "artifacts": [
            {"behavior_id": "BEH-covered", "package_id": "EP-1"},
            {"behavior_id": "BEH-observed", "validation_run_id": "VAL-1"},
        ]
    }

    report = render_behavior_coverage_report(payload)

    assert report["total_behaviors"] == 2
    assert report["covered_behavior_percent"] == 50.0
    assert report["observed_or_covered_behavior_percent"] == 100.0


def test_cli_writes_behavior_coverage_report(tmp_path: Path):
    source = tmp_path / "source.json"
    output = tmp_path / "coverage.json"
    source.write_text(
        json.dumps(
            {
                "artifacts": [
                    {"behavior_id": "BEH-covered", "package_id": "EP-1"},
                    {"behavior_id": "BEH-observed", "validation_run_id": "VAL-1"},
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/render_behavior_coverage_report.py",
            "--input",
            str(source),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Behavior coverage report written" in completed.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["covered_behavior_percent"] == 50.0
    assert report["coverage_bucket_counts"]["covered"] == 1
