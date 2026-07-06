import json
import subprocess
import sys

import pytest

from tools.enforce_execution_evidence_threshold import enforce_evidence_thresholds


def _passing_report():
    return {
        "evidence_backed_items": 6,
        "non_evidence_backed_items": 4,
        "evidence_backed_ratio": 0.6,
        "per_engine_verification_items": {"causality": 5, "temporal": 5},
        "per_engine_evidence_backed_items": {"causality": 3, "temporal": 3},
        "per_engine_evidence_backed_ratio": {"causality": 0.6, "temporal": 0.6},
        "engines_with_no_evidence_backed_output": [],
    }


def test_enforce_evidence_thresholds_accepts_healthy_report():
    result = enforce_evidence_thresholds(
        _passing_report(),
        min_evidence_ratio=0.5,
        max_no_evidence_engines=0,
        min_per_engine_evidence_ratio=0.5,
    )

    assert result["status"] == "passed"
    assert result["failures"] == []


def test_enforce_evidence_thresholds_rejects_low_evidence_ratio():
    report = _passing_report()
    report["evidence_backed_ratio"] = 0.2

    with pytest.raises(ValueError) as exc:
        enforce_evidence_thresholds(report, min_evidence_ratio=0.5, max_no_evidence_engines=0)

    assert "below required" in str(exc.value)


def test_enforce_evidence_thresholds_rejects_no_evidence_engines():
    report = _passing_report()
    report["engines_with_no_evidence_backed_output"] = ["temporal"]

    with pytest.raises(ValueError) as exc:
        enforce_evidence_thresholds(report, min_evidence_ratio=0.5, max_no_evidence_engines=0)

    assert "too many engines" in str(exc.value)


def test_enforce_evidence_thresholds_rejects_weak_per_engine_ratio():
    report = _passing_report()
    report["per_engine_evidence_backed_ratio"]["temporal"] = 0.1

    with pytest.raises(ValueError) as exc:
        enforce_evidence_thresholds(
            report,
            min_evidence_ratio=0.5,
            max_no_evidence_engines=0,
            min_per_engine_evidence_ratio=0.5,
        )

    assert "temporal" in str(exc.value)


def test_evidence_threshold_cli_passes_for_healthy_report(tmp_path):
    path = tmp_path / "evidence_quality.json"
    path.write_text(json.dumps(_passing_report()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "tools/enforce_execution_evidence_threshold.py",
            "--input",
            str(path),
            "--min-evidence-ratio",
            "0.5",
            "--max-no-evidence-engines",
            "0",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert '"status": "passed"' in result.stdout
