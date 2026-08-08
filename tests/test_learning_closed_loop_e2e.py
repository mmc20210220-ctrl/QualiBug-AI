"""End-to-end test for the self-learning closed loop.

Drives the REAL pipeline functions through a three-round cycle against an
isolated SQLite knowledge base (tmp REPO_ROOT) and asserts every link of the
loop holds:

Round 1 (write)   : deliverable findings -> pattern extraction -> SQLite store
Round 2 (read)    : scan-start load -> usage writeback -> planning boost +
                    comprehension memory block (both consumption surfaces)
Round 2 (write)   : one pattern re-confirmed (reinforced), the other consumed
                    but not re-confirmed (non-reinforcement decay)
Round 3 (read)    : decayed confidence survives re-store (no resurrection),
                    usage counts accumulate, ranking reflects history

No LLM provider, no target server: the loop is exercised exactly where it
lives — closed_loop_feedback, LearningPatternBridge/DB, and the consumption
module consumed by planning and the reasoner.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from ai_test_asset_center import learning_knowledge_db as _db_mod
from ai_test_asset_center import learning_pattern_bridge as _bridge_mod
from ai_test_asset_center.closed_loop_feedback import build_closed_loop_context
from ai_test_asset_center.closed_loop_feedback import load_learned_scan_context
from ai_test_asset_center.customer_delivery_gate import is_customer_deliverable_defect
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.learning_knowledge_consumption import (
    apply_learned_boost,
    build_learned_boost_index,
    build_learned_memory_prompt_block,
)
from ai_test_asset_center.learning_knowledge_db import LearningKnowledgeDB

PROJECT = "e2e_learning_loop"

_MAINLINE_RUN = build_mainline_run_contract(
    mainline_authority="experiment_candidate",
    run_id="RUN-E2E-LEARN-1",
    campaign_id="CMP-E2E-LEARN-1",
    target_id="TARGET-E2E-LEARN-1",
    environment_id="ENV-E2E-LEARN-1",
    policy_version="v2",
    evaluation_mode="operational",
)


@pytest.fixture()
def isolated_kb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every knowledge-store path at tmp_path so nothing real is touched."""
    monkeypatch.setattr(_db_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(_bridge_mod, "_REPO_ROOT", tmp_path)
    return tmp_path


def _deliverable_finding(
    *, method: str, path: str, category: str, finding_id: str
) -> dict:
    """A finding that genuinely passes the customer delivery gate."""
    finding = {
        "candidate_id": f"candidate-{finding_id}",
        "slice_id": "slice-1",
        "obligation_id": f"obligation-{finding_id}",
        "experiment_id": f"experiment-{finding_id}",
        "execution_id": f"execution-{finding_id}",
        "evidence_id": f"evidence-{finding_id}",
        "finding_id": finding_id,
        "mainline_run": {"contract_fingerprint": _MAINLINE_RUN["contract_fingerprint"]},
        "title": f"observed defect {finding_id}",
        "category": category,
        "severity": "P1",
        "bug_status": "reproduced",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
        "expected": "rejected",
        "actual": "accepted",
        "timestamp": "2026-08-03T00:00:00Z",
        "evidence_consistency": {"verdict": "confirmed"},
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "CUSTOMER_READY",
            "missing_requirements": [],
        },
        "reproduction": {
            "method": method,
            "path": path,
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"status": "accepted"}},
        },
        "raw_evidence": {
            "request_raw": {"method": method, "path": path},
            "response_raw": {"status_code": 200, "body": {"status": "accepted"}},
            "timestamp": "2026-08-03T00:00:00Z",
            "has_real_evidence": True,
        },
        "evidence": {"cleanup": {"status": "completed", "receipt_ref": "cleanup-receipt-1"}},
    }
    assert is_customer_deliverable_defect(finding), (
        "test fixture must pass the delivery gate"
    )
    return finding


