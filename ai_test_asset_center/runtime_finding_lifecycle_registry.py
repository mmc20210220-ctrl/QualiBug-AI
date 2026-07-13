from __future__ import annotations

"""Phase92Y: stable finding lifecycle registry and signature migration.

Candidate ids are useful within one generated plan, but they can change when the
probe compiler is refined.  Phase92Y keeps lifecycle decisions stable by matching
findings through a small set of durable aliases: normalized endpoint/risk,
violated invariant family, and source-reference fingerprints.
"""

import hashlib
import re
from typing import Any


_ID_SEGMENT_RE = re.compile(r"^(?:[A-Fa-f0-9]{8,}|\d+|qb[_-]?auto[_-]?[\w-]+|[A-Z]{1,5}-\d{1,12})$")
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_PATH_PARAM_RE = re.compile(r"\{[^}/]+\}")


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def normalize_endpoint_path(path: Any) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    raw = raw.split("?", 1)[0]
    raw = _UUID_RE.sub("{id}", raw)
    raw = _PATH_PARAM_RE.sub("{id}", raw)
    parts: list[str] = []
    for part in raw.strip("/").split("/"):
        token = part.strip()
        if not token:
            continue
        if _ID_SEGMENT_RE.match(token):
            parts.append("{id}")
        else:
            parts.append(token.lower().replace("_", "-"))
    return "/" + "/".join(parts) if parts else "/"


