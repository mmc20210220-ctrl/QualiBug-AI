#!/usr/bin/env python
"""
QualiBug AI - Enhanced Optimization Engine Demo

This script demonstrates all Week 2 optimization features:
- Parallel hypothesis execution
- Configuration management
- Audit logging
- Performance monitoring
"""

from __future__ import annotations

import sys
import time
import random
from pathlib import Path

# Add project root to path
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

print("=" * 80)
print("QualiBug AI - Enhanced Optimization Engine Demo")
print("=" * 80)

# =========================================================================
# Demo 1: Create enhanced engine
# =========================================================================

print("\n" + "-" * 60)
print("Demo 1: Creating Enhanced Engine")
print("-" * 60)

try:
    from ai_test_asset_center.optimized_discovery_engine import create_enhanced_engine
    
    engine = create_enhanced_engine(
        enable_checkpoints=True,
        enable_cache=True,
        enable_parallel=True,
        enable_audit=True
    )
    
    print("[OK] Enhanced engine created successfully")
    
except Exception as e:
    print(f"[ERROR] Failed to create enhanced engine: {e}")
    print("\nNote: This is a demo. The enhanced engine extends the basic engine.")
    sys.exit(1)

# =========================================================================
# Demo 2: Configuration management
# =========================================================================

print("\n" + "-" * 60)
print("Demo 2: Configuration Management")
print("-" * 60)

try:
    config = engine.get_config()
    print("\n[Current Configuration]")
    print(f"  Route map cache TTL: {config.route_map_cache_ttl}s")
    print(f"  Max workers: {config.max_workers}")
    print(f"  Parallel execution: {'enabled' if config.enable_parallel_execution else 'disabled'}")
    print(f"  Max retries: {config.max_retries}")
    print(f"  Audit log: {'enabled' if config.enable_audit_log else 'disabled'}")
    
    # Update configuration
    print("\nUpdating configuration...")
    new_config = engine.update_config({
        "max_workers": 8,
        "route_map_cache_ttl": 600.0
    })
    
    print(f"[OK] Configuration updated:")
    print(f"  New max workers: {new_config.max_workers}")
    print(f"  New cache TTL: {new_config.route_map_cache_ttl}s")
    
except Exception as e:
    print(f"[ERROR] Configuration demo failed: {e}")

# =========================================================================
# Demo 3: Generate simulated hypotheses
# =========================================================================

print("\n" + "-" * 60)
print("Demo 3: Generating Simulated Hypotheses")
print("-" * 60)

def generate_simulated_hypotheses(count: int = 50) -> list[dict]:
    """Generate simulated hypotheses for testing"""
    hypotheses = []
    for i in range(count):
        hypothesis = {
            "hypothesis_id": f"hypothesis_{i:03d}",
            "title": f"Test Hypothesis {i + 1}",
            "description": f"This is a simulated hypothesis {i + 1}",
            "severity": random.choice(["P0", "P1", "P2", "P3"]),
            "expected_behavior": "The system should behave correctly",
            "created_at": time.time()
        }
        hypotheses.append(hypothesis)
    return hypotheses

# Generate test hypotheses
hypotheses = generate_simulated_hypotheses(20)
print(f"[OK] Generated {len(hypotheses)} simulated hypotheses")

# =========================================================================
# Demo 4: Parallel execution
# =========================================================================

print("\n" + "-" * 60)
print("Demo 4: Parallel Hypothesis Execution")
print("-" * 60)

try:
    print("\nExecuting hypotheses in parallel...")
    start_time = time.time()
    
    # Use route map cache
    route_map = engine._build_route_map()
    
    # Execute in parallel
    results = engine.stage_execute_parallel(hypotheses, route_map)
    
    parallel_time = time.time() - start_time
    
    print(f"[OK] Parallel execution completed in {parallel_time:.2f}s")
    print(f"  Processed {len(results)} hypotheses")
    
    # Compare with serial (simulated)
    print("\n[Performance Comparison]")
    serial_estimate = len(hypotheses) * 0.5  # Simulated 0.5s per hypothesis
    print(f"  Estimated serial time: {serial_estimate:.2f}s")
    print(f"  Actual parallel time: {parallel_time:.2f}s")
    speedup = (serial_estimate - parallel_time) / serial_estimate * 100 if serial_estimate > 0 else 0
    print(f"  Estimated speedup: {speedup:.1f}%")
    
except Exception as e:
    print(f"[ERROR] Parallel execution demo failed: {e}")

# =========================================================================
# Demo 5: Audit logging
# =========================================================================

print("\n" + "-" * 60)
print("Demo 5: Audit Logging")
print("-" * 60)

try:
    audit_logger = engine.get_audit_logger()
    
    if audit_logger:
        print("[OK] Audit logger initialized")
        
        # Log some custom events
        audit_logger.log_discovery_start("demo_user", "test_project")
        
        audit_logger.log_discovery_complete(
            "demo_user",
            "test_project",
            findings_count=len(results),
            confirmed_count=sum(1 for r in results if r.get("verdict") == "confirmed")
        )
        
        event_count = audit_logger.get_event_count()
        print(f"[OK] Logged {event_count} audit events")
        
    else:
        print("[INFO] Audit logging is disabled")
        
except Exception as e:
    print(f"[ERROR] Audit logging demo failed: {e}")

# =========================================================================
# Demo 6: Enhanced summary
# =========================================================================

print("\n" + "-" * 60)
print("Demo 6: Enhanced Summary")
print("-" * 60)

try:
    engine.print_enhanced_summary()
except Exception as e:
    print(f"[ERROR] Summary demo failed: {e}")

# =========================================================================
# Complete!
# =========================================================================

print("\n" + "=" * 80)
print("Enhanced Optimization Demo Complete!")
print("=" * 80)

print("""
[Summary of Week 2 Optimizations]

1. Parallel Execution
   - Uses ThreadPoolExecutor for parallel hypothesis verification
   - Configurable number of workers (1-32)
   - Expected speedup: 2-4x depending on hypothesis count

2. Configuration Management
   - Structured configuration dataclass with validation
   - Environment variable support
   - Dynamic configuration updates
   - Prevents invalid configurations

3. Audit Logging
   - Structured audit events with timestamps
   - Thread-safe logging
   - JSONL format for easy parsing
   - Tracks all important operations

4. Smart Caching
   - TTL-based cache (configurable)
   - Cache statistics available
   - Can be disabled/enabled dynamically

[Next Steps]

1. Try the performance benchmark:
   python examples/performance_benchmark_test.py

2. Integrate into your workflow:
   from ai_test_asset_center.optimized_discovery_engine import create_enhanced_engine
   engine = create_enhanced_engine()

3. Monitor performance:
   engine.print_enhanced_summary()

4. Check audit logs:
   Check the 'logs/audit_log.jsonl' file
""")
