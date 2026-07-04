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


# =========================================================================
# Week 2 Optimizations: Parallel Execution, Configuration, Audit Logging
# =========================================================================

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================================================================
# Configuration Management
# =========================================================================

@dataclass
class EngineConfig:
    """Engine configuration with validation"""
    # Cache settings
    route_map_cache_ttl: float = 300.0
    
    # Parallelism settings
    max_workers: int = 4
    enable_parallel_execution: bool = True
    
    # Retry settings
    max_retries: int = 3
    initial_retry_delay: float = 0.5
    
    # Logging settings
    enable_audit_log: bool = True
    audit_log_dir: Path = Path("logs")
    
    @classmethod
    def from_env(cls) -> "EngineConfig":
        """Load configuration from environment variables"""
        return cls(
            route_map_cache_ttl=float(os.environ.get("QUALIBUG_CACHE_TTL", "300")),
            max_workers=int(os.environ.get("QUALIBUG_MAX_WORKERS", "4")),
            enable_parallel_execution=os.environ.get("QUALIBUG_PARALLEL", "true").lower() in ("true", "1", "yes"),
            max_retries=int(os.environ.get("QUALIBUG_MAX_RETRIES", "3")),
            initial_retry_delay=float(os.environ.get("QUALIBUG_RETRY_DELAY", "0.5")),
            enable_audit_log=os.environ.get("QUALIBUG_AUDIT_LOG", "true").lower() in ("true", "1", "yes"),
            audit_log_dir=Path(os.environ.get("QUALIBUG_AUDIT_DIR", "logs"))
        )
    
    def validate(self) -> list[str]:
        """Validate configuration, returns list of errors"""
        errors = []
        
        if self.route_map_cache_ttl < 0:
            errors.append("route_map_cache_ttl cannot be negative")
        
        if self.max_workers < 1 or self.max_workers > 32:
            errors.append(f"max_workers must be between 1 and 32, got {self.max_workers}")
        
        if self.max_retries < 0:
            errors.append("max_retries cannot be negative")
        
        return errors
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "route_map_cache_ttl": self.route_map_cache_ttl,
            "max_workers": self.max_workers,
            "enable_parallel_execution": self.enable_parallel_execution,
            "max_retries": self.max_retries,
            "initial_retry_delay": self.initial_retry_delay,
            "enable_audit_log": self.enable_audit_log,
            "audit_log_dir": str(self.audit_log_dir)
        }


# =========================================================================
# Audit Logging
# =========================================================================

@dataclass
class AuditEvent:
    """Audit event data structure"""
    event_type: str
    actor: str
    action: str
    resource: str
    details: Dict[str, Any]
    timestamp: float
    success: bool


class AuditLogger:
    """Audit logger for tracking important operations"""
    
    def __init__(self, log_dir: Path = Path("logs")):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._event_count = 0
    
    def _write_event(self, event: AuditEvent) -> None:
        """Write event to audit log"""
        with self._lock:
            try:
                log_file = self.log_dir / "audit_log.jsonl"
                
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "event_type": event.event_type,
                        "actor": event.actor,
                        "action": event.action,
                        "resource": event.resource,
                        "details": event.details,
                        "timestamp": event.timestamp,
                        "success": event.success
                    }, ensure_ascii=False) + "\n")
                
                self._event_count += 1
                
            except Exception as e:
                logger.warning(f"[AuditLogger] Failed to write audit event: {e}")
    
    def log_discovery_start(self, actor: str, project: str) -> None:
        """Log discovery start"""
        self._write_event(AuditEvent(
            event_type="discovery_start",
            actor=actor,
            action="start_discovery",
            resource=project,
            details={},
            timestamp=time.time(),
            success=True
        ))
    
    def log_discovery_complete(self, actor: str, project: str, 
                               findings_count: int, confirmed_count: int) -> None:
        """Log discovery completion"""
        self._write_event(AuditEvent(
            event_type="discovery_complete",
            actor=actor,
            action="complete_discovery",
            resource=project,
            details={
                "findings_count": findings_count,
                "confirmed_count": confirmed_count
            },
            timestamp=time.time(),
            success=True
        ))
    
    def log_stage_complete(self, actor: str, stage: str, 
                           duration: float, details: Dict[str, Any] = None) -> None:
        """Log stage completion"""
        self._write_event(AuditEvent(
            event_type="stage_complete",
            actor=actor,
            action=f"complete_{stage}",
            resource=stage,
            details={
                "duration": duration,
                **(details or {})
            },
            timestamp=time.time(),
            success=True
        ))
    
    def get_event_count(self) -> int:
        """Get total event count"""
        with self._lock:
            return self._event_count


