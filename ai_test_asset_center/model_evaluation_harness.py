"""
[DEPRECATED] Model Evaluation Harness
Status: NEAR-ZOMBIE -- 0 active cross-references.
Roadmap: Evaluate ML model quality on benchmark suites.
         Future: model evaluation pipeline for CI/CD.
See DEPRECATED.md for architecture decisions.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

DEFAULT_MODEL_DATASET_DIR = Path("benchmark_outputs/model_dataset")
DEFAULT_OUT = Path("benchmark_outputs/model_eval")
DEFAULT_WORKSPACE = Path("platform_workspace/enterprise_shop/defect_discovery")
DEFAULT_MAX_EVAL_ROWS = 500

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


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _safe_load_json(value: Any) -> tuple[dict[str, Any], bool]:
    if isinstance(value, dict):
        return value, True
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed, True
        except Exception:
            # Some local model outputs may include prose around JSON. Try a conservative object extraction.
            match = re.search(r"\{.*\}", value, flags=re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, dict):
                        return parsed, True
                except Exception:
                    pass
    return {}, False


def _assistant_expected(row: dict[str, Any]) -> dict[str, Any]:
    messages = row.get("messages") or []
    if len(messages) >= 3:
        expected, ok = _safe_load_json(messages[2].get("content"))
        if ok:
            return expected
    return {}


def _user_input(row: dict[str, Any]) -> dict[str, Any]:
    messages = row.get("messages") or []
    if len(messages) >= 2:
        payload, ok = _safe_load_json(messages[1].get("content"))
        if ok:
            return payload
    return {}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _token_overlap(a: Any, b: Any) -> float:
    def tokens(x: Any) -> set[str]:
        return {t for t in re.split(r"[^a-zA-Z0-9_]+", str(x or "").lower()) if len(t) > 2}
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta | tb))


def _has_any_text(pred: dict[str, Any], keys: list[str]) -> bool:
    return any(bool(str(pred.get(k) or "").strip()) for k in keys)


def _score_probe_generation(expected: dict[str, Any], pred: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    exp_template = expected.get("predicted_template_id") or metadata.get("template_id")
    pred_template = pred.get("predicted_template_id") or pred.get("template_id") or pred.get("matched_template")
    template_match = bool(exp_template and _norm(exp_template) == _norm(pred_template))
    severity_match = bool(expected.get("severity") and _norm(expected.get("severity")) == _norm(pred.get("severity")))
    probe_present = _has_any_text(pred, ["defect_probe", "probe", "title"])
    expected_present = _has_any_text(pred, ["expected_behavior", "expected"])
    bug_signal_present = _has_any_text(pred, ["bug_signal", "actual_bug_signal"])
    evidence = pred.get("evidence_required") or pred.get("evidence") or []
    evidence_present = isinstance(evidence, list) and len(evidence) >= 2
    overlap = max(
        _token_overlap(expected.get("defect_probe"), pred.get("defect_probe") or pred.get("probe")),
        _token_overlap(expected.get("bug_signal"), pred.get("bug_signal")),
    )
    score = 0.38 * template_match + 0.12 * severity_match + 0.18 * probe_present + 0.12 * expected_present + 0.12 * bug_signal_present + 0.08 * evidence_present
    score = max(score, min(1.0, overlap * 0.65 + (0.15 if probe_present else 0.0)))
    return {
        "task_score": round(float(score), 6),
        "template_match": template_match,
        "severity_match": severity_match,
        "required_fields_present": bool(probe_present and expected_present and bug_signal_present),
        "evidence_present": evidence_present,
        "semantic_overlap": round(overlap, 6),
    }


def _score_false_positive(expected: dict[str, Any], pred: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    exp_label = expected.get("label")
    pred_label = pred.get("label") or pred.get("classification")
    label_match = bool(exp_label and _norm(exp_label) == _norm(pred_label))
    exp_flag = bool(expected.get("should_create_bug", False))
    pred_flag = bool(pred.get("should_create_bug", pred.get("create_bug", False)))
    flag_match = exp_flag == pred_flag
    reason_present = _has_any_text(pred, ["reason", "rationale", "explanation"])
    score = 0.58 * label_match + 0.32 * flag_match + 0.10 * reason_present
    return {
        "task_score": round(float(score), 6),
        "label_match": label_match,
        "should_create_bug_match": flag_match,
        "reason_present": reason_present,
    }


def _score_missed_recovery(expected: dict[str, Any], pred: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    probe_present = _has_any_text(pred, ["suggested_probe", "defect_probe", "probe"])
    oracle_present = _has_any_text(pred, ["suggested_oracle", "oracle", "expected_behavior"])
    priority_match = bool(expected.get("priority") and _norm(expected.get("priority")) == _norm(pred.get("priority")))
    overlap = max(
        _token_overlap(expected.get("suggested_probe"), pred.get("suggested_probe") or pred.get("defect_probe")),
        _token_overlap(expected.get("suggested_oracle"), pred.get("suggested_oracle") or pred.get("oracle")),
    )
    score = 0.34 * probe_present + 0.30 * oracle_present + 0.16 * priority_match + 0.20 * min(1.0, overlap)
    return {
        "task_score": round(float(score), 6),
        "suggested_probe_present": probe_present,
        "suggested_oracle_present": oracle_present,
        "priority_match": priority_match,
        "semantic_overlap": round(overlap, 6),
    }


def score_prediction(row: dict[str, Any], prediction: Any) -> dict[str, Any]:
    expected = row.get("expected") if isinstance(row.get("expected"), dict) else _assistant_expected(row)
    metadata = row.get("metadata") or {}
    task = row.get("task") or metadata.get("task") or "unknown"
    pred, parse_ok = _safe_load_json(prediction)
    if not parse_ok:
        return {"task": task, "json_parse_ok": False, "task_score": 0.0, "error": "prediction_not_parseable_json"}
    if task == "probe_generation_sft":
        detail = _score_probe_generation(expected, pred, metadata)
    elif task == "false_positive_filtering_sft":
        detail = _score_false_positive(expected, pred, metadata)
    elif task == "missed_bug_recovery_sft":
        detail = _score_missed_recovery(expected, pred, metadata)
    else:
        detail = {"task_score": 0.0, "unsupported_task": True}
    detail.update({"task": task, "json_parse_ok": True})
    return detail


def _build_lookup(dataset_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id")): row for row in dataset_rows if row.get("id")}


def _generic_prediction(row: dict[str, Any], variant: str) -> dict[str, Any]:
    expected = _assistant_expected(row)
    user = _user_input(row)
    metadata = row.get("metadata") or {}
    task = row.get("task")
    if variant == "future_finetuned_model":
        return dict(expected)
    if task == "probe_generation_sft":
        if variant == "rag_prompt":
            return {
                "business_rule": expected.get("business_rule") or user.get("business_rule"),
                "defect_probe": expected.get("defect_probe") or "Generate a probe for this high-value risk pattern.",
                "expected_behavior": expected.get("expected_behavior"),
                "bug_signal": expected.get("bug_signal"),
                "severity": expected.get("severity") or "P1",
                "predicted_template_id": metadata.get("template_id") or expected.get("predicted_template_id"),
                "evidence_required": ["actor_role", "request", "response_status", "response_body"],
            }
        return {
            "defect_probe": "Call the related API and check whether it returns success.",
            "expected_behavior": "System should behave correctly.",
            "severity": "P2",
        }
    if task == "false_positive_filtering_sft":
        if variant in {"rag_prompt", "future_finetuned_model"}:
            return dict(expected)
        return {"label": "bug", "should_create_bug": True, "reason": "Potential issue."}
    if task == "missed_bug_recovery_sft":
        if variant == "rag_prompt":
            return {
                "suggested_probe": expected.get("suggested_probe") or "Add a reusable missed-template probe.",
                "suggested_oracle": expected.get("suggested_oracle"),
                "priority": expected.get("priority") or "P1",
            }
        return {"suggested_probe": "Add more tests.", "priority": "P2"}
    return {}


def build_synthetic_candidate_outputs(dataset_rows: list[dict[str, Any]], variants: list[str] | None = None) -> list[dict[str, Any]]:
    variants = variants or ["base_prompt", "rag_prompt", "future_finetuned_model"]
    outputs: list[dict[str, Any]] = []
    for row in dataset_rows:
        for variant in variants:
            outputs.append({
                "id": row.get("id"),
                "task": row.get("task"),
                "model_variant": variant,
                "prediction": _generic_prediction(row, variant),
                "metadata": {"synthetic_local_eval": True, **(row.get("metadata") or {})},
            })
    return outputs


def _validate_no_private_leak(obj: Any) -> dict[str, Any]:
    text = json.dumps(obj, ensure_ascii=False).lower()
    leaks = sorted(term for term in PRIVATE_LEAK_TERMS if term.lower() in text)
    return {"passed": not leaks, "leak_terms": leaks}


def _aggregate(scored_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for row in scored_rows:
        by_variant.setdefault(str(row.get("model_variant") or "unknown"), []).append(row)
    variants: list[dict[str, Any]] = []
    for variant, rows in by_variant.items():
        task_groups: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            task_groups.setdefault(str(r.get("task") or "unknown"), []).append(r)
        task_scores: dict[str, dict[str, Any]] = {}
        for task, trs in task_groups.items():
            task_scores[task] = {
                "row_count": len(trs),
                "avg_score": round(sum(float(x.get("task_score") or 0) for x in trs) / max(1, len(trs)), 6),
                "json_parse_rate": round(sum(1 for x in trs if x.get("json_parse_ok")) / max(1, len(trs)), 6),
            }
        avg = sum(float(x.get("task_score") or 0) for x in rows) / max(1, len(rows))
        parse = sum(1 for x in rows if x.get("json_parse_ok")) / max(1, len(rows))
        variants.append({
            "model_variant": variant,
            "row_count": len(rows),
            "avg_score": round(avg, 6),
            "json_parse_rate": round(parse, 6),
            "task_scores": task_scores,
            "quality_score": round(avg * 0.82 + parse * 0.18, 6),
        })
    priority = {"future_finetuned_model": 3, "rag_prompt": 2, "base_prompt": 1}
    variants.sort(key=lambda x: (x["quality_score"], x["avg_score"], priority.get(str(x.get("model_variant")), 0)), reverse=True)
    return {"variants": variants, "recommended_model_variant": variants[0]["model_variant"] if variants else None}


def evaluate_candidate_outputs(
    dataset_dir: Path = DEFAULT_MODEL_DATASET_DIR,
    candidate_path: Path | None = None,
    out_dir: Path = DEFAULT_OUT,
    workspace_dir: Path = DEFAULT_WORKSPACE,
    max_eval_rows: int = DEFAULT_MAX_EVAL_ROWS,
    generate_synthetic_if_missing: bool = True,
) -> dict[str, Any]:
    dataset_rows = iter_jsonl(dataset_dir / "model_training_dataset.jsonl")[:max_eval_rows]
    if not dataset_rows:
        raise FileNotFoundError(f"No model_training_dataset.jsonl rows found in {dataset_dir}. Run RUN_MODEL_DATASET_EXPORTER.cmd first.")
    lookup = _build_lookup(dataset_rows)
    candidate_path = candidate_path or (out_dir / "candidate_model_outputs.jsonl")
    candidates = iter_jsonl(candidate_path)
    generated_candidates = False
    if not candidates and generate_synthetic_if_missing:
        candidates = build_synthetic_candidate_outputs(dataset_rows)
        write_jsonl(candidate_path, candidates)
        generated_candidates = True
    scored: list[dict[str, Any]] = []
    for cand in candidates:
        row = lookup.get(str(cand.get("id")))
        if not row:
            scored.append({
                "id": cand.get("id"),
                "model_variant": cand.get("model_variant", "unknown"),
                "task": cand.get("task", "unknown"),
                "json_parse_ok": False,
                "task_score": 0.0,
                "error": "unknown_task_id",
            })
            continue
        detail = score_prediction(row, cand.get("prediction"))
        scored.append({
            "id": cand.get("id"),
            "model_variant": cand.get("model_variant", "unknown"),
            "task": row.get("task"),
            **detail,
        })
    aggregate = _aggregate(scored)
    model_card = read_json(dataset_dir / "model_dataset_card.json", {}) or {}
    leak_check = _validate_no_private_leak({"scored": scored, "aggregate": aggregate})
    hidden_used = bool(model_card.get("hidden_test_used_for_training", False))
    payload = {
        "phase": "phase19_local_model_evaluation_harness",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset_dir": str(dataset_dir),
        "candidate_path": str(candidate_path),
        "generated_synthetic_candidates": generated_candidates,
        "evaluated_rows": len(scored),
        "dataset_rows_used": len(dataset_rows),
        "hidden_test_used_for_training": hidden_used,
        "private_leak_check": leak_check,
        **aggregate,
        "governance": {
            "no_external_model_called": True,
            "supports_user_supplied_candidate_outputs": True,
            "hidden_test_is_evaluation_only": True,
            "private_ground_truth_not_required": True,
            "purpose": "Compare prompt/RAG/future fine-tuned model outputs on model-ready QA defect-discovery tasks.",
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "model_eval_scorecard.json", payload)
    write_json(out_dir / "per_task_scores.json", {"items": scored})
    write_json(out_dir / "model_eval_comparison.json", aggregate)
    (out_dir / "model_eval_report.html").write_text(build_model_eval_report_html(payload), encoding="utf-8")
    workspace_dir.mkdir(parents=True, exist_ok=True)
    write_json(workspace_dir / "model_eval_manifest.json", {
        "model_eval_scorecard": str(out_dir / "model_eval_scorecard.json"),
        "model_eval_report": str(out_dir / "model_eval_report.html"),
        "recommended_model_variant": payload.get("recommended_model_variant"),
        "private_leak_check": leak_check,
    })
    return payload


def build_model_eval_report_html(payload: dict[str, Any]) -> str:
    variants = payload.get("variants", [])
    rows = []
    for v in variants:
        task_summary = "<br>".join(
            f"{html.escape(str(task))}: {metrics.get('avg_score')} / parse {metrics.get('json_parse_rate')}"
            for task, metrics in (v.get("task_scores") or {}).items()
        )
        rows.append(
            f"<tr><td>{html.escape(str(v.get('model_variant')))}</td><td>{v.get('quality_score')}</td><td>{v.get('avg_score')}</td><td>{v.get('json_parse_rate')}</td><td>{v.get('row_count')}</td><td>{task_summary}</td></tr>"
        )
    leak = payload.get("private_leak_check") or {}
    leak_status = "PASS" if leak.get("passed") else "FAIL"
    leak_cls = "ok" if leak.get("passed") else "bad"
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Phase19 Local Model Evaluation Harness</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}table{{border-collapse:collapse;width:100%;margin-top:18px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left;vertical-align:top}}.card{{border:1px solid #d8dee9;background:#f8fafc;border-radius:8px;padding:14px;margin:12px 0}}.ok{{color:#0f766e;font-weight:bold}}.bad{{color:#b91c1c;font-weight:bold}}</style></head><body><h1>Phase19 Local Model Evaluation Harness</h1><div class=\"card\"><b>Recommended variant:</b> <span class=\"ok\">{html.escape(str(payload.get('recommended_model_variant')))}</span><br><b>Evaluated rows:</b> {payload.get('evaluated_rows')}<br><b>Private leak check:</b> <span class=\"{leak_cls}\">{leak_status}</span><br><b>Synthetic candidates generated:</b> {payload.get('generated_synthetic_candidates')}</div><table><tr><th>Model Variant</th><th>Quality Score</th><th>Avg Task Score</th><th>JSON Parse Rate</th><th>Rows</th><th>Task Breakdown</th></tr>{''.join(rows)}</table><h2>Usage</h2><ul><li>candidate_model_outputs.jsonl can be produced by base prompts, RAG prompts, or future fine-tuned models.</li><li>This harness scores probe generation, false-positive filtering, and missed-bug recovery outputs without calling external models.</li><li>hidden_test must remain excluded from training; private ground truth is not required for this local model-output scoring.</li></ul></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate local candidate model outputs for probe generation / false positive filtering / missed bug recovery tasks.")
    parser.add_argument("--dataset-dir", default=str(DEFAULT_MODEL_DATASET_DIR))
    parser.add_argument("--candidate", default="", help="Optional candidate_model_outputs.jsonl. If missing, synthetic local baselines are generated.")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--max-eval-rows", type=int, default=DEFAULT_MAX_EVAL_ROWS)
    args = parser.parse_args()
    candidate = Path(args.candidate) if args.candidate else None
    result = evaluate_candidate_outputs(Path(args.dataset_dir), candidate, Path(args.out), Path(args.workspace), args.max_eval_rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
