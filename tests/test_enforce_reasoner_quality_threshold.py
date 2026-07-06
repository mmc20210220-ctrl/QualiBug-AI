import json
import subprocess
import sys

import pytest

from tools.enforce_reasoner_quality_threshold import enforce_thresholds


def _passing_report():
    return {
        "executable_hypotheses": 8,
        "non_executable_hypotheses": 2,
        "executable_hypothesis_ratio": 0.8,
        "per_engine_executable_hypotheses": {"causality": 4, "temporal": 4},
        "per_engine_non_executable_hypotheses": {"causality": 1, "temporal": 1},
        "per_engine_executable_ratio": {"causality": 0.8, "temporal": 0.8},
        "engines_with_no_executable_output": [],
    }


def test_enforce_thresholds_accepts_healthy_report():
    result = enforce_thresholds(
        _passing_report(),
        min_overall_ratio=0.6,
        max_zero_output_engines=0,
        min_per_engine_ratio=0.5,
    )

    assert result["status"] == "passed"
    assert result["failures"] == []


def test_enforce_thresholds_rejects_low_overall_ratio():
    report = _passing_report()
    report["executable_hypothesis_ratio"] = 0.2

    with pytest.raises(ValueError) as exc:
        enforce_thresholds(report, min_overall_ratio=0.6, max_zero_output_engines=0)

    assert "below required" in str(exc.value)


def test_enforce_thresholds_rejects_zero_output_engines():
    report = _passing_report()
    report["engines_with_no_executable_output"] = ["temporal"]

    with pytest.raises(ValueError) as exc:
        enforce_thresholds(report, min_overall_ratio=0.6, max_zero_output_engines=0)

    assert "too many engines" in str(exc.value)


def test_enforce_threshold_cli_passes_for_healthy_report(tmp_path):
    path = tmp_path / "quality.json"
    path.write_text(json.dumps(_passing_report()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "tools/enforce_reasoner_quality_threshold.py",
            "--input",
            str(path),
            "--min-overall-ratio",
            "0.6",
            "--max-zero-output-engines",
            "0",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert '"status": "passed"' in result.stdout
