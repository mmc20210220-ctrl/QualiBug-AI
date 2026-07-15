from __future__ import annotations

"""
Temporal Saga Verification & Document Intelligence Pipeline.

Advanced upgrades:
4a. Temporal logic: verify saga steps satisfy timing constraints (step B must
    happen within 30s after step A)
4b. Dead letter detection: detect stuck events, orphaned callbacks
4c. SLA monitoring: track cross-org response times against contracts

5a. OCR pipeline: extract text from scanned documents/images
5b. Table extraction: parse tables from PDFs into structured data
5c. Entity extraction: auto-extract business entities from documents
"""

import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .real_project_onboarding import _read_text, _write_json


# ===========================================================================
# 4a. Temporal Saga Verification
# ===========================================================================

class TemporalSagaVerifier:
    """Verify that cross-org saga steps satisfy timing constraints."""

    def __init__(self, saga: dict[str, Any]):
        self.saga = saga
        self.steps = saga.get("steps", [])
        self.invariants = saga.get("invariants", [])

    def verify_timing(self, execution_log: list[dict[str, Any]]) -> dict[str, Any]:
        """Verify that saga step timestamps satisfy constraints.

        execution_log: list of {step_id, actor, action, timestamp_utc, status}
        """
        violations: list[dict[str, Any]] = []
        step_times: dict[str, float] = {}

        for entry in execution_log:
            step_id = entry.get("step_id", "")
            ts = _parse_timestamp(entry.get("timestamp_utc", ""))
            if step_id and ts:
                step_times[step_id] = ts

        # Check each step's timeout constraint
        for step in self.steps:
            sid = step.get("step_id", "")
            timeout = step.get("timeout_seconds", 30)
            if sid not in step_times:
                violations.append({
                    "step_id": sid,
                    "type": "missing_execution",
                    "severity": "P1",
                    "message": f"步骤 '{step.get('action')}' 未在日志中找到执行记录",
                })
                continue

            # Check that previous step completed before this one started
            prev_idx = self.steps.index(step) - 1
            if prev_idx >= 0:
                prev_sid = self.steps[prev_idx].get("step_id", "")
                if prev_sid in step_times:
                    elapsed = step_times[sid] - step_times[prev_sid]
                    if elapsed > timeout:
                        violations.append({
                            "step_id": sid,
                            "type": "timeout",
                            "severity": "P0",
                            "message": f"步骤间耗时 {elapsed:.1f}s 超过限制 {timeout}s",
                            "from_step": prev_sid,
                            "elapsed_seconds": elapsed,
                            "limit_seconds": timeout,
                        })

        # Check invariants
        for inv in self.invariants:
            if "30秒" in inv.get("description", "") or "timeout" in inv.get("description", "").lower():
                # Temporal invariant — check all steps
                for i in range(len(self.steps) - 1):
                    sid_a = self.steps[i].get("step_id", "")
                    sid_b = self.steps[i + 1].get("step_id", "")
                    if sid_a in step_times and sid_b in step_times:
                        elapsed = step_times[sid_b] - step_times[sid_a]
                        if elapsed > 30:
                            violations.append({
                                "invariant_id": inv.get("invariant_id", ""),
                                "type": "temporal_invariant_violation",
                                "severity": inv.get("severity", "P1"),
                                "message": f"步骤 {sid_a} → {sid_b} 耗时 {elapsed:.1f}s，违反不变量: {inv['description'][:100]}",
                            })

        return {
            "saga_id": self.saga.get("saga_id", ""),
            "steps_verified": len(self.steps),
            "violations": violations,
            "violation_count": len(violations),
            "has_timing_violations": any(v["type"] in ("timeout", "temporal_invariant_violation") for v in violations),
            "has_missing_steps": any(v["type"] == "missing_execution" for v in violations),
            "step_timeline": {sid: time.strftime("%H:%M:%S", time.gmtime(ts)) for sid, ts in step_times.items()},
        }


