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


class CampaignPersistenceError(RuntimeError):
    """Campaign state cannot be safely resumed or persisted."""


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


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _campaign_identity_payload(
    project_id: str,
    scope_id: str,
    environment_ref: str,
    snapshot: str,
    source_id: str,
    source_hash: str,
    *,
    rerun_key: str = "",
) -> dict[str, str]:
    payload = {
        "project": _text(project_id),
        "scope": _text(scope_id),
        "environment": _text(environment_ref),
        "snapshot": _text(snapshot, 80),
        "source_id": _text(source_id) or f"source_snapshot:{_text(source_hash, 128)[:24]}",
        "source_hash": _text(source_hash, 128) or _text(snapshot, 80),
    }
    if _text(rerun_key, 120):
        payload["rerun_key"] = _text(rerun_key, 120)
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


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
    evidence = _as_dict(row.get("evidence"))
    raw_evidence = _as_dict(row.get("raw_evidence"))
    reproduction = _as_dict(row.get("reproduction"))
    request_raw = _as_dict(raw_evidence.get("request_raw"))
    response_raw = _as_dict(raw_evidence.get("response_raw"))
    db_snapshot = _as_dict(raw_evidence.get("db_snapshot"))

    has_request = bool(
        evidence.get("request")
        or (request_raw.get("method") and request_raw.get("path"))
        or (reproduction.get("method") and reproduction.get("path"))
    )
    has_response = bool(
        evidence.get("response")
        or response_raw.get("status_code")
        or response_raw.get("body")
        or (db_snapshot.get("before") and db_snapshot.get("after"))
        or db_snapshot.get("assertion")
    )
    has_assertion = bool(
        evidence.get("assertion")
        or db_snapshot.get("assertion")
        or any(_text(item) for item in _as_list(row.get("failed_assertions")))
    )
    has_timestamp = bool(
        evidence.get("timestamp")
        or raw_evidence.get("timestamp")
        or row.get("timestamp")
        or row.get("last_verified_at")
    )
    has_target = bool(
        evidence.get("target")
        or request_raw.get("path")
        or reproduction.get("path")
    )
    has_actor = bool(
        evidence.get("actor")
        or request_raw.get("actor")
        or reproduction.get("actor")
        or row.get("actor")
    )
    has_reproduction_steps = bool(
        evidence.get("reproduction_steps")
        or row.get("reproduction_steps")
        or reproduction.get("reproduction_steps")
    )
    return all((
        has_request,
        has_response,
        has_assertion,
        has_timestamp,
        has_target,
        has_actor,
        has_reproduction_steps,
    ))


