"""
[DEPRECATED] Model Deployment Gate
Status: NEAR-ZOMBIE -- 0 active cross-references.
Roadmap: Gate model deployment based on evaluation metrics.
         Future: CI/CD model deployment quality gate.
See DEPRECATED.md for architecture decisions.
"""
from __future__ import annotations

import argparse
import html
import json
import time
from pathlib import Path
from typing import Any

DEFAULT_MODEL_EVAL_DIR = Path("benchmark_outputs/model_eval")
DEFAULT_MODEL_DATASET_DIR = Path("benchmark_outputs/model_dataset")
DEFAULT_OUT = Path("benchmark_outputs/model_deployment_gate")
DEFAULT_WORKSPACE = Path("platform_workspace/enterprise_shop/defect_discovery")

PRIVATE_LEAK_TERMS = {
    "bug_instance_id",
    "enabled_bugs",
    "current_bug_set",
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets/",
    "bug_sets\\",
    "hidden_test_instance",
}

DEFAULT_THRESHOLDS = {
    "min_json_parse_rate": 0.98,
    "max_json_parse_regression": 0.01,
    "min_quality_improvement": 0.02,
    "max_avg_score_regression": 0.0,
    "max_task_score_regression": 0.015,
    "min_probe_generation_score": 0.70,
    "min_false_positive_filter_score": 0.75,
    "min_missed_recovery_score": 0.60,
}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_no_private_leak(obj: Any) -> dict[str, Any]:
    text = json.dumps(obj, ensure_ascii=False).lower()
    # Governance fields may state that private ground truth is not required; this is not a data leak.
    text = text.replace("private_ground_truth_not_required", "private_answer_not_required")
    leaks = sorted(term for term in PRIVATE_LEAK_TERMS if term.lower() in text)
    return {"passed": not leaks, "leak_terms": leaks}


def _variant_map(scorecard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(v.get("model_variant")): v for v in scorecard.get("variants", []) if isinstance(v, dict)}


def _task_score(variant: dict[str, Any], task: str) -> float:
    return float(((variant.get("task_scores") or {}).get(task) or {}).get("avg_score") or 0.0)


def _task_parse(variant: dict[str, Any], task: str) -> float:
    return float(((variant.get("task_scores") or {}).get(task) or {}).get("json_parse_rate") or 0.0)


def _check(name: str, passed: bool, actual: Any, expected: Any, severity: str = "blocker") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "expected": expected,
        "severity": severity,
    }


def _recommended_candidate(scorecard: dict[str, Any], variants: dict[str, dict[str, Any]], explicit_candidate: str | None = None) -> str | None:
    if explicit_candidate and explicit_candidate in variants:
        return explicit_candidate
    recommended = scorecard.get("recommended_model_variant")
    if recommended in variants:
        return str(recommended)
    ordered = sorted(variants.values(), key=lambda v: float(v.get("quality_score") or 0.0), reverse=True)
    return str(ordered[0].get("model_variant")) if ordered else None


