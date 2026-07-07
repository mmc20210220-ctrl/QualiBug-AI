import json
import subprocess
import sys
from pathlib import Path

from tools.enforce_behavior_coverage_threshold import enforce_behavior_coverage_threshold


def test_enforce_behavior_coverage_threshold_passes_when_observed_meets_minimum():
    result = enforce_behavior_coverage_threshold(
        {
            "covered_behavior_percent": 75.0,
            "total_behaviors": 4,
            "coverage_bucket_counts": {"covered": 3},
        },
        70.0,
    )

    assert result == {
        "passed": True,
        "minimum_percent": 70.0,
        "observed_percent": 75.0,
        "total_behaviors": 4,
        "covered_behaviors": 3,
    }


def test_enforce_behavior_coverage_threshold_fails_when_observed_is_below_minimum():
    result = enforce_behavior_coverage_threshold(
        {
            "covered_behavior_percent": 40.0,
            "total_behaviors": 5,
            "coverage_bucket_counts": {"covered": 2},
        },
        80.0,
    )

    assert result["passed"] is False
    assert result["observed_percent"] == 40.0
    assert result["minimum_percent"] == 80.0


def test_cli_exits_zero_when_threshold_passes(tmp_path: Path):
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "covered_behavior_percent": 100.0,
                "total_behaviors": 1,
                "coverage_bucket_counts": {"covered": 1},
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/enforce_behavior_coverage_threshold.py",
            "--input",
            str(report),
            "--minimum-percent",
            "90",
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert '"passed": true' in completed.stdout.lower()


def test_cli_exits_nonzero_when_threshold_fails(tmp_path: Path):
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "covered_behavior_percent": 25.0,
                "total_behaviors": 4,
                "coverage_bucket_counts": {"covered": 1},
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/enforce_behavior_coverage_threshold.py",
            "--input",
            str(report),
            "--minimum-percent",
            "50",
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert '"passed": false' in completed.stdout.lower()