def _entry(db: LearningKnowledgeDB, key: str):
    row = db.conn.execute(
        "SELECT confidence, usage_count FROM knowledge WHERE key = ?", (key,)
    ).fetchone()
    assert row is not None, f"knowledge entry missing: {key}"
    return float(row[0]), int(row[1])


def test_self_learning_three_round_closed_loop(isolated_kb: Path) -> None:
    root = isolated_kb

    # ── Round 1: write side — two deliverable findings become knowledge ──
    sig_a = "authorization_access_control:GET:orders"
    sig_b = "state_integrity:POST:refunds"
    round1 = build_closed_loop_context(
        PROJECT,
        root,
        [
            _deliverable_finding(
                method="GET", path="/api/orders/{id}",
                category="authorization_access_control", finding_id="f1",
            ),
            _deliverable_finding(
                method="POST", path="/api/refunds",
                category="state_integrity", finding_id="f2",
            ),
        ],
    )
    assert round1["new_this_scan"] == 2
    assert round1["sqlite_storage"]["patterns_stored"] == 2
    assert round1["sqlite_storage"]["non_reinforced_decayed"] == 0

    db = LearningKnowledgeDB(project=PROJECT)
    conf_a, usage_a = _entry(db, sig_a)
    conf_b, usage_b = _entry(db, sig_b)
    assert conf_a == pytest.approx(0.95)
    assert conf_b == pytest.approx(0.95)
    assert usage_a == 0 and usage_b == 0

    # ── Round 2 scan start: read side loads knowledge + records usage ──
    consumed = load_learned_scan_context(PROJECT)
    assert consumed["pattern_count"] == 2
    assert consumed.get("usage_recorded") == 2
    assert not consumed.get("load_failure")

    db2 = LearningKnowledgeDB(project=PROJECT)
    assert _entry(db2, sig_a)[1] == 1
    assert _entry(db2, sig_b)[1] == 1

    # Consumption surface 1 — planning ranking boost (bounded) ──
    boost_index = build_learned_boost_index(consumed)
    assert boost_index["status"] == "CONSUMED"
    boosted, matches = apply_learned_boost(
        score=10.0,
        risk_family="authorization",
        path_prefix="/api/orders",
        resolved_path="/api/orders/{id}",
        boost_index=boost_index,
    )
    assert boosted > 10.0 and boosted <= 10.0 * 1.5
    assert matches and matches[0]["match_kind"] == "path_entity"

    # Consumption surface 2 — comprehension memory block for the reasoner ──
    block, receipt = build_learned_memory_prompt_block(consumed)
    assert receipt["status"] == "CONSUMED"
    assert receipt["pattern_count"] == 2
    assert "LEARNED RISK MEMORY" in block
    assert "orders" in block and "refunds" in block

    # ── Round 2 write: only sig_a re-confirmed; sig_b must decay and stay ──
    round2 = build_closed_loop_context(
        PROJECT,
        root,
        [
            _deliverable_finding(
                method="GET", path="/api/orders/{id}",
                category="authorization_access_control", finding_id="f3",
            ),
        ],
        consumed_context=consumed,
    )
    assert round2["sqlite_storage"]["non_reinforced_decayed"] == 1

    db3 = LearningKnowledgeDB(project=PROJECT)
    conf_a2, _ = _entry(db3, sig_a)
    conf_b2, _ = _entry(db3, sig_b)
    assert conf_a2 == pytest.approx(0.95)          # reinforced (capped)
    assert conf_b2 == pytest.approx(0.95 * 0.95)   # non-reinforcement decay

    # ── Round 3 write with no new confirmations: decay must NOT resurrect ──
    consumed3 = load_learned_scan_context(PROJECT)
    round3 = build_closed_loop_context(
        PROJECT, root, [], consumed_context=consumed3
    )
    assert round3["sqlite_storage"]["non_reinforced_decayed"] == 2

    db4 = LearningKnowledgeDB(project=PROJECT)
    conf_a3, usage_a3 = _entry(db4, sig_a)
    conf_b3, usage_b3 = _entry(db4, sig_b)
    # store() kept the current (decayed) confidence; decay applied again
    assert conf_a3 == pytest.approx(0.95 * 0.95)
    assert conf_b3 == pytest.approx(0.95 * 0.95 * 0.95)
    # usage accumulates with every consuming load
    assert usage_a3 == 2 and usage_b3 == 2

    # Reinforcement still wins after decay: one more confirmed round restores
    conf_before = _entry(LearningKnowledgeDB(project=PROJECT), sig_b)[0]
    round4 = build_closed_loop_context(
        PROJECT,
        root,
        [
            _deliverable_finding(
                method="POST", path="/api/refunds",
                category="state_integrity", finding_id="f4",
            ),
        ],
        consumed_context=load_learned_scan_context(PROJECT),
    )
    assert round4["sqlite_storage"]["non_reinforced_decayed"] == 1  # sig_a only
    conf_b4, _ = _entry(LearningKnowledgeDB(project=PROJECT), sig_b)
    assert conf_b4 == pytest.approx(0.95)
    assert conf_b4 > conf_before