def build_model_ab_scorecard(scorecard: dict[str, Any], baseline_variant: str = "base_prompt", candidate_variant: str | None = None) -> dict[str, Any]:
    variants = _variant_map(scorecard)
    baseline = variants.get(baseline_variant)
    candidate_name = _recommended_candidate(scorecard, variants, candidate_variant)
    candidate = variants.get(candidate_name or "")
    tasks = sorted(set((baseline or {}).get("task_scores", {}).keys()) | set((candidate or {}).get("task_scores", {}).keys()))
    task_deltas: dict[str, Any] = {}
    for task in tasks:
        base_score = _task_score(baseline or {}, task)
        cand_score = _task_score(candidate or {}, task)
        base_parse = _task_parse(baseline or {}, task)
        cand_parse = _task_parse(candidate or {}, task)
        task_deltas[task] = {
            "baseline_avg_score": round(base_score, 6),
            "candidate_avg_score": round(cand_score, 6),
            "delta_avg_score": round(cand_score - base_score, 6),
            "baseline_json_parse_rate": round(base_parse, 6),
            "candidate_json_parse_rate": round(cand_parse, 6),
            "delta_json_parse_rate": round(cand_parse - base_parse, 6),
        }
    return {
        "baseline_variant": baseline_variant,
        "candidate_variant": candidate_name,
        "baseline_found": baseline is not None,
        "candidate_found": candidate is not None,
        "baseline_quality_score": round(float((baseline or {}).get("quality_score") or 0.0), 6),
        "candidate_quality_score": round(float((candidate or {}).get("quality_score") or 0.0), 6),
        "delta_quality_score": round(float((candidate or {}).get("quality_score") or 0.0) - float((baseline or {}).get("quality_score") or 0.0), 6),
        "baseline_avg_score": round(float((baseline or {}).get("avg_score") or 0.0), 6),
        "candidate_avg_score": round(float((candidate or {}).get("avg_score") or 0.0), 6),
        "delta_avg_score": round(float((candidate or {}).get("avg_score") or 0.0) - float((baseline or {}).get("avg_score") or 0.0), 6),
        "baseline_json_parse_rate": round(float((baseline or {}).get("json_parse_rate") or 0.0), 6),
        "candidate_json_parse_rate": round(float((candidate or {}).get("json_parse_rate") or 0.0), 6),
        "delta_json_parse_rate": round(float((candidate or {}).get("json_parse_rate") or 0.0) - float((baseline or {}).get("json_parse_rate") or 0.0), 6),
        "task_deltas": task_deltas,
        "variants_ranked": scorecard.get("variants", []),
    }


