"""Project B scan - full execution with progress reporting."""
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# Disable LLM reasoning
os.environ["LLM_BASE_URL"] = ""
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_MODEL"] = ""
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

# ── Performance optimization for local mock server ──
# Reduce discovery rounds (8->3) and slices per round (15->8)
# This reduces scan time from ~70min to ~15-20min for simple targets
os.environ["QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT"] = "3"
os.environ["QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND"] = "8"
# Increase concurrency for faster parallel execution
os.environ["QUALIBUG_MAX_CONCURRENCY"] = "8"
# Reduce HTTP timeout for local mock (default 10s -> 5s)
os.environ["PROBE_TIMEOUT_MS"] = "5000"

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding='utf-8')

from ai_test_asset_center.__main__ import scan

def reset_mock_server():
    """Reset mock server state before scan."""
    try:
        req = urllib.request.Request("http://localhost:9092/api/v2/_reset", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[0] Mock server reset: {resp.status}", flush=True)
    except Exception as e:
        print(f"[0] Mock server reset failed: {e}", flush=True)


def main():
    root = Path(".")
    project = "equipment_maintenance"

    # Reset mock server state
    reset_mock_server()

    api_spec_path = Path("projects/equipment_maintenance/input/API_SPEC.md")
    api_doc_text = api_spec_path.read_text(encoding="utf-8")
    actual_hash = hashlib.sha256(api_doc_text.encode("utf-8")).hexdigest()
    print(f"[1] API_SPEC.md hash: {actual_hash[:24]}...", flush=True)

    source_manifest = {
        "source_id": f"src_{actual_hash[:16]}",
        "source_hash": actual_hash,
    }

    print(f"[2] Running scan (no timeout - let it complete)...", flush=True)
    started = time.time()

    campaign_context = {
        "source_manifest": source_manifest,
        "scope_id": project,
        "environment_type": "test",
        "environment_ref": "equipment-maintenance-mock",
        "execution_mode": "approved_sandbox_write",
    }

    result = scan(
        project=project,
        root=root,
        base_url="http://localhost:9092",
        api_doc_text=api_doc_text,
        save_report=False,
        campaign_context=campaign_context,
    )

    elapsed = time.time() - started
    print(f"\n{'='*60}", flush=True)
    print(f"PROJECT B SCAN RESULTS (elapsed: {elapsed:.1f}s)", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"Success: {result.get('success')}", flush=True)
    print(f"Grade: {result.get('grade')}", flush=True)
    print(f"Total findings: {result.get('total_findings')}", flush=True)
    print(f"Total candidates: {result.get('total_candidates')}", flush=True)
    print(f"Execution status: {result.get('execution_status')}", flush=True)

    # Save result
    out_path = Path("_scan_result_project_b.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {out_path}", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