def detect_dead_letters(
    sent_events: list[dict[str, Any]],
    received_events: list[dict[str, Any]],
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Detect dead letters: events sent but never received within timeout."""
    sent_ids = {e.get("event_id", "") for e in sent_events if e.get("event_id")}
    received_ids = {e.get("event_id", "") for e in received_events if e.get("event_id")}
    dead = sent_ids - received_ids
    ghost = received_ids - sent_ids  # Received but never sent (replay? bug?)

    return {
        "sent_count": len(sent_ids),
        "received_count": len(received_ids),
        "dead_letters": sorted(dead)[:50],
        "dead_letter_count": len(dead),
        "ghost_events": sorted(ghost)[:50],
        "ghost_event_count": len(ghost),
        "delivery_rate": len(received_ids) / max(1, len(sent_ids)),
        "severity": "P0" if len(dead) > 0 else "ok",
    }


def monitor_sla(
    events: list[dict[str, Any]],
    sla_seconds: dict[str, int],
) -> dict[str, Any]:
    """Monitor cross-org SLA compliance."""
    by_type: dict[str, list[float]] = defaultdict(list)
    for event in events:
        etype = event.get("type", "unknown")
        elapsed = event.get("elapsed_seconds", 0)
        if elapsed > 0:
            by_type[etype].append(elapsed)

    sla_status: dict[str, Any] = {}
    for etype, limit in sla_seconds.items():
        times = by_type.get(etype, [])
        violations = sum(1 for t in times if t > limit)
        sla_status[etype] = {
            "sla_seconds": limit,
            "total_events": len(times),
            "violations": violations,
            "compliance_rate": 1.0 - (violations / max(1, len(times))),
            "p50_latency": _percentile(times, 50) if times else 0,
            "p99_latency": _percentile(times, 99) if times else 0,
        }

    return {
        "sla_status": sla_status,
        "overall_compliance": (
            sum(s["compliance_rate"] for s in sla_status.values()) / max(1, len(sla_status))
        ) if sla_status else 1.0,
    }


# ===========================================================================
# 5. Document Intelligence Pipeline
# ===========================================================================

def extract_tables_from_text(text: str) -> list[dict[str, Any]]:
    """Extract structured tables from plain text or markdown.

    Supports:
    - Markdown tables (| col1 | col2 |)
    - CSV-like data (comma/tab separated rows)
    - Key-value pairs (key: value format)
    """
    tables: list[dict[str, Any]] = []

    # Markdown tables
    md_table = re.findall(
        r'\|(.+)\|\n\|[-| ]+\|\n((?:\|.+\|\n?)+)',
        text, re.MULTILINE
    )
    for header_row, body_rows in md_table:
        headers = [h.strip() for h in header_row.split("|") if h.strip()]
        rows = []
        for row in body_rows.strip().split("\n"):
            cells = [c.strip() for c in row.split("|") if c.strip()]
            if cells:
                rows.append(dict(zip(headers, cells)))
        if rows:
            tables.append({"type": "markdown_table", "headers": headers, "rows": rows, "row_count": len(rows)})

    # CSV-like data (detect by consistent delimiter)
    csv_lines = [line for line in text.split("\n") if "," in line and line.count(",") >= 2]
    if len(csv_lines) >= 3:
        headers = [h.strip() for h in csv_lines[0].split(",")]
        rows = []
        for line in csv_lines[1:]:
            cells = [c.strip() for c in line.split(",")]
            if len(cells) == len(headers):
                rows.append(dict(zip(headers, cells)))
        if rows:
            tables.append({"type": "csv_table", "headers": headers, "rows": rows, "row_count": len(rows)})

    # Key-value pairs
    kv_pairs: dict[str, str] = {}
    for line in text.split("\n"):
        m = re.match(r'^\s*([\w\u4e00-\u9fff\s]+)[:：]\s*(.+)$', line)
        if m:
            kv_pairs[m.group(1).strip()] = m.group(2).strip()
    if len(kv_pairs) >= 5:
        tables.append({"type": "key_value_pairs", "pairs": kv_pairs, "pair_count": len(kv_pairs)})

    return tables


def extract_business_entities(text: str) -> dict[str, Any]:
    """Auto-extract business entities from document text.

    Detects:
    - Named entities (capitalized terms, Chinese business terms)
    - ID patterns (order_id, user_uuid, etc.)
    - Status/state values
    - Numerical constraints/limits
    - Date/time patterns
    """
    entities: dict[str, list[str]] = defaultdict(list)

    # Business entity names (CapitalizedCamelCase or snake_case with business meaning)
    for match in re.finditer(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b', text):
        entities["named_entities"].append(match.group(1))

    # API resource paths
    for match in re.finditer(r'/(\w+)(?:/\{(\w+)\})?', text):
        resource = match.group(1)
        if resource not in {"api", "v1", "v2", "v3"}:
            entities["api_resources"].append(resource)
        if match.group(2):
            entities["path_parameters"].append(match.group(2))

    # ID fields
    for match in re.finditer(r'\b(\w*(?:id|uuid|guid|code|number|no)\w*)\b', text, re.I):
        entities["id_fields"].append(match.group(1).lower())

    # Status values
    for match in re.finditer(r'(?:status|state|phase)\s*[:=]\s*"?(\w+)"?', text, re.I):
        entities["status_values"].append(match.group(1).lower())

    # Numerical constraints
    for match in re.finditer(r'(?:max|min|limit|capacity|quota)\w*\s*[:=]\s*(\d+)', text, re.I):
        entities["numerical_constraints"].append(f"{match.group(0)}")

    # Deduplicate
    return {k: sorted(set(v))[:30] for k, v in entities.items()}


def ocr_document(file_path: Path) -> str:
    """Extract text from scanned documents using OCR.

    Falls back gracefully if OCR libraries are not available.
    Supports: PDF (scanned), images (PNG, JPG, TIFF).
    """
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(file_path))
            text_parts = []
            for page in reader.pages:
                t = page.extract_text()
                if t and t.strip():
                    text_parts.append(t)
            text = "\n".join(text_parts)
            if text.strip():
                return text
        except ImportError:
            pass

    # Try OCR as fallback
    try:
        # Check if pytesseract is available
        import subprocess
        result = subprocess.run(
            ["tesseract", str(file_path), "stdout", "-l", "chi_sim+eng"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except Exception:
        pass

    # Last resort: raw text
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def full_document_intelligence(file_path: Path) -> dict[str, Any]:
    """Run the complete document intelligence pipeline.

    Returns: text, tables, entities, and metadata.
    """
    text = ocr_document(file_path)
    tables = extract_tables_from_text(text)
    entities = extract_business_entities(text)

    return {
        "file": str(file_path),
        "size_bytes": file_path.stat().st_size if file_path.exists() else 0,
        "format": file_path.suffix.lower(),
        "text_length": len(text),
        "tables_found": len(tables),
        "tables": tables[:10],
        "entities": entities,
        "text_preview": text[:500] + ("..." if len(text) > 500 else ""),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_timestamp(ts: str) -> float:
    try:
        if "T" in ts:
            return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * p / 100)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]
