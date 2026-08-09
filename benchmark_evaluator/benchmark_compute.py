"""Per-scan benchmark and invariant-coverage metrics computation.

This module bridges the scan pipeline with the benchmark evaluator so that
after every scan the system can compute recall, precision, FPR, FNR, evidence
completeness, reproduction success rate, and regression success rate — but
ONLY when a ground truth file exists.

When ground truth is not available, this module still returns a non-benchmark
coverage matrix derived from real findings/candidates.  That matrix is not a
fabricated recall number; it is an honest product-facing map of which risk
families and business invariants were touched by the current scan, which were
confirmed, and which remain gaps.  This prevents the product from being trapped
at a fixed "20 bug types" ceiling while still avoiding fake 99% claims.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any


from ai_test_asset_center.risk_coverage_projection import (
    classify_risk_family,
    risk_family_ontology,
)


_RISK_FAMILY_ONTOLOGY = risk_family_ontology()
_BENCHMARK_MATCH_ONTOLOGY_PATH = Path(__file__).with_name("_benchmark_match_ontology.json")


def _benchmark_match_ontology() -> dict[str, dict[str, Any]]:
    """Evaluator-only alias ontology for GT matching; not fed into discovery."""

    if not _BENCHMARK_MATCH_ONTOLOGY_PATH.exists():
        return _RISK_FAMILY_ONTOLOGY
    try:
        payload = json.loads(
            _BENCHMARK_MATCH_ONTOLOGY_PATH.read_text(encoding="utf-8") or "{}"
        )
    except Exception:
        return _RISK_FAMILY_ONTOLOGY
    return payload if isinstance(payload, dict) else _RISK_FAMILY_ONTOLOGY


def _read_json(path: Path) -> dict[str, Any] | list[Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "null")
        return data if isinstance(data, (dict, list)) else {}
    except Exception as e:
        import sys
        print(f"[benchmark_compute] Failed to read {path}: {e}", file=sys.stderr)
        return {}


def _load_truth_bugs(gt_path: Path) -> list[dict[str, Any]]:
    """Load ground-truth bugs from dict or bare-list JSON (benchmark_mall format)."""
    data = _read_json(gt_path)
    if isinstance(data, list):
        return [b for b in data if isinstance(b, dict)]
    if isinstance(data, dict):
        bugs = data.get("bugs", [])
        if isinstance(bugs, list):
            return [b for b in bugs if isinstance(b, dict)]
    return []


_API_PATH_RE = re.compile(r"/[^\s\"'<>]+")


def _extract_api_paths(text: str) -> set[str]:
    paths: set[str] = set()
    for match in _API_PATH_RE.findall(str(text or "")):
        cleaned = match.split("?")[0].rstrip("/),.;:，。；：").lower()
        cleaned = re.sub(r":[a-zA-Z_][a-zA-Z0-9_]*", "/*", cleaned)
        cleaned = re.sub(r"\{[^}]+\}", "/*", cleaned)
        cleaned = re.sub(r"/{2,}", "/", cleaned)
        if cleaned.startswith("/"):
            paths.add(cleaned)
    return paths


def _finding_text_blob(finding: dict[str, Any]) -> str:
    parts = [
        finding.get("title"), finding.get("description"), finding.get("summary"),
        finding.get("category"), finding.get("defect_family"), finding.get("risk_type"),
        finding.get("expected"), finding.get("actual"),
        # Source-contract / runtime-evidence paragraphs injected on dedicated
        # fields (finding_source_contract.attach_evidence_paragraphs).  They
        # are matching material, not fingerprinted payload: including them
        # keeps the semantic alignment signal (rule statement text + observed
        # runtime evidence) available to the evaluator.  Old findings without
        # the fields simply contribute nothing extra.
        finding.get("contract_evidence"), finding.get("runtime_observation"),
        # Folding metadata: one operation-level property is delivered once,
        # with every tried actor pair recorded in duplicate_variants. The
        # blob must include those variant titles so the evidence text covers
        # all roles the property was proven against (e.g. a GT keyword that
        # names a specific treatment role).
        " ".join(
            str(value or "") for value in finding.get("duplicate_variants") or []
        ),
    ]
    repro = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
    parts.extend([repro.get("method"), repro.get("path")])
    return " ".join(str(p or "") for p in parts).lower()


def _finding_paths(finding: dict[str, Any]) -> set[str]:
    paths = set()
    repro = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
    if repro.get("path"):
        paths |= _extract_api_paths(str(repro.get("path")))
    key_method, key_path = _method_path_key(finding)
    if key_path:
        paths.add(key_path.lower())
    paths |= _extract_api_paths(_finding_text_blob(finding))
    return paths


def _benchmark_evidence_identity(finding: dict[str, Any]) -> str:
    """Collapse multiple Oracle labels over the same executed behavior trace."""

    slice_id = str(finding.get("behavior_slice_id") or "").strip()
    raw = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
    request = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
    reproduction = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
    method = str(
        request.get("method") or reproduction.get("method") or finding.get("method") or ""
    ).strip().upper()
    path = str(
        request.get("path") or reproduction.get("path") or finding.get("path") or ""
    ).strip().split("?", 1)[0].rstrip("/")
    actor = str(request.get("actor") or finding.get("actor_role") or "").strip()
    if slice_id:
        return f"slice:{slice_id}:{method}:{path}:{actor}"
    evidence_id = str(finding.get("evidence_id") or "").strip()
    if evidence_id:
        return f"evidence:{evidence_id}"
    material = json.dumps(
        {"method": method, "path": path, "actor": actor, "request": request.get("body")},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return f"request:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _deduplicate_benchmark_findings(findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for finding in findings:
        identity = _benchmark_evidence_identity(finding)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(finding)
    return unique, max(0, len(findings) - len(unique))


def _paths_overlap(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    if left & right:
        return True
    for a in left:
        for b in right:
            a_parts = a.split("/")
            b_parts = b.split("/")
            if len(a_parts) == len(b_parts) and all(
                x == y or x in {"*", "/*"} or y in {"*", "/*"} for x, y in zip(a_parts, b_parts)
            ):
                return True
    return False


def _match_finding_to_gt(
    finding: dict[str, Any],
    truth_bugs: list[dict[str, Any]],
    used_ids: set[str],
) -> dict[str, Any] | None:
    """Keyword + API-path + semantic match (post-scan scoring only — never fed into discovery)."""
    blob = _finding_text_blob(finding)
    f_paths = _finding_paths(finding)
    finding_family = _canonical_match_family(finding)
    best: tuple[float, dict[str, Any]] | None = None

    for gt in truth_bugs:
        gt_id = str(gt.get("bug_id") or gt.get("id") or "")
        if not gt_id or gt_id in used_ids:
            continue
        score = 0.0
        keywords = gt.get("match_keywords") if isinstance(gt.get("match_keywords"), list) else []
        kw_hits = sum(1 for kw in keywords if str(kw).lower() in blob)
        if keywords:
            score += min(0.55, kw_hits * 0.12)
        gt_paths = _extract_api_paths(str(gt.get("trigger") or ""))
        gt_paths |= _extract_api_paths(str(gt.get("endpoint_hint") or gt.get("api_path") or ""))
        for endpoint in gt.get("affected_endpoints") or gt.get("related_endpoints") or []:
            if isinstance(endpoint, dict):
                gt_paths |= _extract_api_paths(str(endpoint.get("path") or endpoint.get("api_path") or ""))
            else:
                gt_paths |= _extract_api_paths(str(endpoint))
        gt_paths |= _extract_api_paths(" ".join(str(k) for k in keywords))
        path_matches = _paths_overlap(f_paths, gt_paths)
        if path_matches:
            # Endpoint overlap proves where a probe ran, not which defect it
            # found. Keep it below the acceptance threshold so broad
            # permission/concurrency oracles cannot earn a true positive merely
            # by touching every documented endpoint.
            score += 0.30
        ground_truth_family = _canonical_match_family(gt)
        family_matches = (
            finding_family != "unclassified"
            and ground_truth_family != "unclassified"
            and finding_family == ground_truth_family
        )
        if family_matches:
            score += 0.35
        elif (
            finding_family != "unclassified"
            and ground_truth_family != "unclassified"
            and finding_family != ground_truth_family
        ):
            score -= 0.20
        gt_title = str(gt.get("title") or "").lower()
        if gt_title and any(tok in blob for tok in gt_title.split() if len(tok) >= 4):
            score += 0.12
        # Require endpoint plus semantic evidence, or sufficiently strong
        # non-endpoint semantic evidence. Endpoint-only matches are benchmark
        # coverage, never a detected bug.
        if score < 0.58 or (path_matches and not family_matches and score < 0.70):
            continue
        if best is None or score > best[0]:
            best = (score, gt)

    if best is None:
        return None
    matched = dict(best[1])
    matched["__match_score"] = round(best[0], 4)
    return matched


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        v = float(value)
        return v if v == v else fallback  # NaN guard
    except (TypeError, ValueError):
        return fallback


def _method_path_key(finding: dict[str, Any]) -> tuple[str, str]:
    """Extract a stable (method, path) key from a finding for matching."""
    method = str(finding.get("method") or finding.get("_api_method") or "").upper().strip()
    path = str(finding.get("path") or finding.get("_api_path") or "").strip().rstrip("/")
    # Normalize path params
    path = re.sub(r"/\d+", "/{id}", path)
    path = re.sub(r"/\{[^}]+\}", "/{id}", path)
    return (method, path)


def _text_blob(item: dict[str, Any]) -> str:
    fields = [
        item.get("risk_family"), item.get("family"), item.get("defect_family"),
        item.get("risk_type"), item.get("category"), item.get("type"),
        item.get("title"), item.get("summary"), item.get("description"),
        item.get("expected"), item.get("actual"), item.get("path"), item.get("_api_path"),
    ]
    raw_evidence = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
    request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
    response_raw = raw_evidence.get("response_raw") if isinstance(raw_evidence.get("response_raw"), dict) else {}
    fields.extend([request_raw.get("path"), request_raw.get("method"), response_raw.get("status_code")])
    return " ".join(str(v or "") for v in fields).lower()


def _explicit_family(item: dict[str, Any]) -> str:
    ontology = _benchmark_match_ontology()
    # Isolation experiments reuse assertion kind owner_tenant_visibility, but
    # their obligation/assertion identity is isolation. Prefer that over the
    # authorization alias on the shared assertion kind / category label.
    if _isolation_owner_visibility_signal(item):
        return "tenant_isolation"
    for key in ("risk_family", "family", "defect_family", "risk_type", "category", "type"):
        value = str(item.get(key) or "").strip().lower()
        if value in ontology:
            return value
        normalized = value.replace("-", "_").replace(" ", "_")
        if normalized in ontology:
            return normalized
        # Product short ids that are not literal aliases of their evaluator
        # counterpart (conservation, idempotency, temporal, privacy, ...) must
        # normalize here -- classify_risk_family never sees them and would
        # otherwise drop them to "unclassified".
        mapped = _evaluator_family(normalized)
        if mapped in ontology:
            return mapped
        for family, spec in ontology.items():
            aliases = {str(alias or "").strip().lower() for alias in spec.get("aliases", ())}
            if value and value in aliases:
                return family
    return ""


def _isolation_owner_visibility_signal(item: dict[str, Any]) -> bool:
    """True when owner_tenant_visibility evidence came from an isolation obligation."""

    risk = str(item.get("risk_family") or "").strip().lower()
    if risk == "isolation":
        return True
    template = str(
        item.get("property_template")
        or _dict(item.get("property")).get("template")
        or ""
    ).strip().lower()
    if template == "owner_viewer_isolation":
        return True
    assertion_ids: list[str] = []
    for key in ("assertion_id",):
        value = str(item.get(key) or "").strip().lower()
        if value:
            assertion_ids.append(value)
    for row in item.get("failed_assertions") or []:
        if isinstance(row, dict):
            value = str(row.get("assertion_id") or "").strip().lower()
            if value:
                assertion_ids.append(value)
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    assertion = evidence.get("assertion") if isinstance(evidence.get("assertion"), dict) else {}
    assertion_ids.append(str(assertion.get("assertion_id") or "").strip().lower())
    return any(value == "assert_isolation" for value in assertion_ids if value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# ── Product family vocabulary → evaluator match ontology normalization ─────
#
# Two taxonomies describe the same defect classes: the product side
# (ai_test_asset_center.test_obligation.CANONICAL_RISK_FAMILIES short ids and
# bug_ontology_registry family ids, surfaced here by classify_risk_family) and
# the evaluator match ontology (_benchmark_match_ontology.json).  Some product
# labels are NOT literal aliases of their evaluator counterpart
# (``conservation`` vs ``money_quantity_conservation``, ``idempotency`` vs
# ``idempotency_duplicate_submit``), so direct string comparison rejected
# semantically identical families as a family mismatch (-0.20).  This table is
# the declared semantic correspondence between the two taxonomies; each entry
# is grounded in the product-side definition cited in the comment.
_PRODUCT_FAMILY_TO_EVALUATOR: dict[str, str] = {
    # --- test_obligation.CANONICAL_RISK_FAMILIES short ids ------------------
    "authorization": "authorization_access_control",  # 权限与访问控制
    "isolation": "tenant_isolation",  # 租户/组织/数据隔离 (alias tenant_isolation→isolation)
    "state": "state_machine",  # 状态机与生命周期 (assertion kind state_transition)
    "conservation": "money_quantity_conservation",  # 金额/数量/库存守恒
    "idempotency": "idempotency_duplicate_submit",  # 幂等与重复提交 (kind idempotency_effect)
    "concurrency": "concurrency_race_condition",  # 并发竞态 (kind concurrency_final_invariant)
    "validation": "input_validation_boundary",  # 输入校验与边界
    "visibility": "visibility_disclosure",  # 可见性与数据泄露
    # product DSL resolves temporal as eventual_consistency
    # (assertion_dsl_base.KIND_ALIASES["temporal"]="eventual_consistency"):
    # the evaluator counterpart with eventual-completion semantics is the
    # async/eventual-consistency key.
    "temporal": "async_eventual_consistency",
    # product "privacy" = sensitive identity fields (email/phone/status/role)
    # must be absent/masked for unauthorized actors (account_enumeration_guard);
    # matches the evaluator invariant sensitive_fields_must_be_masked_or_omitted.
    "privacy": "visibility_disclosure",
    # --- bug_ontology_registry family ids (classify_risk_family output) -----
    "tenant_isolation": "tenant_isolation",  # identity
    "state_machine": "state_machine",  # identity
    "input_boundary": "input_validation_boundary",  # Input Boundary → 输入校验与边界
    "data_integrity": "data_consistency",  # invariant_type data_consistency_invariant
    "lifecycle": "state_machine",  # Lifecycle Integrity → 状态机与生命周期
    "eventual_consistency": "async_eventual_consistency",  # Eventual Consistency
    "audit_trail": "audit_traceability",  # Audit Trail → 审计与可追踪性
    # --- other product vocabularies -----------------------------------------
    "workflow": "workflow_approval",  # 审批流/工作流
    "audit": "audit_traceability",  # 审计与可追踪性
    "data_consistency": "data_consistency",  # identity
}


def _evaluator_family(family: str) -> str:
    """Normalize a product-side family label onto the evaluator match ontology.

    Unknown labels pass through unchanged (including "unclassified"), so the
    normalization is a strict superset of today's behavior.
    """
    key = str(family or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _PRODUCT_FAMILY_TO_EVALUATOR.get(key, str(family or ""))


def _canonical_match_family(item: dict[str, Any]) -> str:
    """Map product and GT family labels onto the evaluator match ontology.

    Product ontology uses short ids such as ``concurrency``; the evaluator match
    ontology uses ``concurrency_race_condition`` with ``concurrency`` as an
    alias. Matching must resolve both sides through that alias table so a real
    concurrency deliverable is not rejected as a family mismatch.  Product
    labels that are not literal aliases (``conservation``,
    ``idempotency``, ...) are normalized through ``_evaluator_family``.
    """
    explicit = _explicit_family(item)
    if explicit:
        return _evaluator_family(explicit)
    classified = classify_risk_family(item)
    if classified and classified != "unclassified":
        aliased = _explicit_family({"risk_family": classified})
        if aliased:
            return _evaluator_family(aliased)
        ontology = _benchmark_match_ontology()
        if classified in ontology:
            return _evaluator_family(classified)
        return _evaluator_family(classified)
    return _evaluator_family(_risk_family_for_item(item))


def _risk_family_for_item(item: dict[str, Any]) -> str:
    explicit = _explicit_family(item)
    if explicit:
        return explicit
    classified = classify_risk_family(item)
    if classified and classified != "unclassified":
        aliased = _explicit_family({"risk_family": classified})
        if aliased:
            return aliased
        return classified
    blob = _text_blob(item)
    ontology = _benchmark_match_ontology()
    best_family = ""
    best_hits = 0
    for family, spec in ontology.items():
        hits = 0
        for alias in spec.get("aliases", ()):
            token = str(alias or "").lower().strip()
            if token and token in blob:
                hits += 1
        if hits > best_hits:
            best_family, best_hits = family, hits
    return best_family or "unclassified"


def _invariants_for_item(item: dict[str, Any], family: str) -> list[str]:
    explicit = item.get("invariant") or item.get("business_invariant") or item.get("oracle")
    if isinstance(explicit, str) and explicit.strip():
        return [explicit.strip()[:160]]
    if isinstance(explicit, list):
        values = [str(v).strip()[:160] for v in explicit if str(v).strip()]
        if values:
            return values[:8]
    spec = _RISK_FAMILY_ONTOLOGY.get(family) or {}
    invariants = [str(v) for v in spec.get("invariants", ()) if str(v)]
    return invariants[:3] if invariants else ["unclassified_invariant"]


def _evidence_profile(item: dict[str, Any]) -> dict[str, bool]:
    raw = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
    reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
    db_evidence = item.get("db_evidence") if isinstance(item.get("db_evidence"), dict) else {}
    request_raw = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
    response_raw = raw.get("response_raw") if isinstance(raw.get("response_raw"), dict) else {}
    ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
    return {
        "has_request": bool(item.get("request") or request_raw or item.get("_api_path") or item.get("path")),
        "has_response": bool(item.get("response") or response_raw or reproduction.get("har_evidence")),
        "has_assertion": bool(item.get("expected") and item.get("actual")) or bool(item.get("assertion") or item.get("oracle_result")),
        "has_db_evidence": bool(db_evidence and db_evidence.get("status") == "captured"),
        "has_ui_evidence": bool(ui_result or item.get("har_evidence") or item.get("ui_verification")),
        "has_regression_probe": bool(item.get("regression") or item.get("regression_probe") or item.get("regression_suggestions")),
    }


def _is_confirmed(item: dict[str, Any]) -> bool:
    status = str(item.get("confirmation_status") or item.get("bug_status") or "").strip().lower()
    if status in {"confirmed", "validated", "validated_candidate", "reproduced"}:
        return True
    if item.get("customer_delivery_status") == "defect" and item.get("gate_passed"):
        return True
    return False


def _coverage_matrix(
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None = None,
    *,
    truth: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an honest risk-family and invariant coverage matrix.

    This is not benchmark recall unless a ground-truth set is supplied.  It is a
    product coverage view derived from real scan outputs, so it is safe to show
    even for arbitrary customer projects.
    """
    all_items = [f for f in list(findings or []) + list(candidates or []) if isinstance(f, dict)]
    family_rows: dict[str, dict[str, Any]] = {}
    invariant_rows: dict[str, dict[str, Any]] = {}

    for family, spec in _RISK_FAMILY_ONTOLOGY.items():
        family_rows[family] = {
            "family": family,
            "display_name": spec.get("display_name") or family,
            "target_invariant_count": len(spec.get("invariants", ()) or ()),
            "confirmed_count": 0,
            "candidate_count": 0,
            "evidence_complete_count": 0,
            "touched_invariants": [],
            "coverage_status": "gap",
        }
        for invariant in spec.get("invariants", ()) or ():
            invariant_rows[str(invariant)] = {
                "invariant": str(invariant),
                "family": family,
                "confirmed_count": 0,
                "candidate_count": 0,
                "evidence_complete_count": 0,
                "coverage_status": "gap",
            }

    unclassified_count = 0
    for item in all_items:
        family = _risk_family_for_item(item)
        if family == "unclassified":
            unclassified_count += 1
            continue
        row = family_rows.setdefault(family, {
            "family": family,
            "display_name": family,
            "target_invariant_count": 0,
            "confirmed_count": 0,
            "candidate_count": 0,
            "evidence_complete_count": 0,
            "touched_invariants": [],
            "coverage_status": "gap",
        })
        confirmed = _is_confirmed(item)
        if confirmed:
            row["confirmed_count"] += 1
        else:
            row["candidate_count"] += 1
        evidence = _evidence_profile(item)
        evidence_complete = evidence["has_request"] and evidence["has_response"] and evidence["has_assertion"]
        if evidence_complete:
            row["evidence_complete_count"] += 1
        for invariant in _invariants_for_item(item, family):
            if invariant not in row["touched_invariants"]:
                row["touched_invariants"].append(invariant)
            inv_row = invariant_rows.setdefault(invariant, {
                "invariant": invariant,
                "family": family,
                "confirmed_count": 0,
                "candidate_count": 0,
                "evidence_complete_count": 0,
                "coverage_status": "gap",
            })
            if confirmed:
                inv_row["confirmed_count"] += 1
            else:
                inv_row["candidate_count"] += 1
            if evidence_complete:
                inv_row["evidence_complete_count"] += 1

    for row in family_rows.values():
        if row["confirmed_count"] and row["evidence_complete_count"]:
            row["coverage_status"] = "confirmed_with_evidence"
        elif row["confirmed_count"]:
            row["coverage_status"] = "confirmed_needs_evidence"
        elif row["candidate_count"]:
            row["coverage_status"] = "candidate_only"
        row["touched_invariant_count"] = len(row.get("touched_invariants") or [])
        row["touched_invariants"] = list(row.get("touched_invariants") or [])[:12]

    for row in invariant_rows.values():
        if row["confirmed_count"] and row["evidence_complete_count"]:
            row["coverage_status"] = "confirmed_with_evidence"
        elif row["confirmed_count"]:
            row["coverage_status"] = "confirmed_needs_evidence"
        elif row["candidate_count"]:
            row["coverage_status"] = "candidate_only"

    truth_family_totals: dict[str, int] = {}
    for bug in truth or []:
        if not isinstance(bug, dict):
            continue
        family = _risk_family_for_item(bug)
        if family != "unclassified":
            truth_family_totals[family] = truth_family_totals.get(family, 0) + 1
    for family, count in truth_family_totals.items():
        family_rows.setdefault(family, {"family": family, "display_name": family})["ground_truth_total"] = count

    rows = sorted(family_rows.values(), key=lambda row: (row.get("coverage_status") == "gap", str(row.get("family") or "")))
    invariant_list = sorted(invariant_rows.values(), key=lambda row: (row.get("coverage_status") == "gap", str(row.get("family") or ""), str(row.get("invariant") or "")))
    covered_families = [row for row in rows if row.get("coverage_status") != "gap"]
    confirmed_families = [row for row in rows if str(row.get("coverage_status")) .startswith("confirmed")]
    total_target_families = len(_RISK_FAMILY_ONTOLOGY)
    return {
        "schema_version": "risk_invariant_coverage_v1",
        "ontology_family_count": total_target_families,
        "ontology_invariant_count": sum(len(spec.get("invariants", ()) or ()) for spec in _RISK_FAMILY_ONTOLOGY.values()),
        "covered_family_count": len(covered_families),
        "confirmed_family_count": len(confirmed_families),
        "family_coverage_rate": round(len(covered_families) / total_target_families, 4) if total_target_families else 0.0,
        "confirmed_family_rate": round(len(confirmed_families) / total_target_families, 4) if total_target_families else 0.0,
        "unclassified_signal_count": unclassified_count,
        "families": rows,
        "invariants": invariant_list[:200],
        "honesty_note": "This is risk/invariant coverage from scan outputs, not bug recall unless ground_truth_available is true.",
    }


