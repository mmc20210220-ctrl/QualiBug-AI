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

from .real_id_resolver import normalize_path_placeholders


def _extract_api_path(url: str) -> str:
    """Extract /api/... path from a full URL."""
    if url.startswith("http"):
        parsed = urlparse(url)
        return parsed.path
    return url


def _path_similarity(path_a: str, path_b: str) -> float:
    """Calculate path similarity (0-1) for fuzzy matching."""
    path_a = normalize_path_placeholders(path_a or "")
    path_b = normalize_path_placeholders(path_b or "")
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


def _declared_path_matches(path_pattern: str, observed_path: str) -> bool:
    declared = normalize_path_placeholders(str(path_pattern or "")).split("?", 1)[0]
    observed = normalize_path_placeholders(str(observed_path or "")).split("?", 1)[0]
    if not declared or not observed:
        return False
    if declared == observed:
        return True
    if re.search(r"\{[A-Za-z_]\w*\}", declared):
        pattern = re.escape(declared)
        pattern = re.sub(r"\\\{[A-Za-z_]\w*\\\}", r"[^/]+", pattern)
        return bool(re.fullmatch(pattern, observed))
    return False


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
    normalized_path = normalize_path_placeholders(str(path or ""))
    category = finding.get("category") or finding.get("risk_type") or ""
    
    # Also try to extract path from description/title
    if not path:
        path_match = re.search(r'(/api/[\w/{}\.\-%:]+)', text)
        if path_match:
            path = path_match.group(1)
            normalized_path = normalize_path_placeholders(str(path or ""))

    # No declared path and no path mention in the claim means we do not have a
    # reliable request identity. In that case, binding the finding to an
    # arbitrary 4xx/5xx HAR row creates false evidence such as unrelated login
    # failures. Leave HAR empty rather than fabricating a link.
    if not path:
        return []
    
    scored: list[tuple[float, dict[str, Any]]] = []
    
    for entry in har_entries:
        req = entry.get("request", {})
        entry_url = req.get("url", "")
        entry_method = req.get("method", "").upper()
        entry_path = _extract_api_path(entry_url)
        entry_status = entry.get("response", {}).get("status", 0)
        declared_path_match = bool(normalized_path and _declared_path_matches(normalized_path, entry_path))
        
        score = 0.0
        binding_signal = False
        
        # 1. Exact path + method match (highest weight)
        if declared_path_match and method and entry_method == method:
            score += 10.0
            binding_signal = True
        elif declared_path_match:
            score += 8.0
            binding_signal = True
        
        # 2. Once the finding already declares a request identity, only reward
        # weaker textual/path similarity signals after the declared path matched.
        if declared_path_match and normalize_path_placeholders(entry_path) == normalized_path:
            score += 5.0
        elif declared_path_match and path and path in entry_url:
            score += 5.0
        if declared_path_match and entry_path and entry_path in text:
            score += 4.0
        
        # 3. Method match (weaker signal)
        if method and entry_method == method:
            score += 2.0
        
        # 4. Path similarity
        if declared_path_match and normalized_path and entry_path:
            sim = _path_similarity(normalized_path, entry_path)
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
        if declared_path_match:
            for cp in cat_paths:
                if cp in entry_path:
                    score += 1.5
                    break

        # Error responses are only meaningful after we have bound the runtime
        # row to the finding. Otherwise a random login 401 will hijack a
        # pathless business-rule finding.
        if binding_signal:
            if entry_status >= 400:
                score += 1.0
            if entry_status >= 500:
                score += 1.0  # Double weight for 500 errors

        if binding_signal and score > 0:
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


def load_playwright_har(har_file_path: str | Path) -> list[dict[str, Any]]:
    """Load a Playwright-generated HAR file (standard HAR 1.2 format).

    Extracts entries into the same format used by match_finding_to_har()
    and enrich_finding_with_har(). Returns empty list on missing/corrupt files.
    """
    path = Path(har_file_path)
    if not path.exists() or not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
    except Exception:
        return []
    log = raw.get("log") if isinstance(raw, dict) else {}
    entries = log.get("entries") if isinstance(log, dict) else []
    if not isinstance(entries, list):
        return []
    # Normalize each entry to the format expected by match_finding_to_har
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request") if isinstance(entry.get("request"), dict) else {}
        response = entry.get("response") if isinstance(entry.get("response"), dict) else {}
        normalized.append({
            "request": {
                "method": str(request.get("method") or "GET").upper(),
                "url": str(request.get("url") or ""),
            },
            "response": {
                "status": int(response.get("status") or 0),
                "body": str(response.get("content", {}).get("text", "") if isinstance(response.get("content"), dict) else ""),
            },
            "time": int(entry.get("time") or 0),
            "startedDateTime": str(entry.get("startedDateTime") or ""),
        })
    return normalized


def bridge_browser_har_to_findings(
    browser_execution_result: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Connect a browser_execution HAR file to findings for evidence enrichment.

    Args:
        browser_execution_result: Output of execute_browser_plan()
        findings: List of finding dicts to enrich
        root: Project root for resolving relative har_ref paths

    Returns:
        Dict with enriched_findings list and har_summary stats.
    """
    har_ref = str(browser_execution_result.get("har_ref") or "")
    if not har_ref:
        return {"enriched_findings": findings, "har_summary": {"status": "no_har_ref", "entry_count": 0}}

    har_path = Path(har_ref)
    if root and not har_path.is_absolute():
        har_path = Path(root) / har_ref

    entries = load_playwright_har(har_path)
    if not entries:
        return {"enriched_findings": findings, "har_summary": {"status": "har_empty_or_missing", "entry_count": 0}}

    enriched = enrich_findings_batch_with_har(findings, entries)
    matched_count = sum(1 for f in enriched if isinstance(f, dict) and f.get("har_evidence"))
    return {
        "enriched_findings": enriched,
        "har_summary": {
            "status": "enriched",
            "har_path": str(har_path),
            "har_entry_count": len(entries),
            "findings_enriched": matched_count,
            "findings_total": len(findings),
        },
    }
