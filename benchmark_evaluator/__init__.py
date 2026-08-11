"""Benchmark evaluator for hidden bug ground truth.

The evaluator package installs its commercial scoring projection at import time
so every caller of ``benchmark_compute.compute_benchmark`` receives the same
truthful seeded-benchmark semantics.  Matching remains owned by
``benchmark_compute``; only metric naming/measurement status is governed here.
"""

from .commercial_scoring_contract import install_benchmark_compute_contract

install_benchmark_compute_contract()

__all__ = ["install_benchmark_compute_contract"]