_STAGE_LOSS_STAGES = (
    "hypothesis_generated",
    "endpoint_bound",
    "selected",
    "executed",
    "oracle_evaluated",
    "oracle_matched",
    "deliverable",
)


def _truth_paths(truth: dict[str, Any]) -> set[str]:
    paths = _extract_api_paths(str(truth.get("trigger") or ""))
    paths |= _extract_api_paths(
        str(truth.get("endpoint_hint") or truth.get("api_path") or "")
    )
    for endpoint in truth.get("affected_endpoints") or truth.get("related_endpoints") or []:
        if isinstance(endpoint, dict):
            paths |= _extract_api_paths(
                str(endpoint.get("path") or endpoint.get("api_path") or "")
            )
        else:
            paths |= _extract_api_paths(str(endpoint))
    keywords = truth.get("match_keywords")
    if isinstance(keywords, list):
        paths |= _extract_api_paths(" ".join(str(item) for item in keywords))
    return paths


def _trace_family(value: Any) -> str:
    return _risk_family_for_item({"risk_family": value})


def _trace_paths(trace: dict[str, Any]) -> set[str]:
    generation = trace.get("generation") if isinstance(trace.get("generation"), dict) else {}
    execution = trace.get("execution") if isinstance(trace.get("execution"), dict) else {}
    values = list(generation.get("endpoint_shapes") or [])
    values.append(generation.get("bound_path_shape"))
    values.extend(execution.get("normalized_paths") or [])
    values.extend(trace.get("operation_refs") or [])
    return _extract_api_paths(" ".join(str(item or "") for item in values))


