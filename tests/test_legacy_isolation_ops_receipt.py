"""Legacy adapter must tolerate isolation traces without operational receipts."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from ai_test_asset_center.discovery_mainline import DiscoveryMainlineInputs
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.discovery_runtime import (
    DiscoveryPlanningBundle,
    adapt_legacy_champion_result,
)


class _FakeCampaign:
    def __init__(self, campaign_id: str) -> None:
        self.campaign_id = campaign_id

    def record_obligation_attempt_ledger(self, ledger: dict) -> None:
        self.ledger = ledger

    def public_contract(self) -> dict:
        return {"campaign_id": self.campaign_id, "campaign_status": "active"}


class _FakeStore:
    def save(self, campaign: object) -> None:
        self.campaign = campaign


def test_adapt_legacy_synthesizes_ops_receipt_when_sandbox_audit_present():
    contract = build_mainline_run_contract(
        campaign_id="CMP_test_isolation_ops",
        mainline_authority="legacy_champion",
        evaluation_mode="operational",
        policy_version="v1.0.0-baseline",
        run_id="run_test_isolation_ops",
        target_id="demo_target",
        environment_id="demo_test",
    )
    inputs = DiscoveryMainlineInputs(
        project="demo",
        root=Path("."),
        prd_text="",
        api_spec_text="### GET /api/orders\n",
        db_schema_text="",
        approved_base_url="http://127.0.0.1:8080",
        campaign_context={"mainline_authority": "legacy_champion"},
    )
    plan = DiscoveryPlanningBundle(
        mainline_run=contract,
        behavior_ir={"operations": []},
        obligations={"obligations": []},
        experiments={"experiments": []},
    )
    legacy = {
        "campaign": {"campaign_id": contract["campaign_id"]},
        "behavior_slice_ledger": {
            "campaign_id": contract["campaign_id"],
            "selected_slice_ids": ["BHV_iso_orders"],
            "attempted_slice_ids": ["BHV_iso_orders"],
            "confirmed_slice_ids": ["BHV_iso_orders"],
        },
        "behavior_slices": [
            {
                "slice_id": "BHV_iso_orders",
                "kind": "isolation",
                "endpoints": ["/api/orders/{id}"],
                "source_refs": [{"kind": "ownership_contract", "quote": "own orders"}],
            }
        ],
        "findings": [
            {
                "finding_id": "F_iso_1",
                "title": "[V12 TenantIsolationOracle] | [Data isolation probe]",
                "category": "isolation",
                "severity": "P0",
                "behavior_slice_id": "BHV_iso_orders",
                "scenario_id": "SCN_iso_1",
                "gate_passed": True,
                "bug_status": "confirmed",
                "confirmation_status": "confirmed",
                "execution_status": "EXECUTED",
                "source_refs": [{"kind": "ownership_contract", "quote": "own orders"}],
                "raw_evidence": {
                    "has_real_evidence": True,
                    "execution_trace": {"scenario_id": "SCN_iso_1"},
                },
            },
            {
                "finding_id": "F_iso_2",
                "title": "[V12 PermissionOracle] | [Data isolation probe]",
                "category": "isolation",
                "severity": "P0",
                "behavior_slice_id": "BHV_iso_orders",
                "scenario_id": "SCN_iso_1",
                "gate_passed": True,
                "bug_status": "confirmed",
                "confirmation_status": "confirmed",
                "execution_status": "EXECUTED",
                "source_refs": [{"kind": "ownership_contract", "quote": "own orders"}],
                "raw_evidence": {
                    "has_real_evidence": True,
                    "execution_trace": {"scenario_id": "SCN_iso_1"},
                },
            },
        ],
        "execution_trace_summaries": [
            {
                "scenario": {
                    "id": "SCN_iso_1",
                    "behavior_slice_id": "BHV_iso_orders",
                    "discovery_round": 1,
                },
                "execution_trace": {
                    "scenario_id": "SCN_iso_1",
                    "actor_role": "buyer",
                    "steps": [
                        {"method": "POST", "path": "/api/auth/login", "status": 200},
                        {"method": "GET", "path": "/api/orders/{id}", "status": 200},
                    ],
                    "errors": [],
                    "precondition_not_met": [],
                    # Presence of sandbox audit without operational_receipt used to
                    # skip synthesis and abort the whole legacy adapter.
                    "sandbox_write": {
                        "status": "accepted",
                        "cleanup": {"status": "completed"},
                        "audit_record_count": 1,
                    },
                },
                "oracle_results": [
                    {
                        "oracle_name": "TenantIsolationOracle",
                        "passed": False,
                        "verdict": "cross_user_access",
                    }
                ],
            }
        ],
        "phases": {},
        "auto_har": {"entries": []},
    }
    handle = {
        "campaign": _FakeCampaign(contract["campaign_id"]),
        "store": _FakeStore(),
        "mode": "legacy_champion",
    }
    adapted = adapt_legacy_champion_result(inputs, handle, plan, legacy)
    assert adapted["mainline_run"]["campaign_id"] == contract["campaign_id"]
    assert adapted["obligation_attempt_ledger"]["complete"] is True
