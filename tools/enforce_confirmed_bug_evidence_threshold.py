"""Fail when confirmed-bug promotions are not backed by runtime evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def enforce_confirmed_bug_evidence_threshold(
    report: dict[str, Any],
    *,
    min_confirmed_bug_evidence_ratio: float = 1.0,
    max_blocked_promotions: int = 0,
) -> None:
    ratio = float(report.get("confirmed_bug_evidence_ratio", 0.0) or 0.0)
    blocked = int(report.get("confirmed_bug_promotion_blocked", 0) or 0)

    failures: list[str] = []
    if ratio < min_confirmed_bug_evidence_ratio:
        failures.append(
            "confirmed_bug_evidence_ratio "
            f"{ratio:.4f} is below required {min_confirmed_bug_evidence_ratio:.4f}"
        )

    if blocked > max_blocked_promotions:
        failures.append(
            "confirmed_bug_promotion_blocked "
            f"{blocked} exceeds allowed {max_blocked_promotions}"
        )

    if failures:
        raise SystemExit("Confirmed bug evidence threshold failed:\n" + "\n".join(f"- {item}" for item in failures))


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce confirmed bug evidence promotion threshold")
    parser.add_argument("--input", required=True, help="Path to confirmed bug evidence report JSON")
    parser.add_argument(
        "--min-confirmed-bug-evidence-ratio",
        type=float,
        default=1.0,
        help="Minimum allowed evidence-backed ratio for confirmed-bug candidates",
    )
    parser.add_argument(
        "--max-blocked-promotions",
        type=int,
        default=0,
        help="Maximum allowed confirmed-bug candidates missing runtime evidence",
    )
    args = parser.parse_args()

    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    enforce_confirmed_bug_evidence_threshold(
        report,
        min_confirmed_bug_evidence_ratio=args.min_confirmed_bug_evidence_ratio,
        max_blocked_promotions=args.max_blocked_promotions,
    )
    print("Confirmed bug evidence threshold passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
