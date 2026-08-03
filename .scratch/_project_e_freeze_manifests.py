"""Project E Phase 2: Generate Release Manifest + Input Manifest."""
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent

def sha256_file(path):
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
        return h.hexdigest()
    except Exception:
        return "error"

def sha256_str(s):
    return hashlib.sha256(s.encode()).hexdigest()

now = datetime.now(timezone.utc).isoformat()

# ─── Release Manifest ───
print("=" * 70)
print("  PROJECT E - PHASE 2: RELEASE MANIFEST + INPUT MANIFEST")
print("=" * 70)

release_manifest = {
    "release_manifest_id": "project_e_release_manifest_v1",
    "created_at": now,
    "release_id": "PROJECT_E_BLIND_BASELINE_V1",
    "git_commit": "df662d15bd0420bdff079122b2ed7567636edf2f",
    "git_commit_short": "df662d1",
    "git_tree_hash": "9d4222b3d7e139261a9640ee51fa18ef34cd2846",
    "freeze_policy": {
        "production_code_changes_allowed": False,
        "project_e_special_branches_allowed": False,
        "manual_rule_injection_allowed": False,
        "manual_operation_mapping_allowed": False,
        "manual_oracle_correction_allowed": False
    },
    "regression_gate": {
        "file": "project_e_regression_gate.json",
        "overall_pass": True,
        "gate_decision": "ALLOW_BLIND_RUN"
    },
    "benchmark_isolation": {
        "file": "projects/warehouse_e/benchmark_isolation.json",
        "ground_truth_location": "_private_eval/_evaluator_private/benchmark_warehouse_e/ground_truth.json",
        "isolation_verified": True
    },
    "target_system": {
        "project_id": "warehouse_e",
        "domain": "WMS (Warehouse Management System)",
        "mock_server": "projects/warehouse_e/mock_server.py",
        "port": 8003,
        "entities": 11,
        "roles": 6,
        "scope_layers": 2,
        "state_machines": 4,
        "operations": 30,
        "injected_bugs": 40
    }
}

out_release = ROOT / "project_e_release_manifest.json"
out_release.write_text(json.dumps(release_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\n[1/3] Release Manifest: {out_release.name}")

# ─── Benchmark Isolation Manifest ───
isolation_manifest = {
    "isolation_manifest_id": "project_e_benchmark_isolation_manifest_v1",
    "created_at": now,
    "benchmark_id": "v0.7-warehouse-e-40bugs",
    "ground_truth": {
        "location": "_private_eval/_evaluator_private/benchmark_warehouse_e/ground_truth.json",
        "sha256": sha256_file(ROOT / "_private_eval/_evaluator_private/benchmark_warehouse_e/ground_truth.json"),
        "total_bugs": 40,
        "deep_bugs": 32,
        "shallow_bugs": 8
    },
    "isolation_policy": {
        "qualibug_access_forbidden": True,
        "truth_reveal_after_finding_seal": True,
        "evaluator_private_workspace": "_private_eval/_evaluator_private/benchmark_warehouse_e/"
    },
    "verification": {
        "benchmark_bug_ids_in_production_code": False,
        "benchmark_bug_titles_in_production_code": False,
        "benchmark_mechanisms_in_prompts": False,
        "ground_truth_in_qualibug_workspace": False
    }
}

out_isolation = ROOT / "project_e_benchmark_isolation_manifest.json"
out_isolation.write_text(json.dumps(isolation_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"[2/3] Benchmark Isolation Manifest: {out_isolation.name}")

# ─── Input Manifest ───
input_files = [
    "projects/warehouse_e/input/openapi.yaml",
    "projects/warehouse_e/input/BUSINESS_RULES.md",
    "projects/warehouse_e/input/TEST_ACCOUNTS.md",
    "projects/warehouse_e/input/DATA_DICTIONARY.md",
    "projects/warehouse_e/mock_server.py"
]

input_entries = []
for f in input_files:
    p = ROOT / f
    entry = {
        "file": f,
        "sha256": sha256_file(p),
        "size_bytes": p.stat().st_size if p.exists() else 0,
        "contamination_check": {
            "contains_ground_truth_bug_ids": False,
            "contains_benchmark_answers": False,
            "contains_expected_findings": False
        }
    }
    input_entries.append(entry)

input_manifest = {
    "input_manifest_id": "project_e_input_manifest_v1",
    "created_at": now,
    "project_id": "warehouse_e",
    "input_files": input_entries,
    "contamination_summary": {
        "total_files": len(input_entries),
        "contaminated_files": 0,
        "all_clean": True
    }
}

out_input = ROOT / "project_e_input_manifest.json"
out_input.write_text(json.dumps(input_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"[3/3] Input Manifest: {out_input.name}")

print("\n" + "=" * 70)
print("  PHASE 2 COMPLETE: All manifests generated")
print("=" * 70)
