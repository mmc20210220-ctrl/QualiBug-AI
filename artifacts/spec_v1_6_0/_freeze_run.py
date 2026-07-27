#!/usr/bin/env python3
"""V1.6.0 P0-19: freeze real-run identity before formal product entry."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "spec_v1_6_0"

FREEZE_FILES = [
    "ai_test_asset_center/behavior_ir.py",
    "ai_test_asset_center/experiment_compiler_obligation.py",
    "ai_test_asset_center/experiment_protocols_base.py",
    "ai_test_asset_center/observer_contracts_base.py",
    "ai_test_asset_center/assertion_dsl_base.py",
    "ai_test_asset_center/oracle_expression_resolver.py",
    "ai_test_asset_center/customer_delivery_gate_v2.py",
    "ai_test_asset_center/contract_oracles.py",
    "ai_test_asset_center/disposable_fixture_contract.py",
    "ai_test_asset_center/source_declared_readback_resolver.py",
    "ai_test_asset_center/state_precondition_planner.py",
    "artifacts/spec_v1_6_0/v160_golden_rule_set.json",
]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    golden = json.loads((OUT / "v160_golden_rule_set.json").read_text(encoding="utf-8"))
    file_hashes = {
        rel: _sha256_file(ROOT / rel) for rel in FREEZE_FILES if (ROOT / rel).exists()
    }
    bundle = hashlib.sha256(
        json.dumps(file_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    commit_sha = _git("rev-parse", "HEAD")
    tree_hash = _git("rev-parse", "HEAD^{tree}")
    dirty = bool(_git("status", "--porcelain"))

    accounts_path = (
        ROOT / "platform_inputs" / "benchmark_mall" / "test_accounts.json"
    )
    account_hash = _sha256_file(accounts_path) if accounts_path.exists() else ""

    freeze = {
        "schema_version": "qualibug.v160-run-freeze.v1",
        "spec_version": "V1.6.0",
        "run_name": "V1_6_0_FIELD_LEVEL_BUSINESS_ORACLE_RUNTIME_V1",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "baseline_input_commit": "f440c3df96aadc32f578ab976a35b558bcb5eefb",
        "commit_sha": commit_sha,
        "tree_hash": tree_hash,
        "working_tree_dirty": dirty,
        "dirty_policy": (
            "V1.6.0 in-place Stage A edits are intentionally included in run freeze; "
            "no further code/rule changes after this freeze"
        ),
        "golden_rule_set_hash": golden.get("golden_rule_set_hash"),
        "GOLDEN_RULE_SOURCE_ASSET_LIMITED": golden.get(
            "GOLDEN_RULE_SOURCE_ASSET_LIMITED"
        ),
        "field_binding_hash": file_hashes.get("ai_test_asset_center/behavior_ir.py", ""),
        "operation_binding_hash": file_hashes.get(
            "ai_test_asset_center/experiment_compiler_obligation.py", ""
        ),
        "observer_contract_hash": file_hashes.get(
            "ai_test_asset_center/observer_contracts_base.py", ""
        ),
        "typed_expression_hash": file_hashes.get(
            "ai_test_asset_center/assertion_dsl_base.py", ""
        ),
        "oracle_registry_hash": file_hashes.get(
            "ai_test_asset_center/contract_oracles.py", ""
        ),
        "fixture_policy_hash": file_hashes.get(
            "ai_test_asset_center/disposable_fixture_contract.py", ""
        ),
        "cleanup_policy_hash": file_hashes.get(
            "ai_test_asset_center/customer_delivery_gate_v2.py", ""
        ),
        "target": {
            "project_id": "benchmark_mall_131",
            "base_url": "http://localhost:8080",
            "approved_base_url": "http://localhost:8080",
            "environment_type": "test",
            "environment_ref": "sandbox",
            "product_backend": "http://localhost:8088",
            "product_frontend": "http://localhost:5174",
        },
        "target_hash": hashlib.sha256(
            b"benchmark_mall_131|http://localhost:8080|test|sandbox"
        ).hexdigest(),
        "account_manifest_hash": account_hash,
        "budget_hash": hashlib.sha256(
            b"adaptive-planning-budget:unchanged;no_silent_expansion"
        ).hexdigest(),
        "database_seed_hash": "NOT_CAPTURED_AT_FREEZE_USE_TARGET_RECEIPT",
        "freeze_bundle_hash": bundle,
        "file_hashes": file_hashes,
        "entry_request": {
            "method": "POST",
            "url": "http://localhost:8088/api/v1/scan",
            "body": {
                "project_id": "benchmark_mall_131",
                "base_url": "http://localhost:8080",
                "approved_base_url": "http://localhost:8080",
                "environment_type": "test",
                "environment_ref": "sandbox",
            },
        },
        "post_start_tuning_forbidden": True,
        "INVALID_POST_START_TUNING_policy": "any hash drift after freeze invalidates run",
    }
    out_path = OUT / "v160_run_freeze.json"
    out_path.write_text(json.dumps(freeze, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "wrote": str(out_path),
        "commit_sha": commit_sha,
        "golden_rule_set_hash": freeze["golden_rule_set_hash"],
        "freeze_bundle_hash": bundle[:16],
        "working_tree_dirty": dirty,
    }, indent=2))


if __name__ == "__main__":
    main()