# =========================================================================
# Enhanced OptimizedDiscoveryEngine with Week 2 Features
# =========================================================================

class OptimizedDiscoveryEngineV2(OptimizedDiscoveryEngine):
    """[DEPRECATED] Enhanced discovery engine with Week 2 optimizations

    .. deprecated::
        V2's ``_execute_single_hypothesis`` is a stub that returns
        ``verdict="inconclusive"`` for every hypothesis.  It bypasses the
        real execution logic in ``AutonomousDiscoveryEngine.stage_execute``.
        Use ``OptimizedDiscoveryEngine`` (V1) or ``AutonomousDiscoveryEngine``
        directly for production workloads.

    Week 2 Optimizations:
    - Parallel hypothesis execution
    - Configuration management
    - Audit logging
    - Smart caching strategies
    """
    
    def __init__(self, 
                 base_url: str = "http://127.0.0.1:8000/api",
                 enable_checkpoints: bool = True,
                 checkpoint_dir: Optional[Path] = None,
                 config: Optional[EngineConfig] = None):
        """Initialize enhanced optimized engine"""
        
        # Call parent initialization
        super().__init__(
            base_url=base_url,
            enable_checkpoints=enable_checkpoints,
            checkpoint_dir=checkpoint_dir
        )
        
        # Load or use default configuration
        self._config = config or EngineConfig.from_env()
        
        # Validate configuration
        config_errors = self._config.validate()
        if config_errors:
            logger.warning(f"[OptimizedEngineV2] Configuration warnings: {config_errors}")
        
        # Initialize audit logger
        self._audit_logger: Optional[AuditLogger] = None
        if self._config.enable_audit_log:
            self._audit_logger = AuditLogger(self._config.audit_log_dir)
        
        logger.info(f"[OptimizedEngineV2] Initialized with config: {self._config.to_dict()}")
    
    @measure_time("stage_execute_parallel")
    def stage_execute_parallel(self, hypotheses: list[dict], 
                                route_map: dict = None) -> list[dict]:
        """Execute hypotheses in parallel
        
        This is an enhanced version of stage_execute that uses
        a thread pool for parallel execution of independent hypotheses.
        
        Args:
            hypotheses: List of hypotheses to verify
            route_map: Route mapping for API calls
            
        Returns:
            List of execution results
        """
        logger.info("[OptimizedEngineV2] Starting parallel hypothesis execution")
        
        if not self._config.enable_parallel_execution:
            logger.info("[OptimizedEngineV2] Parallel execution disabled, falling back to serial")
            return self.stage_execute(hypotheses, route_map)
        
        if route_map is None:
            route_map = self._build_route_map()
        
        results = []
        errors = []
        lock = threading.Lock()
        
        # Helper function for executing a single hypothesis
        def execute_single(index: int, hypothesis: dict) -> dict:
            try:
                result = self._execute_single_hypothesis(hypothesis, route_map)
                with lock:
                    results.append(result)
                return result
            except Exception as e:
                error = f"[Hypothesis {index}] Execution failed: {e}"
                logger.error(error)
                with lock:
                    errors.append(error)
                return {
                    "hypothesis_id": hypothesis.get("hypothesis_id", f"error_{index}"),
                    "title": hypothesis.get("title", "Error in hypothesis"),
                    "error": str(e),
                    "execution_error": True
                }
        
        # Execute in parallel
        start_time = time.time()
        if not hypotheses:
            return []
        max_workers = min(self._config.max_workers, len(hypotheses))
        
        logger.info(f"[OptimizedEngineV2] Executing {len(hypotheses)} hypotheses with {max_workers} workers")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            
            for i, hypothesis in enumerate(hypotheses):
                future = executor.submit(execute_single, i, hypothesis)
                futures.append(future)
            
            # Wait for all futures to complete
            for i, future in enumerate(as_completed(futures)):
                try:
                    future.result()
                    if (i + 1) % 10 == 0:
                        logger.info(f"[OptimizedEngineV2] Progress: {i + 1}/{len(hypotheses)} hypotheses processed")
                except Exception as e:
                    logger.error(f"[OptimizedEngineV2] Future failed: {e}")
        
        total_time = time.time() - start_time
        
        logger.info(f"[OptimizedEngineV2] Parallel execution completed in {total_time:.2f}s")
        logger.info(f"[OptimizedEngineV2] Results: {len(results)} successful, {len(errors)} errors")
        
        # Save checkpoint
        if self._enable_checkpoints:
            self._save_checkpoint("stage_execute_parallel", {"execution_results": results})
        
        # Log audit event
        if self._audit_logger:
            self._audit_logger.log_stage_complete(
                actor="system",
                stage="execute_parallel",
                duration=total_time,
                details={
                    "hypotheses_count": len(hypotheses),
                    "results_count": len(results),
                    "errors_count": len(errors)
                }
            )
        
        return results
    
    def _execute_single_hypothesis(self, hypothesis: dict, 
                                    route_map: dict) -> dict:
        """Execute a single hypothesis (helper for parallel execution)
        
        This is a wrapper around the original execution logic,
        extracted to support parallel execution.
        """
        # For this demo, we'll use a simplified version
        # In real implementation, this would be extracted from stage_execute
        
        result = {
            "hypothesis_id": hypothesis.get("hypothesis_id", "unknown"),
            "title": hypothesis.get("title", "Unknown"),
            "severity": hypothesis.get("severity", "P2"),
            "verdict": "inconclusive",
            "execution_timestamp": time.time()
        }
        
        return result
    
    def get_config(self) -> EngineConfig:
        """Get current configuration"""
        return self._config
    
    def update_config(self, updates: Dict[str, Any]) -> EngineConfig:
        """Update configuration"""
        for key, value in updates.items():
            if hasattr(self._config, key):
                setattr(self._config, key, value)
        
        # Validate updated config
        config_errors = self._config.validate()
        if config_errors:
            logger.warning(f"[OptimizedEngineV2] Configuration warnings after update: {config_errors}")
        
        logger.info(f"[OptimizedEngineV2] Configuration updated: {updates}")
        return self._config
    
    def get_audit_logger(self) -> Optional[AuditLogger]:
        """Get audit logger"""
        return self._audit_logger
    
    def print_enhanced_summary(self) -> None:
        """Print enhanced summary including Week 2 features"""
        print("\n" + "=" * 80)
        print("QualiBug AI - Enhanced Optimization Summary (Week 2)")
        print("=" * 80)
        
        super().print_optimization_summary()
        
        print("\n[Configuration]")
        print(f"  Route map cache TTL: {self._config.route_map_cache_ttl}s")
        print(f"  Max workers: {self._config.max_workers}")
        print(f"  Parallel execution: {'enabled' if self._config.enable_parallel_execution else 'disabled'}")
        print(f"  Max retries: {self._config.max_retries}")
        print(f"  Audit log: {'enabled' if self._config.enable_audit_log else 'disabled'}")
        
        if self._audit_logger:
            print(f"  Audit events logged: {self._audit_logger.get_event_count()}")
        
        print("=" * 80 + "\n")


