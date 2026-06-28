from __future__ import annotations

"""
QualiBug AI - Optimized Discovery Engine

This is an optimized version of AutonomousDiscoveryEngine
Implemented via inheritance, zero risk, fully backward compatible

Week 1 Optimizations:
- OpenAPI Spec caching
- Performance monitoring
- Retry mechanism
- Enhanced error handling
- Checkpoint mechanism
"""

import json
import logging
import time
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable

# Import original engine
from .discovery_engine import AutonomousDiscoveryEngine, DiscoveryFinding

# Import optimization modules
from .performance_monitor import measure_time, PerformanceMetrics
from .safe_cache import cached, SafeCache, enable_cache, disable_cache, get_cache_stats
from .safe_retry import safe_retry, safe_retry_network

# Configure logging
logger = logging.getLogger(__name__)
if not logger.handlers:
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


@dataclass
class Checkpoint:
    """Checkpoint data structure"""
    run_id: str
    stage: str
    state: Dict[str, Any]
    timestamp: float
    stage_results: Any = None


class OptimizedDiscoveryEngine(AutonomousDiscoveryEngine):
    """Optimized autonomous discovery engine
    
    Week 1 Optimizations:
    - OpenAPI Spec caching
    - Performance monitoring
    - Retry mechanism
    - Enhanced error handling
    - Checkpoint mechanism
    """
    
    def __init__(self, base_url: str = "http://127.0.0.1:8000/api", 
                 enable_checkpoints: bool = True,
                 checkpoint_dir: Optional[Path] = None):
        """Initialize optimized engine"""
        
        # Call parent initialization
        super().__init__(base_url)
        
        # Optimization config
        self._enable_checkpoints = enable_checkpoints
        self._checkpoint_dir = checkpoint_dir or Path("checkpoints")
        if enable_checkpoints:
            self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache
        self._route_map_cache = None
        self._route_map_last_update = 0.0
        self._route_map_cache_ttl = 300.0  # 5 minutes
        
        # Performance metrics
        self._metrics = PerformanceMetrics()
        
        # Checkpoint manager
        self._checkpoints: Dict[str, Checkpoint] = {}
        
        # Run ID
        self._current_run_id = f"run_{int(time.time())}"
        
        logger.info(f"[OptimizedEngine] Initialized, checkpoints: {'enabled' if enable_checkpoints else 'disabled'}")
    
    # =========================================================================
    # Week 1 Optimization: OpenAPI Spec Cache
    # =========================================================================
    
    @measure_time("build_route_map")
    @cached(ttl_seconds=300.0, key_prefix="route_map")
    def _build_route_map(self) -> dict:
        """Build route map (optimized version)"""
        logger.info("[OptimizedEngine] Building route map...")
        
        # Call parent implementation
        result = super()._build_route_map()
        
        logger.info(f"[OptimizedEngine] Route map built, {len(result)} routes")
        return result
    
    # =========================================================================
    # Week 1 Optimization: Performance Monitoring + Retry
    # =========================================================================
    
    @measure_time("stage_read")
    def stage_read(self, prd_text: str, api_spec_text: str, project_context: dict = None) -> dict:
        """Stage 1: Reader - with performance monitoring"""
        logger.info("[OptimizedEngine] Starting Stage 1: Reader")
        
        start_time = time.time()
        result = super().stage_read(prd_text, api_spec_text, project_context)
        elapsed = time.time() - start_time
        
        logger.info(f"[OptimizedEngine] Stage 1 completed in {elapsed:.1f}s")
        
        # Save checkpoint
        if self._enable_checkpoints:
            self._save_checkpoint("stage_read", {"reader_output": result})
        
        return result
    
    @measure_time("stage_reason_all")
    def stage_reason_all(self, reader_output: dict, prd_text: str, api_spec: str,
                       prior_findings: list[dict] = None) -> list[dict]:
        """Stage 2: Reasoner - with performance monitoring"""
        logger.info("[OptimizedEngine] Starting Stage 2: Reasoner")
        
        start_time = time.time()
        result = super().stage_reason_all(reader_output, prd_text, api_spec, prior_findings)
        elapsed = time.time() - start_time
        
        logger.info(f"[OptimizedEngine] Stage 2 completed in {elapsed:.1f}s, {len(result)} hypotheses")
        
        # Save checkpoint
        if self._enable_checkpoints:
            self._save_checkpoint("stage_reason", {"hypotheses": result})
        
        return result
    
    @measure_time("stage_execute")
    def stage_execute(self, hypotheses: list[dict], route_map: dict = None) -> list[dict]:
        """Stage 3: Executor - with performance monitoring + retry"""
        logger.info("[OptimizedEngine] Starting Stage 3: Executor")
        
        start_time = time.time()
        
        # Use cached route_map
        if route_map is None:
            route_map = self._build_route_map()
        
        result = super().stage_execute(hypotheses, route_map)
        elapsed = time.time() - start_time
        
        logger.info(f"[OptimizedEngine] Stage 3 completed in {elapsed:.1f}s, {len(result)} executions")
        
        # Save checkpoint
        if self._enable_checkpoints:
            self._save_checkpoint("stage_execute", {"execution_results": result})
        
        return result
    
    @measure_time("stage_verify")
    def stage_verify(self, execution_results: list[dict]) -> list[DiscoveryFinding]:
        """Stage 4: Verifier - with performance monitoring"""
        logger.info("[OptimizedEngine] Starting Stage 4: Verifier")
        
        start_time = time.time()
        result = super().stage_verify(execution_results)
        elapsed = time.time() - start_time
        
        confirmed = [f for f in result if f.verdict == "confirmed"]
        
        logger.info(f"[OptimizedEngine] Stage 4 completed in {elapsed:.1f}s, {len(confirmed)} confirmed")
        
        # Save checkpoint
        if self._enable_checkpoints:
            self._save_checkpoint("stage_verify", {"findings": [f.__dict__ for f in result]})
        
        return result
    
    # =========================================================================
    # Week 1 Optimization: HTTP Retry + Enhanced Error Handling
    # =========================================================================
    
    @safe_retry_network
    def _http(self, method: str, path: str, data=None, no_auth=False, role="admin"):
        """HTTP request (with retry)"""
        try:
            return super()._http(method, path, data, no_auth, role)
        except Exception as e:
            logger.error(f"[OptimizedEngine] HTTP request failed: {method} {path} - {e}")
            return {"_http": 0, "_error": str(e)}
    
    # =========================================================================
    # Enhanced Error Handling
    # =========================================================================
    
    def _login(self):
        """Login (enhanced error handling)"""
        try:
            result = super()._login()
            if result:
                logger.info("[OptimizedEngine] Login successful")
            else:
                logger.warning("[OptimizedEngine] Login failed (production mode or connection issue)")
            return result
        except Exception as e:
            logger.error(f"[OptimizedEngine] Login exception: {e}")
            return False
    
    # =========================================================================
    # Checkpoint Mechanism
    # =========================================================================
    
    def _save_checkpoint(self, stage: str, state: Dict[str, Any]) -> None:
        """Save checkpoint"""
        if not self._enable_checkpoints:
            return
        
        checkpoint = Checkpoint(
            run_id=self._current_run_id,
            stage=stage,
            state=state,
            timestamp=time.time()
        )
        
        self._checkpoints[stage] = checkpoint
        
        # Save to file
        checkpoint_file = self._checkpoint_dir / f"{self._current_run_id}_{stage}.json"
        try:
            checkpoint_data = {
                "run_id": checkpoint.run_id,
                "stage": checkpoint.stage,
                "state": checkpoint.state,
                "timestamp": checkpoint.timestamp
            }
            checkpoint_file.write_text(json.dumps(checkpoint_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.debug(f"[OptimizedEngine] Checkpoint saved: {stage}")
        except Exception as e:
            logger.warning(f"[OptimizedEngine] Checkpoint save failed: {e}")
    
    def load_checkpoint(self, stage: str) -> Optional[Checkpoint]:
        """Load checkpoint"""
        if stage in self._checkpoints:
            return self._checkpoints[stage]
        
        # Try to load from file
        self._checkpoint_dir.mkdir(exist_ok=True)
        
        checkpoint_files = sorted(
            self._checkpoint_dir.glob(f"*_{stage}.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        for checkpoint_file in checkpoint_files:
            try:
                data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                checkpoint = Checkpoint(
                    run_id=data["run_id"],
                    stage=data["stage"],
                    state=data["state"],
                    timestamp=data["timestamp"]
                )
                self._checkpoints[stage] = checkpoint
                logger.info(f"[OptimizedEngine] Checkpoint loaded: {stage}")
                return checkpoint
            except Exception:
                continue
        
        return None
    
    def list_checkpoints(self) -> List[str]:
        """List available checkpoints"""
        return list(self._checkpoints.keys())
    
    def clear_checkpoints(self) -> None:
        """Clear checkpoints"""
        self._checkpoints.clear()
        
        # Clear files
        if self._checkpoint_dir.exists():
            for f in self._checkpoint_dir.glob("*.json"):
                try:
                    f.unlink()
                except Exception:
                    pass
        
        logger.info("[OptimizedEngine] Checkpoints cleared")
    
    # =========================================================================
    # Performance Summary
    # =========================================================================
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary"""
        from .performance_monitor import get_performance_summary
        return get_performance_summary()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return get_cache_stats()
    
    def print_optimization_summary(self) -> None:
        """Print optimization summary"""
        print("\n" + "=" * 80)
        print("QualiBug AI - Optimization Summary")
        print("=" * 80)
        
        perf_summary = self.get_performance_summary()
        if perf_summary:
            print("\n[Performance Metrics]")
            print(perf_summary)
        
        cache_stats = self.get_cache_stats()
        if cache_stats:
            print("\n[Cache Statistics]")
            print(cache_stats)
        
        if self._checkpoints:
            print("\n[Checkpoints]")
            for stage, cp in self._checkpoints.items():
                print(f"  - {stage}: {time.ctime(cp.timestamp)}")
        
        print("=" * 80 + "\n")


# Convenient factory function
def create_optimized_engine(
    base_url: str = "http://127.0.0.1:8000/api",
    enable_checkpoints: bool = True,
    enable_cache: bool = True
) -> OptimizedDiscoveryEngine:
    """Create optimized discovery engine
    
    Args:
        base_url: Base URL
        enable_checkpoints: Enable checkpoints
        enable_cache: Enable cache
    
    Returns:
        Optimized engine instance
    """
    if enable_cache:
        enable_cache()
    else:
        disable_cache()
    
    engine = OptimizedDiscoveryEngine(
        base_url=base_url,
        enable_checkpoints=enable_checkpoints
    )
    
    logger.info("[OptimizedEngine] Engine created successfully")
    return engine


# Backward compatible alias
OptimizedEngine = OptimizedDiscoveryEngine