def evaluate_deployment_gate(
    model_eval_dir: Path = DEFAULT_MODEL_EVAL_DIR,
    model_dataset_dir: Path = DEFAULT_MODEL_DATASET_DIR,
    out_dir: Path = DEFAULT_OUT,
    workspace_dir: Path = DEFAULT_WORKSPACE,
    baseline_variant: str = "base_prompt",
    candidate_variant: str | None = None,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    scorecard = read_json(model_eval_dir / "model_eval_scorecard.json", {}) or {}
    dataset_card = read_json(model_dataset_dir / "model_dataset_card.json", {}) or {}
    if not scorecard:
        raise FileNotFoundError(f"Missing model_eval_scorecard.json in {model_eval_dir}. Run RUN_MODEL_EVALUATION.cmd first.")
    ab = build_model_ab_scorecard(scorecard, baseline_variant=baseline_variant, candidate_variant=candidate_variant)
    candidate = ab.get("candidate_variant")
    task_deltas = ab.get("task_deltas") or {}
    leak_check = _validate_no_private_leak({"model_eval_scorecard": scorecard, "model_dataset_card": dataset_card, "ab": ab})
    phase19_leak = scorecard.get("private_leak_check") or {}
    dataset_leak = dataset_card.get("private_leak_check") or {}
    hidden_used = bool(scorecard.get("hidden_test_used_for_training") or dataset_card.get("hidden_test_used_for_training"))

    checks: list[dict[str, Any]] = []
    checks.append(_check("baseline_exists", bool(ab.get("baseline_found")), ab.get("baseline_found"), True))
    checks.append(_check("candidate_exists", bool(ab.get("candidate_found")), ab.get("candidate_found"), True))
    checks.append(_check("model_eval_private_leak_check", bool((phase19_leak or {}).get("passed", True)), phase19_leak, "passed"))
    checks.append(_check("dataset_private_leak_check", bool((dataset_leak or {}).get("passed", True)), dataset_leak, "passed"))
    checks.append(_check("gate_private_leak_check", bool(leak_check.get("passed")), leak_check, "passed"))
    checks.append(_check("hidden_test_not_used_for_training", not hidden_used, hidden_used, False))
    checks.append(_check("json_parse_rate_min", ab.get("candidate_json_parse_rate", 0) >= thresholds["min_json_parse_rate"], ab.get("candidate_json_parse_rate"), f">= {thresholds['min_json_parse_rate']}"))
    checks.append(_check("json_parse_rate_no_regression", ab.get("delta_json_parse_rate", -1) >= -thresholds["max_json_parse_regression"], ab.get("delta_json_parse_rate"), f">= -{thresholds['max_json_parse_regression']}"))
    checks.append(_check("quality_score_improved", ab.get("delta_quality_score", 0) >= thresholds["min_quality_improvement"], ab.get("delta_quality_score"), f">= {thresholds['min_quality_improvement']}", severity="review"))
    checks.append(_check("avg_score_no_regression", ab.get("delta_avg_score", -1) >= -thresholds["max_avg_score_regression"], ab.get("delta_avg_score"), f">= -{thresholds['max_avg_score_regression']}"))

    task_minima = {
        "probe_generation_sft": thresholds["min_probe_generation_score"],
        "false_positive_filtering_sft": thresholds["min_false_positive_filter_score"],
        "missed_bug_recovery_sft": thresholds["min_missed_recovery_score"],
    }
    for task, minimum in task_minima.items():
        t = task_deltas.get(task, {})
        checks.append(_check(f"{task}_min_score", float(t.get("candidate_avg_score") or 0) >= minimum, t.get("candidate_avg_score"), f">= {minimum}"))
        checks.append(_check(f"{task}_no_regression", float(t.get("delta_avg_score") or 0) >= -thresholds["max_task_score_regression"], t.get("delta_avg_score"), f">= -{thresholds['max_task_score_regression']}"))

    blocker_failed = [c for c in checks if not c.get("passed") and c.get("severity") == "blocker"]
    review_failed = [c for c in checks if not c.get("passed") and c.get("severity") == "review"]
    if blocker_failed:
        decision = "reject"
        promotion_allowed = False
        reason = "One or more blocker checks failed. Do not deploy this model/prompt strategy."
    elif review_failed:
        decision = "hold_for_review"
        promotion_allowed = False
        reason = "No blocker failed, but review checks require human approval before promotion."
    else:
        decision = "promote"
        promotion_allowed = True
        reason = "Candidate passed model deployment gate and can be promoted as default model/prompt strategy."

    payload = {
        "phase": "phase20_model_ab_deployment_gate",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline_variant": baseline_variant,
        "candidate_variant": candidate,
        "decision": decision,
        "promotion_allowed": promotion_allowed,
        "reason": reason,
        "thresholds": thresholds,
        "checks": checks,
        "failed_checks": blocker_failed + review_failed,
        "model_ab_scorecard": ab,
        "private_leak_check": leak_check,
        "hidden_test_used_for_training": hidden_used,
        "governance": {
            "no_external_model_called": True,
            "hidden_test_excluded_from_training": not hidden_used,
            "hidden_answer_files_not_required": True,
            "requires_structured_model_eval_scorecard": True,
            "purpose": "Decide whether a candidate model/prompt/RAG variant can be promoted into the default defect-discovery workflow.",
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "model_ab_scorecard.json", ab)
    write_json(out_dir / "model_deployment_gate_result.json", payload)
    write_json(out_dir / "model_promotion_decision.json", {
        "decision": decision,
        "promotion_allowed": promotion_allowed,
        "candidate_variant": candidate,
        "baseline_variant": baseline_variant,
        "reason": reason,
        "failed_checks": [c["name"] for c in payload["failed_checks"]],
        "model_deployment_gate_result": str(out_dir / "model_deployment_gate_result.json"),
    })
    (out_dir / "model_deployment_gate_report.html").write_text(build_model_deployment_gate_report_html(payload), encoding="utf-8")
    workspace_dir.mkdir(parents=True, exist_ok=True)
    write_json(workspace_dir / "model_deployment_gate_manifest.json", {
        "model_deployment_gate_result": str(out_dir / "model_deployment_gate_result.json"),
        "model_promotion_decision": str(out_dir / "model_promotion_decision.json"),
        "model_deployment_gate_report": str(out_dir / "model_deployment_gate_report.html"),
        "decision": decision,
        "candidate_variant": candidate,
        "promotion_allowed": promotion_allowed,
    })
    return payload


def build_model_deployment_gate_report_html(payload: dict[str, Any]) -> str:
    decision = str(payload.get("decision"))
    cls = "ok" if decision == "promote" else ("warn" if decision == "hold_for_review" else "bad")
    ab = payload.get("model_ab_scorecard") or {}
    check_rows = []
    for c in payload.get("checks", []):
        c_cls = "ok" if c.get("passed") else ("warn" if c.get("severity") == "review" else "bad")
        check_rows.append(
            f"<tr><td>{html.escape(str(c.get('name')))}</td><td class='{c_cls}'>{'PASS' if c.get('passed') else 'FAIL'}</td><td>{html.escape(str(c.get('actual')))}</td><td>{html.escape(str(c.get('expected')))}</td><td>{html.escape(str(c.get('severity')))}</td></tr>"
        )
    task_rows = []
    for task, t in (ab.get("task_deltas") or {}).items():
        task_rows.append(
            f"<tr><td>{html.escape(str(task))}</td><td>{t.get('baseline_avg_score')}</td><td>{t.get('candidate_avg_score')}</td><td>{t.get('delta_avg_score')}</td><td>{t.get('candidate_json_parse_rate')}</td></tr>"
        )
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Phase20 Model A/B Deployment Gate</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}table{{border-collapse:collapse;width:100%;margin-top:16px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left;vertical-align:top}}.card{{border:1px solid #d8dee9;background:#f8fafc;border-radius:8px;padding:14px;margin:12px 0}}.ok{{color:#0f766e;font-weight:bold}}.warn{{color:#b45309;font-weight:bold}}.bad{{color:#b91c1c;font-weight:bold}}</style></head><body><h1>Phase20 Model A/B + Deployment Gate</h1><div class=\"card\"><b>Decision:</b> <span class=\"{cls}\">{html.escape(decision)}</span><br><b>Promotion allowed:</b> {payload.get('promotion_allowed')}<br><b>Baseline:</b> {html.escape(str(payload.get('baseline_variant')))}<br><b>Candidate:</b> {html.escape(str(payload.get('candidate_variant')))}<br><b>Reason:</b> {html.escape(str(payload.get('reason')))}</div><h2>Model A/B Summary</h2><table><tr><th>Metric</th><th>Baseline</th><th>Candidate</th><th>Delta</th></tr><tr><td>Quality score</td><td>{ab.get('baseline_quality_score')}</td><td>{ab.get('candidate_quality_score')}</td><td>{ab.get('delta_quality_score')}</td></tr><tr><td>Avg score</td><td>{ab.get('baseline_avg_score')}</td><td>{ab.get('candidate_avg_score')}</td><td>{ab.get('delta_avg_score')}</td></tr><tr><td>JSON parse rate</td><td>{ab.get('baseline_json_parse_rate')}</td><td>{ab.get('candidate_json_parse_rate')}</td><td>{ab.get('delta_json_parse_rate')}</td></tr></table><h2>Task Scores</h2><table><tr><th>Task</th><th>Baseline Avg</th><th>Candidate Avg</th><th>Delta</th><th>Candidate Parse</th></tr>{''.join(task_rows)}</table><h2>Gate Checks</h2><table><tr><th>Check</th><th>Status</th><th>Actual</th><th>Expected</th><th>Severity</th></tr>{''.join(check_rows)}</table><h2>Governance</h2><ul><li>Private leak check must pass.</li><li>hidden_test must remain excluded from training.</li><li>Candidate JSON parse rate and task scores must not regress.</li><li>This gate does not call external models; it evaluates model outputs produced elsewhere.</li></ul></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Decide whether a model/prompt/RAG variant can be promoted into the default defect discovery chain.")
    parser.add_argument("--model-eval-dir", default=str(DEFAULT_MODEL_EVAL_DIR))
    parser.add_argument("--model-dataset-dir", default=str(DEFAULT_MODEL_DATASET_DIR))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--baseline", default="base_prompt")
    parser.add_argument("--candidate", default="", help="Optional explicit candidate model_variant. Defaults to model_eval recommended variant.")
    args = parser.parse_args()
    result = evaluate_deployment_gate(
        model_eval_dir=Path(args.model_eval_dir),
        model_dataset_dir=Path(args.model_dataset_dir),
        out_dir=Path(args.out),
        workspace_dir=Path(args.workspace),
        baseline_variant=args.baseline,
        candidate_variant=args.candidate or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
