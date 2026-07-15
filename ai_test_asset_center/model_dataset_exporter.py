from __future__ import annotations

import argparse
import html
import json
import time
from pathlib import Path
from typing import Any, Iterable

DEFAULT_TRAINING_DIR = Path("benchmark_outputs/training_data")
DEFAULT_OUT = Path("benchmark_outputs/model_dataset")
DEFAULT_WORKSPACE = Path("platform_workspace/enterprise_shop/defect_discovery")
DEFAULT_MAX_ROWS_PER_TASK = 5000

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

SYSTEM_PROBE = (
    "You are an enterprise QA defect-discovery assistant. Generate high-value defect probes from "
    "PRD/OpenAPI/business-rule context. Focus on permissions, IDOR, money, stock, payment, refund, "
    "idempotency, tenant isolation, and state consistency. Return structured JSON only."
)
SYSTEM_FP = (
    "You are an enterprise QA false-positive reviewer. Decide whether an observed behavior is a real bug "
    "or correct protection behavior. Return structured JSON only."
)
SYSTEM_MISSED = (
    "You are an enterprise QA probe-strategy optimizer. Given a missed defect template, propose a reusable "
    "defect probe and oracle. Return structured JSON only."
)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _stable_task_id(prefix: str, index: int, row: dict[str, Any]) -> str:
    template = (((row.get("expected_output") or {}).get("template_id")) or ((row.get("input") or {}).get("missed_template")) or "generic")
    return f"{prefix}_{str(template).upper()}_{index:05d}"


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _messages(system: str, user_payload: dict[str, Any], assistant_payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": _safe_json(user_payload)},
        {"role": "assistant", "content": _safe_json(assistant_payload)},
    ]


