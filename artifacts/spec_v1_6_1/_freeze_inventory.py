#!/usr/bin/env python3
"""V1.6.1 P0-1..3: freeze manifest, resolved inventory, minimal 2+2+2 set."""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "spec_v1_6_1"
OUT.mkdir(parents=True, exist_ok=True)
GOLDEN_PATH = ROOT / "artifacts" / "spec_v1_6_0" / "v160_golden_rule_set.json"


def _sha_file(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> None:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    resolved = [r for r in golden["rules"] if r.get("status") == "RESOLVED"]
    commit = _git("rev-parse", "HEAD")
    parent = _git("rev-parse", "HEAD^")
    tree = _git("log", "-1", "--format=%T")
    dirty = bool(_git("status", "--porcelain"))

    files = {
        "behavior_ir": ROOT / "ai_test_asset_center" / "behavior_ir.py",
        "assertion_dsl": ROOT / "ai_test_asset_center" / "assertion_dsl_base.py",
        "observer": ROOT / "ai_test_asset_center" / "observer_contracts_base.py",
        "compiler": ROOT / "ai_test_asset_center" / "experiment_compiler_obligation.py",
        "protocols": ROOT / "ai_test_asset_center" / "experiment_protocols_base.py",
        "contract_oracles": ROOT / "ai_test_asset_center" / "contract_oracles.py",
        "delivery_gate": ROOT / "ai_test_asset_center" / "customer_delivery_gate_v2.py",
        "golden_set": GOLDEN_PATH,
    }
    file_hashes = {k: _sha_file(v) for k, v in files.items()}

    manifest = {
        "schema_version": "qualibug.v161-release-manifest.v1",
        "spec_version": "V1.6.1",
        "run_name": "V1_6_1_RESOLVED_RULE_ORACLE_TRACE_RUNTIME_V1",
        "phase": "P0-1_BASELINE_FREEZE",
        "branch": "main",
        "commit_sha": commit,
        "parent_commit_sha": parent,
        "tree_hash": tree,
        "working_tree_status": "dirty" if dirty else "clean",
        "working_tree_policy": (
            "SPEC prefers clean; dirty holds V1.6.0 in-place edits + artifacts. "
            "Re-hash at formal-run freeze after code lock."
        ),
        "upstream_v160": {
            "scan_id": "scan_benchmark_mall_131_1785141701072",
            "run_id": "RUN_759828cc1f111aa50c4c3e54",
            "V1_6_0_RESULT_LEVEL": "B",
            "field_oracle_traces": 0,
            "resolved_golden_rules": 12,
            "golden_rule_set_hash": golden.get("golden_rule_set_hash"),
        },
        "frozen_hashes": {
            "golden_rule_set_hash": golden.get("golden_rule_set_hash"),
            "field_binding_hash": file_hashes["behavior_ir"],
            "typed_expression_dsl_hash": file_hashes["assertion_dsl"],
            "observer_registry_hash": file_hashes["observer"],
            "oracle_registry_hash": file_hashes["contract_oracles"],
            "cleanup_policy_hash": file_hashes["delivery_gate"],
        },
        "file_hashes": file_hashes,
        "architecture_policy": {
            "in_place_only": True,
            "new_parallel_oracle_forbidden": True,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (OUT / "v161_release_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    by_type = Counter(str(r.get("rule_type")) for r in resolved)
    inventory_rules = []
    for r in resolved:
        ep = r.get("execution_precondition") or {}
        inventory_rules.append(
            {
                "rule_id": r.get("rule_id"),
                "rule_version": r.get("rule_version"),
                "rule_fingerprint": r.get("rule_fingerprint"),
                "rule_type": r.get("rule_type"),
                "business_description": r.get("business_description"),
                "source_refs": r.get("source_evidence"),
                "entity_ids": [],
                "field_ids": list(ep.get("canonical_field_ids") or []),
                "operation_ids": list(ep.get("operation_refs") or []),
                "scope_field_ids": [],
                "before_observer_contract_ids": ["before_state", "entity_state"],
                "after_observer_contract_ids": ["after_state", "entity_state"],
                "typed_expression": r.get("expression"),
                "fixture_contract_id": "",
                "cleanup_contract_ids": [],
                "execution_precondition": ep,
                "v160_runtime_status": "NO_DIRECT_RULE_ID_IN_LEDGER",
                "v160_experiment_ids": [],
                "v160_trace_count": 0,
            }
        )
    inventory = {
        "schema_version": "qualibug.v161-resolved-rule-inventory.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "golden_rule_set_hash": golden.get("golden_rule_set_hash"),
        "resolved_count": len(resolved),
        "by_type": dict(by_type),
        "causal_postcondition_resolved": int(by_type.get("causal_postcondition", 0)),
        "V161_MINIMAL_SET_SOURCE_ASSET_LIMITED_CAUSAL": by_type.get(
            "causal_postcondition", 0
        )
        < 2,
        "note": (
            "V1.6.0 RESOLVED set has 0 causal_postcondition (6 state + 6 conservation). "
            "Causal minimal slots cannot be filled without inventing rules."
        ),
        "rules": inventory_rules,
    }
    (OUT / "v161_resolved_rule_inventory.json").write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Minimal set: prefer rules with field_ids / concrete operands / ops.
    def score(row: dict) -> tuple:
        ep = row.get("execution_precondition") or {}
        expr = row.get("typed_expression") or {}
        ops = row.get("operation_ids") or []
        fields = row.get("field_ids") or []
        operands = expr.get("operands") if isinstance(expr, dict) else []
        has_states = False
        if isinstance(operands, list):
            for op in operands:
                if isinstance(op, dict) and (
                    op.get("from_state") or op.get("to_state") or op.get("field")
                ):
                    has_states = True
        return (
            1 if ops else 0,
            1 if fields else 0,
            1 if has_states else 0,
            1 if ep.get("operation_binding_resolved") else 0,
            1 if ep.get("cleanup_contract_resolved") else 0,
            -len(ops),  # prefer fewer ops (more specific)
        )

    state = sorted(
        [r for r in inventory_rules if r["rule_type"] == "state_transition"],
        key=score,
        reverse=True,
    )
    cons = sorted(
        [r for r in inventory_rules if r["rule_type"] == "conservation"],
        key=score,
        reverse=True,
    )
    # Prefer bir_* conservation with field_ids over src_md offline-only.
    cons_bir = [r for r in cons if str(r["rule_id"]).startswith("bir_")]
    cons_pick = (cons_bir + cons)[:2]
    state_pick = state[:2]

    minimal_rules = []
    limited = []
    for rtype, picks, need in (
        ("causal_postcondition", [], 2),
        ("state_transition", state_pick, 2),
        ("conservation", cons_pick, 2),
    ):
        if len(picks) < need:
            limited.append(
                {
                    "rule_type": rtype,
                    "available": len(picks),
                    "needed": need,
                    "code": "V161_MINIMAL_SET_SOURCE_ASSET_LIMITED",
                }
            )
        for r in picks:
            minimal_rules.append(
                {
                    "rule_id": r["rule_id"],
                    "rule_fingerprint": r["rule_fingerprint"],
                    "rule_type": r["rule_type"],
                    "field_ids": r["field_ids"],
                    "operation_ids": r["operation_ids"],
                    "typed_expression": r["typed_expression"],
                    "selection_reason": "highest_executable_score_among_resolved",
                }
            )

    payload = json.dumps(minimal_rules, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    minimal = {
        "schema_version": "qualibug.v161-minimal-rule-manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "golden_rule_set_hash": golden.get("golden_rule_set_hash"),
        "minimal_rule_manifest_hash": hashlib.sha256(payload).hexdigest(),
        "target_counts": {
            "causal_postcondition": 2,
            "state_transition": 2,
            "conservation": 2,
        },
        "actual_counts": dict(Counter(r["rule_type"] for r in minimal_rules)),
        "V161_MINIMAL_SET_SOURCE_ASSET_LIMITED": bool(limited),
        "limited": limited,
        "rules": minimal_rules,
    }
    (OUT / "v161_minimal_rule_manifest.json").write_text(
        json.dumps(minimal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    gap = {
        "schema_version": "qualibug.v161-rule-runtime-gap-map.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "first_breakpoint": "OBLIGATION_TO_EXPERIMENT",
        "latent_hard_break": "ORACLE_TO_TRACE",
        "breakpoints": [
            {
                "code": "RULE_TO_OBLIGATION_LINEAGE_LOST",
                "status": "PARTIAL",
                "detail": "src_md:* never enter IR; bir_* lack field_rule_binding persistence in attempt ledger",
            },
            {
                "code": "OBLIGATION_TO_EXPERIMENT",
                "status": "PRIMARY",
                "detail": "state from/to not lifted from expression.operands; conservation empty terms; FIELD_LEVEL_RULE_NOT_EXECUTABLE",
                "fix_points": [
                    "experiment_protocols_base.compile_family_protocol state branch",
                    "experiment_compiler_obligation._field_level_rule_completeness_gate",
                ],
            },
            {
                "code": "ORACLE_TO_TRACE",
                "status": "LATENT_HARD",
                "detail": "validate_assertion_receipt rejects field_oracle_trace key",
                "fix_points": ["assertion_dsl_base.validate_assertion_receipt"],
            },
            {
                "code": "CAUSAL_SOURCE_ASSET_LIMITED",
                "status": "SOURCE_ASSET_LIMITED",
                "detail": "0 of 12 RESOLVED rules are causal_postcondition",
            },
        ],
        "in_place_only": True,
        "gap_map_complete": True,
    }
    (OUT / "v161_rule_runtime_gap_map.json").write_text(
        json.dumps(gap, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "dirty": dirty,
                "resolved": len(resolved),
                "by_type": dict(by_type),
                "minimal": dict(Counter(r["rule_type"] for r in minimal_rules)),
                "limited": limited,
                "minimal_hash": minimal["minimal_rule_manifest_hash"][:16],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
