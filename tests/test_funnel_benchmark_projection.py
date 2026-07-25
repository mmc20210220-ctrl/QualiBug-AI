from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_RUNNER_PATH = ROOT / "benchmark_evaluator" / "funnel_benchmark.py"


def test_funnel_benchmark_summary_reuses_attached_formal_projection() -> None:
    tree = ast.parse(_RUNNER_PATH.read_text(encoding="utf-8"))
    count_assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_counts" for target in node.targets)
    ]

    assert len(count_assignments) == 1
    value = count_assignments[0].value
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Name)
    assert value.func.id == "_projected_formal_count_projection"
