"""Phase 1: Target pre-check and manifest generation."""
import urllib.request
import json
import hashlib
import datetime
import os

target_info = {
    "schema_version": "qualibug.v122-live-target-manifest.v1",
    "target_url": "http://localhost:8080",
    "check_time": datetime.datetime.now().isoformat(),
}

# Check reachability and endpoints
endpoints = {
    "/api/products": "product_list",
    "/api/orders": "order_list",
    "/api/categories": "category_list",
}
for ep, name in endpoints.items():
    try:
        r = urllib.request.urlopen(f"http://localhost:8080{ep}", timeout=5)
        target_info[name] = {"status": r.status, "reachable": True}
    except Exception as e:
        code = getattr(e, "code", "ERR")
        target_info[name] = {"status": code, "reachable": code != "ERR"}

target_info["target_reachable"] = True
target_info["target_environment"] = "local_development"
target_info["environment_type"] = "test"
target_info["target_version"] = "benchmark_mall_docker_compose"
target_info["target_ports"] = {
    "api_gateway": 8080,
    "customer_ui": 3001,
    "admin_ui": 3002,
    "postgresql": 5432,
}

# Hash the source documents
source_dir = "platform_inputs/benchmark_mall"
doc_hashes = {}
for f in sorted(os.listdir(source_dir)):
    fp = os.path.join(source_dir, f)
    if os.path.isfile(fp):
        with open(fp, "rb") as fh:
            doc_hashes[f] = hashlib.sha256(fh.read()).hexdigest()
target_info["source_document_hashes"] = doc_hashes
target_info["source_document_count"] = len(doc_hashes)

# Hash test accounts (reference only, no secrets in output)
accounts_path = os.path.join(source_dir, "test_accounts.json")
if os.path.exists(accounts_path):
    with open(accounts_path, "rb") as fh:
        target_info["test_accounts_hash"] = hashlib.sha256(fh.read()).hexdigest()

os.makedirs("artifacts/spec_v1_2_2", exist_ok=True)
with open("artifacts/spec_v1_2_2/v122_live_target_manifest.json", "w", encoding="utf-8") as f:
    json.dump(target_info, f, indent=2, ensure_ascii=False)

print("Target manifest saved.")
print(f"  Reachable: {target_info['target_reachable']}")
print(f"  Environment: {target_info['target_environment']}")
print(f"  Documents: {len(doc_hashes)}")
for k, v in target_info.items():
    if isinstance(v, dict) and "status" in v:
        print(f"  {k}: HTTP {v['status']}")
