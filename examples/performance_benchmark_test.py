#!/usr/bin/env python
"""
QualiBug AI - Performance Benchmark Test

This script runs a performance comparison between the original engine
and the optimized engine using real project data from benchmark_suite_v3.
"""

from __future__ import annotations

import sys
import time
import json
from pathlib import Path

# Add project root to path
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Benchmark data path
BENCHMARK_ROOT = Path("D:/QualiBug-AI/benchmark_suite_v3/QualiBug_Benchmark_Suite_v3")
TEST_PROJECT = "01_ecommerce_order_payment_inventory"


def load_project_data(project_name: str) -> dict:
    """Load test project data"""
    project_dir = BENCHMARK_ROOT / "projects" / project_name
    input_dir = project_dir / "input"
    
    data = {
        "prd": "",
        "api_spec": "",
        "openapi": "",
        "project_name": project_name
    }
    
    # Load PRD
    prd_path = input_dir / "PRD.md"
    if prd_path.exists():
        data["prd"] = prd_path.read_text(encoding="utf-8")
    
    # Load API spec
    api_path = input_dir / "API.md"
    if api_path.exists():
        data["api_spec"] = api_path.read_text(encoding="utf-8")
    
    # Load OpenAPI yaml
    openapi_path = input_dir / "openapi.yaml"
    if openapi_path.exists():
        data["openapi"] = openapi_path.read_text(encoding="utf-8")
    
    print(f"[OK] Loaded project: {project_name}")
    print(f"  PRD length: {len(data['prd'])} chars")
    print(f"  API spec length: {len(data['api_spec'])} chars")
    print(f"  OpenAPI length: {len(data['openapi'])} chars")
    
    return data


def test_original_engine(project_data: dict) -> dict:
    """Test original AutonomousDiscoveryEngine"""
    print("\n" + "=" * 80)
    print("TEST 1: Original Engine (baseline)")
    print("=" * 80)
    
    from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
    
    start_time = time.time()
    
    try:
        # Initialize engine
        print("\n[1/4] Initializing engine...")
        engine = AutonomousDiscoveryEngine()
        
        # Run Stage 1: Reader
        print("\n[2/4] Stage 1 - Reader...")
        stage1_start = time.time()
        reader_output = engine.stage_read(
            project_data["prd"],
            project_data["api_spec"]
        )
        stage1_time = time.time() - stage1_start
        print(f"  Stage 1 completed in {stage1_time:.2f}s")
        
        # Run Stage 2: Reasoner
        print("\n[3/4] Stage 2 - Reasoner...")
        stage2_start = time.time()
        hypotheses = engine.stage_reason_all(
            reader_output,
            project_data["prd"],
            project_data["api_spec"]
        )
        stage2_time = time.time() - stage2_start
        print(f"  Stage 2 completed in {stage2_time:.2f}s")
        print(f"  Generated {len(hypotheses)} hypotheses")
        
        # Build route map
        print("\n[4/4] Building route map...")
        route_map_start = time.time()
        route_map = engine._build_route_map()
        route_map_time = time.time() - route_map_start
        print(f"  Route map built in {route_map_time:.2f}s")
        
        total_time = time.time() - start_time
        
        result = {
            "engine": "original",
            "success": True,
            "total_time": total_time,
            "stage1_time": stage1_time,
            "stage2_time": stage2_time,
            "route_map_time": route_map_time,
            "hypotheses_count": len(hypotheses),
            "route_map_size": len(route_map)
        }
        
        print(f"\n[OK] Original engine test completed in {total_time:.2f}s")
        
        return result
        
    except Exception as e:
        print(f"[ERROR] Original engine test failed: {e}")
        import traceback
        traceback.print_exc()
        return {
            "engine": "original",
            "success": False,
            "error": str(e),
            "total_time": time.time() - start_time
        }


def test_optimized_engine(project_data: dict, run_number: int = 1) -> dict:
    """Test OptimizedDiscoveryEngine"""
    print("\n" + "=" * 80)
    print(f"TEST 2: Optimized Engine (run {run_number})")
    print("=" * 80)
    
    from ai_test_asset_center.optimized_discovery_engine import create_optimized_engine
    
    start_time = time.time()
    
    try:
        # Initialize engine
        print("\n[1/5] Initializing optimized engine...")
        engine = create_optimized_engine(
            enable_checkpoints=True,
            enable_cache=True
        )
        
        # Run Stage 1: Reader
        print("\n[2/5] Stage 1 - Reader...")
        stage1_start = time.time()
        reader_output = engine.stage_read(
            project_data["prd"],
            project_data["api_spec"]
        )
        stage1_time = time.time() - stage1_start
        print(f"  Stage 1 completed in {stage1_time:.2f}s")
        
        # Run Stage 2: Reasoner
        print("\n[3/5] Stage 2 - Reasoner...")
        stage2_start = time.time()
        hypotheses = engine.stage_reason_all(
            reader_output,
            project_data["prd"],
            project_data["api_spec"]
        )
        stage2_time = time.time() - stage2_start
        print(f"  Stage 2 completed in {stage2_time:.2f}s")
        print(f"  Generated {len(hypotheses)} hypotheses")
        
        # Build route map (twice to test cache)
        print("\n[4/5] Building route map (first call)...")
        route_map_start1 = time.time()
        route_map1 = engine._build_route_map()
        route_map_time1 = time.time() - route_map_start1
        print(f"  Route map built in {route_map_time1:.2f}s (first call)")
        
        print("\n[5/5] Building route map (second call - should hit cache)...")
        route_map_start2 = time.time()
        route_map2 = engine._build_route_map()
        route_map_time2 = time.time() - route_map_start2
        print(f"  Route map built in {route_map_time2:.4f}s (second call)")
        
        # Print summary
        print("\n[Summary] Optimization engine performance:")
        engine.print_optimization_summary()
        
        total_time = time.time() - start_time
        
        result = {
            "engine": "optimized",
            "run_number": run_number,
            "success": True,
            "total_time": total_time,
            "stage1_time": stage1_time,
            "stage2_time": stage2_time,
            "route_map_time_first": route_map_time1,
            "route_map_time_second": route_map_time2,
            "hypotheses_count": len(hypotheses),
            "route_map_size": len(route_map1)
        }
        
        print(f"\n[OK] Optimized engine test completed in {total_time:.2f}s")
        
        return result
        
    except Exception as e:
        print(f"[ERROR] Optimized engine test failed: {e}")
        import traceback
        traceback.print_exc()
        return {
            "engine": "optimized",
            "run_number": run_number,
            "success": False,
            "error": str(e),
            "total_time": time.time() - start_time
        }


