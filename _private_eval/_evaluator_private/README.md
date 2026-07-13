# Evaluator-private ground truth
This directory holds the frozen 131-bug ground truth copied from the desktop enterprise benchmark.
Runtime discovery must never read these files. Only tools/discovery_evaluation.py may load them.
Do not replace with the 71-bug QualiBug-AI/benchmark_mall copy when comparing champion/candidate.