def test_open_taxonomy_survives_the_loop(isolated_kb: Path) -> None:
    """A category with no known bucket must NOT be coerced into a default."""
    root = isolated_kb
    result = build_closed_loop_context(
        PROJECT,
        root,
        [
            _deliverable_finding(
                method="PUT", path="/api/inventory/adjust",
                category="custom_industry_specific_class", finding_id="fx",
            ),
        ],
    )
    assert result["new_this_scan"] == 1
    db = LearningKnowledgeDB(project=PROJECT)
    rows = db.conn.execute("SELECT key FROM knowledge").fetchall()
    keys = [r[0] for r in rows]
    assert keys == ["custom_industry_specific_class:PUT:inventory"]


def test_trigger_decision_on_realistic_scan_result(isolated_kb: Path) -> None:
    from ai_test_asset_center.auto_learning_trigger import (
        AutoLearningTrigger,
        LearningTriggerConfig,
    )

    trigger = AutoLearningTrigger(project=PROJECT, config=LearningTriggerConfig())

    # A real v12 scan carries formal_count_projection / pipeline_health —
    # the pre-fix gate read legacy fields (high_value_summary etc.) that no
    # mainline scan writes, so it never fired.
    ok, reason = trigger.should_trigger(
        {
            "formal_count_projection": {
                "formal_customer_deliverable_count": 5,
                "canonical_defect_count": 5,
            },
            "pipeline_health": {"blocked_obligation_count": 0},
        }
    )
    assert ok and reason.startswith("SIGNALS_MET")

    # Clean scan with zero deliverable defects and zero blockage stays quiet.
    ok, reason = trigger.should_trigger(
        {
            "formal_count_projection": {"formal_customer_deliverable_count": 0},
            "pipeline_health": {"blocked_obligation_count": 0},
        }
    )
    assert not ok and reason.startswith("NO_SIGNALS")


