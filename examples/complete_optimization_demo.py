#!/usr/bin/env python
"""
QualiBug AI - Complete Optimization Demo (Week 1-3)

Demonstrates all optimization features:
- Week 1: Caching, retry, performance monitoring, checkpoints
- Week 2: Parallel execution, config management, audit logging
- Week 3: Report export, external integration
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

# Add project root
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

print("=" * 80)
print("QualiBug AI - Complete Optimization Demo")
print("=" * 80)


# =========================================================================
# Part 1: Create Complete Engine
# =========================================================================
print("\n" + "-" * 60)
print("Part 1: Creating Complete Optimization Engine")
print("-" * 60)

try:
    from ai_test_asset_center.optimized_discovery_engine import create_complete_engine
    
    engine = create_complete_engine(
        enable_checkpoints=True,
        enable_cache=True,
        enable_parallel=True,
        enable_audit=True
    )
    
    print("\n[OK] Complete engine created successfully!")
    
except Exception as e:
    print(f"[ERROR] Failed to create engine: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)


# =========================================================================
# Part 2: Report Export Demo
# =========================================================================
print("\n" + "-" * 60)
print("Part 2: Report Export Demo")
print("-" * 60)

try:
    # Export reports
    output_dir = repo_root / "reports"
    print(f"\nExporting reports to: {output_dir}")
    
    exported_files = engine.export_findings(output_dir)
    
    print(f"\n[OK] Reports exported:")
    for fmt, path in exported_files.items():
        print(f"  - {fmt}: {path.name}")
    
except Exception as e:
    print(f"[ERROR] Report export failed: {e}")
    import traceback
    traceback.print_exc()


# =========================================================================
# Part 3: External Integration Demo
# =========================================================================
print("\n" + "-" * 60)
print("Part 3: External Tracker Integration Demo")
print("-" * 60)

try:
    # Simulate some findings
    findings = [
        {
            "hypothesis_id": "hypothesis_001",
            "title": "API returns 500 when invalid parameter provided",
            "description": "The /api/orders endpoint returns a 500 error when an invalid order_id is provided",
            "severity": "P1",
            "verdict": "confirmed",
            "confidence": 0.98
        },
        {
            "hypothesis_id": "hypothesis_002",
            "title": "Race condition in payment processing",
            "description": "Two concurrent payments to the same order cause double-charging",
            "severity": "P0",
            "verdict": "confirmed",
            "confidence": 0.85
        }
    ]
    
    print(f"\nSyncing {len(findings)} findings to GitHub Issues...")
    
    # Simulate sync (uses mock integration)
    synced = engine.sync_to_tracker(findings, tracker_type="github")
    
    print(f"\n[OK] {len(synced)} findings synced!")
    for i, result in enumerate(synced, 1):
        print(f"  Finding {i}: {result.get('title', 'N/A')}")
        if "url" in result:
            print(f"    URL: {result['url']}")
    
except Exception as e:
    print(f"[ERROR] External integration demo failed: {e}")
    import traceback
    traceback.print_exc()


# =========================================================================
# Part 4: Complete Summary
# =========================================================================
print("\n" + "-" * 60)
print("Part 4: Complete Optimization Summary")
print("-" * 60)

try:
    engine.print_complete_summary()
    
except Exception as e:
    print(f"[ERROR] Summary failed: {e}")


# =========================================================================
# Part 5: Quick Reference
# =========================================================================
print("\n" + "=" * 80)
print("Quick Reference - All Optimization Features")
print("=" * 80)
print("""
Week 1 Features:
- Caching (OpenAPI spec)
- Retry mechanism
- Performance monitoring
- Checkpointing
- Enhanced error handling

Week 2 Features:
- Parallel hypothesis execution
- Configuration management
- Audit logging
- Dynamic config updates

Week 3 Features:
- Report export (JSON, CSV, Markdown, HTML)
- External tracker integration (JIRA, GitHub)
- Complete workflow execution

Quick Start Examples:

# Basic engine (Week 1-2)
from ai_test_asset_center.optimized_discovery_engine import create_enhanced_engine
engine = create_enhanced_engine()

# Complete engine (Week 1-3)
from ai_test_asset_center.optimized_discovery_engine import create_complete_engine
engine = create_complete_engine()

# Export reports
engine.export_findings(Path("reports"))

# Sync to GitHub
findings = [...]
engine.sync_to_tracker(findings, tracker_type="github")

# Run complete workflow
results = engine.run_complete_workflow(prd_text, api_spec_text)
""")


print("\n" + "=" * 80)
print("Demo Complete!")
print("=" * 80)
print(f"""\nNext Steps:
- Check the reports in {repo_root / 'reports'}
- Review the documentation in docs/
- Try the other examples in examples/
""")
