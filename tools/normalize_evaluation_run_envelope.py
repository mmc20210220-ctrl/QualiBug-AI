"""Normalize a funnel/live submission into an evaluator run envelope."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_test_asset_center.artifact_redactor import write_json_redacted


REQUIRED_OPS = (
    "wall_clock_seconds",
    "estimated_cost_usd",
    "request_count",
    "production_http_requests",
    "cleanup_failures",
    "safety_incidents",
    "dirty_test_environments",
    "execution_success_rate",
    "engine_success_rate",
    "duplicate_rate",
)


def _num(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_envelope(raw: dict) -> dict:
    ops = dict(raw.get("operational_metrics") or {})
    # Map diagnostic aliases → required contract fields without inventing success.
    if ops.get("wall_clock_seconds") is None and ops.get("elapsed_seconds") is not None:
        ops["wall_clock_seconds"] = _num(ops.get("elapsed_seconds"))
    # Unknown cost/usage must stay null → aggregate will mark incomplete (honest).
    for key in REQUIRED_OPS:
        if key not in ops:
            ops[key] = None
    # Fill only hard zeros that are observationally safe defaults when absent
    # and the run declares non-production (never invent cost or success rates).
    for key in ("production_http_requests", "safety_incidents"):
        if ops.get(key) is None:
            ops[key] = 0
    envelope = {
        "run_id": str(raw.get("run_id") or "unknown-run"),
        "policy_id": str(raw.get("policy_id") or "unversioned"),
        "evaluation_mode": str(raw.get("evaluation_mode") or "replay"),
        "pipeline_health": dict(raw.get("pipeline_health") or {}),
        "operational_metrics": ops,
        "scan_result": {
            "findings": list((raw.get("scan_result") or {}).get("findings") or []),
            "candidate_findings": list((raw.get("scan_result") or {}).get("candidate_findings") or []),
        },
    }
    if isinstance(raw.get("fixture_governance"), dict):
        envelope["fixture_governance"] = raw["fixture_governance"]
    return envelope


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    envelope = normalize_envelope(raw)
    write_json_redacted(Path(args.output), envelope)
    missing = [k for k in REQUIRED_OPS if envelope["operational_metrics"].get(k) is None]
    print(json.dumps({
        "output": args.output,
        "missing_operational_fields": missing,
        "findings": len(envelope["scan_result"]["findings"]),
        "pipeline_health": (envelope.get("pipeline_health") or {}).get("status"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
