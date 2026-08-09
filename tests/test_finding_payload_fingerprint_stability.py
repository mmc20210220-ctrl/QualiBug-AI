# -*- coding: utf-8 -*-
"""Task 14: finding payload fingerprint stability across evidence injection.

Regression tests for the run6 end-to-end blocker
``formal_finding_gate_invalid:...:finding_payload_fingerprint_mismatch``:

- the delivery gate binds ``finding_payload_fingerprint`` on the finding at
  gate-build time (experiment_batch_executor.py:214);
- the asset-index enrichment pass (discovery_runtime_execution.py:786-809)
  injects 源契约/运行时证据 material AFTER the batch returns;
- delivery re-derivation recomputes the fingerprint on the injected finding.

Before the fix the injection appended paragraphs to ``description``, so the
recomputed fingerprint differed from the gate-bound one.  The injection now
lands on dedicated fields (``contract_evidence`` / ``runtime_observation``)
that are whitelisted out of the payload fingerprint; ``title``/``description``
stay byte-identical and the fingerprint is stable across every pass.

Synthetic industry-neutral data only.
"""
from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center._customer_delivery_gate_v2_mechanics import (
    _DERIVED_FINDING_FIELDS,
    finding_payload_fingerprint,
)
from ai_test_asset_center.finding_source_contract import (
    attach_evidence_paragraphs,
    build_rule_statement_index,
    enrich_governed_result,
    resolve_source_ref_statements,
)
from benchmark_evaluator.benchmark_compute import _finding_text_blob


def _finding(**overrides):
    finding = {
        "finding_id": "finding_fp_stability_1",
        "title": "[ContractOracle] domain_rule: operator PUT /v1/items/{id}",
        "description": "control=admin succeeded; treatment=operator violated the typed assertion",
        "category": "validation",
        "risk_family": "validation",
        "outcome_ref": "outcome_1",
        "source_refs": [
            {"kind": "permission_matrix", "locator": "operator"},
            {"kind": "api_operation", "locator": "PUT /v1/items/a1"},
        ],
        "reproduction": {
            "actor": "operator",
            "method": "PUT",
            "path": "/v1/items/a1",
            "reproduction_steps": ["PUT /v1/items/a1 -> HTTP 200"],
        },
        "evidence": {"actor": "operator", "control_succeeded": True},
        "failed_assertions": [{"kind": "domain_rule"}],
        "raw_evidence": {
            "request_raw": {"method": "PUT", "path": "/v1/items/a1"},
            "response_raw": {"status_code": 200, "body": {"accepted": True}},
        },
    }
    finding.update(overrides)
    return finding


def _experiment():
    return {
        "experiment_id": "exp_1",
        "source_refs": [{"kind": "api_operation", "locator": "PUT /v1/items/a1"}],
        "assertions": [
            {
                "outcome_ref": "outcome_1",
                "status": "VIOLATION",
                "property": {"expression": {"raw": "对象余额不得为负"}},
            }
        ],
    }


def _knowledge_asset():
    return {
        "permission_matrix": [
            {"role": "operator", "evidence": "operator 只能修改自己负责的对象"}
        ],
        "rule_library": [
            {"statement": "库存变更必须记录操作人", "operation_refs": ["PUT /v1/items/{id}"]}
        ],
        "interfaces": [],
    }


def _run6_sequence(finding):
    """Exact run6 order: gate fingerprint -> finalizer -> post-gate asset pass."""
    gate_fingerprint = finding_payload_fingerprint(finding)

    finalizer_result = enrich_governed_result(
        {"finding": deepcopy(finding), "findings": [deepcopy(finding)]},
        exp=_experiment(),
    )
    finalizer_finding = finalizer_result["findings"][0]

    index = build_rule_statement_index(_knowledge_asset())
    extra = resolve_source_ref_statements(finding["source_refs"], index)
    delivered = attach_evidence_paragraphs(
        deepcopy(finalizer_finding),
        statements=extra,
        with_runtime_evidence=False,
    )
    rederived = finding_payload_fingerprint(delivered)
    return gate_fingerprint, finalizer_finding, delivered, rederived


