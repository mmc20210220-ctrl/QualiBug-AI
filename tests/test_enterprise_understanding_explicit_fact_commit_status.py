from __future__ import annotations

from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "chinese-explicit-fact-baseline.yml"
)
STATUS_CONTEXT = "qualibug/chinese-explicit-fact-baseline"


def test_explicit_fact_workflow_publishes_readable_commit_status() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "statuses: write" in text
    assert STATUS_CONTEXT in text
    assert "Publish pending explicit fact status" in text
    assert "Publish final explicit fact status" in text
    assert "github.rest.repos.createCommitStatus" in text
    assert "fact_recall" in text
    assert "slot_exact_accuracy" in text
    assert "p0_exact_fact_recall" in text
    assert "source_locator_exact_accuracy" in text
    assert "accepted_fact_precision" in text
    assert "false_accepted_fact_count" in text
    assert "evidence=${formatMetric" in text
    assert "precision=${formatMetric" in text
    assert "false=${falseAccepted}" in text


def test_explicit_fact_quality_exit_code_remains_ci_authority() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "id: explicit_fact_baseline" in text
    assert "continue-on-error: true" in text
    assert 'echo "exit_code=$code" >> "$GITHUB_OUTPUT"' in text
    assert "Enforce baseline results" in text
    assert 'explicit_code="${EXPLICIT_BASELINE_EXIT_CODE:-2}"' in text
    assert 'if [ "$explicit_code" -ne 0 ]; then' in text
    assert 'exit "$explicit_code"' in text


def test_failed_measurement_still_publishes_summary_and_artifact() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "Print baseline summaries" in text
    assert "Upload explicit fact baseline evidence" in text
    assert text.count("if: always()") >= 4
