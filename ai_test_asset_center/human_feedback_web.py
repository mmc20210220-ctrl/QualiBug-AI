from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

from ai_test_asset_center.human_feedback_loop import (
    DEFAULT_BENCHMARK,
    DEFAULT_DISCOVERED,
    DEFAULT_OUT,
    DEFAULT_WORKSPACE,
    build_review_queue,
    iter_jsonl,
    read_json,
    run_human_feedback_loop,
    write_json,
    write_jsonl,
    _load_discovered,
    _load_missed_plan,
    _validate_no_private_leak,
)

ALLOWED_DISCOVERED_FIELDS = {
    "review_item_id",
    "review_type",
    "source_bug_key",
    "is_valid_bug",
    "is_false_positive",
    "is_duplicate",
    "is_high_value",
    "human_severity",
    "root_cause",
    "feedback_notes",
    "reviewer",
    "reviewed_at_utc",
}

ALLOWED_MISSED_FIELDS = {
    "review_item_id",
    "review_type",
    "missed_template",
    "is_missed_reason_valid",
    "should_add_probe",
    "priority_override",
    "feedback_notes",
    "reviewer",
    "reviewed_at_utc",
}

BOOL_FIELDS = {
    "is_valid_bug",
    "is_false_positive",
    "is_duplicate",
    "is_high_value",
    "is_missed_reason_valid",
    "should_add_probe",
}

SEVERITIES = {"P0", "P1", "P2", "P3", "review", "unknown", ""}
ROOT_CAUSES = {"backend", "frontend", "data", "environment", "test_issue", "product_design", "unknown", ""}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _coerce_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "有效", "是"}:
        return True
    if text in {"false", "0", "no", "n", "无效", "否"}:
        return False
    return None


def ensure_review_queue(
    discovered_path: Path = DEFAULT_DISCOVERED,
    benchmark_dir: Path = DEFAULT_BENCHMARK,
    out_dir: Path = DEFAULT_OUT,
    workspace_dir: Path = DEFAULT_WORKSPACE,
) -> dict[str, Any]:
    """Create/refresh review assets without seeding fake feedback."""
    if not (out_dir / "review_queue.json").exists():
        run_human_feedback_loop(discovered_path=discovered_path, benchmark_dir=benchmark_dir, out_dir=out_dir, workspace_dir=workspace_dir, seed_sample=False)
    queue = read_json(out_dir / "review_queue.json", []) or []
    if not isinstance(queue, list):
        queue = []
    # If discovered/missed inputs changed and queue is empty, rebuild once.
    if not queue:
        discovered = _load_discovered(discovered_path)
        missed_plan = _load_missed_plan(benchmark_dir)
        queue = build_review_queue(discovered, missed_plan, benchmark_dir=benchmark_dir)
        write_json(out_dir / "review_queue.json", queue)
    return load_review_state(out_dir=out_dir)