def _violated_kinds(finding: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in finding.get("violated_invariants") or []:
        if isinstance(item, dict) and item.get("kind"):
            values.append(str(item.get("kind")))
        elif item:
            values.append(str(item))
    return sorted(dict.fromkeys(values))


def _source_ref_fingerprint(finding: dict[str, Any]) -> str:
    parts: list[str] = []
    for ref in finding.get("source_refs") or []:
        if not isinstance(ref, dict):
            continue
        parts.append("/".join([str(ref.get("file") or ""), str(ref.get("section") or ""), str(ref.get("kind") or "")]))
    if not parts:
        return ""
    return _short_hash("|".join(sorted(parts)))


def finding_aliases(finding: dict[str, Any]) -> list[str]:
    risk = str(finding.get("risk_type") or "unknown")
    method = str(finding.get("method") or "").upper()
    path = normalize_endpoint_path(finding.get("path"))
    aliases: list[str] = []
    cid = str(finding.get("candidate_id") or "").strip()
    if cid:
        aliases.append(f"candidate:{cid}")
    if path:
        aliases.append(f"endpoint:{risk}|{method}|{path}")
    kinds = _violated_kinds(finding)
    if kinds and path:
        aliases.append(f"invariant_endpoint:{risk}|{method}|{path}|{','.join(kinds)}")
    source_fp = _source_ref_fingerprint(finding)
    if source_fp:
        aliases.append(f"source:{risk}|{method}|{source_fp}")
        if kinds:
            aliases.append(f"source_invariant:{risk}|{source_fp}|{','.join(kinds)}")
    title = str(finding.get("title") or "").strip().lower()
    if title and risk:
        aliases.append(f"title:{risk}|{_short_hash(title)}")
    return sorted(dict.fromkeys(aliases))


def primary_lifecycle_signature(finding: dict[str, Any]) -> str:
    aliases = finding_aliases(finding)
    for prefix in ("invariant_endpoint:", "endpoint:", "source_invariant:", "source:", "candidate:"):
        for alias in aliases:
            if alias.startswith(prefix):
                return alias
    return aliases[0] if aliases else "unknown"


def _index_previous(previous_report: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    alias_to_finding: dict[str, dict[str, Any]] = {}
    finding_alias_map: dict[str, list[str]] = {}
    if not isinstance(previous_report, dict):
        return alias_to_finding, finding_alias_map
    for finding in previous_report.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        fid = str(finding.get("finding_id") or primary_lifecycle_signature(finding))
        aliases = finding_aliases(finding)
        finding_alias_map[fid] = aliases
        for alias in aliases:
            alias_to_finding.setdefault(alias, finding)
    return alias_to_finding, finding_alias_map


def _best_previous_match(current: dict[str, Any], alias_to_previous: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    for alias in finding_aliases(current):
        prev = alias_to_previous.get(alias)
        if prev:
            return prev, alias
    return None, ""


def apply_lifecycle_registry(report: dict[str, Any], previous_report: dict[str, Any] | None = None) -> dict[str, Any]:
    findings = [f for f in (report.get("findings") or []) if isinstance(f, dict)]
    alias_to_previous, previous_alias_map = _index_previous(previous_report)
    matched_previous_ids: set[str] = set()
    stable_match_count = 0

    for finding in findings:
        aliases = finding_aliases(finding)
        primary = primary_lifecycle_signature(finding)
        previous, matched_alias = _best_previous_match(finding, alias_to_previous)
        registry = {
            "engine": "runtime_finding_lifecycle_registry_v1_phase92y",
            "primary_lifecycle_signature": primary,
            "aliases": aliases,
            "matched_previous_finding_id": None,
            "matched_alias": None,
            "stable_match": False,
        }
        if previous:
            prev_id = str(previous.get("finding_id") or primary_lifecycle_signature(previous))
            matched_previous_ids.add(prev_id)
            stable_match_count += 1
            registry.update({
                "matched_previous_finding_id": previous.get("finding_id"),
                "matched_alias": matched_alias,
                "stable_match": True,
            })
            fx = finding.get("fix_verification") if isinstance(finding.get("fix_verification"), dict) else {}
            if fx:
                fx["lifecycle_status"] = "still_open_after_rerun"
                fx["lifecycle_match_basis"] = "phase92y_stable_alias_registry"
                fx["matched_previous_finding_id"] = previous.get("finding_id")
                fx["matched_alias"] = matched_alias
                finding["fix_verification"] = fx
        finding["lifecycle_registry"] = registry

    current_aliases = {alias for f in findings for alias in finding_aliases(f)}
    closed_by_rerun: list[dict[str, Any]] = []
    if isinstance(previous_report, dict):
        for previous in previous_report.get("findings") or []:
            if not isinstance(previous, dict) or previous.get("status") != "validated_candidate":
                continue
            prev_id = str(previous.get("finding_id") or primary_lifecycle_signature(previous))
            aliases = previous_alias_map.get(prev_id) or finding_aliases(previous)
            if prev_id in matched_previous_ids or any(alias in current_aliases for alias in aliases):
                continue
            closed_by_rerun.append({
                "previous_finding_id": previous.get("finding_id"),
                "primary_lifecycle_signature": primary_lifecycle_signature(previous),
                "aliases": aliases,
                "candidate_id": previous.get("candidate_id"),
                "endpoint": f"{previous.get('method')} {previous.get('path')}",
                "lifecycle_status": "closed_by_rerun",
                "close_basis": "no candidate, endpoint, invariant, source or title alias matched the current rerun findings",
            })

    report["findings"] = findings
    registry_index = {
        "engine": "runtime_finding_lifecycle_registry_v1_phase92y",
        "enabled": True,
        "previous_report_present": isinstance(previous_report, dict),
        "current_finding_count": len(findings),
        "stable_match_count": stable_match_count,
        "closed_by_rerun_count": len(closed_by_rerun),
        "closed_by_rerun": closed_by_rerun,
        "signature_strategy": ["candidate_id", "normalized_endpoint+risk", "violated_invariant+endpoint", "source_ref_fingerprint", "title_hash"],
    }
    report["finding_lifecycle_registry"] = registry_index

    fix_index = report.get("fix_verification_loop_index") if isinstance(report.get("fix_verification_loop_index"), dict) else None
    if fix_index is not None:
        fix_index["phase92y_stable_signature_registry_applied"] = True
        fix_index["stable_match_count"] = stable_match_count
        fix_index["closed_by_rerun"] = closed_by_rerun
        fix_index["closed_by_rerun_count"] = len(closed_by_rerun)
        fix_index["still_open_after_rerun_count"] = sum(
            1
            for f in findings
            if isinstance(f.get("fix_verification"), dict)
            and (f.get("fix_verification") or {}).get("lifecycle_status") == "still_open_after_rerun"
        )
        report["fix_verification_loop_index"] = fix_index
    return report
