"""
HAR Bridge — Links HTTP Archive (HAR) records to findings for evidence enrichment.

Takes the auto_har entries from scan_result.json and matches each finding
to the most relevant HTTP call(s). This gives the evidence enricher real
request/response data instead of placeholders.

Matching strategy:
  1. Exact path+method match (highest confidence)
  2. Substring path match (when finding description doesn't contain full path)
  3. Category-based fallback (match relevant error calls for a category)
"""
from __future__ import annotations

import re
import json
from typing import Any
from urllib.parse import urlparse


def _extract_api_path(url: str) -> str:
    """Extract /api/... path from a full URL."""
    if url.startswith("http"):
        parsed = urlparse(url)
        return parsed.path
    return url


def _path_similarity(path_a: str, path_b: str) -> float:
    """Calculate path similarity (0-1) for fuzzy matching."""
    if path_a == path_b:
        return 1.0
    if not path_a or not path_b:
        return 0.0
    # Common prefix length ratio
    parts_a = [p for p in path_a.strip("/").split("/") if p]
    parts_b = [p for p in path_b.strip("/").split("/") if p]
    common = 0
    for a, b in zip(parts_a, parts_b):
        if a == b:
            common += 1
        else:
            break
    max_len = max(len(parts_a), len(parts_b))
    if max_len == 0:
        return 0.0
    return common / max_len


def match_finding_to_har(
    finding: dict[str, Any],
    har_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match a finding to relevant HAR entries.
    
    Args:
        finding: A finding dict with title, description, _api_path, _api_method, category
        har_entries: List of HAR entry dicts from auto_har.entries
        
    Returns:
        List of matching HAR entries (best match first), or empty list.
    """
    if not har_entries:
        return []
    
    title = str(finding.get("title") or "")
    desc = str(finding.get("description") or finding.get("summary") or "")
    text = f"{title} {desc}"
    method = (finding.get("_api_method") or finding.get("repro_method") or "").upper()
    path = finding.get("_api_path") or finding.get("repro_path") or ""
    category = finding.get("category") or finding.get("risk_type") or ""
    
    # Also try to extract path from description/title
    if not path:
        path_match = re.search(r'(/api/[\w/{}\.\-%:]+)', text)
        if path_match:
            path = path_match.group(1)
    
    scored: list[tuple[float, dict[str, Any]]] = []
    
    for entry in har_entries:
        req = entry.get("request", {})
        entry_url = req.get("url", "")
        entry_method = req.get("method", "").upper()
        entry_path = _extract_api_path(entry_url)
        entry_status = entry.get("response", {}).get("status", 0)
        
        score = 0.0
        
        # 1. Exact path + method match (highest weight)
        if path and method and entry_method == method and entry_path == path:
            score += 10.0
        elif path and entry_path == path:
            score += 8.0
        
        # 2. Path substring match in finding text
        if path and path in entry_url:
            score += 5.0
        if entry_path and entry_path in text:
            score += 4.0
        
        # 3. Method match (weaker signal)
        if method and entry_method == method:
            score += 2.0
        
        # 4. Path similarity
        if path and entry_path:
            sim = _path_similarity(path, entry_path)
            score += sim * 3.0
        
        # 5. Error responses are more likely to be bug-related
        if entry_status >= 400:
            score += 1.0
        if entry_status >= 500:
            score += 1.0  # Double weight for 500 errors
        
        # 6. Category-based matching
        category_paths = {
            "authorization": ["/admin/", "/reports/", "/manage/"],
            "business_rule": ["/coupons/", "/validate"],
            "financial": ["/pay/", "/refund/", "/payments/"],
            "inventory": ["/inventory/", "/products/"],
            "state_machine": ["/orders/", "/cancel", "/pay", "/ship", "/confirm"],
            "idempotency": ["/orders/", "/pay/"],
            "data_leak": ["/products/"],
            "data_integrity": ["/products/"],
        }
        cat_paths = category_paths.get(category, [])
        for cp in cat_paths:
            if cp in entry_path:
                score += 1.5
                break
        
        if score > 0:
            scored.append((score, entry))
    
    # Sort by score descending
    scored.sort(key=lambda x: -x[0])
    
    return [entry for _, entry in scored[:5]]  # Top 5 matches


def enrich_finding_with_har(
    finding: dict[str, Any],
    har_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Enrich a finding with matched HAR call data.
    
    Adds:
      - har_evidence: dict with request/response/status from the best matching call
      - har_evidence_list: list of all matching calls (for evidence chain)
    """
    enriched = dict(finding)
    matches = match_finding_to_har(finding, har_entries)
    
    if not matches:
        return enriched
    
    # Primary evidence from best match
    best = matches[0]
    req = best.get("request", {})
    resp = best.get("response", {})
    
    har_evidence = {
        "method": req.get("method", ""),
        "url": req.get("url", ""),
        "path": _extract_api_path(req.get("url", "")),
        "status_code": resp.get("status", 0),
        "response_body": resp.get("body", "")[:500],
        "actor": best.get("_actor", ""),
        "duration_ms": best.get("time", 0),
    }
    
    enriched["har_evidence"] = har_evidence
    
    # Also update the finding's evidence dict if it exists
    evidence = enriched.get("evidence", {})
    if isinstance(evidence, dict):
        evidence.setdefault("method", har_evidence["method"])
        evidence.setdefault("path", har_evidence["path"])
        evidence.setdefault("status_code", har_evidence["status_code"])
        evidence.setdefault("response_body", har_evidence["response_body"])
    else:
        enriched["evidence"] = har_evidence
    
    # Set _api_path/_api_method if missing
    if not enriched.get("_api_path") and har_evidence["path"]:
        enriched["_api_path"] = har_evidence["path"]
    if not enriched.get("_api_method") and har_evidence["method"]:
        enriched["_api_method"] = har_evidence["method"]
    if not enriched.get("repro_path") and har_evidence["path"]:
        enriched["repro_path"] = har_evidence["path"]
    if not enriched.get("repro_method") and har_evidence["method"]:
        enriched["repro_method"] = har_evidence["method"]
    
    # All matches for evidence chain
    enriched["har_evidence_list"] = [
        {
            "method": m.get("request", {}).get("method", ""),
            "url": m.get("request", {}).get("url", ""),
            "status": m.get("response", {}).get("status", 0),
        }
        for m in matches[:5]
    ]
    
    return enriched


def enrich_findings_batch_with_har(
    findings: list[dict[str, Any]],
    har_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Batch-enrich findings with HAR data."""
    return [enrich_finding_with_har(f, har_entries) for f in findings if isinstance(f, dict)]


def load_har_entries(scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract HAR entries from scan_result.json."""
    auto_har = scan_result.get("auto_har", {})
    if isinstance(auto_har, dict):
        entries = auto_har.get("entries", [])
        if entries:
            return entries
        # Legacy format: entries might be stored as calls
        return auto_har.get("calls", auto_har.get("records", []))
    return []
