from __future__ import annotations

"""Application log analyzer — extracts error patterns from server logs.

Supports common log formats:
- Structured: ``2024-01-15 10:30:45 ERROR module.py:123 message``
- JSON lines: ``{"timestamp":"...","level":"ERROR","message":"..."}``
- Java/Spring: ``2024-01-15 10:30:45.123 ERROR 12345 --- [thread] c.p.Class : msg``
- Nginx/Apache access logs

Outputs structured findings that feed into the hypothesis generation pipeline.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class LogError:
    """A single error entry extracted from logs."""
    timestamp: str = ""
    level: str = "ERROR"
    logger: str = ""
    message: str = ""
    stack_trace: str = ""
    source_file: str = ""
    source_line: int = 0
    raw_line: str = ""


@dataclass
class ErrorCluster:
    """A cluster of similar errors, grouped by fingerprint."""
    fingerprint: str            # normalized error signature
    error_type: str             # exception class name or error category
    message_pattern: str        # template with params abstracted
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    affected_endpoints: list[str] = field(default_factory=list)
    sample_stack: str = ""
    severity: str = "P1"


@dataclass
class SlowEndpoint:
    """A slow endpoint identified from access logs."""
    path: str
    method: str = "GET"
    count: int = 0
    avg_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    max_ms: float = 0.0
    error_rate: float = 0.0  # proportion of 4xx/5xx


# ── Log format detectors and parsers ──────────────────────────────────────

# Structured log: "2024-01-15 10:30:45,123 ERROR module:123 message"
_STRUCTURED_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+'
    r'(?P<level>DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL|TRACE)\s+'
    r'(?:(?P<logger>[\w./]+)(?::(?P<line>\d+))?\s+)?'
    r'(?P<message>.*)$',
    re.I,
)

# Java/Spring Boot: "2024-01-15 10:30:45.123 ERROR 12345 --- [thread] c.p.Class : msg"
_JAVA_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[.,]\d+)\s+'
    r'(?P<level>ERROR|WARN|INFO|DEBUG|TRACE)\s+'
    r'\d+\s+---\s+\[[^\]]+\]\s+'
    r'(?P<logger>[\w.]+)\s*:\s*'
    r'(?P<message>.*)$',
)

# Nginx/Apache access log (combined format)
_ACCESS_LOG_RE = re.compile(
    r'^(?P<ip>[\d.]+)\s+\S+\s+\S+\s+'
    r'\[(?P<ts>[^\]]+)\]\s+'
    r'"(?P<method>\S+)\s+(?P<path>\S+)\s+\S+"\s+'
    r'(?P<status>\d{3})\s+'
    r'(?P<size>\d+)\s+'
    r'"(?P<referer>[^"]*)"\s+'
    r'"(?P<ua>[^"]*)"'
    r'(?:\s+(?P<rt>\d+))?',
)

# Stack trace line: "  at com.example.MyClass.method(MyClass.java:42)" or
# "  File "module.py", line 42, in function_name"
_STACK_FRAME_RE = re.compile(
    r'^\s+(?:at\s+[\w.$]+\([\w.]+:(\d+)\)|File\s+"([^"]+)",\s+line\s+(\d+))'
)

# Exception class: "java.lang.NullPointerException: msg" or "ValueError: msg"
# Also matches exceptions embedded in messages
_EXCEPTION_CLASS_RE = re.compile(
    r'\b([\w.]+(?:Exception|Error|Fault|Failure))(?::\s*(.*))?$'
)
# Search variant: find exception name anywhere in a string
_EXCEPTION_SEARCH_RE = re.compile(
    r'\b([\w.]+(?:Exception|Error|Fault|Failure))\b'
)

# Python traceback header: "Traceback (most recent call last):"
_PY_TRACEBACK_HEADER = re.compile(r'^Traceback\s*\(most recent call last\):\s*$')

# JSON log line (detection by first char)
_JSON_LOG_RE = re.compile(r'^\s*\{')


def _detect_format(first_lines: list[str]) -> str:
    """Detect the log format from the first few lines."""
    if not first_lines:
        return "unknown"
    for line in first_lines[:5]:
        line = line.strip()
        if not line:
            continue
        if _JAVA_RE.match(line):
            return "java"
        if _ACCESS_LOG_RE.match(line):
            return "access"
        if _JSON_LOG_RE.match(line):
            try:
                json.loads(line)
                return "json"
            except json.JSONDecodeError:
                pass
        if _STRUCTURED_RE.match(line):
            return "structured"
    return "structured"  # default


def _parse_json_line(line: str) -> dict[str, Any] | None:
    """Parse a JSON log line into a dict with normalized keys."""
    try:
        data = json.loads(line)
        if not isinstance(data, dict):
            return None
        # Normalize common key names
        normalized: dict[str, Any] = {}
        for key in ("timestamp", "time", "@timestamp", "ts", "datetime"):
            if key in data:
                normalized["ts"] = str(data[key])
                break
        for key in ("level", "severity", "log_level", "loglevel"):
            if key in data:
                normalized["level"] = str(data[key]).upper()
                break
        for key in ("message", "msg", "text", "body", "description"):
            if key in data:
                normalized["message"] = str(data[key])
                break
        for key in ("logger", "logger_name", "name", "component", "service"):
            if key in data:
                normalized["logger"] = str(data[key])
                break
        for key in ("stack_trace", "stacktrace", "traceback", "exception", "error"):
            if key in data and data[key]:
                normalized["stack"] = str(data[key])
                break
        for key in ("path", "url", "endpoint", "request_path"):
            if key in data:
                normalized["path"] = str(data[key])
                break
        for key in ("method", "http_method", "request_method"):
            if key in data:
                normalized["method"] = str(data[key]).upper()
                break
        for key in ("response_time", "duration", "elapsed", "rt", "latency"):
            if key in data:
                try:
                    normalized["response_time"] = float(data[key])
                except (ValueError, TypeError):
                    pass
                break
        for key in ("status", "status_code", "http_status", "response_code"):
            if key in data:
                try:
                    normalized["status"] = int(data[key])
                except (ValueError, TypeError):
                    pass
                break
        return normalized
    except (json.JSONDecodeError, TypeError):
        return None


# ── Error extraction ──────────────────────────────────────────────────────

def _normalize_error_message(msg: str) -> str:
    """Abstract dynamic values from error messages to create a fingerprint."""
    # Replace quoted strings
    msg = re.sub(r"'[^']*'", "''", msg)
    msg = re.sub(r'"[^"]*"', '"..."', msg)
    # Replace numbers
    msg = re.sub(r'\b\d+\b', 'N', msg)
    # Replace hex values
    msg = re.sub(r'0x[0-9a-fA-F]+', '0xHEX', msg)
    # Replace UUIDs
    msg = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', 'UUID', msg, flags=re.I)
    # Replace URLs
    msg = re.sub(r'https?://\S+', 'URL', msg)
    # Collapse whitespace
    msg = re.sub(r'\s+', ' ', msg).strip()
    return msg


def _error_fingerprint(error: LogError) -> str:
    """Create a stable fingerprint for clustering similar errors."""
    # Priority: exception class + normalized message
    exc_match = _EXCEPTION_CLASS_RE.match(error.message) or _EXCEPTION_SEARCH_RE.search(error.message)
    if exc_match:
        exc_class = exc_match.group(1)
        # Try to extract the exception message (text after the colon)
        exc_msg_match = _EXCEPTION_CLASS_RE.match(error.message)
        exc_msg = _normalize_error_message(exc_msg_match.group(2) if exc_msg_match and exc_msg_match.group(2) else "")
        return f"{exc_class}: {exc_msg}" if exc_msg else exc_class
    return f"{error.level}: {_normalize_error_message(error.message)}"


def _extract_affected_endpoints(stack_trace: str, log_paths: list[str]) -> list[str]:
    """Extract API paths mentioned in a stack trace or log context."""
    paths: set[str] = set()
    # Look for path-like strings in stack trace
    for match in re.finditer(r'(/[\w/\-_{}]+(?:/\{[^}]+\})?)', stack_trace):
        p = match.group(1)
        if len(p) > 3 and not p.endswith((".py", ".java", ".js", ".go", ".ts")):
            paths.add(p)
    # Add any paths from log context
    for p in log_paths:
        if p:
            paths.add(p)
    return list(paths)[:10]


def _extract_errors_structured(lines: list[str]) -> list[LogError]:
    """Extract errors from structured/semicolon-free log lines."""
    errors: list[LogError] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        match = _STRUCTURED_RE.match(line)
        if not match:
            i += 1
            continue

        level = match.group("level").upper()
        if level not in ("ERROR", "FATAL", "CRITICAL"):
            i += 1
            continue

        error = LogError(
            timestamp=match.group("ts") or "",
            level=level,
            logger=match.group("logger") or "",
            message=match.group("message") or "",
            raw_line=line,
        )
        if match.group("line"):
            try:
                error.source_line = int(match.group("line"))
            except ValueError:
                pass
        if error.logger:
            error.source_file = error.logger

        # Collect stack trace lines that follow
        j = i + 1
        stack_lines: list[str] = []
        while j < len(lines) and (not lines[j].strip() or _STACK_FRAME_RE.match(lines[j])):
            if lines[j].strip():
                stack_lines.append(lines[j].rstrip())
                # Also extract source file from traceback
                sf_match = _STACK_FRAME_RE.match(lines[j])
                if sf_match and not error.source_file:
                    error.source_file = sf_match.group(2) or sf_match.group(1) or ""
            j += 1

        if stack_lines:
            error.stack_trace = "\n".join(stack_lines)
        errors.append(error)
        i = j

    return errors


def _extract_errors_java(lines: list[str]) -> list[LogError]:
    """Extract errors from Java/Spring Boot log lines."""
    errors: list[LogError] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        match = _JAVA_RE.match(line)
        if not match:
            i += 1
            continue

        level = match.group("level").upper()
        if level not in ("ERROR", "FATAL"):
            i += 1
            continue

        error = LogError(
            timestamp=match.group("ts") or "",
            level=level,
            logger=match.group("logger") or "",
            message=match.group("message") or "",
            raw_line=line,
        )

        # Collect stack trace
        j = i + 1
        stack_lines: list[str] = []
        while j < len(lines):
            next_line = lines[j].rstrip()
            if not next_line:
                break
            if _STACK_FRAME_RE.match(next_line):
                stack_lines.append(next_line)
                # Detect "Caused by:" annotation (nested exception)
            elif next_line.startswith("Caused by:") or next_line.startswith("... "):
                stack_lines.append(next_line)
            elif _JAVA_RE.match(next_line):
                break  # next log entry
            else:
                stack_lines.append(next_line)
            j += 1

        if stack_lines:
            error.stack_trace = "\n".join(stack_lines)
        errors.append(error)
        i = j

    return errors


def _extract_errors_json(lines: list[str]) -> list[LogError]:
    """Extract errors from JSON log lines."""
    errors: list[LogError] = []
    log_paths: list[str] = []

    for line in lines:
        data = _parse_json_line(line)
        if not data:
            continue

        level = data.get("level", "").upper()
        if level not in ("ERROR", "FATAL", "CRITICAL"):
            # Still track paths for context
            if "path" in data:
                log_paths.append(str(data["path"]))
            continue

        error = LogError(
            timestamp=data.get("ts", ""),
            level=level,
            logger=data.get("logger", ""),
            message=data.get("message", ""),
            stack_trace=data.get("stack", ""),
            raw_line=line.strip(),
        )

        if "path" in data:
            log_paths.append(str(data["path"]))
        errors.append(error)

    return errors


def _extract_access_log_errors(lines: list[str]) -> list[LogError]:
    """Extract errors from nginx/apache access logs."""
    errors: list[LogError] = []
    for line in lines:
        match = _ACCESS_LOG_RE.match(line.strip())
        if not match:
            continue
        status = int(match.group("status"))
        if status < 400:
            continue
        errors.append(LogError(
            timestamp=match.group("ts") or "",
            level="ERROR" if status >= 500 else "WARN",
            message=f"HTTP {status} {match.group('method')} {match.group('path')} "
                    f"(size={match.group('size')}, rt={match.group('rt') or '?'}ms)",
            raw_line=line.strip(),
        ))
    return errors


def _cluster_errors(errors: list[LogError]) -> list[ErrorCluster]:
    """Group similar errors into clusters by fingerprint."""
    clusters: dict[str, ErrorCluster] = {}
    log_paths: list[str] = []

    for error in errors:
        fp = _error_fingerprint(error)
        if fp not in clusters:
            # Determine error type
            exc_match = _EXCEPTION_CLASS_RE.match(error.message) or _EXCEPTION_SEARCH_RE.search(error.message)
            error_type = exc_match.group(1) if exc_match else error.level

            clusters[fp] = ErrorCluster(
                fingerprint=fp,
                error_type=error_type,
                message_pattern=_normalize_error_message(error.message),
                first_seen=error.timestamp,
                severity="P0" if error.level in ("FATAL", "CRITICAL") else "P1",
            )

        c = clusters[fp]
        c.count += 1
        if error.timestamp:
            c.last_seen = error.timestamp
        if error.stack_trace and not c.sample_stack:
            c.sample_stack = error.stack_trace[:3000]
        if error.source_file:
            log_paths.append(error.source_file)

    # Post-process: extract affected endpoints from stacks
    for c in clusters.values():
        c.affected_endpoints = _extract_affected_endpoints(c.sample_stack, log_paths)

    # Sort by count descending
    return sorted(clusters.values(), key=lambda c: -c.count)


# ── Slow endpoint analysis ────────────────────────────────────────────────

def _analyze_slow_endpoints_access(lines: list[str], slow_threshold_ms: float = 1000) -> list[SlowEndpoint]:
    """Analyze access logs for slow endpoints."""
    endpoint_data: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: {"times": [], "errors": 0, "total": 0}
    )

    for line in lines:
        match = _ACCESS_LOG_RE.match(line.strip())
        if not match:
            continue
        method = match.group("method")
        raw_path = match.group("path")
        # Normalize path
        path = re.sub(r'/\d{4,}(?=/|$)', '/{id}', raw_path)
        status = int(match.group("status"))
        rt = match.group("rt")

        key = (path, method)
        data = endpoint_data[key]
        data["total"] += 1
        if status >= 400:
            data["errors"] += 1
        if rt:
            try:
                data["times"].append(float(rt))
            except ValueError:
                pass

    results: list[SlowEndpoint] = []
    for (path, method), data in endpoint_data.items():
        times = sorted(data["times"])
        if not times:
            continue
        avg = sum(times) / len(times)
        if avg < slow_threshold_ms:
            continue  # only report slow endpoints

        n = len(times)
        p50 = times[n // 2] if n > 0 else 0
        p95 = times[int(n * 0.95)] if n > 1 else times[-1]
        p99 = times[int(n * 0.99)] if n > 2 else times[-1]

        results.append(SlowEndpoint(
            path=path, method=method,
            count=data["total"],
            avg_ms=avg, p50_ms=p50, p95_ms=p95, p99_ms=p99,
            max_ms=times[-1],
            error_rate=data["errors"] / data["total"] if data["total"] else 0,
        ))

    results.sort(key=lambda s: -s.p95_ms)
    return results


def _analyze_slow_endpoints_json(lines: list[str], slow_threshold_ms: float = 1000) -> list[SlowEndpoint]:
    """Analyze JSON log lines for slow endpoints."""
    endpoint_data: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: {"times": [], "errors": 0, "total": 0}
    )

    for line in lines:
        data = _parse_json_line(line)
        if not data:
            continue
        path = data.get("path", "")
        method = data.get("method", "GET")
        rt = data.get("response_time", 0)

        if not path or not rt:
            continue
        # Normalize
        path = re.sub(r'/\d{4,}(?=/|$)', '/{id}', path)

        key = (path, method)
        ed = endpoint_data[key]
        ed["total"] += 1
        ed["times"].append(float(rt))
        if data.get("status", 200) >= 400:
            ed["errors"] += 1

    results: list[SlowEndpoint] = []
    for (path, method), data in endpoint_data.items():
        times = sorted(data["times"])
        if not times:
            continue
        avg = sum(times) / len(times)
        if avg < slow_threshold_ms:
            continue

        n = len(times)
        results.append(SlowEndpoint(
            path=path, method=method, count=data["total"],
            avg_ms=avg, p50_ms=times[n // 2] if n > 0 else 0,
            p95_ms=times[int(n * 0.95)] if n > 1 else times[-1],
            p99_ms=times[int(n * 0.99)] if n > 2 else times[-1],
            max_ms=times[-1],
            error_rate=data["errors"] / data["total"] if data["total"] else 0,
        ))

    results.sort(key=lambda s: -s.p95_ms)
    return results


# ── Main analysis function ────────────────────────────────────────────────

def analyze_logs(
    log_path: str | Path,
    *,
    slow_threshold_ms: float = 1000.0,
    max_errors: int = 200,
) -> dict[str, Any]:
    """Analyze application/server logs and return structured findings.

    Args:
        log_path: Path to log file
        slow_threshold_ms: Response time threshold for "slow" (ms)
        max_errors: Maximum error clusters to return

    Returns:
        dict with keys:
        - error_clusters: list[ErrorCluster]
        - slow_endpoints: list[SlowEndpoint]
        - error_summary: dict with total_errors, error_types, etc.
        - format: detected log format
    """
    path = Path(log_path) if not isinstance(log_path, Path) else log_path
    if not path.exists():
        print(f"  [WARN] log_analyzer: file not found: {path}", flush=True, file=sys.stderr)
        return {"error_clusters": [], "slow_endpoints": [], "error_summary": {}, "format": "unknown"}

    try:
        all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        print(f"  [WARN] log_analyzer: failed to read {path}: {e}", flush=True, file=sys.stderr)
        return {"error_clusters": [], "slow_endpoints": [], "error_summary": {}, "format": "unknown"}

    if not all_lines:
        return {"error_clusters": [], "slow_endpoints": [], "error_summary": {}, "format": "unknown"}

    fmt = _detect_format(all_lines[:20])
    print(f"  [INFO] log_analyzer: detected format={fmt}, {len(all_lines)} lines", flush=True)

    # Extract errors
    if fmt == "java":
        errors = _extract_errors_java(all_lines)
    elif fmt == "json":
        errors = _extract_errors_json(all_lines)
    elif fmt == "access":
        errors = _extract_access_log_errors(all_lines)
    else:
        errors = _extract_errors_structured(all_lines)

    # Cluster errors
    clusters = _cluster_errors(errors)
    clusters = clusters[:max_errors]

    # Slow endpoints
    if fmt == "access":
        slow = _analyze_slow_endpoints_access(all_lines, slow_threshold_ms)
    elif fmt == "json":
        slow = _analyze_slow_endpoints_json(all_lines, slow_threshold_ms)
    else:
        slow = []

    # Summary
    error_types = Counter(c.error_type for c in clusters)
    total_errors = sum(c.count for c in clusters)

    summary = {
        "total_error_lines": len(errors),
        "total_error_clusters": len(clusters),
        "total_error_occurrences": total_errors,
        "top_error_types": error_types.most_common(10),
        "total_slow_endpoints": len(slow),
    }

    print(f"  [OK] log_analyzer: {summary['total_error_lines']} errors → "
          f"{summary['total_error_clusters']} clusters, "
          f"{summary['total_slow_endpoints']} slow endpoints", flush=True)

    return {
        "error_clusters": clusters,
        "slow_endpoints": slow,
        "error_summary": summary,
        "format": fmt,
    }


def log_errors_to_candidates(
    log_path: str | Path,
    *,
    project_id: str = "",
    slow_threshold_ms: float = 1000.0,
) -> list[dict[str, Any]]:
    """Convert log analysis findings to GroundedCandidate-like structures
    ready for the discovery pipeline.

    This is the main integration point.
    """
    result = analyze_logs(log_path, slow_threshold_ms=slow_threshold_ms)
    log_name = str(Path(log_path).name) if isinstance(log_path, (str, Path)) else "logs"
    source_ref = {"source": log_name, "type": "application_log"}

    candidates: list[dict[str, Any]] = []

    # ── Error clusters → candidates ──
    for i, cluster in enumerate(result["error_clusters"]):
        # Map error type to risk categories
        risk_type = "runtime_error"
        if any(kw in cluster.error_type.lower() for kw in
               ("null", "nullpointer", "none", "undefined", "attribute")):
            risk_type = "null_reference"
        elif any(kw in cluster.error_type.lower() for kw in
                 ("timeout", "timedout", "connection", "refused", "socket")):
            risk_type = "connection_failure"
        elif any(kw in cluster.error_type.lower() for kw in
                 ("sql", "database", "integrity", "constraint", "duplicate")):
            risk_type = "database_error"
        elif any(kw in cluster.error_type.lower() for kw in
                 ("auth", "permission", "forbidden", "unauthorized", "403")):
            risk_type = "auth"
        elif any(kw in cluster.error_type.lower() for kw in
                 ("validation", "valueerror", "illegalarg", "typeerror", "format")):
            risk_type = "validation"
        elif any(kw in cluster.error_type.lower() for kw in
                 ("outofmemory", "memory", "heap", "stackoverflow")):
            risk_type = "resource_exhaustion"

        candidates.append({
            "candidate_id": f"LOG_{project_id}_ERR_{i:04d}",
            "title": f"[LOG] {cluster.error_type}: {cluster.message_pattern[:80]}",
            "status": "open",
            "risk_type": risk_type,
            "severity": cluster.severity,
            "confidence": min(0.95, 0.6 + min(cluster.count, 10) * 0.03),
            "endpoint": (
                {"path": cluster.affected_endpoints[0], "method": "ANY"}
                if cluster.affected_endpoints else {"path": "unknown", "method": "ANY"}
            ),
            "affected_entities": [],
            "actors": ["system"],
            "expected_behavior": f"不应该出现 {cluster.error_type} 错误",
            "suspected_failure_pattern": f"日志中发现 {cluster.count} 次 {cluster.error_type}: {cluster.message_pattern[:100]}",
            "probe_plan": {
                "analyze_logs": True,
                "error_fingerprint": cluster.fingerprint,
                "affected_endpoints": cluster.affected_endpoints[:10],
            },
            "execution_policy": "analysis_only",
            "required_evidence": ["log_excerpt", "stack_trace"],
            "source_refs": [source_ref],
            "grounding_basis": {
                "source": "application_log",
                "error_type": cluster.error_type,
                "occurrences": cluster.count,
                "first_seen": cluster.first_seen,
                "last_seen": cluster.last_seen,
            },
            "rationale": f"应用日志中发现 {cluster.count} 次 {cluster.error_type} 错误: {cluster.message_pattern[:150]}",
        })

    # ── Slow endpoints → candidates ──
    for i, slow in enumerate(result["slow_endpoints"]):
        severity = "P0" if slow.p99_ms > 10000 else "P1" if slow.p99_ms > 5000 else "P2"

        candidates.append({
            "candidate_id": f"LOG_{project_id}_SLOW_{i:04d}",
            "title": f"[LOG] 慢接口: {slow.method} {slow.path} (P99={slow.p99_ms:.0f}ms, n={slow.count})",
            "status": "open",
            "risk_type": "performance",
            "severity": severity,
            "confidence": min(0.9, 0.5 + min(slow.count, 20) * 0.02),
            "endpoint": {"path": slow.path, "method": slow.method},
            "affected_entities": [],
            "actors": ["system"],
            "expected_behavior": f"响应时间应在合理范围内 (P95 < {slow_threshold_ms}ms)",
            "suspected_failure_pattern": f"P99={slow.p99_ms:.0f}ms, P95={slow.p95_ms:.0f}ms, 错误率={slow.error_rate:.1%}",
            "probe_plan": {
                "method": slow.method,
                "path": slow.path,
                "benchmark": True,
                "expected_p95_ms": slow_threshold_ms,
            },
            "execution_policy": "safe_read_only",
            "required_evidence": ["response_time", "p95", "p99"],
            "source_refs": [source_ref],
            "grounding_basis": {
                "source": "application_log",
                "p99_ms": slow.p99_ms,
                "p95_ms": slow.p95_ms,
                "avg_ms": slow.avg_ms,
                "request_count": slow.count,
                "error_rate": slow.error_rate,
            },
            "rationale": f"访问日志分析发现慢接口 P99={slow.p99_ms:.0f}ms，共{slow.count}次请求，错误率{slow.error_rate:.1%}",
        })

    return candidates


# ── Quick CLI test ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Application log analyzer")
    parser.add_argument("log_file", help="Path to log file")
    parser.add_argument("--slow-threshold", type=float, default=1000, help="Slow threshold in ms")
    parser.add_argument("--max-errors", type=int, default=50, help="Max error clusters")
    args = parser.parse_args()

    result = analyze_logs(args.log_file, slow_threshold_ms=args.slow_threshold,
                          max_errors=args.max_errors)
    summary = result["error_summary"]

    print(f"\n=== Log Analysis: {args.log_file} ===")
    print(f"  Format: {result['format']}")
    print(f"  Errors: {summary.get('total_error_lines', 0)} lines → "
          f"{summary.get('total_error_clusters', 0)} clusters")
    print(f"  Slow endpoints: {summary.get('total_slow_endpoints', 0)}")
    print(f"  Top error types: {summary.get('top_error_types', [])[:5]}")

    print(f"\n=== Error Clusters ({min(len(result['error_clusters']), 10)}) ===")
    for c in result["error_clusters"][:10]:
        print(f"  [{c.severity}] {c.error_type} (x{c.count}): {c.message_pattern[:100]}")

    if result["slow_endpoints"]:
        print(f"\n=== Slow Endpoints ({len(result['slow_endpoints'])}) ===")
        for s in result["slow_endpoints"][:10]:
            print(f"  {s.method:6s} {s.path:40s} P95={s.p95_ms:8.0f}ms  err={s.error_rate:.1%}")
