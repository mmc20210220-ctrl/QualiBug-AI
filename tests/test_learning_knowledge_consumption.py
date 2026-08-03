"""Tests for closed-loop READ consumption in the discovery planning mainline.

Covers learning_knowledge_consumption (boost index, bounded boost application,
consumption receipt) and the plan_obligation_round integration: learned
patterns may re-rank compiled obligations but never change compile status,
selection semantics, or budget.
"""

from ai_test_asset_center.adaptive_discovery_planner import plan_obligation_round
from ai_test_asset_center.learning_knowledge_consumption import (
    apply_learned_boost,
    build_learning_consumption_receipt,
    build_learned_boost_index,
)


def _knowledge(patterns, load_failure=""):
    payload = {"source": "sqlite_knowledge_base", "learned_patterns": patterns}
    if load_failure:
        payload["load_failure"] = load_failure
    return payload


def _pattern(entity, ptype, confidence=0.9, usage=3, path="", method=""):
    return {
        "_key": f"{ptype}::{method or 'X'}:{entity}",
        "type": ptype,
        "entity": entity,
        "method": method,
        "path": path,
        "_confidence": confidence,
        "_usage_count": usage,
    }


class TestBuildLearnedBoostIndex:
    def test_consumed_entries(self):
        index = build_learned_boost_index(
            _knowledge([_pattern("cart", "state_violation", method="DELETE")])
        )
        assert index["status"] == "CONSUMED"
        assert index["pattern_count"] == 1
        entry = index["entries"][0]
        assert entry["entity"] == "cart"
        assert entry["strength"] > 0.0

    def test_empty_payload_no_patterns(self):
        index = build_learned_boost_index(_knowledge([]))
        assert index["status"] == "NO_PATTERNS"
        assert index["pattern_count"] == 0

    def test_load_failure_stays_visible(self):
        index = build_learned_boost_index(
            _knowledge([], load_failure="OperationalError:locked")
        )
        assert index["status"] == "LOAD_FAILED"
        assert index["load_failure"] == "OperationalError:locked"

    def test_low_confidence_skipped(self):
        index = build_learned_boost_index(
            _knowledge([_pattern("cart", "state_violation", confidence=0.01)])
        )
        assert index["status"] == "NO_PATTERNS"
        assert index["skipped_count"] == 1

    def test_missing_payload(self):
        index = build_learned_boost_index(None)
        assert index["status"] == "NO_PATTERNS"


class TestApplyLearnedBoost:
    def _index(self, patterns):
        return build_learned_boost_index(_knowledge(patterns))

    def test_entity_segment_match_boosts(self):
        index = self._index([_pattern("cart", "state_violation")])
        boosted, matches = apply_learned_boost(
            score=1.0,
            risk_family="authorization",
            path_prefix="/api/cart",
            resolved_path="/api/cart/items/{id}",
            boost_index=index,
        )
        assert boosted > 1.0
        assert boosted <= 1.5
        assert matches and matches[0]["match_kind"] == "path_entity"

    def test_family_match_weaker_than_entity_match(self):
        index = self._index([_pattern("payments", "idempotency")])
        family_boosted, family_matches = apply_learned_boost(
            score=1.0,
            risk_family="idempotency",
            path_prefix="/api/orders",
            resolved_path="",
            boost_index=index,
        )
        entity_boosted, _ = apply_learned_boost(
            score=1.0,
            risk_family="authorization",
            path_prefix="/api/payments",
            resolved_path="",
            boost_index=index,
        )
        assert family_matches and family_matches[0]["match_kind"] == "risk_family"
        assert 1.0 < family_boosted < entity_boosted

    def test_no_match_no_change(self):
        index = self._index([_pattern("cart", "state_violation")])
        boosted, matches = apply_learned_boost(
            score=0.8,
            risk_family="validation",
            path_prefix="/api/invoices",
            resolved_path="/api/invoices/{id}",
            boost_index=index,
        )
        assert boosted == 0.8
        assert matches == []

    def test_not_consumed_index_is_inert(self):
        index = build_learned_boost_index(_knowledge([]))
        boosted, matches = apply_learned_boost(
            score=1.0,
            risk_family="state_violation",
            path_prefix="/api/cart",
            resolved_path="",
            boost_index=index,
        )
        assert boosted == 1.0 and matches == []

    def test_zero_score_stays_zero(self):
        index = self._index([_pattern("cart", "state_violation")])
        boosted, matches = apply_learned_boost(
            score=0.0,
            risk_family="authorization",
            path_prefix="/api/cart",
            resolved_path="",
            boost_index=index,
        )
        assert boosted == 0.0 and matches == []

    def test_boost_is_bounded(self):
        index = self._index(
            [_pattern("cart", "state_violation", usage=1000)] * 5
        )
        boosted, _ = apply_learned_boost(
            score=2.0,
            risk_family="authorization",
            path_prefix="/api/cart",
            resolved_path="",
            boost_index=index,
        )
        assert boosted <= 2.0 * 1.5


