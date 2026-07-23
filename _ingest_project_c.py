"""Ingest Project C documents into QualiBug knowledge center."""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

project = "contractflow_project_c"
root = Path(".")
input_dir = root / "platform_inputs" / project

print(f"=== Ingesting documents for {project} ===")
print(f"Input dir: {input_dir.resolve()}")

# List files
files = sorted(p for p in input_dir.iterdir() if p.is_file())
print(f"\nFiles to ingest ({len(files)}):")
for f in files:
    print(f"  {f.name} ({f.stat().st_size:,} bytes)")

# Ingest
from ai_test_asset_center.enterprise_knowledge_center import (
    ingest_enterprise_knowledge_files,
    build_enterprise_business_knowledge_asset,
    load_enterprise_business_knowledge_asset,
)

actor = {"name": "blind_test_runner", "role": "project_owner"}
print("\n--- Ingesting ---")
result = ingest_enterprise_knowledge_files(project, files, root=root, actor=actor)
print(f"Created: {len(result.get('created') or [])}")
print(f"Duplicates: {len(result.get('duplicates') or [])}")
print(f"Errors: {len(result.get('errors') or [])}")
for err in (result.get('errors') or []):
    print(f"  ERROR: {err}")

# Build knowledge asset
print("\n--- Building knowledge asset ---")
asset = build_enterprise_business_knowledge_asset(project, root)
summary = asset.get("summary", {})
print(f"Knowledge ready: {summary.get('knowledge_ready')}")
print(f"Business objects: {summary.get('business_object_count')}")
print(f"Interfaces: {summary.get('interface_count')}")
print(f"Field dictionary: {summary.get('field_dictionary_count')}")
print(f"Data tables: {summary.get('data_table_count')}")
print(f"State machines: {summary.get('state_machine_count')}")
print(f"Rules: {summary.get('rule_count')}")
print(f"Permissions: {summary.get('permission_matrix_count')}")
print(f"Roles: {summary.get('role_count')}")

# Check key asset sections
print(f"\n--- Asset sections ---")
for key in ("business_objects", "interfaces", "data_tables", "state_machines", "rule_library", "permission_matrix", "field_dictionary"):
    items = asset.get(key) or []
    print(f"  {key}: {len(items)} items")

# Verify test_accounts
print("\n--- Verifying test_accounts ---")
from ai_test_asset_center.discovery_runtime_planning import _runtime_actors
actors = _runtime_actors(root, project, {})
print(f"Runtime actors found: {len(actors)}")
for a in actors[:5]:
    print(f"  role={a.get('role')}, account_ref={a.get('account_ref')}, tenant={a.get('tenant')}")
if len(actors) > 5:
    print(f"  ... and {len(actors)-5} more")

print("\n=== DONE ===")
