from pathlib import Path


def test_stage_reasoner_report_contains_executable_quality_fields():
    source = Path("ai_test_asset_center/stage_reason_all_v2.py").read_text(encoding="utf-8")

    assert "from .reasoner_quality_report import build_executable_quality_report" in source
    assert "executable_quality_report = build_executable_quality_report(" in source
    assert "self._last_engine_report.update(executable_quality_report)" in source

    for field in (
        "executable_hypotheses",
        "non_executable_hypotheses",
        "executable_hypothesis_ratio",
        "per_engine_executable_hypotheses",
        "per_engine_non_executable_hypotheses",
        "per_engine_executable_ratio",
        "engines_with_no_executable_output",
    ):
        assert field in source


def test_local_bootstrap_only_report_is_also_wired():
    source = Path("ai_test_asset_center/stage_reason_all_v2.py").read_text(encoding="utf-8")

    assert "local_executable_quality_report = build_executable_quality_report(" in source
    assert "self._last_engine_report.update(local_executable_quality_report)" in source