def print_comparison(results: list[dict]) -> None:
    """Print performance comparison"""
    print("\n" + "=" * 80)
    print("PERFORMANCE COMPARISON")
    print("=" * 80)
    
    original = next((r for r in results if r["engine"] == "original"), None)
    optimized_runs = [r for r in results if r["engine"] == "optimized"]
    
    if original and original["success"]:
        print(f"\n[Original Engine]")
        print(f"  Total time: {original['total_time']:.2f}s")
        print(f"  Stage 1: {original['stage1_time']:.2f}s")
        print(f"  Stage 2: {original['stage2_time']:.2f}s")
        print(f"  Route map: {original['route_map_time']:.2f}s")
        print(f"  Hypotheses: {original['hypotheses_count']}")
        print(f"  Route map size: {original['route_map_size']}")
    
    for opt in optimized_runs:
        if opt["success"]:
            print(f"\n[Optimized Engine - Run {opt['run_number']}]")
            print(f"  Total time: {opt['total_time']:.2f}s")
            print(f"  Stage 1: {opt['stage1_time']:.2f}s")
            print(f"  Stage 2: {opt['stage2_time']:.2f}s")
            print(f"  Route map (first): {opt['route_map_time_first']:.2f}s")
            print(f"  Route map (second): {opt['route_map_time_second']:.4f}s")
            print(f"  Hypotheses: {opt['hypotheses_count']}")
            print(f"  Route map size: {opt['route_map_size']}")
            
            if original and original["success"]:
                speedup = (original["total_time"] - opt["total_time"]) / original["total_time"] * 100
                cache_improvement = (opt["route_map_time_first"] - opt["route_map_time_second"]) / opt["route_map_time_first"] * 100 if opt["route_map_time_first"] > 0 else 0
                print(f"\n  [Performance Improvements]")
                print(f"  Total speedup: {speedup:+.1f}%")
                print(f"  Cache improvement: {cache_improvement:.1f}% (first vs second call)")
    
    print("\n" + "=" * 80)


def save_results(results: list[dict], output_path: Path) -> None:
    """Save benchmark results to JSON"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.time(),
            "project": TEST_PROJECT,
            "results": results
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n[OK] Results saved to: {output_path}")


def main():
    """Main benchmark function"""
    print("=" * 80)
    print("QualiBug AI - Performance Benchmark Test")
    print("=" * 80)
    print(f"Benchmark project: {TEST_PROJECT}")
    print(f"Data path: {BENCHMARK_ROOT}")
    
    # Load test data
    try:
        project_data = load_project_data(TEST_PROJECT)
    except Exception as e:
        print(f"[ERROR] Failed to load project data: {e}")
        print("\nNote: The benchmark test requires actual project data.")
        print("This is a demo showing the optimization framework.")
        print("Let's create a simulated benchmark result...")
        
        # Create simulated results for demonstration
        simulated_results = [
            {
                "engine": "original",
                "success": True,
                "total_time": 30.5,
                "stage1_time": 12.0,
                "stage2_time": 15.0,
                "route_map_time": 3.5,
                "hypotheses_count": 50,
                "route_map_size": 25
            },
            {
                "engine": "optimized",
                "run_number": 1,
                "success": True,
                "total_time": 25.0,
                "stage1_time": 12.0,
                "stage2_time": 15.0,
                "route_map_time_first": 3.5,
                "route_map_time_second": 0.001,
                "hypotheses_count": 50,
                "route_map_size": 25
            }
        ]
        print_comparison(simulated_results)
        return
    
    # Run tests
    results = []
    
    # Test original engine
    results.append(test_original_engine(project_data))
    
    # Test optimized engine (twice to see cache effect)
    results.append(test_optimized_engine(project_data, run_number=1))
    results.append(test_optimized_engine(project_data, run_number=2))
    
    # Print comparison
    print_comparison(results)
    
    # Save results
    output_path = repo_root / "benchmark_results" / f"benchmark_{int(time.time())}.json"
    save_results(results, output_path)
    
    print("\n" + "=" * 80)
    print("Benchmark complete!")
    print("=" * 80)


if __name__ == "__main__":
    main()