def _trace_methods(trace: dict[str, Any]) -> set[str]:
    generation = trace.get("generation") if isinstance(trace.get("generation"), dict) else {}
    execution = trace.get("execution") if isinstance(trace.get("execution"), dict) else {}
    values = list(execution.get("methods") or [])
    values.append(generation.get("bound_method"))
    return {str(item or "").strip().upper() for item in values if str(item or "").strip()}


def _truth_methods(truth: dict[str, Any]) -> set[str]:
    methods: set[str] = set()
    for key in ("method", "http_method", "verb"):
        value = str(truth.get(key) or "").strip().upper()
        if value in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            methods.add(value)
    for endpoint in truth.get("affected_endpoints") or truth.get("related_endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        value = str(endpoint.get("method") or endpoint.get("http_method") or "").strip().upper()
        if value in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            methods.add(value)
    trigger = str(truth.get("trigger") or "").upper()
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        if re.search(rf"\b{method}\b", trigger):
            methods.add(method)
    return methods


def _candidate_id(candidate: dict[str, Any]) -> str:
    for key in (
        "candidate_id",
        "hypothesis_id",
        "finding_id",
        "behavior_slice_id",
        "evidence_id",
        "id",
    ):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value
    return ""


def compute_stage_loss_matrix(
    *,
    ground_truth_path: Path | str,
    candidates: list[dict[str, Any]] | None,
    trace_ledger: dict[str, Any],
    delivered_bug_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build evaluator-private per-Bug loss diagnostics from redacted traces.

    Candidate and trace matches are diagnostic only: they never increase TP or
    alter Recall/Precision. Ground-truth titles, keywords and paths are not
    copied into the returned matrix.
    """

    truth_bugs = _load_truth_bugs(Path(ground_truth_path))
    traces = [item for item in (trace_ledger.get("attempts") or []) if isinstance(item, dict)]
    delivered = {str(item) for item in delivered_bug_ids if str(item).strip()}
    candidate_rows = [item for item in (candidates or []) if isinstance(item, dict)]

    candidates_by_truth: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidate_rows:
        matched = _match_finding_to_gt(candidate, truth_bugs, set())
        if not matched:
            continue
        bug_id = str(matched.get("bug_id") or matched.get("id") or "").strip()
        if bug_id:
            candidates_by_truth.setdefault(bug_id, []).append(candidate)

    family_counts: dict[str, int] = {}
    for truth in truth_bugs:
        family = _risk_family_for_item(truth)
        family_counts[family] = family_counts.get(family, 0) + 1

    rows: list[dict[str, Any]] = []
    first_loss_counts: dict[str, int] = {}
    stage_reached_counts = {stage: 0 for stage in _STAGE_LOSS_STAGES}
    ambiguous_count = 0

    for truth in truth_bugs:
        bug_id = str(truth.get("bug_id") or truth.get("id") or "").strip()
        if not bug_id:
            continue
        family = _risk_family_for_item(truth)
        truth_paths = _truth_paths(truth)
        truth_methods = _truth_methods(truth)
        matched_candidates = candidates_by_truth.get(bug_id, [])
        candidate_slice_ids = {
            str(item.get("behavior_slice_id") or "").strip()
            for item in matched_candidates
            if str(item.get("behavior_slice_id") or "").strip()
        }

        trace_matches: list[tuple[float, str, dict[str, Any]]] = []
        family_only_collision = False
        for trace in traces:
            generation = trace.get("generation") if isinstance(trace.get("generation"), dict) else {}
            trace_family = _trace_family(
                trace.get("risk_family") or generation.get("family")
            )
            trace_path_values = _trace_paths(trace)
            trace_method_values = _trace_methods(trace)
            slice_id = str(trace.get("behavior_slice_id") or "").strip()
            identity_link = bool(slice_id and slice_id in candidate_slice_ids)
            path_link = _paths_overlap(truth_paths, trace_path_values)
            family_link = trace_family == family
            method_link = bool(truth_methods and trace_method_values and truth_methods & trace_method_values)

            if identity_link:
                trace_matches.append((1.0, "candidate_slice_identity", trace))
            elif path_link:
                score = 0.65 + (0.25 if family_link else 0.0) + (0.1 if method_link else 0.0)
                trace_matches.append((min(1.0, score), "endpoint_path", trace))
            elif family_link and family_counts.get(family, 0) == 1:
                trace_matches.append((0.35, "unique_family", trace))
            elif family_link:
                family_only_collision = True

        trace_matches.sort(
            key=lambda item: (
                -item[0],
                str(item[2].get("attempt_id") or item[2].get("trace_id") or ""),
            )
        )
        matched_traces = [item[2] for item in trace_matches]
        is_delivered = bug_id in delivered
        ambiguous = bool(
            family_only_collision
            and not matched_candidates
            and not matched_traces
            and not is_delivered
        )

        if ambiguous:
            ambiguous_count += 1
            stage_state: dict[str, bool | None] = {stage: None for stage in _STAGE_LOSS_STAGES}
            first_loss_stage = "diagnostic_ambiguity"
            diagnostic_status = "AMBIGUOUS"
        else:
            hypothesis_generated = bool(matched_candidates or matched_traces or is_delivered)
            endpoint_bound = bool(
                is_delivered
                or any(_finding_paths(item) for item in matched_candidates)
                or any(
                    int((trace.get("generation") or {}).get("endpoint_count") or 0) > 0
                    or bool(trace.get("operation_refs"))
                    for trace in matched_traces
                )
            )
            selected = bool(
                is_delivered
                or any(
                    bool((trace.get("selection") or {}).get("selected"))
                    or bool(trace.get("obligation_id"))
                    for trace in matched_traces
                )
            )
            executed = bool(
                is_delivered
                or any(
                    int((trace.get("execution") or {}).get("http_step_count") or 0) > 0
                    or str(trace.get("execution_status") or "").upper() == "EXECUTED"
                    or str(trace.get("terminal_status") or "").upper()
                    in {"DELIVERABLE", "REJECTED"}
                    for trace in matched_traces
                )
            )
            oracle_evaluated = bool(
                is_delivered
                or any(
                    int((trace.get("verification") or {}).get("oracle_evaluated_count") or 0) > 0
                    or bool(trace.get("oracle_receipt_id"))
                    for trace in matched_traces
                )
            )
            oracle_matched = bool(
                is_delivered
                or any(
                    int((trace.get("verification") or {}).get("oracle_failure_votes") or 0) > 0
                    or str(trace.get("terminal_status") or "").upper() == "DELIVERABLE"
                    or (
                        str(trace.get("gate_status") or "").upper() == "REJECTED"
                        and str(trace.get("gate_reason_code") or "").upper()
                        != "ORACLE_NOT_VIOLATED"
                    )
                    for trace in matched_traces
                )
            )
            stage_state = {
                "hypothesis_generated": hypothesis_generated,
                "endpoint_bound": endpoint_bound,
                "selected": selected,
                "executed": executed,
                "oracle_evaluated": oracle_evaluated,
                "oracle_matched": oracle_matched,
                "deliverable": is_delivered,
            }
            if not hypothesis_generated:
                first_loss_stage = "hypothesis_generation"
            elif not endpoint_bound:
                first_loss_stage = "endpoint_binding"
            elif not matched_traces and not is_delivered:
                first_loss_stage = "trace_observability"
            elif not selected:
                first_loss_stage = "selection"
            elif not executed:
                first_loss_stage = "execution"
            elif not oracle_evaluated:
                first_loss_stage = "oracle_evaluation"
            elif not oracle_matched:
                first_loss_stage = "oracle_resolution"
            elif not is_delivered:
                first_loss_stage = "delivery_gate"
            else:
                first_loss_stage = "delivered"
            diagnostic_status = "COMPLETE"
            for stage, reached in stage_state.items():
                if reached is True:
                    stage_reached_counts[stage] += 1

        first_loss_counts[first_loss_stage] = first_loss_counts.get(first_loss_stage, 0) + 1
        rows.append(
            {
                "bug_id": bug_id,
                "category": family,
                "severity": str(truth.get("severity") or ""),
                **stage_state,
                "first_loss_stage": first_loss_stage,
                "diagnostic_status": diagnostic_status,
                "match_basis": trace_matches[0][1] if trace_matches else ("candidate_semantic" if matched_candidates else "none"),
                "match_confidence": round(trace_matches[0][0], 4) if trace_matches else (0.6 if matched_candidates else 0.0),
                "candidate_ids": [value for value in (_candidate_id(item) for item in matched_candidates[:8]) if value],
                "trace_ids": [str(item.get("trace_id") or "") for item in matched_traces[:8] if str(item.get("trace_id") or "")],
            }
        )

    definite_count = max(0, len(rows) - ambiguous_count)
    return {
        "schema_version": "qualibug.discovery-stage-loss-matrix.v1",
        "status": "READY" if rows and ambiguous_count == 0 else ("PARTIAL" if rows else "EMPTY"),
        "ground_truth_bug_count": len(rows),
        "diagnostic_complete_count": definite_count,
        "diagnostic_ambiguous_count": ambiguous_count,
        "first_loss_stage_counts": dict(
            sorted(first_loss_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "stage_reached_counts": stage_reached_counts,
        "stage_reached_rates": {
            stage: round(count / definite_count, 4) if definite_count else None
            for stage, count in stage_reached_counts.items()
        },
        "bugs": rows,
        "scoring_contract": "diagnostic_only_never_changes_tp_fp_fn",
    }


def compute_benchmark(
    project: str,
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None = None,
    *,
    root: Path | None = None,
    ground_truth_path: str = "",
) -> dict[str, Any]:
    """Compute benchmark metrics for a scan run.

    With ground truth, returns benchmark recall/precision.  Without ground truth,
    returns only the non-fabricated risk/invariant coverage matrix.
    """
    root = Path(root or os.environ.get("QUALIBUG_WORKSPACE_ROOT", Path.cwd()))

    # Resolve ground truth path
    gt_path: Path | None = None
    if ground_truth_path:
        gt_path = Path(ground_truth_path)
    elif os.environ.get("QUALIBUG_BENCHMARK_GROUND_TRUTH"):
        gt_path = Path(os.environ["QUALIBUG_BENCHMARK_GROUND_TRUTH"])
    else:
        # Try project-local benchmark dir first
        candidates_paths = [
            root / "platform_workspace" / project / "private_ground_truth" / "ground_truth_bugs.json",
            root / "platform_workspace" / project / "benchmark_ground_truth" / "bugs.json",
        ]
        for p in candidates_paths:
            if p.exists():
                gt_path = p
                break

    if gt_path is None or not gt_path.exists():
        return {
            "benchmark_active": False,
            "ground_truth_available": False,
            "reason": "ground_truth_missing",
            "coverage_matrix": _coverage_matrix(findings, candidates),
        }

    truth_bugs = _load_truth_bugs(gt_path)
    if not truth_bugs:
        return {
            "benchmark_active": False,
            "ground_truth_available": False,
            "reason": "ground_truth_empty",
            "coverage_matrix": _coverage_matrix(findings, candidates),
        }

    # ── Match confirmed findings against ground truth (post-scan scoring only) ──
    raw_confirmed_findings = [
        f for f in findings
        if isinstance(f, dict) and (
            f.get("gate_passed") is True
            or str(f.get("customer_delivery_status") or "") == "defect"
            or str(f.get("confirmation_status") or "") == "confirmed"
        )
    ]
    confirmed_findings, duplicate_findings_excluded = _deduplicate_benchmark_findings(
        raw_confirmed_findings
    )
    all_findings = list(confirmed_findings) + [
        c for c in (candidates or [])
        if isinstance(c, dict) and c.get("gate_passed") is True
    ]

    matched_gt_ids: set[str] = set()
    matched_pairs: list[dict[str, Any]] = []
    false_positives: list[dict[str, Any]] = []

    for finding in confirmed_findings:
        gt = _match_finding_to_gt(finding, truth_bugs, matched_gt_ids)
        if gt:
            gt_id = str(gt.get("bug_id") or gt.get("id") or "")
            matched_gt_ids.add(gt_id)
            matched_pairs.append({
                "finding_title": finding.get("title", ""),
                "finding_severity": finding.get("severity", ""),
                "match_score": gt.get("__match_score", 0),
                "gt_bug_id": gt_id,
                "gt_title": gt.get("title", ""),
                "gt_severity": gt.get("severity", ""),
                "gt_type": gt.get("type", ""),
                "gt_risk_family": _risk_family_for_item(gt),
            })
        elif finding.get("customer_delivery_status") == "defect" or finding.get("gate_passed") is True:
            false_positives.append(finding)

    total_gt = len(truth_bugs)
    total_found = len(all_findings)
    true_pos = len(matched_pairs)
    false_pos = len(false_positives)
    false_neg = max(0, total_gt - true_pos)

    # ── Sub-metrics ──
    p0p1_gt = [b for b in truth_bugs if b.get("severity") in ("P0", "P1", "critical", "high")]
    p0p1_found = [m for m in matched_pairs if m.get("gt_severity") in ("P0", "P1", "critical", "high")]

    # Evidence completeness: % of confirmed findings that have request + response + assertion
    confirmed = [f for f in findings if f.get("confirmation_status") in ("confirmed", "validated_candidate")]
    evidence_complete = 0
    for f in confirmed:
        profile = _evidence_profile(f)
        if profile["has_request"] and profile["has_response"] and profile["has_assertion"]:
            evidence_complete += 1

    # Reproduction success rate: confirmed findings that are NOT synthetic and passed gates
    repro_total = len(confirmed)
    repro_success = 0
    for f in confirmed:
        repro = f.get("reproduction")
        if not isinstance(repro, dict):
            repro = {}
        is_synthetic = repro.get("is_synthetic", False)
        gate_passed = bool(f.get("gate_passed"))
        if not is_synthetic and gate_passed:
            repro_success += 1

    # Regression success rate (from findings that have regression data)
    reg_total = 0
    reg_passed = 0
    for f in findings:
        reg = f.get("regression")
        if not isinstance(reg, dict):
            continue
        if reg.get("included_in_suite"):
            reg_total += 1
            if reg.get("latest_status") == "passed":
                reg_passed += 1

    metrics = {
        "benchmark_active": True,
        "ground_truth_available": True,
        "ground_truth_source": str(gt_path),
        "ground_truth_bug_count": total_gt,
        "scan_findings_total": total_found,
        "duplicate_findings_excluded": duplicate_findings_excluded,
        "true_positives": true_pos,
        "false_positives": false_pos,
        "false_negatives": false_neg,
        "recall": round(true_pos / total_gt, 4) if total_gt else 0,
        "precision": round(true_pos / total_found, 4) if total_found else 0,
        "false_positive_rate": round(false_pos / total_found, 4) if total_found else 0,
        "false_negative_rate": round(false_neg / total_gt, 4) if total_gt else 0,
        "f1_score": round(2 * true_pos / (2 * true_pos + false_pos + false_neg), 4) if (2 * true_pos + false_pos + false_neg) > 0 else 0,
        "high_value_recall": round(len(p0p1_found) / len(p0p1_gt), 4) if p0p1_gt else 0,
        "evidence_completeness_rate": round(evidence_complete / len(confirmed), 4) if confirmed else 0,
        "evidence_complete_count": evidence_complete,
        "evidence_total_count": len(confirmed),
        "reproduction_success_rate": round(repro_success / repro_total, 4) if repro_total else 0,
        "reproduction_success_count": repro_success,
        "reproduction_total_count": repro_total,
        "regression_success_rate": round(reg_passed / reg_total, 4) if reg_total else 0,
        "regression_total_count": reg_total,
        "regression_passed_count": reg_passed,
        "matched_bugs": matched_pairs[:50],
        "matched_bug_ids": sorted(matched_gt_ids),
        "missed_bug_ids": [b.get("bug_id") for b in truth_bugs if b.get("bug_id") not in matched_gt_ids],
        "bug_type_breakdown": _bug_type_breakdown(matched_pairs, truth_bugs),
        "risk_family_breakdown": _risk_family_breakdown(matched_pairs, truth_bugs),
        "coverage_matrix": _coverage_matrix(findings, candidates, truth=truth_bugs),
    }
    return metrics


def _bug_type_breakdown(
    matched: list[dict[str, Any]],
    truth: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Per-bug-type recall breakdown."""
    type_map: dict[str, dict[str, int]] = {}
    for bug in truth:
        btype = str(bug.get("type") or "other").strip() or "other"
        entry = type_map.setdefault(btype, {"total": 0, "detected": 0})
        entry["total"] += 1

    gt_ids_matched = {m["gt_bug_id"] for m in matched}
    for bug in truth:
        btype = str(bug.get("type") or "other").strip() or "other"
        if bug.get("bug_id") in gt_ids_matched:
            type_map.setdefault(btype, {"total": 0, "detected": 0})["detected"] += 1

    return type_map


def _risk_family_breakdown(
    matched: list[dict[str, Any]],
    truth: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Per-risk-family recall breakdown decoupled from fixed bug type labels."""
    family_map: dict[str, dict[str, int]] = {}
    for bug in truth:
        family = _risk_family_for_item(bug)
        entry = family_map.setdefault(family, {"total": 0, "detected": 0})
        entry["total"] += 1

    gt_ids_matched = {m["gt_bug_id"] for m in matched}
    for bug in truth:
        family = _risk_family_for_item(bug)
        if bug.get("bug_id") in gt_ids_matched:
            family_map.setdefault(family, {"total": 0, "detected": 0})["detected"] += 1

    return family_map




# ═════════════════════════════════════════════════════════════════════════════
# End-to-End Benchmark Pipeline
# ═════════════════════════════════════════════════════════════════════════════

def run_benchmark_end_to_end(
    industry: str,
    bug_count: int = 50,
    seed: int | None = None,
    *,
    root: str | Path | None = None,
    findings: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a complete benchmark pipeline end-to-end for one industry.

    Flow:
      1. BenchmarkBugFactory generates known bugs
      2. Ground truth written to PRIVATE_BLOCKLIST path
      3. Public artifacts generated for blind discovery
      4. Runtime seeds built for benchmark_runtime target
      5. If findings/candidates provided, compute benchmark metrics
      6. Baseline snapshot recorded and compared

    This is the SINGLE entry point for a complete benchmark run.
    It never fabricates data — all metrics are computed from real inputs.

    Args:
        industry: Industry ID (crm, ecommerce, erp, finance, medical, education, saas).
        bug_count: Number of bug instances to generate.
        seed: Random seed for reproducibility.
        root: Workspace root directory.
        findings: Optional list of discovery findings (from a scan).
        candidates: Optional list of candidate findings.

    Returns:
        Dict with full pipeline result including paths, counts, and metrics.
    """
    from .benchmark_bug_factory import (
        BenchmarkBugFactory,
        validate_ground_truth_integrity,
    )
    from ai_test_asset_center.benchmark_baseline_tracker import BenchmarkBaselineTracker

    root_path = Path(root or os.environ.get("QUALIBUG_WORKSPACE_ROOT", Path.cwd()))

    result: dict[str, Any] = {
        "pipeline": "benchmark_end_to_end.v1",
        "industry": industry,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stages": {},
    }

    # ── Stage 1: Generate bugs ──────────────────────────────────────
    try:
        factory = BenchmarkBugFactory(industry)
        bugs = factory.generate(count=bug_count, seed=seed)

        result["stages"]["bug_generation"] = {
            "status": "ok",
            "bug_count": len(bugs),
            "templates_used": sorted({b["template_id"] for b in bugs}),
            "risk_types": sorted({b["risk_type"] for b in bugs}),
            "severity_distribution": {
                sev: len([b for b in bugs if b["severity"] == sev])
                for sev in sorted({b["severity"] for b in bugs})
            },
        }
    except Exception as e:
        result["stages"]["bug_generation"] = {"status": "failed", "error": str(e)}
        return result

    # ── Stage 2: Write ground truth ─────────────────────────────────
    gt_path: Path | None = None
    try:
        gt_path = factory.write_ground_truth(bugs, output_dir=root_path)
        integrity = validate_ground_truth_integrity(gt_path)

        result["stages"]["ground_truth"] = {
            "status": "ok" if integrity["valid"] else "invalid",
            "path": str(gt_path),
            "integrity": integrity,
        }
    except Exception as e:
        result["stages"]["ground_truth"] = {"status": "failed", "error": str(e)}

    # ── Stage 3: Public artifacts ───────────────────────────────────
    try:
        public = factory.generate_public_artifacts(bugs, output_dir=root_path)
        result["stages"]["public_artifacts"] = {
            "status": "ok",
            "files": {k: str(v) for k, v in public.items()},
        }
    except Exception as e:
        result["stages"]["public_artifacts"] = {"status": "failed", "error": str(e)}

    # ── Stage 4: Runtime seeds ──────────────────────────────────────
    try:
        seeds = factory.build_runtime_seeds(bugs)
        seeds_path = root_path / "platform_workspace" / industry / "oracle" / "BUG_GROUND_TRUTH.json"
        seeds_path.parent.mkdir(parents=True, exist_ok=True)
        seeds_path.write_text(json.dumps(seeds, ensure_ascii=False, indent=2), encoding="utf-8")

        result["stages"]["runtime_seeds"] = {
            "status": "ok",
            "seed_count": seeds.get("total_seeds", 0),
            "path": str(seeds_path),
        }
    except Exception as e:
        result["stages"]["runtime_seeds"] = {"status": "failed", "error": str(e)}

    # ── Stage 5: Compute metrics (if findings provided) ─────────────
    if findings or candidates:
        try:
            truth_bugs = bugs  # Use the generated bugs as ground truth
            all_findings = list(findings or []) + list(candidates or [])

            metrics = compute_benchmark(
                industry,
                findings or [],
                candidates,
                root=root_path,
                ground_truth_path=str(gt_path) if gt_path is not None else "",
            )

            result["stages"]["metrics"] = {
                "status": "ok",
                "benchmark_active": metrics.get("benchmark_active", False),
                "recall": metrics.get("recall"),
                "precision": metrics.get("precision"),
                "f1_score": metrics.get("f1_score"),
                "true_positives": metrics.get("true_positives"),
                "false_positives": metrics.get("false_positives"),
                "false_negatives": metrics.get("false_negatives"),
            }

            # ── Stage 6: Baseline tracking ─────────────────────────
            tracker = BenchmarkBaselineTracker(industry, root=root_path)
            snapshot = tracker.record_run(
                metrics,
                ground_truth_bug_count=len(bugs),
                scan_findings_total=len(all_findings),
                true_positives=metrics.get("true_positives", 0),
                false_positives=metrics.get("false_positives", 0),
                false_negatives=metrics.get("false_negatives", 0),
            )

            result["stages"]["baseline"] = {
                "status": "ok",
                "run_id": snapshot.run_id,
                "total_runs": tracker.get_run_count(),
            }

            # Detect regressions if we have at least 2 runs
            if tracker.get_run_count() >= 2:
                regressions = tracker.detect_regressions()
                result["stages"]["regression_check"] = regressions

            # ── Stage 7: Cross-round bridge ──────────────────────────
            try:
                from ai_test_asset_center.cross_round_bridge import CrossRoundBridge
                bridge = CrossRoundBridge()
                priority_signals = bridge.derive_priority_signals_from_benchmark(metrics)
                bridge.on_learning_generated({
                    "probes": 0, "oracles": 0, "fixtures": 0,
                })
                result["stages"]["cross_round"] = bridge.build_closed_loop_summary()
            except Exception:
                result["stages"]["cross_round"] = {"status": "unavailable"}

        except Exception as e:
            result["stages"]["metrics"] = {"status": "failed", "error": str(e)}
    else:
        result["stages"]["metrics"] = {
            "status": "skipped",
            "reason": "No findings or candidates provided — metrics require scan output",
        }

    # ── Summary ─────────────────────────────────────────────────────
    stage_statuses = [
        s.get("status") for s in result["stages"].values()
        if isinstance(s, dict)
    ]
    all_ok = all(s == "ok" for s in stage_statuses if s != "skipped")
    result["overall_status"] = "ok" if all_ok else "partial"

    return result
