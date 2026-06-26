from __future__ import annotations


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
        "recall": round(true_pos / known, 4) if known else 0,
        "instance_recall": round(true_pos / known, 4) if known else 0,
        "template_recall": round(len(matched_templates) / len(known_templates), 4) if known_templates else 0,
        "precision": round(true_pos / found, 4) if found else (1.0 if known == 0 and found == 0 else 0),
        "false_positive_rate": round(false_pos / found, 4) if found else 0,
        "clean_mode_false_positive_rate": round(false_pos / found, 4) if known == 0 and found else 0,
        "high_value_bug_recall": round(len(p0p1_found) / len(p0p1), 4) if p0p1 else 0,
        "p0_p1_recall": round(len(p0p1_found) / len(p0p1), 4) if p0p1 else 0,
        "p0_p1_instance_recall": round(len(p0p1_found) / len(p0p1), 4) if p0p1 else 0,
        "p0_p1_template_recall": round(len(p0p1_found_templates) / len(p0p1_templates), 4) if p0p1_templates else 0,
        "evidence_completeness_avg": round(sum(float(d.get("confidence", 0)) for d in discovered) / found, 4) if found else 0,
        "estimated_test_design_hours_saved": round(found * 0.7 + true_pos * 0.8, 1),
        "estimated_bug_report_hours_saved": round(true_pos * 0.5, 1),
    }
