"""Diagnose why the blind run produced 0 findings."""
import json
from pathlib import Path

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
scan = json.loads((ROOT / "platform_outputs/warehouse_e/scan_result.json").read_text(encoding="utf-8"))

# Check obligation attempt ledger
ledger = scan.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
print(f"Total attempts: {len(attempts)}")
print(f"Status counts: {ledger.get('terminal_status_counts')}")

# Sample BLOCKED attempts
blocked = [a for a in attempts if a.get("terminal_status") == "BLOCKED"]
print(f"\n--- BLOCKED samples (first 5) ---")
for a in blocked[:5]:
    print(f"  {a.get('candidate_id')}: {a.get('terminal_reason')}")

# Sample DEFERRED attempts
deferred = [a for a in attempts if a.get("terminal_status") == "DEFERRED"]
print(f"\n--- DEFERRED samples (first 5) ---")
for a in deferred[:5]:
    print(f"  {a.get('candidate_id')}: {a.get('terminal_reason')}")

# Check v12 pipeline phases
v12 = scan.get("v12", {})
phases = v12.get("phases", {})
print(f"\n--- V12 Phases ---")
for name, phase in phases.items():
    if isinstance(phase, dict):
        status = phase.get("status", "?")
        print(f"  {name}: {status}")

# Check behavior IR
behavior_ir = v12.get("behavior_ir", {})
if behavior_ir:
    ops = behavior_ir.get("operations", {})
    actors = behavior_ir.get("actors", {})
    entities = behavior_ir.get("entities", {})
    obligations = behavior_ir.get("obligations", [])
    print(f"\n--- Behavior IR ---")
    print(f"  Operations: {len(ops)}")
    print(f"  Actors: {len(actors)}")
    print(f"  Entities: {len(entities)}")
    print(f"  Obligations: {len(obligations)}")
    
    # Show actor details
    print(f"\n--- Actors ---")
    if isinstance(actors, list):
        for actor in actors[:5]:
            if isinstance(actor, dict):
                print(f"  {actor.get('id')}: role={actor.get('role')}, secret={actor.get('credential_secret_ref', actor.get('secret_ref', 'NONE'))}")
    elif isinstance(actors, dict):
        for aid, actor in list(actors.items())[:5]:
            print(f"  {aid}: role={actor.get('role')}, secret={actor.get('credential_secret_ref', actor.get('secret_ref', 'NONE'))}")

# Check experiment compilation
exp_compile = v12.get("experiment_compile", {})
if exp_compile:
    print(f"\n--- Experiment Compile ---")
    print(f"  Status: {exp_compile.get('status')}")
    print(f"  Total: {exp_compile.get('total')}")
    print(f"  Compiled: {exp_compile.get('compiled')}")
    print(f"  Blocked: {exp_compile.get('blocked')}")

# Check execution phase
exec_phase = phases.get("execution", {})
if exec_phase:
    print(f"\n--- Execution Phase ---")
    for k, v in exec_phase.items():
        if not isinstance(v, (dict, list)):
            print(f"  {k}: {v}")
        elif isinstance(v, list):
            print(f"  {k}: [{len(v)} items]")
