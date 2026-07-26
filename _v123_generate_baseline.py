"""V1.2.3 P0-1/P0-2: Generate readback baseline and gap map from V1.2.2 ledger."""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, ".")

# Load V1.2.2 obligation funnel ledger
with open("artifacts/spec_v1_2_2/v122_obligation_funnel_ledger.json", "r", encoding="utf-8") as f:
    ledger = json.load(f)

obligations = ledger.get("obligations", [])
affected = [
    o for o in obligations
    if o.get("terminal_reason") in ("BINDING_GRAPH_BLOCKED", "OBSERVER_COMPILE_NOT_GROUNDED")
]

# ── P0-1: Baseline ──
baseline = {
    "schema_version": "qualibug.v123-readback-baseline.v1",
    "v122_release_commit": "3992b7076f1e0db3343eb6535c6660d6b0bd2c95",
    "run_id": ledger.get("run_id"),
    "campaign_id": ledger.get("campaign_id"),
    "total_obligations": len(obligations),
    "readback_affected_count": len(affected),
    "binding_block_count": sum(1 for o in affected if o.get("terminal_reason") == "BINDING_GRAPH_BLOCKED"),
    "observer_block_count": sum(1 for o in affected if o.get("terminal_reason") == "OBSERVER_COMPILE_NOT_GROUNDED"),
    "root_breakpoint": "SOURCE_DECLARED_READBACK_NOT_RESOLVED",
    "candidates": [],
}

for o in affected:
    locators = [sr.get("locator", "") for sr in o.get("source_refs", []) if sr.get("kind") == "api_operation"]
    baseline["candidates"].append({
        "obligation_id": o.get("obligation_id"),
        "obligation_type": o.get("obligation_type"),
        "risk_family": o.get("risk_family"),
        "operation_refs": o.get("operation_refs", []),
        "actor_refs": o.get("actor_refs", []),
        "api_locators": list(set(locators)),
        "terminal_reason": o.get("terminal_reason"),
        "raw_reason_code": o.get("raw_reason_code"),
        "binding_complete": o.get("binding_complete"),
        "observer_compile_status": o.get("observer_compile_status"),
    })

os.makedirs("artifacts/spec_v1_2_3", exist_ok=True)
with open("artifacts/spec_v1_2_3/v123_readback_baseline.json", "w", encoding="utf-8") as f:
    json.dump(baseline, f, indent=2, ensure_ascii=False)

print(f"Baseline written: {len(baseline['candidates'])} candidates")
print(f"  Binding block: {baseline['binding_block_count']}")
print(f"  Observer block: {baseline['observer_block_count']}")

# ── P0-2: Gap Map ──
# Now build the Behavior IR to analyze available readback surfaces
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center import load_enterprise_business_knowledge_asset
from ai_test_asset_center.runtime_binding_graph import declared_effect_observers
from ai_test_asset_center.real_id_resolver import normalize_path_placeholders

# Load knowledge asset
asset = load_enterprise_business_knowledge_asset("benchmark_mall_131", root=Path("."))
if not asset:
    print("ERROR: Could not load knowledge asset for benchmark_mall_131")
    sys.exit(1)

# Load API operations from source registry
import yaml

project_input_dir = Path("platform_inputs/benchmark_mall_131")
api_operations = []

# Try to load OpenAPI spec
source_registry_path = Path("platform_workspace/benchmark_mall_131/enterprise_knowledge_center/source_registry.json")
if source_registry_path.exists():
    with open(source_registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)
    print(f"Source registry: {len(registry.get('sources', []))} sources")

# Build Behavior IR
behavior_ir = build_behavior_ir_from_knowledge_asset(
    asset,
    project_id="benchmark_mall_131",
    api_operations=api_operations,
)

operations = behavior_ir.get("operations", [])
relations = behavior_ir.get("relations", [])
entities = behavior_ir.get("entities", [])

print(f"\nBehavior IR:")
print(f"  Operations: {len(operations)}")
print(f"  Relations: {len(relations)}")
print(f"  Entities: {len(entities)}")

# Build operations index
ops_by_id = {op.get("id"): op for op in operations if isinstance(op, dict) and op.get("id")}

# Analyze each affected obligation
gap_map_entries = []
unique_ops_analyzed = set()

