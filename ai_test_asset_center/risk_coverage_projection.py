"""Ground-truth-free risk and invariant coverage for product runtime.

This module is safe to ship with the product. It consumes only current scan
outputs and the industry-neutral Bug Ontology Registry. Recall, precision,
matched IDs, missed IDs, seed generation, and ground-truth path resolution are
evaluator-only concerns and intentionally have no API here.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .bug_ontology_registry import get_ontology_registry
from .artifact_redactor import write_json_redacted


PRODUCT_COVERAGE_SCHEMA = "qualibug.risk-invariant-coverage.v2"


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _ontology() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    registry = get_ontology_registry()
    families = {
        str(row.get("family_id") or "").strip(): dict(row)
        for row in registry.list_families()
        if str(row.get("family_id") or "").strip()
    }
    subtype_to_family = {
        _normalized(entry.subtype): str(entry.family_id)
        for entry in registry.list_entries()
        if _normalized(entry.subtype) and str(entry.family_id)
    }
    return families, subtype_to_family


def _method_path_key(finding: dict[str, Any]) -> tuple[str, str]:
    """Extract a stable method/path key without using evaluator answers."""

    method = str(
        finding.get("method") or finding.get("_api_method") or ""
    ).upper().strip()
    path = str(
        finding.get("path") or finding.get("_api_path") or ""
    ).strip().rstrip("/")
    path = re.sub(r"/\d+", "/{id}", path)
    path = re.sub(r"/\{[^}]+\}", "/{id}", path)
    return method, path


def _text_blob(item: dict[str, Any]) -> str:
    values = [
        item.get("risk_family"),
        item.get("family"),
        item.get("defect_family"),
        item.get("risk_type"),
        item.get("category"),
        item.get("type"),
        item.get("title"),
        item.get("summary"),
        item.get("description"),
        item.get("expected"),
        item.get("actual"),
        item.get("path"),
        item.get("_api_path"),
    ]
    raw = item.get("raw_evidence")
    raw = raw if isinstance(raw, dict) else {}
    request = raw.get("request_raw")
    request = request if isinstance(request, dict) else {}
    response = raw.get("response_raw")
    response = response if isinstance(response, dict) else {}
    values.extend([
        request.get("path"),
        request.get("method"),
        response.get("status_code"),
    ])
    return " ".join(str(value or "") for value in values).lower()


def _family_for_item(item: dict[str, Any]) -> str:
    families, subtype_to_family = _ontology()
    normalized_families = {_normalized(key): key for key in families}
    for field in (
        "risk_family",
        "family",
        "defect_family",
        "risk_type",
        "category",
        "type",
    ):
        value = _normalized(item.get(field))
        if value in normalized_families:
            return normalized_families[value]
        if value in subtype_to_family:
            return subtype_to_family[value]

    blob = _text_blob(item)
    best_family = ""
    best_score = 0
    for family_id, family in families.items():
        terms = {
            _normalized(family_id),
            _normalized(family.get("display_name_en")),
            _normalized(family.get("invariant_type")),
        }
        terms.update(
            subtype
            for subtype, owner in subtype_to_family.items()
            if owner == family_id
        )
        score = sum(
            1
            for term in terms
            if len(term) >= 4 and term.replace("_", " ") in blob
        )
        if score > best_score:
            best_family = family_id
            best_score = score
    return best_family or "unclassified"


def classify_risk_family(item: dict[str, Any]) -> str:
    """Classify one runtime or evaluator row against the shared ontology."""

    return _family_for_item(item)


def risk_family_ontology() -> dict[str, dict[str, Any]]:
    """Return the shared, industry-neutral family catalog for evaluator reuse."""

    families, subtype_to_family = _ontology()
    registry = get_ontology_registry()
    result: dict[str, dict[str, Any]] = {}
    for family_id, family in families.items():
        entries = registry.get_entries_for_family(family_id)
        aliases = sorted({
            family_id,
            str(family.get("display_name_en") or "").strip(),
            str(family.get("invariant_type") or "").strip(),
            *(
                subtype
                for subtype, owner in subtype_to_family.items()
                if owner == family_id
            ),
        } - {""})
        invariants = list(dict.fromkeys(
            str(entry.invariant).strip()
            for entry in entries
            if str(entry.invariant).strip()
        ))
        result[family_id] = {
            "display_name": family.get("display_name_en")
            or family.get("display_name")
            or family_id,
            "aliases": tuple(aliases),
            "invariants": tuple(invariants),
        }
    return result


def _invariants_for_item(
    item: dict[str, Any],
    family_id: str,
) -> list[str]:
    # Findings may contain customer text, credentials, or model-authored oracle
    # prose. Coverage exposes only ontology-owned invariant identifiers.
    registry = get_ontology_registry()
    values = [
        str(entry.invariant).strip()[:160]
        for entry in registry.get_entries_for_family(family_id)
        if str(entry.invariant).strip()
    ]
    if values:
        return list(dict.fromkeys(values))[:3]
    family = next(
        (
            row
            for row in registry.list_families()
            if str(row.get("family_id") or "") == family_id
        ),
        {},
    )
    fallback = str(
        family.get("core_invariant_en")
        or family.get("core_invariant")
        or "unclassified_invariant"
    ).strip()
    return [fallback[:160]]


def _evidence_profile(item: dict[str, Any]) -> dict[str, bool]:
    raw = item.get("raw_evidence")
    raw = raw if isinstance(raw, dict) else {}
    reproduction = item.get("reproduction")
    reproduction = reproduction if isinstance(reproduction, dict) else {}
    request = raw.get("request_raw")
    request = request if isinstance(request, dict) else {}
    response = raw.get("response_raw")
    response = response if isinstance(response, dict) else {}
    return {
        "has_request": bool(
            item.get("request")
            or request
            or item.get("_api_path")
            or item.get("path")
        ),
        "has_response": bool(
            item.get("response")
            or response
            or reproduction.get("har_evidence")
        ),
        "has_assertion": bool(item.get("expected") and item.get("actual"))
        or bool(item.get("assertion") or item.get("oracle_result")),
    }


def _is_confirmed(item: dict[str, Any]) -> bool:
    status = str(
        item.get("confirmation_status") or item.get("bug_status") or ""
    ).strip().lower()
    return status in {
        "confirmed",
        "validated",
        "validated_candidate",
        "reproduced",
    } or bool(
        item.get("customer_delivery_status") == "defect"
        and item.get("gate_passed")
    )


def build_risk_coverage_matrix(
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build product coverage without accepting a ground-truth argument."""

    families, _ = _ontology()
    family_rows: dict[str, dict[str, Any]] = {}
    invariant_rows: dict[str, dict[str, Any]] = {}
    for family_id, spec in families.items():
        invariants = _invariants_for_item({}, family_id)
        family_rows[family_id] = {
            "family": family_id,
            "display_name": spec.get("display_name_en")
            or spec.get("display_name")
            or family_id,
            "target_invariant_count": len(invariants),
            "confirmed_count": 0,
            "candidate_count": 0,
            "evidence_complete_count": 0,
            "touched_invariants": [],
            "coverage_status": "gap",
        }
        for invariant in invariants:
            invariant_rows[invariant] = {
                "invariant": invariant,
                "family": family_id,
                "confirmed_count": 0,
                "candidate_count": 0,
                "evidence_complete_count": 0,
                "coverage_status": "gap",
            }

    unclassified_count = 0
    rows = [
        item
        for item in list(findings or []) + list(candidates or [])
        if isinstance(item, dict)
    ]
    for item in rows:
        family_id = _family_for_item(item)
        if family_id == "unclassified":
            unclassified_count += 1
            continue
        family = family_rows[family_id]
        confirmed = _is_confirmed(item)
        key = "confirmed_count" if confirmed else "candidate_count"
        family[key] += 1
        evidence = _evidence_profile(item)
        complete = all(evidence.values())
        if complete:
            family["evidence_complete_count"] += 1
        for invariant in _invariants_for_item(item, family_id):
            if invariant not in family["touched_invariants"]:
                family["touched_invariants"].append(invariant)
            invariant_row = invariant_rows.setdefault(invariant, {
                "invariant": invariant,
                "family": family_id,
                "confirmed_count": 0,
                "candidate_count": 0,
                "evidence_complete_count": 0,
                "coverage_status": "gap",
            })
            invariant_row[key] += 1
            if complete:
                invariant_row["evidence_complete_count"] += 1

    for row in list(family_rows.values()) + list(invariant_rows.values()):
        if row["confirmed_count"] and row["evidence_complete_count"]:
            row["coverage_status"] = "confirmed_with_evidence"
        elif row["confirmed_count"]:
            row["coverage_status"] = "confirmed_needs_evidence"
        elif row["candidate_count"]:
            row["coverage_status"] = "candidate_only"
    for row in family_rows.values():
        row["touched_invariant_count"] = len(row["touched_invariants"])
        row["touched_invariants"] = row["touched_invariants"][:12]

    family_list = sorted(
        family_rows.values(),
        key=lambda row: (row["coverage_status"] == "gap", row["family"]),
    )
    invariant_list = sorted(
        invariant_rows.values(),
        key=lambda row: (
            row["coverage_status"] == "gap",
            row["family"],
            row["invariant"],
        ),
    )
    covered = [row for row in family_list if row["coverage_status"] != "gap"]
    confirmed = [
        row
        for row in family_list
        if str(row["coverage_status"]).startswith("confirmed")
    ]
    family_count = len(family_list)
    return {
        "schema_version": PRODUCT_COVERAGE_SCHEMA,
        "ontology_family_count": family_count,
        "ontology_invariant_count": len(invariant_rows),
        "covered_family_count": len(covered),
        "confirmed_family_count": len(confirmed),
        "family_coverage_rate": round(len(covered) / family_count, 4)
        if family_count
        else 0.0,
        "confirmed_family_rate": round(len(confirmed) / family_count, 4)
        if family_count
        else 0.0,
        "unclassified_signal_count": unclassified_count,
        "families": family_list,
        "invariants": invariant_list[:200],
        "honesty_note": (
            "Risk/invariant coverage from current scan outputs; not Bug recall."
        ),
    }


def compute_product_coverage_projection(
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "benchmark_active": False,
        "ground_truth_available": False,
        "measurement_status": "NOT_MEASURED",
        "reason": "external_evaluator_receipt_required",
        "coverage_matrix": build_risk_coverage_matrix(findings, candidates),
    }


def persist_product_coverage_projection(
    project: str,
    projection: dict[str, Any],
    *,
    root: Path | None = None,
) -> Path:
    if not projection:
        return Path()
    project_id = str(project or "").strip()
    if (
        not project_id
        or project_id in {".", ".."}
        or any(character in project_id for character in ("/", "\\", "\x00"))
        or any(ord(character) < 32 for character in project_id)
    ):
        raise ValueError("project_id_path_unsafe")
    destination = (
        Path(root or Path.cwd())
        / "platform_outputs"
        / project_id
        / "benchmark"
        / "benchmark_metrics.json"
    )
    write_json_redacted(destination, projection)
    return destination