class TestPlannerIntegration:
    def _obligation(self, oid, family, prefix):
        return {
            "obligation_id": oid,
            "risk_family": family,
            "confidence": 0.8,
            "risk_priority": 1.0,
            "subject_refs": [oid],
            "property": {"operation_path_prefix": prefix},
        }

    def _experiments(self, oids):
        return {
            oid: {
                "experiment_id": f"EXP_{oid}",
                "compile_receipt": {"status": "COMPILED"},
                "treatment_plan": [],
                "control_plan": [],
            }
            for oid in oids
        }

    def test_matching_obligation_ranked_first(self):
        obligations = [
            self._obligation("OBL_A", "validation", "/api/invoices"),
            self._obligation("OBL_B", "validation", "/api/cart"),
        ]
        experiments = self._experiments(["OBL_A", "OBL_B"])
        baseline = plan_obligation_round(
            obligations,
            experiments_by_obligation=experiments,
            behavior_ir={"operations": []},
            budget=2,
        )
        boosted = plan_obligation_round(
            obligations,
            experiments_by_obligation=experiments,
            behavior_ir={"operations": []},
            budget=2,
            learned_boost_index=build_learned_boost_index(
                _knowledge([_pattern("cart", "state_violation")])
            ),
        )
        base_first = baseline["selected"][0]["obligation_id"]
        boosted_first = boosted["selected"][0]["obligation_id"]
        assert base_first == "OBL_A"
        assert boosted_first == "OBL_B"
        boost_row = next(
            row
            for row in boosted["selected"]
            if row["obligation_id"] == "OBL_B"
        )
        assert boost_row["learned_boost"] is not None
        assert boost_row["learned_boost"]["boost_factor"] > 1.0

    def test_no_knowledge_no_boost_field(self):
        obligations = [self._obligation("OBL_A", "validation", "/api/invoices")]
        plan = plan_obligation_round(
            obligations,
            experiments_by_obligation=self._experiments(["OBL_A"]),
            behavior_ir={"operations": []},
            budget=2,
        )
        assert plan["selected"][0]["learned_boost"] is None
        assert plan["budget"] == 2

    def test_uncompiled_still_excluded_with_knowledge(self):
        obligations = [self._obligation("OBL_A", "validation", "/api/cart")]
        experiments = {
            "OBL_A": {
                "experiment_id": "EXP_A",
                "compile_receipt": {"status": "BLOCKED_MISSING_BINDING"},
                "treatment_plan": [],
                "control_plan": [],
            }
        }
        plan = plan_obligation_round(
            obligations,
            experiments_by_obligation=experiments,
            behavior_ir={"operations": []},
            budget=2,
            learned_boost_index=build_learned_boost_index(
                _knowledge([_pattern("cart", "state_violation")])
            ),
        )
        assert plan["selected"] == []


class TestConsumptionReceipt:
    def test_receipt_shape(self):
        index = build_learned_boost_index(
            _knowledge([_pattern("cart", "state_violation")])
        )
        receipt = build_learning_consumption_receipt(
            index,
            boosted_rows=[
                {"obligation_id": "OBL_B", "boost_factor": 1.3, "matches": []},
                {"obligation_id": "OBL_A", "boost_factor": 1.1, "matches": []},
            ],
        )
        assert receipt["schema_version"] == "qualibug.learning-consumption-receipt.v1"
        assert receipt["status"] == "CONSUMED"
        assert receipt["pattern_count"] == 1
        assert receipt["obligations_boosted"] == 2
        assert receipt["top_boosts"][0]["obligation_id"] == "OBL_B"

    def test_receipt_load_failure_visible(self):
        index = build_learned_boost_index(
            _knowledge([], load_failure="OSError:disk")
        )
        receipt = build_learning_consumption_receipt(index, boosted_rows=[])
        assert receipt["status"] == "LOAD_FAILED"
        assert receipt["load_failure"] == "OSError:disk"
