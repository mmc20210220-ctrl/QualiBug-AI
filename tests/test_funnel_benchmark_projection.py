from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# ``_funnel_benchmark.py`` is an operator-local runner kept out of the repo by
# the ``_funnel_*.*`` ignore rule.
_RUNNER_PATH = ROOT / "_funnel_benchmark.py"


def test_funnel_benchmark_summary_reuses_attached_formal_projection() -> None:
    if not _RUNNER_PATH.is_file():
        pytest.skip(f"operator-local funnel runner absent: {_RUNNER_PATH}")
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