def test_binding_resolver_loop_closes_via_kb(isolated_kb: Path) -> None:
    """binding-experience write -> KB -> read reorder is a closed loop.

    A BOUND resolver mapping from one scan is persisted, loaded by the
    scan-start read, and consumed by the planning-time reorder.
    """
    from ai_test_asset_center.binding_experience_learning import (
        apply_binding_experience_reorder,
        build_binding_experience_context,
        build_binding_experience_index,
    )
    from ai_test_asset_center.learning_pattern_bridge import LearningPatternBridge

    root = isolated_kb
    scan_result = {
        "v12": {
            "experiment_execution": {
                "results": [
                    {
                        "schema_version": "qualibug.experiment-execution.v1",
                        "experiment_id": "exp_1",
                        "obligation_id": "obl_1",
                        "status": "DELIVERABLE",
                        "binding_materialization_receipts": [
                            {
                                "target": "sku",
                                "status": "BOUND",
                                "source_priority": "same_actor_list_read",
                                "resolver_path": "/api/products",
                                "resolver_operation_ref": "bir_products_list",
                                "status_code": 200,
                                "resolver_actor_ref": "bir_actor_1",
                                "value_fingerprint": "deadbeef",
                            }
                        ],
                    }
                ]
            }
        }
    }

    # Round 1: write side persists the verified mapping.
    write_receipt = build_binding_experience_context(PROJECT, root, scan_result)
    assert write_receipt["status"] == "OK"
    assert write_receipt["stored_count"] == 1

    # Round 2 scan start: read side loads it with usage writeback.
    learned = LearningPatternBridge(project=PROJECT).load_learned_context()
    resolvers = LearningPatternBridge(project=PROJECT).load_binding_experience()
    assert len(resolvers) == 1
    assert resolvers[0]["operation_ref"] == "bir_products_list"
    assert resolvers[0]["target"] == "sku"
    # The resolved business value never rides along.
    assert "deadbeef" not in json.dumps(resolvers)

    learned["binding_resolvers"] = resolvers
    index = build_binding_experience_index(learned)
    assert index["status"] == "CONSUMED"

    # Round 2 planning: reorder consumes the experience.
    experiment = {
        "obligation_id": "obl_1",
        "binding_plan": [
            {
                "target": "sku",
                "status": "runtime_resolvable",
                "source_priority": "same_actor_list_read",
                "resolver_operations": [
                    {"operation_ref": "bir_alt", "method": "GET", "path": "/api/x"},
                    {"operation_ref": "bir_products_list", "method": "GET", "path": "/api/products"},
                ],
            }
        ],
    }
    receipt = apply_binding_experience_reorder({"obl_1": experiment}, learned)
    assert receipt["status"] == "CONSUMED"
    assert receipt["reordered_count"] == 1
    order = [r["operation_ref"] for r in experiment["binding_plan"][0]["resolver_operations"]]
    assert order[0] == "bir_products_list"


def test_pattern_carries_semantic_features(isolated_kb: Path) -> None:
    """The closed-loop pattern carries comprehension-layer semantics from
    the finding's own observed fields (actor, description, behavior delta)
    so the reasoner's learned-memory block guides hypothesis generation by
    violated behavior class, not just endpoint.
    """
    from ai_test_asset_center.closed_loop_feedback import _extract_pattern
    from ai_test_asset_center.learning_knowledge_consumption import (
        build_learned_memory_prompt_block,
    )

    finding = _deliverable_finding(
        method="GET", path="/api/orders/{id}", category="owner_tenant_visibility",
        finding_id="sem1",
    )
    finding["reproduction"] = {
        "method": "GET", "path": "/api/orders/{id}", "actor": "buyer",
        "reproduction_steps": ["GET /api/orders/1"],
    }
    finding["description"] = "control=seller succeeded; treatment=buyer violated the typed assertion"
    finding["expected"] = {"viewer_can_access": False, "leak_detected": False}
    finding["actual"] = {"viewer_can_access": True, "leak_detected": True}

    pattern = _extract_pattern(finding)
    assert pattern["assertion_kind"] == "owner_tenant_visibility"
    assert pattern["actor"] == "buyer"
    assert "treatment=buyer violated" in pattern["semantic_summary"]
    assert pattern["behavior_delta"] == {
        "viewer_can_access": {"expected": False, "actual": True},
        "leak_detected": {"expected": False, "actual": True},
    }
    # No full response bodies in the delta — only differing fields.
    assert len(pattern["behavior_delta"]) == 2

    # The prompt block renders the semantics (actor + description) inside the
    # 1200-char budget, still attention guidance only.
    block, receipt = build_learned_memory_prompt_block({
        "learned_patterns": [
            {"type": pattern["type"], "entity": pattern["entity"],
             "method": pattern["method"], "count": 2,
             "actor": pattern["actor"],
             "semantic_summary": pattern["semantic_summary"]},
        ]
    })
    assert receipt["status"] == "CONSUMED"
    assert "actor=buyer" in block
    assert "treatment=buyer violated" in block
    assert len(block) <= 1200