# ── 1. fingerprint stability (the run6 blocker) ────────────────────────────
def test_fingerprint_stable_across_finalizer_and_post_gate_injection():
    finding = _finding()
    gate_fp, finalizer_finding, delivered, rederived = _run6_sequence(finding)

    assert finding_payload_fingerprint(finalizer_finding) == gate_fp
    assert rederived == gate_fp
    assert delivered["description"] == finding["description"]
    assert delivered["title"] == finding["title"]
    assert delivered["contract_evidence"]
    assert delivered["runtime_observation"]


def test_new_fields_are_whitelisted_out_of_payload_fingerprint():
    assert "contract_evidence" in _DERIVED_FINDING_FIELDS
    assert "runtime_observation" in _DERIVED_FINDING_FIELDS

    finding = _finding()
    base = finding_payload_fingerprint(finding)
    assert finding_payload_fingerprint({
        **finding,
        "contract_evidence": "源契约: 对象余额不得为负",
        "runtime_observation": "运行时证据: 角色 operator 对照实验",
    }) == base


# ── 2. evaluator blob carries the new fields ───────────────────────────────
def test_finding_text_blob_includes_new_fields():
    finding = _finding(
        contract_evidence="源契约: 对象余额不得为负；operator 只能修改自己负责的对象",
        runtime_observation="运行时证据: 角色 operator 对照实验；PUT /v1/items/a1；观察HTTP状态=200",
    )
    blob = _finding_text_blob(finding)
    assert "源契约: 对象余额不得为负" in blob
    assert "operator 只能修改自己负责的对象" in blob
    assert "运行时证据: 角色 operator 对照实验" in blob
    assert "观察http状态=200" in blob
    # existing core material still present
    assert "[contractoracle] domain_rule: operator put /v1/items/{id}" in blob
    assert "control=admin succeeded; treatment=operator violated" in blob


def test_finding_text_blob_legacy_finding_without_new_fields():
    # Old findings without the new fields: blob behavior unchanged.
    finding = _finding()
    assert finding.get("contract_evidence") is None
    assert _finding_text_blob(finding) == _finding_text_blob(dict(finding))


# ── 3. injection idempotence ───────────────────────────────────────────────
def test_injection_idempotent_no_fingerprint_drift():
    finding = _finding()
    once = attach_evidence_paragraphs(finding, statements=["规则一"])
    twice = attach_evidence_paragraphs(once, statements=["规则一", "规则二"])
    thrice = attach_evidence_paragraphs(twice, statements=["规则二"])
    assert thrice["contract_evidence"] == twice["contract_evidence"]
    assert thrice["runtime_observation"] == once["runtime_observation"]
    assert thrice["description"] == finding["description"]
    assert finding_payload_fingerprint(thrice) == finding_payload_fingerprint(finding)


# ── 4. legacy finding compatibility ────────────────────────────────────────
def test_legacy_finding_with_description_paragraphs_stays_fingerprint_stable():
    # Pre-fix persisted finding: paragraphs inside description, gate
    # fingerprint already bound to that text.  The fixed injector must not
    # rewrite description (would break its own stored gate) — new statements
    # merge into the dedicated fields only.
    legacy = _finding()
    legacy["description"] = (
        "control=admin succeeded; treatment=operator violated the typed assertion\n"
        "源契约: 旧规则一\n"
        "运行时证据: 角色 operator 对照实验"
    )
    legacy_fp = finding_payload_fingerprint(legacy)
    out = attach_evidence_paragraphs(legacy, statements=["新规则二"])
    assert out["description"] == legacy["description"]
    assert finding_payload_fingerprint(out) == legacy_fp
    assert "旧规则一" in out["contract_evidence"]
    assert "新规则二" in out["contract_evidence"]
    # legacy 运行时证据 paragraph stays in description; no duplicate field
    assert not out.get("runtime_observation")
