from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_product_runtime_contains_no_understanding_ground_truth_evaluator() -> None:
    root = Path(__file__).resolve().parents[1]
    product_root = root / "ai_test_asset_center"
    forbidden = (
        "benchmark_evaluator.enterprise_understanding",
        "enterprise_understanding_benchmark",
        "qualibug.enterprise-understanding-ground-truth.v1",
    )

    violations: list[str] = []
    for path in product_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                violations.append(f"{path.relative_to(root)}:{token}")

    assert violations == []


def test_product_facade_import_does_not_load_evaluator_package() -> None:
    script = """
import json
import sys
import ai_test_asset_center.enterprise_knowledge_center  # noqa: F401
print(json.dumps(sorted(name for name in sys.modules if name.startswith('benchmark_evaluator.enterprise_understanding'))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.stdout.strip() == "[]"


def test_evaluator_import_is_declarative_and_does_not_import_product_facade() -> None:
    script = """
import json
import sys
import benchmark_evaluator.enterprise_understanding  # noqa: F401
print(json.dumps(sorted(name for name in sys.modules if name == 'ai_test_asset_center.enterprise_knowledge_center')))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.stdout.strip() == "[]"
