"""Enterprise Campaign contract used by the V12 discovery pipeline."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


MAX_SLICES_PER_ROUND = 15
MAX_AUTOMATIC_ROUNDS = 12


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _text(value: Any, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def _hash(value: Any, length: int = 24) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", _text(value, 120)).strip("._") or "unscoped"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def source_snapshot_hash(prd_text: str, api_spec_text: str, db_schema_text: str, scope_id: str, environment_ref: str) -> str:
    return _hash({
        "prd": hashlib.sha256(str(prd_text or "").encode()).hexdigest(),
        "api": hashlib.sha256(str(api_spec_text or "").encode()).hexdigest(),
        "schema": hashlib.sha256(str(db_schema_text or "").encode()).hexdigest(),
        "scope_id": scope_id,
        "environment_ref": environment_ref,
    }, 64)


def has_real_confirmation_receipt(item: Any) -> bool:
    """Only a complete executed delivery receipt may close a behavior slice."""
    row = _as_dict(item)
    if row.get("simulation") is True:
        return False
    if _text(row.get("execution_status")).lower() != "executed":
        return False
    if _text(row.get("confirmation_status") or row.get("verdict")).lower() != "confirmed":
        return False
    if row.get("gate_passed") is not True:
        return False
    evidence = _as_dict(row.get("evidence") or row.get("raw_evidence"))
    required = ("request", "response", "assertion", "timestamp", "target", "actor", "reproduction_steps")
    return all(evidence.get(key) for key in required)


@dataclass
class EnterpriseCampaign:
    campaign_id: str
    project_id: str
    scope_id: str
    environment_ref: str
    source_snapshot_hash: str
    policy_version: str = ""
    status: str = "active"
    run_count: int = 0
    round_count: int = 0
    slice_budget: int = MAX_SLICES_PER_ROUND
    automatic_round_limit: int = 3
    attempted_slice_ids: list[str] = field(default_factory=list)
    confirmation_receipts: dict[str, str] = field(default_factory=dict)
    coverage_deferred_reason: str = ""
    next_campaign_reason: str = ""
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    created_at_utc: str = field(default_factory=_now)
    updated_at_utc: str = field(default_factory=_now)

    @classmethod
    def create(cls, project_id: str, scope_id: str, environment_ref: str, snapshot: str, *, policy_version: str = "", slice_budget: int = 15, automatic_round_limit: int = 3) -> "EnterpriseCampaign":
        campaign_id = "CMP_" + _hash({"project": project_id, "scope": scope_id, "environment": environment_ref, "snapshot": snapshot, "policy": policy_version})
        return cls(
            campaign_id=campaign_id,
            project_id=_text(project_id),
            scope_id=_text(scope_id),
            environment_ref=_text(environment_ref),
            source_snapshot_hash=_text(snapshot, 80),
            policy_version=_text(policy_version, 120),
            slice_budget=max(1, min(int(slice_budget or 1), MAX_SLICES_PER_ROUND)),
            automatic_round_limit=max(1, min(int(automatic_round_limit or 1), MAX_AUTOMATIC_ROUNDS)),
        )

    def history_item(self) -> dict[str, Any]:
        return {"behavior_slice_ledger": {"campaign_id": self.campaign_id, "selected_slice_ids": list(self.attempted_slice_ids), "attempted_slice_ids": list(self.attempted_slice_ids), "round": self.round_count}}

    def record_cycle(self, *, round_number: int, selection: dict[str, Any], findings: Iterable[Any], coverage_gap_count: int, execution_status: str) -> None:
        selected = [_text(value) for value in selection.get("selected_slice_ids", []) if _text(value)]
        self.attempted_slice_ids = list(dict.fromkeys(self.attempted_slice_ids + selected))
        self.round_count = max(self.round_count, int(round_number or 0))
        for item in findings:
            if has_real_confirmation_receipt(item):
                slice_id = _text(_as_dict(item).get("behavior_slice_id") or _as_dict(item).get("slice_id"))
                if slice_id:
                    self.confirmation_receipts[slice_id] = _hash({"slice": slice_id, "evidence": _as_dict(item).get("evidence_id"), "time": _as_dict(item).get("timestamp")}, 32)
        reason = _text(selection.get("stop_reason"), 240)
        remaining = max(0, int(selection.get("remaining_slice_count") or 0))
        if reason == "all_source_bound_slices_confirmed":
            self.status = "completed"
            self.coverage_deferred_reason = ""
            self.next_campaign_reason = ""
        elif reason in {"configured_round_limit_reached", "all_pending_slices_attempted_needs_new_evidence_or_policy", "no_remaining_slice_in_configured_round"} or (remaining > 0 and not selection.get("next_round")):
            self.status = "coverage_deferred"
            self.coverage_deferred_reason = reason or "automatic_campaign_budget_exhausted"
            self.next_campaign_reason = "source_binding_or_runtime_evidence_required" if coverage_gap_count else "new_runtime_evidence_fixture_actor_or_policy_required"
        elif reason == "no_source_bound_behavior_slices":
            self.status = "blocked"
            self.coverage_deferred_reason = reason
            self.next_campaign_reason = "source_assets_or_runtime_observation_required"
        else:
            self.status = "active"
        self.audit_events.append({"at_utc": _now(), "event": "cycle", "round": self.round_count, "selected": len(selected), "confirmed": len(self.confirmation_receipts), "remaining": remaining, "execution_status": _text(execution_status, 80), "reason": reason, "status": self.status})
        self.audit_events = self.audit_events[-200:]
        self.updated_at_utc = _now()

    def public_contract(self) -> dict[str, Any]:
        return {"campaign_id": self.campaign_id, "campaign_status": self.status, "project_id": self.project_id, "scope_id": self.scope_id, "environment_ref": self.environment_ref, "source_snapshot_hash": self.source_snapshot_hash, "policy_version": self.policy_version, "run_count": self.run_count, "round_count": self.round_count, "slice_budget": self.slice_budget, "automatic_round_limit": self.automatic_round_limit, "attempted_slice_count": len(self.attempted_slice_ids), "confirmed_slice_count": len(self.confirmation_receipts), "coverage_deferred_reason": self.coverage_deferred_reason, "next_campaign_reason": self.next_campaign_reason}

    def to_dict(self) -> dict[str, Any]:
        return {**self.public_contract(), "attempted_slice_ids": self.attempted_slice_ids, "confirmation_receipts": self.confirmation_receipts, "audit_events": self.audit_events, "created_at_utc": self.created_at_utc, "updated_at_utc": self.updated_at_utc}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EnterpriseCampaign":
        return cls(
            campaign_id=_text(value.get("campaign_id"), 80), project_id=_text(value.get("project_id")), scope_id=_text(value.get("scope_id")), environment_ref=_text(value.get("environment_ref")), source_snapshot_hash=_text(value.get("source_snapshot_hash"), 80), policy_version=_text(value.get("policy_version"), 120), status=_text(value.get("campaign_status") or value.get("status"), 80) or "active", run_count=max(0, int(value.get("run_count") or 0)), round_count=max(0, int(value.get("round_count") or 0)), slice_budget=max(1, min(int(value.get("slice_budget") or 15), MAX_SLICES_PER_ROUND)), automatic_round_limit=max(1, min(int(value.get("automatic_round_limit") or 3), MAX_AUTOMATIC_ROUNDS)), attempted_slice_ids=[_text(item) for item in value.get("attempted_slice_ids", []) if _text(item)], confirmation_receipts={_text(key): _text(item, 80) for key, item in _as_dict(value.get("confirmation_receipts")).items() if _text(key)}, coverage_deferred_reason=_text(value.get("coverage_deferred_reason"), 240), next_campaign_reason=_text(value.get("next_campaign_reason"), 240), audit_events=[_as_dict(item) for item in value.get("audit_events", []) if isinstance(item, dict)][-200:], created_at_utc=_text(value.get("created_at_utc"), 64) or _now(), updated_at_utc=_text(value.get("updated_at_utc"), 64) or _now())


class EnterpriseCampaignStore:
    def __init__(self, root: Path, project_id: str):
        self.path = Path(root) / "platform_workspace" / _safe(project_id) / "defect_discovery" / "campaigns"

    def open_or_create(self, campaign: EnterpriseCampaign) -> tuple[EnterpriseCampaign, str]:
        path = self.path / f"{_safe(campaign.campaign_id)}.json"
        if path.exists():
            try:
                restored = EnterpriseCampaign.from_dict(json.loads(path.read_text(encoding="utf-8")))
                restored.run_count += 1
                restored.updated_at_utc = _now()
                return restored, "resumed"
            except Exception:
                pass
        campaign.run_count = 1
        return campaign, "created"

    def save(self, campaign: EnterpriseCampaign) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / f"{_safe(campaign.campaign_id)}.json").write_text(json.dumps(campaign.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
