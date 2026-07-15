"""
[DEPRECATED] Human Feedback Loop
Status: NEAR-ZOMBIE -- 1 active cross-reference.
Roadmap: Collect human feedback on findings for RLHF-style continuous improvement.
         Wire into finding lifecycle for human-in-the-loop review workflow.
See DEPRECATED.md for architecture decisions.
"""
from __future__ import annotations

import argparse
import html
import json
import time
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DISCOVERED = Path("platform_outputs/enterprise_shop/defect_discovery/discovered_bugs.json")
DEFAULT_BENCHMARK = Path("benchmark_outputs")
DEFAULT_OUT = Path("benchmark_outputs/human_feedback")
DEFAULT_WORKSPACE = Path("platform_workspace/enterprise_shop/defect_discovery")

PRIVATE_LEAK_TERMS = {
    "bug_instance_id",
    "enabled_bugs",
    "current_bug_set",
    "enabled_ids",
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
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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


def _validate_no_private_leak(obj: Any) -> dict[str, Any]:
    text = json.dumps(obj, ensure_ascii=False).lower()
    text = text.replace("private_ground_truth_not_required", "private_answer_not_required")
    leaks = sorted(term for term in PRIVATE_LEAK_TERMS if term.lower() in text)
    return {"passed": not leaks, "leak_terms": leaks}


def _as_list_discovered(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("discovered_bugs", "bugs", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _short_evidence(bug: dict[str, Any]) -> dict[str, Any]:
    evidence = bug.get("evidence") or bug.get("evidence_bundle") or {}
    if isinstance(evidence, list):
        evidence = {"items": evidence[:3]}
    if not isinstance(evidence, dict):
        evidence = {"raw": str(evidence)[:500]}
    request = evidence.get("request") or bug.get("request") or {}
    response = evidence.get("response") or bug.get("response") or {}
    return {
        "actor": bug.get("actor") or evidence.get("actor"),
        "request": request if isinstance(request, dict) else str(request)[:300],
        "response_status": (response or {}).get("status_code") if isinstance(response, dict) else bug.get("response_status"),
        "expected": bug.get("expected"),
        "actual": bug.get("actual"),
        "confidence": bug.get("confidence"),
    }


def _load_discovered(discovered_path: Path) -> list[dict[str, Any]]:
    return _as_list_discovered(read_json(discovered_path, []))


def _load_missed_plan(benchmark_dir: Path) -> list[dict[str, Any]]:
    plan = read_json(benchmark_dir / "probe_improvement_plan.json", [])
    if isinstance(plan, list):
        return [x for x in plan if isinstance(x, dict)]
    return []


def _fallback_review_items_from_scorecard(benchmark_dir: Path) -> list[dict[str, Any]]:
    scorecard = read_json(benchmark_dir / "benchmark_scorecard.json", {}) or {}
    metrics = scorecard.get("metrics") or scorecard
    items: list[dict[str, Any]] = []
    if metrics:
        items.append({
            "review_item_id": "benchmark_summary_review",
            "review_type": "benchmark_summary",
            "title": "Benchmark summary needs QA review",
            "severity": "review",
            "risk_type": "benchmark_governance",
            "summary": {
                "known_bugs": metrics.get("known_bugs") or metrics.get("known_bug_instances"),
                "discovered_bugs": metrics.get("discovered_bugs"),
                "precision": metrics.get("precision"),
                "instance_recall": metrics.get("instance_recall"),
                "template_recall": metrics.get("template_recall"),
            },
            "recommended_human_action": "Review top false-positive and missed-template risks before promoting policies.",
        })
    return items


def build_review_queue(
    discovered_bugs: list[dict[str, Any]],
    missed_plan: list[dict[str, Any]],
    benchmark_dir: Path = DEFAULT_BENCHMARK,
    max_discovered: int = 200,
    max_missed: int = 100,
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for i, bug in enumerate(discovered_bugs[:max_discovered], start=1):
        bug_key = bug.get("discovered_bug_id") or bug.get("bug_id") or bug.get("probe_id") or f"discovered_{i:05d}"
        queue.append({
            "review_item_id": f"review_bug_{i:05d}",
            "review_type": "discovered_bug",
            "source_bug_key": bug_key,
            "title": bug.get("title") or bug.get("name") or str(bug_key),
            "severity": bug.get("severity") or "P2",
            "risk_type": bug.get("risk_type") or bug.get("predicted_risk_type") or "unknown",
            "predicted_template_id": bug.get("predicted_template_id") or bug.get("template_id"),
            "affected_api": bug.get("affected_api") or bug.get("api") or bug.get("path"),
            "actor": bug.get("actor"),
            "confidence": bug.get("confidence"),
            "evidence_summary": _short_evidence(bug),
            "human_review_schema": {
                "is_valid_bug": "true|false",
                "is_false_positive": "true|false",
                "is_duplicate": "true|false",
                "is_high_value": "true|false",
                "human_severity": "P0|P1|P2|P3",
                "root_cause": "backend|frontend|data|environment|test_issue|unknown",
                "feedback_notes": "free text",
            },
        })
    for i, missed in enumerate(missed_plan[:max_missed], start=1):
        template = missed.get("missed_template") or missed.get("template_id") or f"missed_{i:05d}"
        queue.append({
            "review_item_id": f"review_missed_{i:05d}",
            "review_type": "missed_template",
            "missed_template": template,
            "title": f"Missed template: {template}",
            "severity": missed.get("severity") or "P1",
            "risk_type": missed.get("risk_type") or "unknown",
            "missed_count": missed.get("missed_count") or 0,
            "likely_reason": missed.get("likely_reason"),
            "suggested_probe": missed.get("suggested_probe"),
            "suggested_oracle": missed.get("suggested_oracle"),
            "priority": missed.get("priority") or missed.get("severity") or "P1",
            "human_review_schema": {
                "is_missed_reason_valid": "true|false",
                "should_add_probe": "true|false",
                "priority_override": "P0|P1|P2|P3",
                "feedback_notes": "free text",
            },
        })
    if not queue:
        queue.extend(_fallback_review_items_from_scorecard(benchmark_dir))
    return queue


def make_feedback_template(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in queue:
        if item.get("review_type") == "discovered_bug":
            rows.append({
                "review_item_id": item.get("review_item_id"),
                "review_type": "discovered_bug",
                "source_bug_key": item.get("source_bug_key"),
                "is_valid_bug": None,
                "is_false_positive": None,
                "is_duplicate": False,
                "is_high_value": None,
                "human_severity": item.get("severity"),
                "root_cause": None,
                "feedback_notes": "",
                "reviewer": "",
                "reviewed_at_utc": "",
            })
        elif item.get("review_type") == "missed_template":
            rows.append({
                "review_item_id": item.get("review_item_id"),
                "review_type": "missed_template",
                "missed_template": item.get("missed_template"),
                "is_missed_reason_valid": None,
                "should_add_probe": None,
                "priority_override": item.get("priority"),
                "feedback_notes": "",
                "reviewer": "",
                "reviewed_at_utc": "",
            })
    return rows


def seed_sample_feedback(queue: list[dict[str, Any]], max_items: int = 120) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for item in queue[:max_items]:
        if item.get("review_type") == "discovered_bug":
            confidence = float(item.get("confidence") or 0.85)
            severity = str(item.get("severity") or "P2")
            high_value = severity in {"P0", "P1"} or str(item.get("risk_type")) in {"permission_bypass", "money_loss", "tenant_isolation", "stock_consistency"}
            is_fp = confidence < 0.45
            rows.append({
                "review_item_id": item.get("review_item_id"),
                "review_type": "discovered_bug",
                "source_bug_key": item.get("source_bug_key"),
                "is_valid_bug": not is_fp,
                "is_false_positive": is_fp,
                "is_duplicate": False,
                "is_high_value": bool(high_value and not is_fp),
                "human_severity": severity,
                "root_cause": "backend" if not is_fp else "test_issue",
                "feedback_notes": "Sample seeded QA feedback. Replace with real reviewer feedback in production.",
                "reviewer": "sample_reviewer",
                "reviewed_at_utc": now,
                "source": "sample_seeded_feedback",
            })
        elif item.get("review_type") == "missed_template":
            rows.append({
                "review_item_id": item.get("review_item_id"),
                "review_type": "missed_template",
                "missed_template": item.get("missed_template"),
                "is_missed_reason_valid": True,
                "should_add_probe": True,
                "priority_override": item.get("priority") or item.get("severity") or "P1",
                "feedback_notes": "Sample seeded feedback confirms this missed template should be converted into a reusable probe.",
                "reviewer": "sample_reviewer",
                "reviewed_at_utc": now,
                "source": "sample_seeded_feedback",
            })
    return rows


def summarize_feedback(feedback_rows: list[dict[str, Any]], queue: list[dict[str, Any]]) -> dict[str, Any]:
    discovered = [r for r in feedback_rows if r.get("review_type") == "discovered_bug"]
    missed = [r for r in feedback_rows if r.get("review_type") == "missed_template"]
    valid = [r for r in discovered if r.get("is_valid_bug") is True]
    false_pos = [r for r in discovered if r.get("is_false_positive") is True]
    high_value = [r for r in discovered if r.get("is_high_value") is True]
    duplicates = [r for r in discovered if r.get("is_duplicate") is True]
    add_probe = [r for r in missed if r.get("should_add_probe") is True]
    severity_counts: dict[str, int] = {}
    for r in discovered:
        sev = str(r.get("human_severity") or "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    return {
        "queue_items": len(queue),
        "feedback_rows": len(feedback_rows),
        "reviewed_discovered_bugs": len(discovered),
        "reviewed_missed_templates": len(missed),
        "valid_bugs": len(valid),
        "false_positives": len(false_pos),
        "duplicates": len(duplicates),
        "high_value_bugs": len(high_value),
        "missed_templates_to_add_probe": len(add_probe),
        "valid_bug_rate": round(len(valid) / len(discovered), 6) if discovered else 0.0,
        "false_positive_rate_from_human_feedback": round(len(false_pos) / len(discovered), 6) if discovered else 0.0,
        "high_value_rate_from_human_feedback": round(len(high_value) / len(discovered), 6) if discovered else 0.0,
        "severity_counts": severity_counts,
    }


def build_preference_pairs(feedback_rows: list[dict[str, Any]], queue_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for i, fb in enumerate(feedback_rows, start=1):
        item = queue_by_id.get(str(fb.get("review_item_id"))) or {}
        if fb.get("review_type") == "discovered_bug":
            context = {
                "title": item.get("title"),
                "risk_type": item.get("risk_type"),
                "affected_api": item.get("affected_api"),
                "evidence_summary": item.get("evidence_summary"),
                "human_feedback": {k: fb.get(k) for k in ["is_valid_bug", "is_false_positive", "is_high_value", "human_severity", "root_cause", "feedback_notes"]},
            }
            if fb.get("is_false_positive") is True or fb.get("is_valid_bug") is False:
                preferred = {"decision": "suppress_or_downgrade", "reason": "Human reviewer marked this as false positive or invalid.", "final_label": "not_a_bug"}
                rejected = {"decision": "report_as_bug", "severity": item.get("severity") or "P1", "reason": "Reports without respecting human false-positive feedback."}
            else:
                preferred = {"decision": "report_bug", "severity": fb.get("human_severity") or item.get("severity"), "is_high_value": fb.get("is_high_value"), "requires_evidence": True}
                rejected = {"decision": "generic_warning", "severity": "unknown", "requires_evidence": False}
            pairs.append({
                "id": f"human_pref_{i:05d}",
                "task": "human_feedback_preference",
                "input": context,
                "preferred_output": preferred,
                "rejected_output": rejected,
                "source": "human_feedback_loop",
            })
        elif fb.get("review_type") == "missed_template":
            context = {
                "missed_template": item.get("missed_template") or fb.get("missed_template"),
                "risk_type": item.get("risk_type"),
                "missed_count": item.get("missed_count"),
                "likely_reason": item.get("likely_reason"),
                "human_feedback": {k: fb.get(k) for k in ["is_missed_reason_valid", "should_add_probe", "priority_override", "feedback_notes"]},
            }
            preferred = {"decision": "add_or_raise_probe", "priority": fb.get("priority_override") or "P1", "reason": "Human reviewer confirmed missed template should improve probe policy."}
            rejected = {"decision": "ignore_missed_template", "reason": "Fails to act on confirmed missed high-value bug pattern."}
            pairs.append({
                "id": f"human_pref_{i:05d}",
                "task": "missed_bug_recovery_from_human_feedback",
                "input": context,
                "preferred_output": preferred,
                "rejected_output": rejected,
                "source": "human_feedback_loop",
            })
    return pairs


def build_policy_update(feedback_rows: list[dict[str, Any]], queue_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raise_templates: dict[str, dict[str, Any]] = {}
    suppress_risks: dict[str, dict[str, Any]] = {}
    severity_overrides: dict[str, int] = {}
    for fb in feedback_rows:
        item = queue_by_id.get(str(fb.get("review_item_id"))) or {}
        if fb.get("review_type") == "missed_template" and fb.get("should_add_probe") is True:
            template = str(item.get("missed_template") or fb.get("missed_template") or "unknown")
            entry = raise_templates.setdefault(template, {"template_id": template, "weight_delta": 0.0, "feedback_count": 0, "priority": fb.get("priority_override") or "P1"})
            entry["weight_delta"] = round(float(entry["weight_delta"]) + 0.15, 6)
            entry["feedback_count"] += 1
        if fb.get("review_type") == "discovered_bug":
            risk = str(item.get("risk_type") or "unknown")
            sev = str(fb.get("human_severity") or item.get("severity") or "unknown")
            severity_overrides[sev] = severity_overrides.get(sev, 0) + 1
            if fb.get("is_false_positive") is True or fb.get("is_valid_bug") is False:
                entry = suppress_risks.setdefault(risk, {"risk_type": risk, "weight_delta": 0.0, "feedback_count": 0})
                entry["weight_delta"] = round(float(entry["weight_delta"]) - 0.1, 6)
                entry["feedback_count"] += 1
            elif fb.get("is_high_value") is True:
                template = str(item.get("predicted_template_id") or risk or "unknown")
                entry = raise_templates.setdefault(template, {"template_id": template, "weight_delta": 0.0, "feedback_count": 0, "priority": sev})
                entry["weight_delta"] = round(float(entry["weight_delta"]) + 0.08, 6)
                entry["feedback_count"] += 1
    return {
        "phase": "phase21_human_feedback_review_loop",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "raise_template_weights": sorted(raise_templates.values(), key=lambda x: (-float(x.get("weight_delta") or 0), x.get("template_id") or "")),
        "suppress_risk_weights": sorted(suppress_risks.values(), key=lambda x: (float(x.get("weight_delta") or 0), x.get("risk_type") or "")),
        "severity_feedback_counts": severity_overrides,
        "governance": {
            "source": "human_feedback_only",
            "requires_reviewer_approval_before_policy_promotion": True,
            "does_not_require_ground_truth": True,
        },
    }


def build_report(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    checks = result.get("private_leak_check") or {}
    policy = result.get("policy_update") or {}
    rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
        for k, v in summary.items()
        if not isinstance(v, (dict, list))
    )
    raised = "".join(
        f"<tr><td>{html.escape(str(x.get('template_id')))}</td><td>{html.escape(str(x.get('weight_delta')))}</td><td>{html.escape(str(x.get('feedback_count')))}</td><td>{html.escape(str(x.get('priority')))}</td></tr>"
        for x in (policy.get("raise_template_weights") or [])[:20]
    )
    suppressed = "".join(
        f"<tr><td>{html.escape(str(x.get('risk_type')))}</td><td>{html.escape(str(x.get('weight_delta')))}</td><td>{html.escape(str(x.get('feedback_count')))}</td></tr>"
        for x in (policy.get("suppress_risk_weights") or [])[:20]
    )
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Phase21 Human Feedback Review Loop</title>
<style>body{{font-family:Arial,Helvetica,sans-serif;margin:32px;background:#f7f8fb;color:#172033}}.card{{background:white;border-radius:14px;padding:22px;margin:16px 0;box-shadow:0 8px 24px rgba(15,23,42,.08)}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #e5e7eb;padding:9px;text-align:left}}.ok{{color:#047857;font-weight:700}}.bad{{color:#b91c1c;font-weight:700}}code{{background:#eef2ff;padding:2px 5px;border-radius:5px}}</style></head>
<body><h1>Phase21 Human Feedback Review Loop</h1>
<div class='card'><h2>Summary</h2><table><tbody>{rows}</tbody></table></div>
<div class='card'><h2>Governance</h2><p>Private leak check: <span class='{ 'ok' if checks.get('passed') else 'bad' }'>{html.escape(str(checks.get('passed')))}</span></p><p>Leak terms: <code>{html.escape(str(checks.get('leak_terms') or []))}</code></p><p>Human feedback is used to build preference pairs and policy updates without exposing ground truth or enabled bug sets.</p></div>
<div class='card'><h2>Templates to Raise</h2><table><thead><tr><th>Template</th><th>Weight delta</th><th>Feedback count</th><th>Priority</th></tr></thead><tbody>{raised}</tbody></table></div>
<div class='card'><h2>Risks to Suppress / Review</h2><table><thead><tr><th>Risk type</th><th>Weight delta</th><th>Feedback count</th></tr></thead><tbody>{suppressed}</tbody></table></div>
<div class='card'><h2>Generated Assets</h2><ul><li><code>review_queue.json</code></li><li><code>human_feedback_template.jsonl</code></li><li><code>human_feedback.jsonl</code></li><li><code>preference_pairs_from_human_feedback.jsonl</code></li><li><code>human_feedback_policy_update.json</code></li></ul></div>
</body></html>"""


def run_human_feedback_loop(
    discovered_path: Path = DEFAULT_DISCOVERED,
    benchmark_dir: Path = DEFAULT_BENCHMARK,
    out_dir: Path = DEFAULT_OUT,
    workspace_dir: Path = DEFAULT_WORKSPACE,
    seed_sample: bool = False,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    discovered = _load_discovered(discovered_path)
    missed_plan = _load_missed_plan(benchmark_dir)
    queue = build_review_queue(discovered, missed_plan, benchmark_dir=benchmark_dir)
    template_rows = make_feedback_template(queue)
    write_json(out_dir / "review_queue.json", queue)
    write_jsonl(out_dir / "human_feedback_template.jsonl", template_rows)

    feedback_path = out_dir / "human_feedback.jsonl"
    feedback_rows = iter_jsonl(feedback_path)
    if not feedback_rows and seed_sample:
        feedback_rows = seed_sample_feedback(queue)
        write_jsonl(feedback_path, feedback_rows)
    elif not feedback_rows:
        write_jsonl(feedback_path, [])

    queue_by_id = {str(x.get("review_item_id")): x for x in queue}
    pairs = build_preference_pairs(feedback_rows, queue_by_id)
    policy_update = build_policy_update(feedback_rows, queue_by_id)
    summary = summarize_feedback(feedback_rows, queue)
    leak_check = _validate_no_private_leak({
        "review_queue": queue,
        "human_feedback": feedback_rows,
        "preference_pairs": pairs,
        "policy_update": policy_update,
    })

    result = {
        "phase": "phase21_human_feedback_review_loop",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": summary,
        "private_leak_check": leak_check,
        "seed_sample_feedback_used": bool(seed_sample and feedback_rows),
        "outputs": {
            "review_queue": str(out_dir / "review_queue.json"),
            "human_feedback_template": str(out_dir / "human_feedback_template.jsonl"),
            "human_feedback": str(feedback_path),
            "preference_pairs": str(out_dir / "preference_pairs_from_human_feedback.jsonl"),
            "policy_update": str(out_dir / "human_feedback_policy_update.json"),
            "report": str(out_dir / "human_feedback_report.html"),
        },
        "policy_update": policy_update,
        "governance": {
            "ground_truth_not_required": True,
            "hidden_test_not_used_for_training": True,
            "human_review_required_for_real_feedback": True,
            "sample_feedback_is_demo_only": bool(seed_sample),
        },
    }
    write_json(out_dir / "human_feedback_summary.json", result)
    write_jsonl(out_dir / "preference_pairs_from_human_feedback.jsonl", pairs)
    write_json(out_dir / "human_feedback_policy_update.json", policy_update)
    (out_dir / "human_feedback_report.html").write_text(build_report(result), encoding="utf-8")

    # Workspace copies consumed by later policy/model builders.
    write_json(workspace_dir / "human_feedback_summary.json", result)
    write_json(workspace_dir / "human_feedback_policy_update.json", policy_update)
    write_jsonl(workspace_dir / "preference_pairs_from_human_feedback.jsonl", pairs)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase21 Human Feedback Review Loop")
    parser.add_argument("--discovered-path", default=str(DEFAULT_DISCOVERED))
    parser.add_argument("--benchmark-dir", default=str(DEFAULT_BENCHMARK))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--workspace-dir", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--seed-sample", action="store_true", help="Create demo/sample feedback when no human_feedback.jsonl exists.")
    args = parser.parse_args()
    result = run_human_feedback_loop(
        discovered_path=Path(args.discovered_path),
        benchmark_dir=Path(args.benchmark_dir),
        out_dir=Path(args.out_dir),
        workspace_dir=Path(args.workspace_dir),
        seed_sample=bool(args.seed_sample),
    )
    print(json.dumps({
        "phase": result.get("phase"),
        "summary": result.get("summary"),
        "private_leak_check": result.get("private_leak_check"),
        "report": result.get("outputs", {}).get("report"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
