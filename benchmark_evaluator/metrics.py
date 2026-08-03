from __future__ import annotations

import re


def compute_metrics(truth: list[dict], discovered: list[dict], matches: list[dict]) -> dict:
    known = len(truth)
    found = len(discovered)
    matched_ids = {m["ground_truth"].get("bug_id") for m in matches}
    matched_templates = {m["ground_truth"].get("template_id") for m in matches if m["ground_truth"].get("template_id")}
    known_templates = {b.get("template_id") for b in truth if b.get("template_id")}
    discovered_templates = {b.get("predicted_template_id") for b in discovered if b.get("predicted_template_id") and b.get("predicted_template_id") != "UNKNOWN_TEMPLATE"}
    exact = sum(1 for m in matches if m.get("match_type") == "exact_instance")
    partial_instance = sum(1 for m in matches if m.get("match_type") == "partial_instance")
    template_match = sum(1 for m in matches if m.get("match_type") == "template_match")
    true_pos = len(matches)
    false_pos = max(0, found - true_pos)
    missed = max(0, known - true_pos)
    p0p1 = [b for b in truth if b.get("severity") in {"P0", "P1"}]
    p0p1_templates = {b.get("template_id") for b in p0p1 if b.get("template_id")}
    p0p1_found = [m for m in matches if m["ground_truth"].get("severity") in {"P0", "P1"}]
    p0p1_found_templates = {m["ground_truth"].get("template_id") for m in p0p1_found if m["ground_truth"].get("template_id")}

    # ── F1 Score ────────────────────────────────────────────────────────
    # F1 = 2 * (precision * recall) / (precision + recall)
    recall_val = true_pos / known if known else 0
    precision_val = true_pos / found if found else (1.0 if known == 0 and found == 0 else 0)
    if recall_val + precision_val > 0:
        f1_score = round(2 * recall_val * precision_val / (recall_val + precision_val), 4)
    else:
        f1_score = 0.0

    # ── Per-risk-type recall breakdown ──────────────────────────────────
    risk_type_breakdown: dict[str, dict[str, int]] = {}
    for bug in truth:
        rt = str(bug.get("risk_type") or bug.get("type") or "other").strip() or "other"
        entry = risk_type_breakdown.setdefault(rt, {"total": 0, "detected": 0, "recall": 0.0})
        entry["total"] += 1

    for m in matches:
        rt = str(m["ground_truth"].get("risk_type") or m["ground_truth"].get("type") or "other").strip() or "other"
        if rt in risk_type_breakdown:
            risk_type_breakdown[rt]["detected"] += 1

    for rt, entry in risk_type_breakdown.items():
        entry["recall"] = round(entry["detected"] / entry["total"], 4) if entry["total"] else 0.0

    # ── Per-severity recall breakdown ───────────────────────────────────
    severity_breakdown: dict[str, dict[str, int]] = {}
    for bug in truth:
        sev = str(bug.get("severity") or "unknown").strip().upper()
        if sev not in severity_breakdown:
            severity_breakdown[sev] = {"total": 0, "detected": 0, "recall": 0.0}
        severity_breakdown[sev]["total"] += 1

    for m in matches:
        sev = str(m["ground_truth"].get("severity") or "").strip().upper()
        if sev in severity_breakdown:
            severity_breakdown[sev]["detected"] += 1

    for sev, entry in severity_breakdown.items():
        entry["recall"] = round(entry["detected"] / entry["total"], 4) if entry["total"] else 0.0

    # ── Evidence-strength-weighted recall ───────────────────────────────
    # Weights follow the evidence-strength convention:
    #   runtime_strong: 0.96, runtime_observed: 0.78, schema_grounded: 0.55,
    #   contract_inferred: 0.45, static_inferred: 0.30, llm_inferred: 0.10
    evidence_weights = {
        "runtime_strong": 0.96,
        "runtime_observed": 0.78,
        "schema_grounded": 0.55,
        "contract_inferred": 0.45,
        "static_inferred": 0.30,
        "llm_inferred": 0.10,
    }
    weighted_tp_sum = 0.0
    max_weight = 0.96
    for m in matches:
        # Determine evidence strength from the discovered finding
        disc = m.get("discovered", {})
        evidence_type = str(disc.get("evidence_type") or disc.get("evidence_strength") or "").strip()
        weight = evidence_weights.get(evidence_type, 0.30)  # default to static_inferred
        weighted_tp_sum += weight / max_weight  # normalize to [0,1]

    evidence_weighted_recall = round(weighted_tp_sum / known, 4) if known else 0.0
    evidence_weighted_precision = round(weighted_tp_sum / found, 4) if found else (1.0 if known == 0 and found == 0 else 0)

    # ── False positive analysis ─────────────────────────────────────────
    # Categorize false positives by likely cause
    fp_analysis = _categorize_false_positives(discovered, matches, truth)

    return {
        "known_bugs": known,
        "known_bug_instances": known,
        "known_bug_templates": len(known_templates),
        "discovered_bugs": found,
        "discovered_bug_instances": found,
        "discovered_bug_templates": len(discovered_templates),
        "matched_true_positives": true_pos,
        "exact_instance_matches": exact,
        "partial_instance_matches": partial_instance,
        "template_matches": template_match,
        "exact_matches": exact,
        "partial_matches": partial_instance + template_match,
        "matched_templates": len(matched_templates),
        "false_positives": false_pos,
        "missed_bugs": missed,
        "missed_instances": missed,
        "missed_templates": len(known_templates - matched_templates),
        "recall": round(recall_val, 4),
        "instance_recall": round(true_pos / known, 4) if known else 0,
        "template_recall": round(len(matched_templates) / len(known_templates), 4) if known_templates else 0,
        "precision": round(precision_val, 4),
        "f1_score": f1_score,
        "false_positive_rate": round(false_pos / found, 4) if found else 0,
        "clean_mode_false_positive_rate": round(false_pos / found, 4) if known == 0 and found else 0,
        "high_value_bug_recall": round(len(p0p1_found) / len(p0p1), 4) if p0p1 else 0,
        "p0_p1_recall": round(len(p0p1_found) / len(p0p1), 4) if p0p1 else 0,
        "p0_p1_instance_recall": round(len(p0p1_found) / len(p0p1), 4) if p0p1 else 0,
        "p0_p1_template_recall": round(len(p0p1_found_templates) / len(p0p1_templates), 4) if p0p1_templates else 0,
        "evidence_completeness_avg": round(
            sum(float(d.get("confidence", 0) or 0) for d in discovered) / found, 4
        ) if found else 0.0,
        "evidence_weighted_recall": evidence_weighted_recall,
        "evidence_weighted_precision": evidence_weighted_precision,
        "risk_type_breakdown": risk_type_breakdown,
        "severity_breakdown": severity_breakdown,
        "false_positive_analysis": fp_analysis,
        "estimated_test_design_hours_saved": round(found * 0.7 + true_pos * 0.8, 1),
        "estimated_bug_report_hours_saved": round(true_pos * 0.5, 1),
    }