def _probe_sft_rows(source_rows: list[dict[str, Any]], max_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(source_rows[:max_rows], start=1):
        inp = dict(row.get("input") or {})
        out = dict(row.get("expected_output") or {})
        template_id = out.get("template_id")
        assistant = {
            "business_rule": out.get("business_rule"),
            "defect_probe": out.get("defect_probe"),
            "expected_behavior": out.get("expected_behavior"),
            "bug_signal": out.get("bug_signal"),
            "severity": out.get("severity"),
            "predicted_template_id": template_id,
            "risk_type": inp.get("risk_type"),
            "evidence_required": ["actor_role", "request", "response_status", "response_body", "expected", "actual"],
        }
        rows.append({
            "id": _stable_task_id("probe_sft", i, row),
            "task": "probe_generation_sft",
            "split": "train",
            "messages": _messages(SYSTEM_PROBE, inp, assistant),
            "metadata": {
                "template_id": template_id,
                "risk_type": inp.get("risk_type"),
                "safe_for_sft": True,
                "contains_hidden_test": False,
                "contains_private_answers": False,
                "source": "phase14_probe_policy_training",
            },
        })
    return rows


def _false_positive_sft_rows(source_rows: list[dict[str, Any]], max_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(source_rows[:max_rows], start=1):
        inp = dict(row.get("input") or {})
        out = dict(row.get("expected_output") or {})
        assistant = {
            "label": out.get("label", "not_bug"),
            "should_create_bug": bool(out.get("should_create_bug", False)),
            "reason": out.get("reason"),
            "confidence": 0.95,
        }
        rows.append({
            "id": _stable_task_id("fp_sft", i, row),
            "task": "false_positive_filtering_sft",
            "split": "train",
            "messages": _messages(SYSTEM_FP, inp, assistant),
            "metadata": {"risk_type": inp.get("risk_type"), "safe_for_sft": True, "source": "phase14_false_positive_training"},
        })
    return rows


def _missed_bug_recovery_rows(source_rows: list[dict[str, Any]], max_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(source_rows[:max_rows], start=1):
        inp = dict(row.get("input") or {})
        out = dict(row.get("expected_output") or {})
        assistant = {
            "suggested_probe": out.get("suggested_probe"),
            "suggested_oracle": out.get("suggested_oracle"),
            "priority": out.get("priority", "P1"),
            "implementation_hint": "Add a reusable probe for this missed template, then validate by blind benchmark and clean-mode false-positive checks.",
        }
        rows.append({
            "id": _stable_task_id("missed_sft", i, row),
            "task": "missed_bug_recovery_sft",
            "split": "train",
            "messages": _messages(SYSTEM_MISSED, inp, assistant),
            "metadata": {"missed_template": inp.get("missed_template"), "safe_for_sft": True, "source": "phase14_missed_template_training"},
        })
    return rows


def _preference_pairs(probe_rows: list[dict[str, Any]], fp_rows: list[dict[str, Any]], max_rows: int) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for i, row in enumerate(probe_rows[: max_rows // 2], start=1):
        user = row["messages"][1]["content"]
        chosen = row["messages"][2]["content"]
        rejected = _safe_json({"defect_probe": "call the API and check 200", "reason": "Too generic; no business invariant, actor, oracle, or evidence."})
        pairs.append({
            "id": f"pref_probe_{i:05d}",
            "task": "probe_generation_preference",
            "split": "train",
            "input": user,
            "chosen": chosen,
            "rejected": rejected,
            "preference_reason": "Chosen answer contains business invariant, bug signal, severity, template, and evidence requirements.",
            "metadata": row.get("metadata", {}),
        })
    for i, row in enumerate(fp_rows[: max_rows // 2], start=1):
        user = row["messages"][1]["content"]
        chosen = row["messages"][2]["content"]
        rejected = _safe_json({"label": "bug", "should_create_bug": True, "reason": "Incorrectly reports correct protective behavior as a bug."})
        pairs.append({
            "id": f"pref_fp_{i:05d}",
            "task": "false_positive_filtering_preference",
            "split": "train",
            "input": user,
            "chosen": chosen,
            "rejected": rejected,
            "preference_reason": "Chosen answer suppresses normal protection behavior and reduces false positives.",
            "metadata": row.get("metadata", {}),
        })
    return pairs[:max_rows]


def _combined_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    for group in groups:
        combined.extend(group)
    return combined


def _validate_no_private_leak(obj: Any) -> dict[str, Any]:
    text = json.dumps(obj, ensure_ascii=False).lower()
    leak_terms = sorted(term for term in PRIVATE_LEAK_TERMS if term.lower() in text)
    return {"passed": not leak_terms, "leak_terms": leak_terms}


def build_model_dataset(
    training_dir: Path = DEFAULT_TRAINING_DIR,
    out_dir: Path = DEFAULT_OUT,
    workspace_dir: Path = DEFAULT_WORKSPACE,
    max_rows_per_task: int = DEFAULT_MAX_ROWS_PER_TASK,
) -> dict[str, Any]:
    probe_source = list(iter_jsonl(training_dir / "probe_policy_training.jsonl"))
    fp_source = list(iter_jsonl(training_dir / "false_positive_training.jsonl"))
    missed_source = list(iter_jsonl(training_dir / "missed_template_training.jsonl"))
    training_card = read_json(training_dir / "training_data_card.json", {}) or {}
    rag_kb = read_json(training_dir / "rag_knowledge_base.json", {"documents": []}) or {"documents": []}

    probe_sft = _probe_sft_rows(probe_source, max_rows_per_task)
    fp_sft = _false_positive_sft_rows(fp_source, max_rows_per_task)
    missed_sft = _missed_bug_recovery_rows(missed_source, max_rows_per_task)
    combined = _combined_rows(probe_sft, fp_sft, missed_sft)
    prefs = _preference_pairs(probe_sft, fp_sft, max_rows_per_task)

    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {
        "probe_generation_sft_rows": write_jsonl(out_dir / "probe_generation_sft.jsonl", probe_sft),
        "false_positive_filtering_sft_rows": write_jsonl(out_dir / "false_positive_filtering_sft.jsonl", fp_sft),
        "missed_bug_recovery_sft_rows": write_jsonl(out_dir / "missed_bug_recovery_sft.jsonl", missed_sft),
        "model_training_dataset_rows": write_jsonl(out_dir / "model_training_dataset.jsonl", combined),
        "preference_pairs_rows": write_jsonl(out_dir / "preference_pairs.jsonl", prefs),
    }

    training_private_check = training_card.get("private_leak_check") or {}
    if isinstance(training_private_check, dict) and "passed" not in training_private_check:
        # Older cards used {leak_terms, passed}; keep conservative fallback.
        training_private_check = {"passed": training_private_check == "passed", "raw": training_private_check}

    leak_check = _validate_no_private_leak({"combined": combined, "prefs": prefs, "rag_kb_summary": {"doc_count": len(rag_kb.get("documents", []))}})
    hidden_used = bool(training_card.get("hidden_test_used_for_training", False))
    card = {
        "phase": "phase18_model_ready_dataset_export",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_training_dir": str(training_dir),
        "output_dir": str(out_dir),
        **counts,
        "total_sft_rows": counts["model_training_dataset_rows"],
        "total_preference_pairs": counts["preference_pairs_rows"],
        "rag_documents_available": len(rag_kb.get("documents", [])) if isinstance(rag_kb, dict) else 0,
        "hidden_test_used_for_training": hidden_used,
        "private_leak_check": leak_check,
        "upstream_training_private_leak_check": training_private_check,
        "governance": {
            "model_ready": True,
            "contains_concrete_bug_instances": False,
            "contains_enabled_bug_lists": False,
            "contains_hidden_test_answers": False,
            "hidden_test_is_evaluation_only": True,
            "requires_human_review_before_external_training": True,
        },
        "recommended_use": [
            "Use probe_generation_sft.jsonl for supervised probe-generation experiments.",
            "Use preference_pairs.jsonl for preference/ranking experiments after human review.",
            "Use false_positive_filtering_sft.jsonl to reduce over-reporting of correct protection behavior.",
            "Keep hidden_test split out of training and use it only for evaluation.",
        ],
    }
    write_json(out_dir / "model_dataset_card.json", card)
    (out_dir / "model_dataset_card.html").write_text(build_model_dataset_card_html(card), encoding="utf-8")

    # Copy compact pointers into workspace for future pipeline stages without duplicating large JSONL files.
    workspace_dir.mkdir(parents=True, exist_ok=True)
    write_json(workspace_dir / "model_dataset_manifest.json", {
        "model_dataset_card": str(out_dir / "model_dataset_card.json"),
        "model_training_dataset": str(out_dir / "model_training_dataset.jsonl"),
        "preference_pairs": str(out_dir / "preference_pairs.jsonl"),
        "private_leak_check": leak_check,
    })
    return {"out_dir": str(out_dir), "card": card, "artifacts": {
        "model_training_dataset": str(out_dir / "model_training_dataset.jsonl"),
        "preference_pairs": str(out_dir / "preference_pairs.jsonl"),
        "probe_generation_sft": str(out_dir / "probe_generation_sft.jsonl"),
        "false_positive_filtering_sft": str(out_dir / "false_positive_filtering_sft.jsonl"),
        "missed_bug_recovery_sft": str(out_dir / "missed_bug_recovery_sft.jsonl"),
        "model_dataset_card": str(out_dir / "model_dataset_card.html"),
    }}


def build_model_dataset_card_html(card: dict[str, Any]) -> str:
    gov = card.get("governance", {})
    gov_rows = "".join(f"<li>{html.escape(str(k))}: <b>{html.escape(str(v))}</b></li>" for k, v in gov.items())
    leak = card.get("private_leak_check", {})
    status = "PASS" if leak.get("passed") else "FAIL"
    status_class = "ok" if leak.get("passed") else "bad"
    rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(card.get(k)))}</td></tr>"
        for k in [
            "probe_generation_sft_rows",
            "false_positive_filtering_sft_rows",
            "missed_bug_recovery_sft_rows",
            "model_training_dataset_rows",
            "preference_pairs_rows",
            "rag_documents_available",
            "hidden_test_used_for_training",
        ]
    )
    uses = "".join(f"<li>{html.escape(str(x))}</li>" for x in card.get("recommended_use", []))
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Phase18 Model-ready Dataset Export</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}table{{border-collapse:collapse;width:100%;margin:16px 0}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.card{{border:1px solid #d8dee9;background:#f8fafc;border-radius:10px;padding:14px;margin:12px 0}}.ok{{color:#0f766e;font-weight:bold}}.bad{{color:#b91c1c;font-weight:bold}}</style></head><body><h1>Phase18 Model-ready Dataset Export</h1><div class=\"card\"><b>Private leak check:</b> <span class=\"{status_class}\">{status}</span><br><b>Output:</b> {html.escape(str(card.get('output_dir')))}</div><h2>Dataset Counts</h2><table><tr><th>Artifact</th><th>Rows / Value</th></tr>{rows}</table><h2>Governance</h2><ul>{gov_rows}</ul><h2>Recommended Use</h2><ul>{uses}</ul><p>这些数据用于模型实验准备。正式外部训练前仍需人工复核、脱敏和合规检查。hidden_test 不进入训练，只用于后续评测。</p></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Phase18 model-ready SFT and preference datasets from safe Phase14 training assets.")
    parser.add_argument("--training-dir", default=str(DEFAULT_TRAINING_DIR))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--max-rows-per-task", type=int, default=DEFAULT_MAX_ROWS_PER_TASK)
    args = parser.parse_args()
    result = build_model_dataset(Path(args.training_dir), Path(args.out), Path(args.workspace), args.max_rows_per_task)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
