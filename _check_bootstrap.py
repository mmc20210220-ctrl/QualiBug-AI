"""Check test_data_bootstrap details from scan result."""
from pathlib import Path
import json

sr_file = Path("platform_outputs/real_project_demo/scan_result.json")
if sr_file.exists():
    s = json.load(open(sr_file, "r", encoding="utf-8"))
    
    # Check test_data_bootstrap
    bootstrap = s.get("test_data_bootstrap", {})
    print("test_data_bootstrap:")
    print(f"  status: {bootstrap.get('status')}")
    print(f"  reason: {bootstrap.get('reason')}")
    if bootstrap.get("governance_reason"):
        print(f"  governance_reason: {bootstrap.get('governance_reason')}")
    if bootstrap.get("control_attempts"):
        attempts = bootstrap.get("control_attempts", [])
        print(f"  control_attempts: {len(attempts)}")
        for a in attempts[:3]:
            print(f"    profile: {a.get('credential_profile')}")
            print(f"    lifecycle_status: {a.get('governed_lifecycle_status')}")
            print(f"    lifecycle_reason: {a.get('governed_lifecycle_reason')}")
            print(f"    setup_accepted: {a.get('setup_accepted')}")
            print(f"    cleanup_accepted: {a.get('cleanup_accepted')}")
    
    # Check test_data_plan
    plan = s.get("test_data_plan", {})
    print(f"\ntest_data_plan:")
    print(f"  status: {plan.get('status')}")
    print(f"  strategy: {plan.get('strategy')}")
    print(f"  missing_requirements: {plan.get('missing_requirements')}")
    
    # Check ui_test_data_bootstrap
    ui_bootstrap = s.get("ui_test_data_bootstrap", {})
    print(f"\nui_test_data_bootstrap:")
    print(f"  status: {ui_bootstrap.get('status')}")
    print(f"  reason: {ui_bootstrap.get('reason')}")
    
    # Check runtime_contract
    rc = s.get("runtime_contract", {})
    print(f"\nruntime_contract:")
    print(f"  status: {rc.get('status')}")
    print(f"  execution_mode: {rc.get('execution_mode')}")
    print(f"  environment_ref: {rc.get('environment_ref')}")
    print(f"  environment_kind: {rc.get('environment_kind')}")
else:
    print(f"File not found: {sr_file}")
