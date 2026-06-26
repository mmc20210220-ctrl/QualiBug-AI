from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qsl
from typing import Any


RISK_CATEGORY_HINTS: dict[str, set[str]] = {
    "auth_boundary_probe": {"C03", "C05", "C17"},
    "ownership_scope_probe": {"C03", "C05", "C16", "C17"},
    "idempotency_replay_probe": {"C10", "C11", "C19", "C20", "C32"},
    "state_transition_probe": {"C06", "C07", "C18"},
    "conservation_probe": {"C08", "C09", "C14", "C23", "C24"},
    "audit_privacy_probe": {"C03", "C05", "C16", "C22", "C31"},
    "async_external_event_probe": {"C10", "C19", "C20", "C32"},
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _project_id_from_output(path: Path, payload: dict[str, Any]) -> str:
    value = str(payload.get("project_id") or path.parent.parent.name)
    match = re.search(r"(?:^|_)(\d{2})(?:_|$)", value)
    return match.group(1) if match else value


def _category_id(category: Any) -> str:
    if isinstance(category, dict):
        return str(category.get("id") or category.get("name") or "")
    return str(category or "")


def _candidate_category_codes(candidate: dict[str, Any]) -> set[str]:
    endpoint = candidate.get("endpoint") or {}
    basis = candidate.get("grounding_basis") or {}
    codes = {str(endpoint.get("capability_code") or "")}
    codes.update(str(code) for code in (basis.get("rule_codes") or []))
    codes.update(RISK_CATEGORY_HINTS.get(str(candidate.get("risk_type") or ""), set()))
    return {code for code in codes if re.fullmatch(r"C\d{2}", code)}


def _candidate_path(candidate: dict[str, Any]) -> str:
    endpoint = candidate.get("endpoint") or {}
    return str(endpoint.get("path") or "").strip()


def _candidate_method(candidate: dict[str, Any]) -> str:
    endpoint = candidate.get("endpoint") or {}
    return str(endpoint.get("method") or "").upper().strip()


def _canonical_path(value: str) -> str:
    raw = str(value or "").strip().lower()
    path, _, query = raw.partition("?")
    path = re.sub(r"/api/v\d+/[^/]+", "", path, count=1) or path
    segments = []
    for segment in path.split("/"):
        if not segment:
            continue
        if re.fullmatch(r"\d+", segment) or re.fullmatch(r"\{[^/{}]+\}", segment):
            segments.append("*")
        else:
            segments.append(segment)
    normalized = "/" + "/".join(segments)
    query_keys = sorted(key for key, _ in parse_qsl(query, keep_blank_values=True))
    if query_keys:
        normalized += "?" + "&".join(f"{key}=*" for key in query_keys)
    return normalized


def _paths_compatible(left: str, right: str) -> bool:
    left_c = _canonical_path(left)
    right_c = _canonical_path(right)
    if left_c == right_c:
        return True
    return left_c.split("?", 1)[0] == right_c.split("?", 1)[0] and ("?" not in left_c or "?" not in right_c)


def _load_candidates(outputs_root: Path, glob_pattern: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(outputs_root.glob(glob_pattern)):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        project_id = _project_id_from_output(path, payload)
        for candidate in payload.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            enriched = dict(candidate)
            enriched["_output_file"] = str(path)
            enriched["_project_id"] = project_id
            enriched["_path"] = _candidate_path(candidate)
            enriched["_method"] = _candidate_method(candidate)
            enriched["_category_codes"] = sorted(_candidate_category_codes(candidate))
            candidates.append(enriched)
    return candidates


def _load_ground_truth(suite_root: Path) -> list[dict[str, Any]]:
    payload = _read_json(suite_root / "ground_truth" / "all_bugs.json")
    if isinstance(payload, dict):
        bugs = payload.get("bugs") or payload.get("items") or []
    else:
        bugs = payload
    return [bug for bug in bugs if isinstance(bug, dict)]


def _bug_key(bug: dict[str, Any]) -> str:
    return str(bug.get("bug_id") or f"{bug.get('project_id')}:{bug.get('endpoint_hint')}:{bug.get('title')}")


def _candidate_key(candidate: dict[str, Any]) -> str:
    candidate_id = str(candidate.get("candidate_id") or "")
    project_id = str(candidate.get("_project_id") or "")
    if candidate_id and project_id:
        return f"{project_id}:{candidate_id}"
    return f"{project_id}:{candidate.get('_method')}:{candidate.get('_path')}:{candidate.get('risk_type')}"


def _make_indexes(candidates: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[tuple[str, str], list[dict[str, Any]]]]:
    by_project_path: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_project_path_category: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        project_id = str(candidate.get("_project_id") or "")
        path = _canonical_path(str(candidate.get("_path") or ""))
        if not project_id or not path:
            continue
        by_project_path[(project_id, path)].append(candidate)
        for code in candidate.get("_category_codes") or []:
            by_project_path_category[(project_id, f"{path}::{code}")].append(candidate)
    return by_project_path, by_project_path_category


def evaluate_suite_v3(
    suite_root: str | Path,
    outputs_root: str | Path,
    *,
    glob_pattern: str = "qb_v3_*/input_only_run/grounded_candidates.json",
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    suite_path = Path(suite_root).resolve()
    outputs_path = Path(outputs_root).resolve()
    truth = _load_ground_truth(suite_path)
    candidates = _load_candidates(outputs_path, glob_pattern)
    by_path, by_path_category = _make_indexes(candidates)

    surface_hits: list[dict[str, Any]] = []
    category_hits: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    matched_candidate_keys: set[str] = set()
    surface_candidate_keys: set[str] = set()

    for bug in truth:
        project_id = str(bug.get("project_id") or "")
        endpoint_hint_raw = str(bug.get("endpoint_hint") or "").strip()
        endpoint_hint = _canonical_path(endpoint_hint_raw)
        primary_category = _category_id(bug.get("primary_category"))
        surface = by_path.get((project_id, endpoint_hint), [])
        aligned = by_path_category.get((project_id, f"{endpoint_hint}::{primary_category}"), [])
        if surface:
            best_surface = surface[0]
            surface_candidate_keys.add(_candidate_key(best_surface))
            surface_hits.append({
                "bug_id": _bug_key(bug),
                "project_id": project_id,
                "severity": bug.get("severity"),
                "primary_category": primary_category,
                "endpoint_hint": endpoint_hint_raw,
                "endpoint_template": endpoint_hint,
                "candidate_id": best_surface.get("candidate_id"),
                "candidate_risk_type": best_surface.get("risk_type"),
                "candidate_categories": best_surface.get("_category_codes"),
            })
        if aligned:
            best_aligned = aligned[0]
            matched_candidate_keys.add(_candidate_key(best_aligned))
            category_hits.append({
                "bug_id": _bug_key(bug),
                "project_id": project_id,
                "severity": bug.get("severity"),
                "primary_category": primary_category,
                "endpoint_hint": endpoint_hint_raw,
                "endpoint_template": endpoint_hint,
                "candidate_id": best_aligned.get("candidate_id"),
                "candidate_risk_type": best_aligned.get("risk_type"),
                "candidate_categories": best_aligned.get("_category_codes"),
                "match_type": "endpoint_and_category_alignment",
            })
        if not aligned:
            missed.append({
                "bug_id": _bug_key(bug),
                "project_id": project_id,
                "severity": bug.get("severity"),
                "primary_category": primary_category,
                "endpoint_hint": endpoint_hint_raw,
                "endpoint_template": endpoint_hint,
                "title": bug.get("title"),
                "surface_hit": bool(surface),
            })

    severity_counter = Counter(str(bug.get("severity") or "unknown") for bug in truth)
    category_counter = Counter(_category_id(bug.get("primary_category")) for bug in truth)
    missed_counter = Counter(item["primary_category"] for item in missed)
    surface_by_severity = Counter(str(item["severity"]) for item in surface_hits)
    aligned_by_severity = Counter(str(item["severity"]) for item in category_hits)
    candidate_risks = Counter(str(c.get("risk_type") or "unknown") for c in candidates)
    candidate_categories = Counter(code for c in candidates for code in (c.get("_category_codes") or []))

    known = len(truth)
    found = len(candidates)
    surface_count = len(surface_hits)
    aligned_count = len(category_hits)
    high_truth = [bug for bug in truth if bug.get("severity") in {"P0", "P1"}]
    high_aligned = [hit for hit in category_hits if hit.get("severity") in {"P0", "P1"}]
    truth_by_template: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    truth_by_template_category: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for bug in truth:
        project_id = str(bug.get("project_id") or "")
        path_template = _canonical_path(str(bug.get("endpoint_hint") or ""))
        category = _category_id(bug.get("primary_category"))
        truth_by_template[(project_id, path_template)].append(bug)
        truth_by_template_category[(project_id, f"{path_template}::{category}")].append(bug)
    candidates_with_surface_truth = 0
    candidates_with_category_truth = 0
    for candidate in candidates:
        project_id = str(candidate.get("_project_id") or "")
        path_template = _canonical_path(str(candidate.get("_path") or ""))
        if truth_by_template.get((project_id, path_template)):
            candidates_with_surface_truth += 1
        if any(truth_by_template_category.get((project_id, f"{path_template}::{code}")) for code in (candidate.get("_category_codes") or [])):
            candidates_with_category_truth += 1

    scorecard = {
        "mode": "benchmark_suite_v3_offline_oracle_alignment",
        "note": "This scorer reads hidden ground truth only after blind candidate generation. It measures candidate alignment, not runtime-confirmed bugs.",
        "suite_root": str(suite_path),
        "outputs_root": str(outputs_path),
        "glob_pattern": glob_pattern,
        "metrics": {
            "ground_truth_bugs": known,
            "candidate_count": found,
            "surface_hits": surface_count,
            "category_aligned_hits": aligned_count,
            "missed_category_aligned": known - aligned_count,
            "surface_recall": round(surface_count / known, 4) if known else 0,
            "category_aligned_recall": round(aligned_count / known, 4) if known else 0,
            "high_value_category_aligned_recall": round(len(high_aligned) / len(high_truth), 4) if high_truth else 0,
            "candidate_surface_truth_rate": round(candidates_with_surface_truth / found, 4) if found else 0,
            "candidate_category_truth_rate": round(candidates_with_category_truth / found, 4) if found else 0,
            "candidate_selected_surface_proxy": round(len(surface_candidate_keys) / found, 4) if found else 0,
            "candidate_selected_category_proxy": round(len(matched_candidate_keys) / found, 4) if found else 0,
            "runtime_confirmed_bugs": 0,
        },
        "distribution": {
            "ground_truth_by_severity": dict(sorted(severity_counter.items())),
            "surface_hits_by_severity": dict(sorted(surface_by_severity.items())),
            "category_hits_by_severity": dict(sorted(aligned_by_severity.items())),
            "ground_truth_by_category": dict(sorted(category_counter.items())),
            "missed_by_category": dict(missed_counter.most_common()),
            "candidate_by_risk_type": dict(candidate_risks.most_common()),
            "candidate_by_category_hint": dict(candidate_categories.most_common()),
        },
        "surface_hits": surface_hits[:500],
        "category_aligned_hits": category_hits[:500],
        "top_missed_categories": [{"category": key, "missed_count": value} for key, value in missed_counter.most_common(20)],
        "sample_misses": missed[:100],
    }
    scorecard["commercial_assessment"] = build_commercial_assessment(scorecard)
    if out_dir is not None:
        output = Path(out_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        (output / "suite_v3_scorecard.json").write_text(json.dumps(scorecard, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "suite_v3_scorecard.md").write_text(render_suite_v3_markdown(scorecard), encoding="utf-8")
    return scorecard


def build_commercial_assessment(scorecard: dict[str, Any]) -> dict[str, Any]:
    metrics = scorecard.get("metrics") or {}
    gates = [
        {
            "gate": "hidden_oracle_discipline",
            "status": "passed",
            "evidence": "Ground truth is read only by this offline scorer after blind candidate generation.",
        },
        {
            "gate": "risk_surface_modeling",
            "status": "passed" if float(metrics.get("category_aligned_recall") or 0) >= 0.9 else "needs_work",
            "evidence": f"category_aligned_recall={metrics.get('category_aligned_recall')}",
        },
        {
            "gate": "candidate_quality",
            "status": "passed" if float(metrics.get("candidate_category_truth_rate") or 0) >= 0.85 else "needs_work",
            "evidence": f"candidate_category_truth_rate={metrics.get('candidate_category_truth_rate')}",
        },
        {
            "gate": "runtime_reproduction",
            "status": "failed" if int(metrics.get("runtime_confirmed_bugs") or 0) == 0 else "passed",
            "evidence": f"runtime_confirmed_bugs={metrics.get('runtime_confirmed_bugs')}",
        },
        {
            "gate": "customer_evidence_packet",
            "status": "failed" if int(metrics.get("runtime_confirmed_bugs") or 0) == 0 else "needs_review",
            "evidence": "Candidate alignment is not enough for customer-signable bug reports.",
        },
    ]
    failed = [gate for gate in gates if gate["status"] == "failed"]
    needs_work = [gate for gate in gates if gate["status"] == "needs_work"]
    if failed:
        readiness = "not_commercial_until_runtime_evidence"
    elif needs_work:
        readiness = "pilot_ready_with_review"
    else:
        readiness = "commercial_candidate"
    return {
        "readiness": readiness,
        "gates": gates,
        "next_investments": [
            "Attach disposable sandbox targets or customer staging targets so grounded probes can produce reproducible evidence.",
            "Prioritize P0/P1 category-aligned candidates into a daily validation queue instead of handing all candidates to humans.",
            "Export request, response, before/after state and DB/log diffs as a customer evidence packet for every reproduced bug.",
        ],
    }


def render_suite_v3_markdown(scorecard: dict[str, Any]) -> str:
    metrics = scorecard.get("metrics") or {}
    distribution = scorecard.get("distribution") or {}
    assessment = scorecard.get("commercial_assessment") or {}
    lines = [
        "# QualiBug Benchmark Suite v3 Scorecard",
        "",
        "## Scope",
        "",
        f"- mode: `{scorecard.get('mode')}`",
        "- hidden ground truth is used only after blind candidate generation",
        "- category-aligned hits are not runtime-confirmed bugs",
        "",
        "## Metrics",
        "",
    ]
    for key in [
        "ground_truth_bugs",
        "candidate_count",
        "surface_hits",
        "category_aligned_hits",
        "surface_recall",
        "category_aligned_recall",
        "high_value_category_aligned_recall",
        "candidate_surface_truth_rate",
        "candidate_category_truth_rate",
        "candidate_selected_surface_proxy",
        "candidate_selected_category_proxy",
        "runtime_confirmed_bugs",
    ]:
        lines.append(f"- {key}: `{metrics.get(key)}`")
    lines.extend(["", "## Candidate Risk Mix", ""])
    for key, value in (distribution.get("candidate_by_risk_type") or {}).items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Commercial Assessment", ""])
    lines.append(f"- readiness: `{assessment.get('readiness')}`")
    for gate in assessment.get("gates") or []:
        lines.append(f"- {gate.get('gate')}: `{gate.get('status')}` - {gate.get('evidence')}")
    lines.extend(["", "## Top Missed Categories", ""])
    for item in scorecard.get("top_missed_categories") or []:
        lines.append(f"- {item.get('category')}: `{item.get('missed_count')}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite-root", required=True)
    parser.add_argument("--outputs-root", default="platform_outputs")
    parser.add_argument("--glob", default="qb_v3_*/input_only_run/grounded_candidates.json")
    parser.add_argument("--out", default="platform_outputs/benchmark_suite_v3_score")
    args = parser.parse_args()
    scorecard = evaluate_suite_v3(args.suite_root, args.outputs_root, glob_pattern=args.glob, out_dir=args.out)
    print(json.dumps(scorecard["metrics"], ensure_ascii=False, indent=2))
    print(f"scorecard={Path(args.out).resolve() / 'suite_v3_scorecard.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
