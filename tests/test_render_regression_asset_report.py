import json
import subprocess
import sys

from tools.render_regression_asset_report import (
    extract_regression_results,
    extract_regression_sources,
    render_regression_asset_report,
)


def test_extract_regression_sources_from_violations_container():
    payload = {"violations": [{"violation_id": "VIO-1"}]}

    assert extract_regression_sources(payload) == [{"violation_id": "VIO-1"}]


def test_extract_regression_sources_from_packages_container():
    payload = {"packages": [{"violation_id": "VIO-2", "behavior_id": "BEH-2"}]}

    assert extract_regression_sources(payload) == [{"violation_id": "VIO-2", "behavior_id": "BEH-2"}]


def test_extract_regression_sources_falls_back_to_single_payload():
    payload = {"violation_id": "VIO-3"}

    assert extract_regression_sources(payload) == [payload]


def test_extract_regression_results_from_payload():
    payload = {"regression_results": [{"asset_id": "REG-1", "passed": True}]}

    assert extract_regression_results(payload) == [{"asset_id": "REG-1", "passed": True}]


def test_render_regression_asset_report_outputs_asset_counts():
    report = render_regression_asset_report(
        {
            "confirmed_bugs": [
                {"violation_id": "VIO-ORDER-001", "confirmed": True, "behavior_id": "BEH-ORDER"}
            ]
        }
    )

    assert report["total_assets"] == 1
    assert report["confirmed_violation_assets"] == 1
    assert report["behavior_ids"] == ["BEH-ORDER"]


def test_render_regression_asset_report_includes_comparisons():
    report = render_regression_asset_report(
        {
            "confirmed_bugs": [
                {
                    "regression_asset_id": "REG-ORDER-001",
                    "violation_id": "VIO-ORDER-001",
                    "confirmed": True,
                    "behavior_id": "BEH-ORDER",
                }
            ],
            "regression_results": [
                {"asset_id": "REG-ORDER-001", "result_id": "RUN-1", "passed": True}
            ],
        }
    )

    assert report["comparison_counts"]["validated"] == 1
    assert report["comparisons"][0]["result_id"] == "RUN-1"


def test_render_regression_asset_report_cli_writes_output(tmp_path):
    input_path = tmp_path / "violations.json"
    output_path = tmp_path / "regression_assets.json"
    input_path.write_text(
        json.dumps(
            {
                "violations": [
                    {"regression_asset_id": "REG-1", "violation_id": "VIO-1", "confirmed": True, "behavior_id": "BEH-1"}
                ],
                "regression_results": [{"asset_id": "REG-1", "passed": True}],
            }
        ),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            "tools/render_regression_asset_report.py",
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
    assert report["total_assets"] == 1
    assert report["comparisons"][0]["comparison_status"] == "validated"