for candidate in baseline["candidates"]:
    op_refs = candidate.get("operation_refs", [])
    for op_ref in op_refs:
        op = ops_by_id.get(op_ref, {})
        op_path = op.get("path", "")
        op_method = (op.get("method") or "").upper()
        op_id = op.get("id", op_ref)

        # Find declared effect observers
        observers = declared_effect_observers(
            op,
            behavior_ir=behavior_ir,
            max_candidates=5,
        )

        # Check for identity GET (path with placeholder)
        identity_gets = [
            obs for obs in operations
            if isinstance(obs, dict)
            and (obs.get("method") or "").upper() in ("GET", "HEAD")
            and "{" in (obs.get("path") or "")
            and normalize_path_placeholders(obs.get("path", "")).rsplit("/", 1)[0]
            == normalize_path_placeholders(op_path).rsplit("/", 1)[0]
        ]

        # Check for collection GET
        collection_gets = [
            obs for obs in operations
            if isinstance(obs, dict)
            and (obs.get("method") or "").upper() in ("GET", "HEAD")
            and "{" not in (obs.get("path") or "")
            and normalize_path_placeholders(obs.get("path", ""))
            == normalize_path_placeholders(op_path).rsplit("/{", 1)[0].rstrip("/")
        ]

        # Check write response schema
        response_schema = op.get("response_schema") or op.get("response_example") or {}
        has_response_body = bool(response_schema)

        # Determine identity source
        identity_source = "UNKNOWN"
        if op_method in ("PUT", "PATCH", "DELETE") and "{" in op_path:
            identity_source = "REQUEST_PATH_ID"
        elif op_method == "POST" and has_response_body:
            identity_source = "WRITE_RESPONSE_ID"

        entry = {
            "obligation_id": candidate["obligation_id"],
            "write_operation_id": op_id,
            "write_method": op_method,
            "write_path": op_path,
            "target_entity_id": "",
            "required_after_fields": [],
            "current_observer_status": candidate.get("observer_compile_status", "NOT_ATTEMPTED"),
            "current_block": candidate.get("terminal_reason"),
            "declared_effect_observers": observers,
            "identity_get_available": len(identity_gets) > 0,
            "identity_get_ops": [{"id": g.get("id"), "path": g.get("path")} for g in identity_gets[:3]],
            "collection_get_available": len(collection_gets) > 0,
            "collection_get_ops": [{"id": g.get("id"), "path": g.get("path")} for g in collection_gets[:3]],
            "write_response_has_body": has_response_body,
            "identity_source": identity_source,
            "readback_surface_candidates": [],
            "gap_type": "",
        }

        # Classify gap
        if observers:
            entry["gap_type"] = "OBSERVER_EXISTS_BUT_NOT_BOUND"
            entry["readback_surface_candidates"] = [
                {"type": "IDENTITY_GET" if "{" in r.get("path", "") else "COLLECTION_GET",
                 "operation_ref": r.get("operation_ref"),
                 "path": r.get("path")}
                for r in observers
            ]
        elif identity_gets:
            entry["gap_type"] = "IDENTITY_GET_EXISTS_NOT_JOINED"
            entry["readback_surface_candidates"] = [
                {"type": "IDENTITY_GET", "operation_ref": g.get("id"), "path": g.get("path")}
                for g in identity_gets[:2]
            ]
        elif collection_gets:
            entry["gap_type"] = "COLLECTION_GET_EXISTS_NO_FILTER"
            entry["readback_surface_candidates"] = [
                {"type": "FILTERED_COLLECTION_GET", "operation_ref": g.get("id"), "path": g.get("path")}
                for g in collection_gets[:2]
            ]
        elif has_response_body:
            entry["gap_type"] = "WRITE_RESPONSE_ONLY"
            entry["readback_surface_candidates"] = [
                {"type": "WRITE_RESPONSE_REPRESENTATION", "operation_ref": op_id, "path": op_path}
            ]
        else:
            entry["gap_type"] = "NO_SOURCE_DECLARED_READBACK"

        gap_map_entries.append(entry)
        unique_ops_analyzed.add(op_id)

# Summary statistics
gap_types = Counter(e["gap_type"] for e in gap_map_entries)
print(f"\nGap Map Summary ({len(gap_map_entries)} entries, {len(unique_ops_analyzed)} unique ops):")
for gt, cnt in gap_types.most_common():
    print(f"  {gt}: {cnt}")

# Write gap map
gap_map = {
    "schema_version": "qualibug.v123-readback-gap-map.v1",
    "generated_from": "v122_obligation_funnel_ledger.json",
    "total_candidates": len(gap_map_entries),
    "unique_write_operations": len(unique_ops_analyzed),
    "gap_type_summary": dict(gap_types),
    "entries": gap_map_entries,
}

with open("artifacts/spec_v1_2_3/v123_readback_gap_map.json", "w", encoding="utf-8") as f:
    json.dump(gap_map, f, indent=2, ensure_ascii=False)

print(f"\nGap map written to artifacts/spec_v1_2_3/v123_readback_gap_map.json")
