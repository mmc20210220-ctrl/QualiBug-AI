"""An unprovable stored artifact degrades visibly; a self-contradicting one raises.

``attach_quality_projection_to_scan_result`` used to raise ``MainlineContractError``
for both cases, which took down the whole ``/api/v1/projects/{id}/command-center``
response. Four of the eight real projects under ``platform_outputs/`` hit that path,
so the console returned HTTP 500 for half of them.

The split these tests pin:

* CANNOT RE-DERIVE the authority (no mainline run, no attempt ledger, a ledger that
  no longer revalidates) -> ``authority_status="UNVERIFIABLE"`` with the exact reason
  and zero counts. Nothing is claimed, matching the repo's NOT_MEASURED discipline.
* SELF-CONTRADICTION (registry invalid, occurrence scope mismatch, run-delivery
  contradiction, a finding that cannot prove its authority fingerprint) -> still
  raises. Two disagreeing answers must never be smoothed over.

Degrading must never become a v1-heuristic fallback: the counts go to zero, they are
not recomputed from display fields.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ai_test_asset_center.discovery_mainline_contract import MainlineContractError
from ai_test_asset_center.discovery_quality_projection import (
    _is_unverifiable_authority,
    attach_quality_projection_to_scan_result,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PLATFORM_OUTPUTS = REPO_ROOT / "platform_outputs"

# Codes that mean "this stored artifact cannot prove anything", so the projection
# reports UNVERIFIABLE instead of collapsing the response.
UNPROVABLE_CODES = (
    "mainline_run_missing",
    "attempt_ledger_missing",
    "formal_attempt_ledger_invalid:obligation_attempt_gate_bundle_invalid:obl_1",
)

# Codes that mean the product computed two disagreeing answers.
CONTRADICTION_CODES = (
    "canonical_defect_registry_invalid:cdef_1",
    "canonical_defect_registry_occurrence_scope_mismatch",
    "run_delivery_projection_contradiction:release_ready",
    "defect_identity_consistency_invalid",
    # A row inside an authoritative list that cannot prove it belongs there is a
    # contradiction, not a legacy artifact. Pinned separately by
    # tests/test_discovery_mainline_authority.py.
    "finding_authority_fingerprint_missing:FINDING-1",
    "finding_authority_fingerprint_mismatch:FINDING-2",
)


@pytest.mark.parametrize("code", UNPROVABLE_CODES)
def test_unprovable_authority_codes_degrade(code: str) -> None:
    assert _is_unverifiable_authority(MainlineContractError(code)) is True


@pytest.mark.parametrize("code", CONTRADICTION_CODES)
def test_contradiction_codes_still_raise(code: str) -> None:
    assert _is_unverifiable_authority(MainlineContractError(code)) is False


@pytest.mark.parametrize(
    "code",
    [
        # Prefix matching is on the whole token before ':' -- a longer code that merely
        # starts with an allowlisted one must not inherit the degrade path.
        "mainline_run_missing_but_actually_something_else",
        "formal_attempt_ledger_invalidated_by_policy",
        "attempt_ledger_missing_field_check_failed",
    ],
)
def test_prefix_lookalike_codes_still_raise(code: str) -> None:
    assert _is_unverifiable_authority(MainlineContractError(code)) is False


def test_scan_result_without_mainline_run_projects_as_unverifiable() -> None:
    """The minimal legacy shape: findings present, no authority to bind them to."""
    scan_result = {
        "project": "legacy_archive",
        "findings": [{"finding_id": "FINDING-1", "title": "t", "severity": "P1"}],
    }
    projected = attach_quality_projection_to_scan_result(scan_result)
    counts = projected["formal_count_projection"]

    assert counts["authority_status"] == "UNVERIFIABLE"
    assert counts["authority_reason"]
    # Zero by refusal, not recomputed from the finding rows.
    assert counts["formal_customer_deliverable_count"] == 0
    assert counts["canonical_defect_count"] == 0
    assert counts["canonical_representative_findings"] == []
    assert counts["delivery_occurrence_count"] == 0
    assert projected["finding_classification"]["deliverable"] == []


def test_unverifiable_projection_does_not_publish_findings() -> None:
    """A degraded authority must not let any row through as customer-deliverable."""
    scan_result = {
        "project": "legacy_archive",
        "findings": [
            {
                "finding_id": "FINDING-1",
                "title": "t",
                "severity": "P0",
                # Deliberately the v1 fields that would satisfy the old heuristic gate.
                "bug_status": "reproduced",
                "gate_passed": True,
                "customer_delivery_status": "defect",
                "evidence_quality": {"level": "validated", "score": 95},
                "semantic_verdict": "SEMANTIC_CONFIRMED",
                "business_evidence_status": "VALIDATED",
            }
        ],
    }
    projected = attach_quality_projection_to_scan_result(scan_result)

    assert projected["formal_count_projection"]["authority_status"] == "UNVERIFIABLE"
    assert projected["formal_count_projection"]["formal_customer_deliverable_count"] == 0
    assert projected["finding_classification"]["deliverable"] == []
    assert projected["run_delivery_readiness"]["release_ready"] is not True


def _committed_scan_results() -> list[Path]:
    if not PLATFORM_OUTPUTS.is_dir():
        return []
    return sorted(PLATFORM_OUTPUTS.glob("*/scan_result.json"))


def test_every_committed_scan_result_projects_without_raising() -> None:
    """Regression guard for the HTTP 500: no stored artifact may kill the response.

    Skips rather than fails when no artifacts are present, so the suite stays valid
    on a clean checkout.
    """
    artifacts = _committed_scan_results()
    if not artifacts:
        pytest.skip("no platform_outputs/*/scan_result.json artifacts in this checkout")

    failures: list[str] = []
    for path in artifacts:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            projected = attach_quality_projection_to_scan_result(copy.deepcopy(raw))
        except MainlineContractError as exc:
            failures.append(f"{path.parent.name}: {type(exc).__name__}: {exc}")
            continue
        status = projected["formal_count_projection"]["authority_status"]
        assert status in {"VERIFIED", "BLOCKED", "UNVERIFIABLE"}, (
            f"{path.parent.name} reported authority_status={status!r}"
        )
        if status == "UNVERIFIABLE":
            # An unverifiable artifact must name why and must publish nothing.
            assert projected["formal_count_projection"]["authority_reason"]
            assert projected["formal_count_projection"]["canonical_defect_count"] == 0

    assert not failures, (
        "stored scan_result artifacts still raise instead of degrading: " + "; ".join(failures)
    )
