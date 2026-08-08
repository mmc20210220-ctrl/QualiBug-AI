# -*- coding: utf-8 -*-
"""Task 10 offline verification: apply grading to run3 persisted findings.

Reads the 259 findings from
platform_workspace/native_stable_e2e/evidence_bundles/evb_5a2c622bad9eede460693392/findings.json
and reports the new severity_grade / confidence distribution produced by
ai_test_asset_center.finding_risk_grading.apply_finding_grading.

Expected outcome: findings are no longer flat (P1 / 0.85 / validated-90) —
severity splits into critical/high/medium/low and confidence varies with the
evidence chain (occurrence multiplicity among others).
"""
from __future__ import annotations

import collections
import json
import sys

sys.path.insert(0, ".")

from ai_test_asset_center.finding_risk_grading import apply_finding_grading

BUNDLE = (
    "platform_workspace/native_stable_e2e/evidence_bundles/"
    "evb_5a2c622bad9eede460693392/findings.json"
)


def main() -> None:
    with open(BUNDLE, encoding="utf-8") as handle:
        findings = json.load(handle)
    graded = [apply_finding_grading(f) for f in findings]

    severity = collections.Counter(f.get("severity_grade") for f in graded)
    confidence = collections.Counter(f.get("evidence_quality", {}).get("confidence_grade") for f in graded)
    confidence_values = collections.Counter(f.get("evidence_quality", {}).get("confidence") for f in graded)
    rules = collections.Counter()
    for f in graded:
        for rule in f.get("grading", {}).get("rules_applied", []):
            rules[rule] += 1

    print(f"total findings: {len(graded)}")
    print("severity_grade:", dict(severity.most_common()))
    print("confidence_grade:", dict(confidence.most_common()))
    print("confidence values:", dict(sorted(confidence_values.items())))
    print("rules_applied:", dict(rules.most_common()))

    # Cross-tab severity x risk_family for the report.
    cross = collections.Counter(
        (f.get("risk_family"), f.get("severity_grade")) for f in graded
    )
    print("severity x risk_family:")
    for key in sorted(cross):
        print("  ", key, cross[key])

    # Guard: backward compatibility — original fields preserved.
    flat = sum(1 for f in graded if f.get("severity") != "P1")
    eq_kept = sum(
        1 for f in graded if f.get("evidence_quality", {}).get("score") == 90
        and f.get("evidence_quality", {}).get("level") == "validated"
    )
    print(f"severity field kept P1: {len(graded) - flat}/{len(graded)}")
    print(f"evidence_quality level/score preserved (validated/90): {eq_kept}/{len(graded)}")


if __name__ == "__main__":
    main()
