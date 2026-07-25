"""Pre-merge regression gate: the known-failure set may shrink, never grow.

The suite currently carries a set of failing tests that are being worked
down. This gate freezes that set. A change may fix tests (the set shrinks)
but may not break a test that passes today, and may not silently disable a
guardrail by editing it away.

Usage::

    python tools/regression_gate.py            # enforce (CI / pre-merge)
    python tools/regression_gate.py --update   # re-baseline after real fixes

``--update`` is deliberately explicit. It rewrites the baseline from an
observed run and prints the delta, so shrinking the set is a reviewable act
rather than a side effect.

Exit codes: 0 pass, 1 new failures or collection error, 2 baseline stale.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / "regression_gate_baseline.json"
SCHEMA = "qualibug.regression-gate-baseline.v1"


def run_suite() -> tuple[set[str], str]:
    # -rf keeps the "FAILED <id>" summary lines this gate parses. Never pass
    # -rN here: it suppresses them and the gate reports a clean run instead.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:randomly", "--no-header", "-rf"],
        cwd=ROOT, capture_output=True, text=True, errors="replace",
    )
    output = proc.stdout + proc.stderr
    failures = {m.group(1) for m in re.finditer(r"^FAILED (\S+)", output, re.M)}

    reported = re.search(r"(\d+) failed", output)
    expected = int(reported.group(1)) if reported else 0
    if len(failures) != expected:
        raise SystemExit(
            f"gate self-check failed: parsed {len(failures)} failure ids but pytest "
            f"reported {expected} failed. The summary format changed; fix the parser "
            "before trusting this gate."
        )
    return failures, output


def load_baseline() -> set[str]:
    if not BASELINE.is_file():
        raise SystemExit(
            f"baseline missing: {BASELINE}\n"
            "Create it with: python tools/regression_gate.py --update"
        )
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA:
        raise SystemExit(f"unexpected baseline schema: {data.get('schema_version')}")
    return set(data.get("known_failures") or [])


def write_baseline(failures: set[str]) -> None:
    BASELINE.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA,
                "note": (
                    "Tests known to fail. This set may only shrink. Regenerate "
                    "with `python tools/regression_gate.py --update` after a real fix."
                ),
                "known_failure_count": len(failures),
                "known_failures": sorted(failures),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="re-baseline from an observed run")
    args = parser.parse_args()

    observed, output = run_suite()
    if "during collection" in output:
        print("GATE FAIL: collection error — the suite could not be assembled.")
        print("\n".join(ln for ln in output.splitlines() if "ERROR" in ln)[:4000])
        return 1

    if args.update:
        seeding = not BASELINE.is_file()
        previous = set() if seeding else load_baseline()
        write_baseline(observed)
        if seeding:
            print(f"baseline seeded with {len(observed)} known failures.")
            return 0
        fixed, added = sorted(previous - observed), sorted(observed - previous)
        print(f"baseline updated: {len(previous)} -> {len(observed)} known failures")
        for t in fixed:
            print("  fixed  ", t)
        for t in added:
            print("  ADDED  ", t)
        if added:
            print("\nRefusing to call this a clean update: the set grew.")
            return 1
        return 0

    baseline = load_baseline()
    new = sorted(observed - baseline)
    recovered = sorted(baseline - observed)

    if recovered:
        print(f"{len(recovered)} baselined test(s) now pass — shrink the baseline:")
        for t in recovered:
            print("  +", t)
        print("  run: python tools/regression_gate.py --update\n")

    if new:
        print(f"GATE FAIL: {len(new)} test(s) broke that passed at the baseline:")
        for t in new:
            print("  -", t)
        return 1

    print(f"GATE PASS: no new failures ({len(observed)} known failures unchanged).")
    return 2 if recovered else 0


if __name__ == "__main__":
    raise SystemExit(main())