# =========================================================================
# Enhanced Factory Function
# =========================================================================

def create_enhanced_engine(
    base_url: str = "http://127.0.0.1:8000/api",
    enable_checkpoints: bool = True,
    enable_cache: bool = True,
    enable_parallel: bool = True,
    enable_audit: bool = True
) -> OptimizedDiscoveryEngineV2:
    """Create enhanced discovery engine with Week 2 optimizations
    
    Args:
        base_url: Base URL for the target system
        enable_checkpoints: Enable checkpointing
        enable_cache: Enable caching
        enable_parallel: Enable parallel execution
        enable_audit: Enable audit logging
        
    Returns:
        Enhanced optimized engine instance
    """
    if enable_cache:
        enable_cache()
    else:
        disable_cache()
    
    config = EngineConfig(
        enable_parallel_execution=enable_parallel,
        enable_audit_log=enable_audit
    )
    
    engine = OptimizedDiscoveryEngineV2(
        base_url=base_url,
        enable_checkpoints=enable_checkpoints,
        config=config
    )
    
    logger.info("[OptimizedEngineV2] Enhanced engine created successfully")
    return engine


# =========================================================================
# Week 3: Complete Optimization Engine
# =========================================================================

class OptimizedDiscoveryEngineV3(OptimizedDiscoveryEngineV2):
    """[DEPRECATED] Complete optimization engine with Week 3 features

    .. deprecated::
        V3's ``export_findings`` returns hardcoded mock data instead of real
        findings.  Use ``OptimizedDiscoveryEngine`` (V1) for production.

    Week 3 Optimizations:
    - Report exporting (JSON, CSV, Markdown, HTML)
    - External issue tracker integration
    - Performance visualization
    """
    
    def export_findings(self, output_dir: Path, 
                        formats: List[str] = ["json", "csv", "md", "html"]) -> Dict[str, Path]:
        """Export findings to multiple formats
        
        Args:
            output_dir: Directory to save reports
            formats: Formats to export
            
        Returns:
            Dictionary mapping formats to file paths
        """
        from .report_exporter import ReportExporter, create_exporter_from_results
        
        # Load real findings from checkpoints (never simulate)
        findings = []
        verify_cp = self._checkpoints.get("stage_verify")
        if verify_cp and verify_cp.state.get("findings"):
            findings = verify_cp.state["findings"]
        else:
            # Try loading from disk
            cp_file = self._checkpoint_dir / "stage_verify.json"
            if cp_file.exists():
                try:
                    import json
                    data = json.loads(cp_file.read_text(encoding="utf-8"))
                    findings = data.get("state", {}).get("findings", [])
                except Exception:
                    pass
        
        if not findings:
            print(f"  [WARN] No verified findings available — export returned empty", flush=True)
            return {}
        
        exporter = create_exporter_from_results(findings, self._current_run_id)
        return exporter.export_all(output_dir, formats)
    
    def sync_to_tracker(self, findings: List[Dict[str, Any]], 
                        tracker_type: str = "github",
                        config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Sync findings to an external issue tracker
        
        Args:
            findings: Findings to sync
            tracker_type: Type of tracker ("jira", "github")
            config: Optional tracker configuration
            
        Returns:
            List of created issue information
        """
        from .external_integration import sync_findings_to_tracker
        
        logger.info(f"[OptimizedEngineV3] Syncing {len(findings)} findings to {tracker_type}")
        
        return sync_findings_to_tracker(findings, tracker_type, config)
    
    def run_complete_workflow(self, prd_text: str, 
                              api_spec_text: str) -> Dict[str, Any]:
        """Run the complete discovery and optimization workflow
        
        Args:
            prd_text: PRD document text
            api_spec_text: API spec text
            
        Returns:
            Complete results dictionary
        """
        logger.info("[OptimizedEngineV3] Starting complete workflow")
        
        results = {}
        
        # Log discovery start
        if self._audit_logger:
            self._audit_logger.log_discovery_start("system", "auto_discovery")
        
        # Run the stages
        try:
            # Stage 1: Read
            reader_output = self.stage_read(prd_text, api_spec_text)
            results["reader"] = reader_output
            
            # Stage 2: Reason
            hypotheses = self.stage_reason_all(reader_output, prd_text, api_spec_text)
            results["hypotheses"] = hypotheses
            
            # Stage 3: Execute (parallel)
            exec_results = self.stage_execute_parallel(hypotheses)
            results["execution"] = exec_results
            
            # Stage 4: Verify
            findings = self.stage_verify(exec_results)
            results["findings"] = findings
            
            # Log completion
            if self._audit_logger:
                confirmed = sum(1 for f in findings if f.verdict == "confirmed")
                self._audit_logger.log_discovery_complete(
                    "system", "auto_discovery", 
                    len(findings), confirmed
                )
            
        except Exception as e:
            logger.error(f"[OptimizedEngineV3] Workflow failed: {e}")
            raise
        
        return results
    
    def print_complete_summary(self) -> None:
        """Print complete summary with all Week 1-3 features"""
        print("\n" + "=" * 80)
        print("QualiBug AI - Complete Optimization Summary (Week 1-3)")
        print("=" * 80)
        
        self.print_enhanced_summary()
        
        print("\n[Week 3 Features]")
        print("  Report Export: Enabled")
        print("  External Integration: Enabled")
        print("  Complete Workflow: Enabled")
        print("=" * 80 + "\n")


# =========================================================================
# Complete Factory Function
# =========================================================================

def create_complete_engine(
    base_url: str = "http://127.0.0.1:8000/api",
    enable_checkpoints: bool = True,
    enable_cache: bool = True,
    enable_parallel: bool = True,
    enable_audit: bool = True
) -> OptimizedDiscoveryEngineV3:
    """Create complete optimization engine with all Week 1-3 features
    
    Args:
        base_url: Base URL for the target system
        enable_checkpoints: Enable checkpointing
        enable_cache: Enable caching
        enable_parallel: Enable parallel execution
        enable_audit: Enable audit logging
        
    Returns:
        Complete optimized engine instance
    """
    if enable_cache:
        from .safe_cache import enable_cache as enable_cache_fn
        enable_cache_fn()
    else:
        from .safe_cache import disable_cache as disable_cache_fn
        disable_cache_fn()
    
    config = EngineConfig(
        enable_parallel_execution=enable_parallel,
        enable_audit_log=enable_audit
    )
    
    engine = OptimizedDiscoveryEngineV3(
        base_url=base_url,
        enable_checkpoints=enable_checkpoints,
        config=config
    )
    
    logger.info("[OptimizedEngineV3] Complete engine created successfully")
    return engine
