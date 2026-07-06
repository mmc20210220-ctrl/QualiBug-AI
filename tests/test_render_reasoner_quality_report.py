import json
import subprocess
import sys

from tools.render_reasoner_quality_report import extract_reasoner_quality_report


def _sample_report():
    return {
        "total_engines": 2,
        "engine_outputs": {"causality": 2, "temporal": 1},
        "executable_hypotheses": 2,
        "non_executable_hypotheses": 1,
        "executable_hypothesis_ratio": 0.6667,
        "per_engine_executable_hypotheses": {"causality": 2, "temporal": 0},
        "per_engine_non_executable_hypotheses": {"causality": 0, "temporal": 1},
        "per_engine_executable_ratio": {"causality": 1.0, "temporal": 0.0},
        "engines_with_no_executable_output": ["temporal"],
    }


def test_extract_reasoner_quality_report_returns_stable_subset():
    extracted = extract_reasoner_quality_report(_sample_report())

    assert extracted == {
        "executable_hypotheses": 2,
        "non_executable_hypotheses": 1,
        "executable_hypothesis_ratio": 0.6667,
        "per_engine_executable_hypotheses": {"causality": 2, "temporal": 0},
        "per_engine_non_executable_hypotheses": {"causality": 0, "temporal": 1},
        "per_engine_executable_ratio": {"causality": 1.0, "temporal": 0.0},
        "engines_with_no_executable_output": ["temporal"],
    }


def test_render_reasoner_quality_report_cli_writes_json(tmp_path):
    input_path = tmp_path / "engine_report.json"
    output_path = tmp_path / "quality_report.json"
    input_path.write_text(json.dumps(_sample_report()), encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            "tools/render_reasoner_quality_report.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        check=True,
    )

    rendered = json.loads(output_path.read_text(encoding="utf-8"))
    assert rendered["executable_hypothesis_ratio"] == 0.6667
    assert rendered["engines_with_no_executable_output"] == ["temporal"]