def _categorize_false_positives(
    discovered: list[dict],
    matches: list[dict],
    truth: list[dict],
) -> dict[str, Any]:
    """Categorize false positives by likely root cause.

    Categories:
      - path_mismatch: FP has an API path that doesn't match any ground truth
      - semantic_mismatch: path matches but risk_type/severity don't
      - hallucination: FP references entities/roles not in the system
      - duplicate: FP is a duplicate of an already-matched finding
      - unknown: cannot determine cause
    """
    matched_discovered_ids = {m.get("discovered", {}).get("bug_id") or m.get("discovered", {}).get("title") for m in matches}
    truth_paths = {b.get("api", "") or b.get("trigger", "") for b in truth}
    truth_risk_types = {b.get("risk_type", "") or b.get("type", "") for b in truth}
    truth_entities: set[str] = set()
    for b in truth:
        for field in ("entity", "entities", "domain"):
            val = b.get(field)
            if isinstance(val, str) and val:
                truth_entities.add(val.lower())
            elif isinstance(val, list):
                truth_entities.update(str(v).lower() for v in val)

    categories: dict[str, int] = {
        "path_mismatch": 0,
        "semantic_mismatch": 0,
        "hallucination": 0,
        "duplicate": 0,
        "unknown": 0,
    }
    fp_details: list[dict[str, Any]] = []

    for disc in discovered:
        disc_id = disc.get("bug_id") or disc.get("title", "")
        if disc_id in matched_discovered_ids:
            continue  # This is a true positive, skip

        disc_api = str(disc.get("api") or disc.get("path") or disc.get("trigger") or "")
        disc_risk = str(disc.get("risk_type") or disc.get("type") or "")

        # Check path mismatch
        has_path_match = any(
            _paths_overlap(disc_api, tp) for tp in truth_paths
        )

        # Check hallucination: references entities not in truth
        disc_text = str(disc.get("title", "")) + " " + str(disc.get("expected", "")) + " " + str(disc.get("actual", ""))
        disc_text_lower = disc_text.lower()
        has_hallucination = False
        if truth_entities:
            # If the finding mentions entities not in the ground truth entities list
            # but doesn't match any known risk type, it might be a hallucination
            has_known_risk = disc_risk in truth_risk_types
            has_known_entity = any(e in disc_text_lower for e in truth_entities)
            if not has_known_risk and not has_known_entity:
                has_hallucination = True

        # Check duplicate
        is_duplicate = any(
            disc.get("title") == m.get("discovered", {}).get("title")
            for m in matches
        )

        if is_duplicate:
            category = "duplicate"
        elif has_hallucination:
            category = "hallucination"
        elif not has_path_match:
            category = "path_mismatch"
        elif disc_risk not in truth_risk_types:
            category = "semantic_mismatch"
        else:
            category = "unknown"

        categories[category] += 1
        fp_details.append({
            "title": disc.get("title", "")[:120],
            "risk_type": disc_risk,
            "api": disc_api[:120],
            "category": category,
        })

    return {
        "total": sum(categories.values()),
        "categories": categories,
        "details": fp_details[:30],  # Cap at 30 for readability
    }


def _paths_overlap(path_a: str, path_b: str) -> bool:
    """Check if two API paths share the same structure (ignoring params)."""
    norm_a = re.sub(r"/\d+", "/{id}", re.sub(r"/\{[^}]+\}", "/{id}", path_a.strip().lower().rstrip("/")))
    norm_b = re.sub(r"/\d+", "/{id}", re.sub(r"/\{[^}]+\}", "/{id}", path_b.strip().lower().rstrip("/")))
    if norm_a == norm_b:
        return True
    # Also check if the first path segment matches (e.g., /orders vs /orders/123)
    seg_a = norm_a.split("/")[:2] if norm_a else []
    seg_b = norm_b.split("/")[:2] if norm_b else []
    return seg_a == seg_b and len(seg_a) >= 2
