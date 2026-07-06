import json
import subprocess
import sys

import pytest

from tools.enforce_confirmed_bug_evidence_threshold import enforce_confirmed_bug_evidence_threshold


def test_confirmed_bug_evidence_threshold_passes_clean_report():
    enforce_confirmed_bug_evidence_threshold(
        {
            "confirmed_bug_candidates": 2,
            "evidence_backed_confirmed_bugs": 2,
            "non_evidence_backed_confirmed_bugs": 0,
            "confirmed_bug_evidence_ratio": 1.0,
            "confirmed_bug_promotion_blocked": 0,
        }
    )


def test_confirmed_bug_evidence_threshold_fails_low_ratio():
    with pytest.raises(SystemExit) as exc:
        enforce_confirmed_bug_evidence_threshold(
            {
                "confirmed_bug_candidates": 2,
                "evidence_backed_confirmed_bugs": 1,
                "non_evidence_backed_confirmed_bugs": 1,
                "confirmed_bug_evidence_ratio": 0.5,
                "confirmed_bug_promotion_blocked": 1,
            }
        )

    message = str(exc.value)
    assert "confirmed_bug_evidence_ratio" in message
    assert "confirmed_bug_promotion_blocked" in message


def test_confirmed_bug_evidence_threshold_allows_configured_blocked_count():
    enforce_confirmed_bug_evidence_threshold(
        {
            "confirmed_bug_candidates": 4,
            "evidence_backed_confirmed_bugs": 3,
            "non_evidence_backed_confirmed_bugs": 1,
            "confirmed_bug_evidence_ratio": 0.75,
            "confirmed_bug_promotion_blocked": 1,
        },
        min_confirmed_bug_evidence_ratio=0.75,
        max_blocked_promotions=1,
    )


def test_confirmed_bug_evidence_threshold_cli_passes(tmp_path):
    input_path = tmp_path / "confirmed_bug_evidence.json"
    input_path.write_text(
        json.dumps(
            {
                "confirmed_bug_candidates": 1,
                "evidence_backed_confirmed_bugs": 1,
                "non_evidence_backed_confirmed_bugs": 0,
                "confirmed_bug_evidence_ratio": 1.0,
                "confirmed_bug_promotion_blocked": 0,
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/enforce_confirmed_bug_evidence_threshold.py",
            "--input",
            str(input_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Confirmed bug evidence threshold passed" in completed.stdout
