#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

root = Path("_comparison")
b = json.loads(next(root.glob("baseline/**/baseline.summary.json")).read_text("utf-8"))
c = json.loads(next(root.glob("candidate/**/candidate.summary.json")).read_text("utf-8"))
bs = json.loads(next(root.glob("baseline/**/baseline.external_score.json")).read_text("utf-8"))
cs = json.loads(next(root.glob("candidate/**/candidate.external_score.json")).read_text("utf-8"))
keys = ["generated", "selected", "compiled", "executed", "oracle_violations", "formal_deliveries"]
comparison = {
    "baseline": b,
    "candidate": c,
    "delta": {k: int(c.get(k) or 0) - int(b.get(k) or 0) for k in keys},
    "external_score": {
        "baseline": bs,
        "candidate": cs,
        "matched_delta": int(cs.get("matched_total") or 0) - int(bs.get("matched_total") or 0),
        "coverage_delta": round(float(cs.get("coverage_rate") or 0) - float(bs.get("coverage_rate") or 0), 4),
        "false_positive_delta": int(cs.get("estimated_false_positive_total") or 0) - int(bs.get("estimated_false_positive_total") or 0),
    },
}
out = Path("_ci_results")
out.mkdir(exist_ok=True)
(out / "comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), "utf-8")
lines = ["# Baseline vs candidate real benchmark", "", "| Metric | Baseline | Candidate | Delta |", "|---|---:|---:|---:|"]
for k in keys:
    lines.append(f"| {k} | {b.get(k, 0)} | {c.get(k, 0)} | {comparison['delta'][k]:+d} |")
lines += [
    "",
    "| External score | Baseline | Candidate | Delta |",
    "|---|---:|---:|---:|",
    f"| matched bugs | {bs.get('matched_total', 0)} | {cs.get('matched_total', 0)} | {comparison['external_score']['matched_delta']:+d} |",
    f"| coverage | {bs.get('coverage_rate', 0)} | {cs.get('coverage_rate', 0)} | {comparison['external_score']['coverage_delta']:+.4f} |",
    f"| estimated false positives | {bs.get('estimated_false_positive_total', 0)} | {cs.get('estimated_false_positive_total', 0)} | {comparison['external_score']['false_positive_delta']:+d} |",
]
(out / "comparison.md").write_text("\n".join(lines) + "\n", "utf-8")
print(json.dumps(comparison, ensure_ascii=False, indent=2))
