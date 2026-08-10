"""Learning-effect observation: executed-set diff across discovery rounds.

Purpose
-------
The learning loop's internal counts (patterns stored, obligations boosted,
resolver reorders) are mechanism observables, not effect observables. The
effect of learning is a change in what gets *executed* and *delivered*:
``BLOCKED_MISSING_BINDING`` counts falling, executed obligations rising,
new canonical defects / delivery occurrences appearing.

This module reads the per-round immutable trace ledgers
(``platform_outputs/<project>/discovery_evolution/trace_ledgers/``), groups
rounds by campaign (campaign_id is deterministic across rounds on the same
project/scope/environment/source snapshot), and diffs adjacent rounds:

- executed obligation id sets (attempts with a resolved execution),
- terminal status counts,
- blocked reason-code distribution,
- delivery occurrence finding ids,
- canonical defect ids.

Outputs
-------
- ``platform_outputs/<project>/learning_effect/round_diff_<prev_run>_<next_run>.json``
  per adjacent pair;
- ``platform_outputs/<project>/learning_effect/learning_effect_report.json``
  aggregated across all campaigns/rounds, with the direction of change
  (executed set grew/shrunk, blocked fell/rose).

Honesty contract
----------------
This is diagnostic observability only. Diff numbers are never presented as
recall/precision/commercial capability; they are the input for pairing a
learning change with a controlled re-run (per AGENTS.md promotion rules).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPORT_SCHEMA = "qualibug.learning-effect-report.v1"
_DIFF_SCHEMA = "qualibug.learning-effect-round-diff.v1"

# Terminal statuses that count as "executed" for the executed-set diff.
_EXECUTED_TERMINAL_STATUSES = {
    "DELIVERABLE",
    "REJECTED",
    "PASS",
    "HARNESS_FAILED",
    "FAILED",
    "COMPLETE",
}
# Terminal statuses that count as "blocked" (never reached transport).
# DEFERRED is deliberately excluded: it means "not selected into the plan"
# (budget/selection), not an execution blockage.
_BLOCKED_TERMINAL_STATUSES = {
    "BLOCKED",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ledger_dir(root: Path, project: str) -> Path:
    return (
        root / "platform_outputs" / project / "discovery_evolution" / "trace_ledgers"
    )


def _effect_dir(root: Path, project: str) -> Path:
    return root / "platform_outputs" / project / "learning_effect"


def load_round_ledgers(project: str, root: Path | None = None) -> list[dict[str, Any]]:
    """Load all persisted trace ledgers for a project (newest last).

    Trace ledgers are per-round immutable (persisted by the scan close hook),
    so this is the authoritative round history for effect observation.

    P0-4 Dual Read (SPEC §33): artifactized ledgers (referenced by Run
    Manifests) are hydrated from the ArtifactStore first; legacy
    ``*.trace-ledger.json`` files remain the fallback for older runs.
    """
    root = root or Path(__file__).resolve().parents[1]
    try:
        from .trace_artifactization import load_round_trace_ledgers

        return load_round_trace_ledgers(project, root)
    except Exception:
        pass
    base = _ledger_dir(root, project)
    ledgers: list[dict[str, Any]] = []
    if not base.exists():
        return ledgers
    for path in sorted(base.glob("*/*.trace-ledger.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                ledgers.append(data)
        except Exception:
            continue
    ledgers.sort(key=lambda item: _text(item.get("created_at_utc")))
    return ledgers


def _attempts(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    attempts = _list(ledger.get("attempts"))
    if attempts:
        return [a for a in attempts if isinstance(a, dict)]
    # Fallback: attempts may be nested under a keyed map in older ledgers.
    attempts_map = _dict(ledger.get("attempt_map"))
    return [a for a in attempts_map.values() if isinstance(a, dict)]


def round_effect_snapshot(ledger: dict[str, Any]) -> dict[str, Any]:
    """Project one round ledger into the diffable effect snapshot."""
    attempts = _attempts(ledger)
    executed_ids: list[str] = []
    blocked_ids: list[str] = []
    reason_counts: dict[str, int] = {}
    for attempt in attempts:
        obligation_id = _text(
            attempt.get("executed_obligation_id") or attempt.get("obligation_id")
        )
        terminal_status = _text(attempt.get("terminal_status")).upper()
        reason_code = _text(attempt.get("reason_code"))
        if reason_code:
            reason_counts[reason_code] = reason_counts.get(reason_code, 0) + 1
        if not obligation_id:
            continue
        if terminal_status in _EXECUTED_TERMINAL_STATUSES:
            executed_ids.append(obligation_id)
        elif terminal_status in _BLOCKED_TERMINAL_STATUSES:
            blocked_ids.append(obligation_id)
    terminal_status_counts = dict(_dict(ledger.get("terminal_status_counts")))
    return {
        "run_id": _text(ledger.get("run_id")),
        "created_at_utc": _text(ledger.get("created_at_utc")),
        "terminal_status_counts": terminal_status_counts,
        "executed_obligation_ids": sorted(set(executed_ids)),
        "blocked_obligation_ids": sorted(set(blocked_ids)),
        "blocked_reason_counts": dict(
            sorted(reason_counts.items(), key=lambda item: -item[1])
        ),
        "delivery_occurrence_finding_ids": sorted(
            {
                _text(item)
                for item in _list(ledger.get("delivery_occurrence_finding_ids"))
                if _text(item)
            }
        ),
        "canonical_defect_ids": sorted(
            {
                _text(item)
                for item in _list(ledger.get("canonical_defect_ids"))
                if _text(item)
            }
        ),
    }


def diff_rounds(prev_ledger: dict[str, Any], next_ledger: dict[str, Any]) -> dict[str, Any]:
    """Diff two adjacent rounds of the same campaign.

    Returns executed-set add/remove, blocked add/remove, reason-code delta,
    delivery/defect add/remove — the observables that pair a learning change
    with an outcome.
    """
    prev = round_effect_snapshot(prev_ledger)
    curr = round_effect_snapshot(next_ledger)

    def _delta(before: list[str], after: list[str]) -> dict[str, list[str]]:
        before_set, after_set = set(before), set(after)
        return {
            "added": sorted(after_set - before_set),
            "removed": sorted(before_set - after_set),
            "count_before": len(before_set),
            "count_after": len(after_set),
            "delta": len(after_set) - len(before_set),
        }

    all_reasons = sorted(
        set(prev["blocked_reason_counts"]) | set(curr["blocked_reason_counts"])
    )
    reason_delta = {
        reason: int(curr["blocked_reason_counts"].get(reason, 0))
        - int(prev["blocked_reason_counts"].get(reason, 0))
        for reason in all_reasons
    }
    prev_blocked_total = sum(prev["blocked_reason_counts"].values())
    curr_blocked_total = sum(curr["blocked_reason_counts"].values())
    return {
        "schema_version": _DIFF_SCHEMA,
        "campaign_id": _text(next_ledger.get("campaign_id")),
        "prev_run_id": prev["run_id"],
        "next_run_id": curr["run_id"],
        "prev_created_at_utc": prev["created_at_utc"],
        "next_created_at_utc": curr["created_at_utc"],
        "executed_obligations": _delta(prev["executed_obligation_ids"], curr["executed_obligation_ids"]),
        "blocked_obligations": _delta(prev["blocked_obligation_ids"], curr["blocked_obligation_ids"]),
        "blocked_reason_delta": reason_delta,
        "blocked_total": {
            "before": prev_blocked_total,
            "after": curr_blocked_total,
            "delta": curr_blocked_total - prev_blocked_total,
        },
        "terminal_status_counts_after": curr["terminal_status_counts"],
        "delivery_occurrence_findings": _delta(
            prev["delivery_occurrence_finding_ids"], curr["delivery_occurrence_finding_ids"]
        ),
        "canonical_defects": _delta(
            prev["canonical_defect_ids"], curr["canonical_defect_ids"]
        ),
    }


def build_learning_effect_report(
    project: str, root: Path | None = None
) -> dict[str, Any]:
    """Aggregate per-campaign round diffs into the effect report.

    Returns the report dict; callers persist it via
    ``write_learning_effect_report``.
    """
    ledgers = load_round_ledgers(project, root)
    if not ledgers:
        return {
            "schema_version": _REPORT_SCHEMA,
            "project": project,
            "status": "NO_ROUNDS",
            "campaigns": [],
            "generated_at_utc": "",
        }
    campaigns: dict[str, list[dict[str, Any]]] = {}
    for ledger in ledgers:
        campaigns.setdefault(_text(ledger.get("campaign_id")), []).append(ledger)
    campaign_reports = []
    for campaign_id, round_ledgers in campaigns.items():
        diffs = [
            diff_rounds(round_ledgers[i], round_ledgers[i + 1])
            for i in range(len(round_ledgers) - 1)
        ]
        campaign_reports.append({
            "campaign_id": campaign_id,
            "round_count": len(round_ledgers),
            "run_ids": [_text(r.get("run_id")) for r in round_ledgers],
            "round_diffs": diffs,
        })
    return {
        "schema_version": _REPORT_SCHEMA,
        "project": project,
        "status": "OK",
        "campaign_count": len(campaign_reports),
        "round_count": len(ledgers),
        "campaigns": campaign_reports,
        "generated_at_utc": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def write_learning_effect_report(
    project: str, root: Path | None = None
) -> dict[str, Any]:
    """Persist the effect report and per-pair diff files.

    Called at scan close so the learning effect is observable after every
    round. Returns the report dict; failures stay visible in the report.
    """
    try:
        report = build_learning_effect_report(project, root)
        out_dir = _effect_dir(root or Path(__file__).resolve().parents[1], project)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "learning_effect_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for campaign in report.get("campaigns") or []:
            for diff in campaign.get("round_diffs") or []:
                name = f"round_diff_{diff.get('prev_run_id')}_{diff.get('next_run_id')}.json"
                (out_dir / name).write_text(
                    json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        return report
    except Exception as exc:
        return {
            "schema_version": _REPORT_SCHEMA,
            "project": project,
            "status": "FAILED",
            "failure": f"{type(exc).__name__}:{str(exc)[:200]}",
            "campaigns": [],
            "generated_at_utc": "",
        }