@dataclass
class EnterpriseCampaign:
    campaign_id: str
    project_id: str
    scope_id: str
    environment_ref: str
    source_snapshot_hash: str
    source_id: str = ""
    source_hash: str = ""
    policy_version: str = ""
    lineage_campaign_id: str = ""
    rerun_key: str = ""
    rerun_reason: str = ""
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
    def create(
        cls,
        project_id: str,
        scope_id: str,
        environment_ref: str,
        snapshot: str,
        *,
        source_id: str = "",
        source_hash: str = "",
        policy_version: str = "",
        rerun_key: str = "",
        rerun_reason: str = "",
        slice_budget: int = 15,
        automatic_round_limit: int = 3,
    ) -> "EnterpriseCampaign":
        resolved_snapshot = _text(snapshot, 80)
        resolved_source_hash = _text(source_hash, 128) or resolved_snapshot
        resolved_source_id = _text(source_id) or f"source_snapshot:{resolved_source_hash[:24]}"
        lineage_payload = _campaign_identity_payload(
            project_id,
            scope_id,
            environment_ref,
            resolved_snapshot,
            resolved_source_id,
            resolved_source_hash,
        )
        lineage_campaign_id = "CMP_" + _hash(lineage_payload)
        campaign_id = "CMP_" + _hash({
            **lineage_payload,
            **({"rerun_key": _text(rerun_key, 120)} if _text(rerun_key, 120) else {}),
        })
        return cls(
            campaign_id=campaign_id,
            project_id=_text(project_id),
            scope_id=_text(scope_id),
            environment_ref=_text(environment_ref),
            source_snapshot_hash=resolved_snapshot,
            source_id=resolved_source_id,
            source_hash=resolved_source_hash,
            policy_version=_text(policy_version, 120),
            lineage_campaign_id=lineage_campaign_id,
            rerun_key=_text(rerun_key, 120),
            rerun_reason=_text(rerun_reason, 240),
            slice_budget=max(1, min(int(slice_budget or 1), MAX_SLICES_PER_ROUND)),
            automatic_round_limit=max(1, min(int(automatic_round_limit or 1), MAX_AUTOMATIC_ROUNDS)),
        )

    def history_item(self) -> dict[str, Any]:
        return {
            "behavior_slice_ledger": {
                "campaign_id": self.campaign_id,
                "selected_slice_ids": [],
                "attempted_slice_ids": list(self.attempted_slice_ids),
                "confirmed_slice_ids": sorted(self.confirmation_receipts),
                "round": self.round_count,
            }
        }

    def record_cycle(self, *, round_number: int, selection: dict[str, Any], findings: Iterable[Any], coverage_gap_count: int, execution_status: str, attempted_slice_ids: Iterable[str] | None = None) -> None:
        selected = [_text(value) for value in selection.get("selected_slice_ids", []) if _text(value)]
        if attempted_slice_ids is None:
            realized_attempts = selected if _text(execution_status, 80).lower() == "completed" else []
        else:
            realized_attempts = [_text(value) for value in attempted_slice_ids if _text(value)]
        self.attempted_slice_ids = list(dict.fromkeys(self.attempted_slice_ids + realized_attempts))
        if realized_attempts or _text(execution_status, 80).lower() == "completed":
            self.round_count = max(self.round_count, int(round_number or 0))
        for item in findings:
            if has_real_confirmation_receipt(item):
                slice_id = _text(_as_dict(item).get("behavior_slice_id") or _as_dict(item).get("slice_id"))
                if slice_id:
                    self.confirmation_receipts[slice_id] = _hash({
                        "slice": slice_id,
                        "evidence": _as_dict(item).get("evidence_id"),
                        "time": _as_dict(item).get("timestamp"),
                    }, 32)
        reason = _text(selection.get("stop_reason"), 240)
        remaining = max(0, int(selection.get("remaining_slice_count") or 0))
        if reason.startswith("campaign_") and self.status in {"coverage_deferred", "completed", "blocked"}:
            pass
        elif reason == "all_source_bound_slices_confirmed":
            self.status = "completed"
            self.coverage_deferred_reason = ""
            self.next_campaign_reason = ""
        elif remaining == 0 and not selection.get("next_round") and _text(execution_status, 80).lower() == "completed":
            self.status = "completed"
            self.coverage_deferred_reason = ""
            self.next_campaign_reason = ""
        elif reason in {
            "configured_round_limit_reached",
            "all_pending_slices_attempted_needs_new_evidence_or_policy",
            "no_remaining_slice_in_configured_round",
        } or (remaining > 0 and not selection.get("next_round")):
            self.status = "coverage_deferred"
            self.coverage_deferred_reason = reason or "automatic_campaign_budget_exhausted"
            self.next_campaign_reason = "source_binding_or_runtime_evidence_required" if coverage_gap_count else "new_runtime_evidence_fixture_actor_or_policy_required"
        elif reason == "no_source_bound_behavior_slices":
            self.status = "blocked"
            self.coverage_deferred_reason = reason
            self.next_campaign_reason = "source_assets_or_runtime_observation_required"
        else:
            self.status = "active"
        self.audit_events.append({
            "at_utc": _now(),
            "event": "cycle",
            "round": self.round_count,
            "selected": len(selected),
            "confirmed": len(self.confirmation_receipts),
            "remaining": remaining,
            "execution_status": _text(execution_status, 80),
            "reason": reason,
            "status": self.status,
        })
        self.audit_events = self.audit_events[-200:]
        self.updated_at_utc = _now()

    def public_contract(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "campaign_status": self.status,
            "project_id": self.project_id,
            "scope_id": self.scope_id,
            "environment_ref": self.environment_ref,
            "source_id": self.source_id,
            "source_hash": self.source_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
            "policy_version": self.policy_version,
            "lineage_campaign_id": self.lineage_campaign_id or self.campaign_id,
            "rerun_key": self.rerun_key,
            "rerun_reason": self.rerun_reason,
            "run_count": self.run_count,
            "round_count": self.round_count,
            "slice_budget": self.slice_budget,
            "automatic_round_limit": self.automatic_round_limit,
            "attempted_slice_count": len(self.attempted_slice_ids),
            "confirmed_slice_count": len(self.confirmation_receipts),
            "coverage_deferred_reason": self.coverage_deferred_reason,
            "next_campaign_reason": self.next_campaign_reason,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.public_contract(),
            "attempted_slice_ids": self.attempted_slice_ids,
            "confirmation_receipts": self.confirmation_receipts,
            "audit_events": self.audit_events,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EnterpriseCampaign":
        snapshot = _text(value.get("source_snapshot_hash"), 80)
        source_hash = _text(value.get("source_hash"), 128) or snapshot
        source_id = _text(value.get("source_id")) or f"source_snapshot:{source_hash[:24]}"
        return cls(
            campaign_id=_text(value.get("campaign_id"), 80),
            project_id=_text(value.get("project_id")),
            scope_id=_text(value.get("scope_id")),
            environment_ref=_text(value.get("environment_ref")),
            source_snapshot_hash=snapshot,
            source_id=source_id,
            source_hash=source_hash,
            policy_version=_text(value.get("policy_version"), 120),
            lineage_campaign_id=_text(value.get("lineage_campaign_id"), 80) or _text(value.get("campaign_id"), 80),
            rerun_key=_text(value.get("rerun_key"), 120),
            rerun_reason=_text(value.get("rerun_reason"), 240),
            status=_text(value.get("campaign_status") or value.get("status"), 80) or "active",
            run_count=max(0, int(value.get("run_count") or 0)),
            round_count=max(0, int(value.get("round_count") or 0)),
            slice_budget=max(1, min(int(value.get("slice_budget") or 15), MAX_SLICES_PER_ROUND)),
            automatic_round_limit=max(1, min(int(value.get("automatic_round_limit") or 3), MAX_AUTOMATIC_ROUNDS)),
            attempted_slice_ids=[_text(item) for item in value.get("attempted_slice_ids", []) if _text(item)],
            confirmation_receipts={_text(key): _text(item, 80) for key, item in _as_dict(value.get("confirmation_receipts")).items() if _text(key)},
            coverage_deferred_reason=_text(value.get("coverage_deferred_reason"), 240),
            next_campaign_reason=_text(value.get("next_campaign_reason"), 240),
            audit_events=[_as_dict(item) for item in value.get("audit_events", []) if isinstance(item, dict)][-200:],
            created_at_utc=_text(value.get("created_at_utc"), 64) or _now(),
            updated_at_utc=_text(value.get("updated_at_utc"), 64) or _now(),
        )


class EnterpriseCampaignStore:
    def __init__(self, root: Path, project_id: str):
        self.root = Path(root)
        self.project_id = _text(project_id)
        self.path = self.root / "platform_workspace" / _safe(self.project_id) / "defect_discovery" / "campaigns"

    @staticmethod
    def _assert_identity(stored: EnterpriseCampaign, expected: EnterpriseCampaign) -> None:
        keys = (
            "campaign_id",
            "project_id",
            "scope_id",
            "environment_ref",
            "source_snapshot_hash",
            "source_id",
            "source_hash",
            "rerun_key",
        )
        mismatches = [key for key in keys if getattr(stored, key) != getattr(expected, key)]
        if mismatches:
            raise CampaignPersistenceError("campaign_state_identity_mismatch:" + ",".join(mismatches))

    def open_or_create(self, campaign: EnterpriseCampaign) -> tuple[EnterpriseCampaign, str]:
        path = self.path / f"{_safe(campaign.campaign_id)}.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8") or "null")
            except (OSError, json.JSONDecodeError) as exc:
                raise CampaignPersistenceError(f"campaign_state_unreadable:{path.name}") from exc
            if not isinstance(payload, dict):
                raise CampaignPersistenceError(f"campaign_state_invalid:{path.name}")
            stored = EnterpriseCampaign.from_dict(payload)
            self._assert_identity(stored, campaign)
            stored.run_count += 1
            stored.updated_at_utc = _now()
            return stored, "resumed"
        campaign.run_count = 1
        return campaign, "created"

    def save(self, campaign: EnterpriseCampaign) -> None:
        path = self.path / f"{_safe(campaign.campaign_id)}.json"
        _atomic_write_json(path, campaign.to_dict())
        self._persist_command_center_projection(campaign)

    def _persist_command_center_projection(self, campaign: EnterpriseCampaign) -> None:
        """Publish only safe governance facts to the existing command-center channel."""
        path = self.root / "platform_outputs" / _safe(self.project_id) / "real_project" / "real_project_defect_data.json"
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
                if isinstance(loaded, dict):
                    payload = loaded
            except (OSError, json.JSONDecodeError):
                # Do not replace a corrupted command-center artifact with a partial snapshot.
                raise CampaignPersistenceError(f"command_center_snapshot_unreadable:{path.name}")
        latest = _as_dict(campaign.audit_events[-1]) if campaign.audit_events else {}
        current_run = {
            "status": _text(latest.get("execution_status"), 80) or campaign.status,
            "campaign_status": campaign.status,
            "round": campaign.round_count,
            "stop_reason": _text(latest.get("reason"), 240),
            "selected_slice_count": max(0, int(latest.get("selected") or 0)),
            "confirmed_slice_count": len(campaign.confirmation_receipts),
            "remaining_slice_count": max(0, int(latest.get("remaining") or 0)),
            "finished_at": campaign.updated_at_utc,
        }
        existing = _as_dict(payload.get("continuous_discovery_campaign"))
        payload["continuous_discovery_campaign"] = {
            **existing,
            "schema_version": "enterprise-campaign-projection-v1",
            "campaign": campaign.public_contract(),
            "summary": {
                "campaign_state": campaign.status,
                "campaign_id": campaign.campaign_id,
                "lineage_campaign_id": campaign.lineage_campaign_id or campaign.campaign_id,
                "coverage_deferred_reason": campaign.coverage_deferred_reason,
                "next_campaign_reason": campaign.next_campaign_reason,
                "attempted_slice_count": len(campaign.attempted_slice_ids),
                "confirmed_slice_count": len(campaign.confirmation_receipts),
            },
            "current_run": current_run,
            "updated_at_utc": campaign.updated_at_utc,
        }
        _atomic_write_json(path, payload)