def _index_feedback(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        rid = str(row.get("review_item_id") or "").strip()
        if rid:
            indexed[rid] = row
    return indexed


def _queue_summary(queue: list[dict[str, Any]], feedback_rows: list[dict[str, Any]]) -> dict[str, Any]:
    fb_by_id = _index_feedback(feedback_rows)
    reviewed = 0
    discovered_count = 0
    missed_count = 0
    valid = 0
    false_positive = 0
    high_value = 0
    add_probe = 0
    for item in queue:
        rid = str(item.get("review_item_id") or "")
        fb = fb_by_id.get(rid)
        if item.get("review_type") == "discovered_bug":
            discovered_count += 1
        elif item.get("review_type") == "missed_template":
            missed_count += 1
        if not fb:
            continue
        reviewed += 1
        if fb.get("is_valid_bug") is True:
            valid += 1
        if fb.get("is_false_positive") is True:
            false_positive += 1
        if fb.get("is_high_value") is True:
            high_value += 1
        if fb.get("should_add_probe") is True:
            add_probe += 1
    return {
        "queue_items": len(queue),
        "reviewed_items": reviewed,
        "pending_items": max(len(queue) - reviewed, 0),
        "discovered_bug_items": discovered_count,
        "missed_template_items": missed_count,
        "valid_bugs": valid,
        "false_positives": false_positive,
        "high_value_bugs": high_value,
        "missed_templates_to_add_probe": add_probe,
        "review_progress": round(reviewed / len(queue), 6) if queue else 0.0,
    }


def load_review_state(out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    queue = read_json(out_dir / "review_queue.json", []) or []
    if not isinstance(queue, list):
        queue = []
    feedback_rows = iter_jsonl(out_dir / "human_feedback.jsonl")
    fb_by_id = _index_feedback(feedback_rows)
    items: list[dict[str, Any]] = []
    for item in queue:
        rid = str(item.get("review_item_id") or "")
        merged = dict(item)
        merged["feedback"] = fb_by_id.get(rid)
        merged["review_status"] = "reviewed" if rid in fb_by_id else "pending"
        items.append(merged)
    payload = {
        "ok": True,
        "phase": "phase22_human_feedback_web_review_ui",
        "generated_at_utc": _now(),
        "summary": _queue_summary(queue, feedback_rows),
        "items": items,
        "outputs": {
            "review_queue": str(out_dir / "review_queue.json"),
            "human_feedback": str(out_dir / "human_feedback.jsonl"),
            "report": str(out_dir / "human_feedback_report.html"),
            "web_review_report": str(out_dir / "human_feedback_web_review_report.html"),
        },
        "private_leak_check": _validate_no_private_leak({"queue": queue, "feedback": feedback_rows}),
    }
    return payload


def _sanitize_feedback(payload: dict[str, Any], queue_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rid = str(payload.get("review_item_id") or "").strip()
    if not rid or rid not in queue_by_id:
        raise ValueError("review_item_id not found in review queue")
    item = queue_by_id[rid]
    review_type = str(item.get("review_type") or payload.get("review_type") or "")
    allowed = ALLOWED_DISCOVERED_FIELDS if review_type == "discovered_bug" else ALLOWED_MISSED_FIELDS
    clean: dict[str, Any] = {"review_item_id": rid, "review_type": review_type}
    for key in allowed:
        if key not in payload:
            continue
        value = payload.get(key)
        if key in BOOL_FIELDS:
            clean[key] = _coerce_bool(value)
        elif key in {"feedback_notes", "reviewer", "root_cause", "human_severity", "priority_override"}:
            clean[key] = str(value or "").strip()[:2000]
        else:
            clean[key] = value
    if review_type == "discovered_bug":
        clean.setdefault("source_bug_key", item.get("source_bug_key"))
        sev = str(clean.get("human_severity") or item.get("severity") or "P2").strip()
        clean["human_severity"] = sev if sev in SEVERITIES else "P2"
        root = str(clean.get("root_cause") or "unknown").strip()
        clean["root_cause"] = root if root in ROOT_CAUSES else "unknown"
        clean.setdefault("is_duplicate", False)
    elif review_type == "missed_template":
        clean.setdefault("missed_template", item.get("missed_template"))
        priority = str(clean.get("priority_override") or item.get("priority") or item.get("severity") or "P1").strip()
        clean["priority_override"] = priority if priority in SEVERITIES else "P1"
    clean["reviewer"] = str(clean.get("reviewer") or "web_reviewer").strip()[:120] or "web_reviewer"
    clean["reviewed_at_utc"] = str(clean.get("reviewed_at_utc") or _now())
    clean["source"] = "web_review_ui"
    leak = _validate_no_private_leak(clean)
    if not leak.get("passed"):
        raise ValueError("feedback contains private leak terms: " + ", ".join(leak.get("leak_terms") or []))
    return clean


def save_review_feedback(
    payload: dict[str, Any],
    out_dir: Path = DEFAULT_OUT,
    discovered_path: Path = DEFAULT_DISCOVERED,
    benchmark_dir: Path = DEFAULT_BENCHMARK,
    workspace_dir: Path = DEFAULT_WORKSPACE,
) -> dict[str, Any]:
    state = ensure_review_queue(discovered_path=discovered_path, benchmark_dir=benchmark_dir, out_dir=out_dir, workspace_dir=workspace_dir)
    queue = state.get("items") or []
    queue_by_id = {str(x.get("review_item_id")): x for x in queue if x.get("review_item_id")}
    feedback = _sanitize_feedback(payload, queue_by_id)
    rows = iter_jsonl(out_dir / "human_feedback.jsonl")
    updated = False
    next_rows: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("review_item_id")) == feedback["review_item_id"]:
            next_rows.append(feedback)
            updated = True
        else:
            next_rows.append(row)
    if not updated:
        next_rows.append(feedback)
    write_jsonl(out_dir / "human_feedback.jsonl", next_rows)
    result = run_human_feedback_loop(discovered_path=discovered_path, benchmark_dir=benchmark_dir, out_dir=out_dir, workspace_dir=workspace_dir, seed_sample=False)
    web_report = build_web_review_report(load_review_state(out_dir))
    (out_dir / "human_feedback_web_review_report.html").write_text(web_report, encoding="utf-8")
    return {
        "ok": True,
        "updated": True,
        "review_item_id": feedback["review_item_id"],
        "summary": result.get("summary"),
        "private_leak_check": result.get("private_leak_check"),
    }


def build_web_review_report(state: dict[str, Any]) -> str:
    summary = state.get("summary") or {}
    rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
        for k, v in summary.items()
    )
    item_rows = "".join(
        f"<tr><td>{html.escape(str(item.get('review_item_id')))}</td><td>{html.escape(str(item.get('review_type')))}</td><td>{html.escape(str(item.get('title')))}</td><td>{html.escape(str(item.get('severity')))}</td><td>{html.escape(str(item.get('review_status')))}</td></tr>"
        for item in (state.get("items") or [])[:200]
    )
    leak = state.get("private_leak_check") or {}
    return f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><title>Phase22 Human Feedback Web Review</title>
<style>body{{font-family:Arial,Microsoft YaHei,sans-serif;margin:32px;background:#f6f7fb;color:#172033}}.card{{background:white;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 8px 24px rgba(15,23,42,.08)}}table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left}}.ok{{color:#047857;font-weight:700}}.bad{{color:#b91c1c;font-weight:700}}code{{background:#eef2ff;padding:2px 6px;border-radius:6px}}</style></head>
<body><h1>Phase22 Human Feedback Web Review UI</h1>
<div class='card'><h2>Review Summary</h2><table><tbody>{rows}</tbody></table></div>
<div class='card'><h2>Governance</h2><p>Private leak check: <span class='{ 'ok' if leak.get('passed') else 'bad' }'>{html.escape(str(leak.get('passed')))}</span></p><p>Leak terms: <code>{html.escape(str(leak.get('leak_terms') or []))}</code></p><p>Web review writes only reviewer labels and notes. It does not expose ground truth, bug sets, or enabled bug config.</p></div>
<div class='card'><h2>Review Queue Preview</h2><table><thead><tr><th>ID</th><th>Type</th><th>Title</th><th>Severity</th><th>Status</th></tr></thead><tbody>{item_rows}</tbody></table></div>
</body></html>"""


def refresh_web_review_assets(
    discovered_path: Path = DEFAULT_DISCOVERED,
    benchmark_dir: Path = DEFAULT_BENCHMARK,
    out_dir: Path = DEFAULT_OUT,
    workspace_dir: Path = DEFAULT_WORKSPACE,
) -> dict[str, Any]:
    state = ensure_review_queue(discovered_path=discovered_path, benchmark_dir=benchmark_dir, out_dir=out_dir, workspace_dir=workspace_dir)
    report = build_web_review_report(state)
    (out_dir / "human_feedback_web_review_report.html").write_text(report, encoding="utf-8")
    return state


def main() -> None:
    state = refresh_web_review_assets()
    print(json.dumps({
        "phase": state.get("phase"),
        "summary": state.get("summary"),
        "private_leak_check": state.get("private_leak_check"),
        "report": state.get("outputs", {}).get("web_review_report"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
