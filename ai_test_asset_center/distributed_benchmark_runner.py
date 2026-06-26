from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from typing import Any

DEFAULT_GROUND_TRUTH = Path("enterprise_bug_factory/private_ground_truth/ground_truth_bugs.json")
DEFAULT_DISCOVERED = Path("platform_outputs/enterprise_shop/defect_discovery/discovered_bugs.json")
DEFAULT_EVIDENCE = Path("platform_outputs/enterprise_shop/defect_discovery/evidence_bundle.json")
DEFAULT_SCORECARD = Path("benchmark_outputs/benchmark_scorecard.json")
DEFAULT_OUT = Path("benchmark_outputs/distributed")

PRIVATE_TOKENS = {"ground_truth", "private_ground_truth", "bug_sets", "enabled_bugs", "current_bug_set"}


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def stable_bucket(value: str, shard_count: int) -> int:
    if shard_count <= 0:
        return 0
    digest = sha256(value.encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:12], 16) % shard_count


def bug_id_of(bug: dict[str, Any]) -> str:
    return str(bug.get("bug_id") or bug.get("bug_instance_id") or bug.get("id") or "")


def template_of(bug: dict[str, Any]) -> str:
    return str(bug.get("template_id") or bug.get("predicted_template_id") or "UNKNOWN_TEMPLATE")


def severity_of(bug: dict[str, Any]) -> str:
    return str(bug.get("severity") or "P2")


def domain_of(bug: dict[str, Any]) -> str:
    return str(bug.get("domain") or bug.get("risk_type") or "unknown")


def risk_of(bug: dict[str, Any]) -> str:
    return str(bug.get("risk_type") or bug.get("predicted_risk_type") or "unknown")


def related_api_key(bug: dict[str, Any]) -> str:
    related = bug.get("related_apis") or []
    if isinstance(related, list) and related:
        return str(related[0])
    return str(bug.get("affected_api") or bug.get("api") or "")


def summarize_bugs(bugs: list[dict[str, Any]]) -> dict[str, Any]:
    templates = Counter(template_of(b) for b in bugs)
    domains = Counter(domain_of(b) for b in bugs)
    severities = Counter(severity_of(b) for b in bugs)
    risks = Counter(risk_of(b) for b in bugs)
    return {
        "bug_count": len(bugs),
        "template_count": len(templates),
        "template_distribution": dict(sorted(templates.items())),
        "domain_distribution": dict(sorted(domains.items())),
        "severity_distribution": dict(sorted(severities.items())),
        "risk_type_distribution": dict(sorted(risks.items())),
        "p0_p1_count": severities.get("P0", 0) + severities.get("P1", 0),
    }


def make_public_shard(shard: dict[str, Any]) -> dict[str, Any]:
    public = {k: v for k, v in shard.items() if k not in {"bug_ids", "bug_instance_ids", "ground_truth_refs"}}
    public["private_identifiers_redacted"] = True
    public["ai_discovery_can_read_this"] = True
    return public


def create_shard_manifest(truth_payload: dict[str, Any], shard_count: int = 4, strategy: str = "template_balanced") -> dict[str, Any]:
    bugs = list(truth_payload.get("bugs", []) or [])
    shard_count = max(1, int(shard_count or 1))
    shards: list[list[dict[str, Any]]] = [[] for _ in range(shard_count)]

    if strategy == "hash":
        for bug in bugs:
            bucket = stable_bucket(bug_id_of(bug) or template_of(bug), shard_count)
            shards[bucket].append(bug)
    else:
        # Template-balanced distribution: spread each template's instances over
        # all shards, always placing the next item into the currently smallest shard.
        by_template: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for bug in sorted(bugs, key=lambda b: (template_of(b), severity_of(b), risk_of(b), bug_id_of(b))):
            by_template[template_of(bug)].append(bug)
        for _, items in sorted(by_template.items()):
            for bug in items:
                bucket = min(range(shard_count), key=lambda i: (len(shards[i]), i))
                shards[bucket].append(bug)

    private_shards: list[dict[str, Any]] = []
    public_shards: list[dict[str, Any]] = []
    for i, items in enumerate(shards):
        summary = summarize_bugs(items)
        private = {
            "shard_id": f"shard_{i:03d}",
            "shard_index": i,
            "strategy": strategy,
            **summary,
            "bug_ids": [bug_id_of(b) for b in items],
            "bug_instance_ids": [str(b.get("bug_instance_id") or bug_id_of(b)) for b in items],
            "ground_truth_refs": [
                {
                    "bug_id": bug_id_of(b),
                    "bug_instance_id": b.get("bug_instance_id"),
                    "template_id": template_of(b),
                    "risk_type": risk_of(b),
                    "severity": severity_of(b),
                    "related_apis": b.get("related_apis", []),
                }
                for b in items
            ],
        }
        private_shards.append(private)
        public_shards.append(make_public_shard(private))

    return {
        "phase": "phase12_distributed_benchmark_runner",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "shard_count": shard_count,
        "strategy": strategy,
        "benchmark_summary": summarize_bugs(bugs),
        "public_manifest": {
            "shard_count": shard_count,
            "strategy": strategy,
            "shards": public_shards,
            "anti_cheat": {
                "public_manifest_has_private_identifiers": False,
                "public_manifest_exposes_ground_truth": False,
                "ai_discovery_should_only_receive_public_artifacts": True,
            },
        },
        "private_manifest": {
            "shard_count": shard_count,
            "strategy": strategy,
            "shards": private_shards,
            "for_evaluator_only": True,
            "must_not_be_read_by_ai_test_asset_center": True,
        },
    }


def discovered_key(bug: dict[str, Any]) -> str:
    return str(bug.get("discovered_bug_id") or bug.get("probe_id") or "")


def assign_discovered_to_shard(bug: dict[str, Any], shard_count: int) -> int:
    key = "|".join([
        str(bug.get("predicted_template_id") or ""),
        str(bug.get("risk_type") or bug.get("predicted_risk_type") or ""),
        related_api_key(bug),
        discovered_key(bug),
    ])
    return stable_bucket(key, shard_count)


def split_discovered_by_shard(discovered_payload: dict[str, Any], shard_count: int) -> list[dict[str, Any]]:
    bugs = list(discovered_payload.get("bugs", []) or [])
    shards = [{"bugs": []} for _ in range(max(1, shard_count))]
    for bug in bugs:
        shards[assign_discovered_to_shard(bug, shard_count)]["bugs"].append(bug)
    for i, payload in enumerate(shards):
        payload.update({
            "phase": "phase12_distributed_shard_output",
            "shard_id": f"shard_{i:03d}",
            "discovery_mode": discovered_payload.get("discovery_mode", "unknown"),
            "benchmark_compat_enabled": discovered_payload.get("benchmark_compat_enabled", False),
        })
    return shards


def split_evidence_by_shard(evidence_payload: Any, shard_count: int) -> list[dict[str, Any]]:
    if isinstance(evidence_payload, dict):
        rows = evidence_payload.get("evidence", evidence_payload.get("items", []))
    elif isinstance(evidence_payload, list):
        rows = evidence_payload
    else:
        rows = []
    shards = [{"evidence": []} for _ in range(max(1, shard_count))]
    for item in rows:
        pid = str(item.get("probe_id") or item.get("evidence_ref") or "")
        bucket = stable_bucket(pid, shard_count)
        shards[bucket]["evidence"].append(item)
    for i, payload in enumerate(shards):
        payload.update({"phase": "phase12_distributed_shard_evidence", "shard_id": f"shard_{i:03d}"})
    return shards


def merge_discovered_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        for bug in payload.get("bugs", []) or []:
            key = discovered_key(bug) or json.dumps(bug, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            merged.append(bug)
    return {
        "phase": "phase12_merged_discovered_bugs",
        "merged_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "shard_count": len(payloads),
        "bugs": merged,
        "deduped_bug_count": len(merged),
    }


def merge_evidence_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        for item in payload.get("evidence", []) or []:
            key = str(item.get("probe_id") or json.dumps(item, sort_keys=True, ensure_ascii=False))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return {
        "phase": "phase12_merged_evidence_bundle",
        "merged_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "shard_count": len(payloads),
        "evidence": merged,
        "deduped_evidence_count": len(merged),
    }


def truth_shard_lookup(private_manifest: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for shard in private_manifest.get("shards", []) or []:
        sid = str(shard.get("shard_id"))
        for bid in shard.get("bug_ids", []) or []:
            lookup[str(bid)] = sid
        for bid in shard.get("bug_instance_ids", []) or []:
            lookup[str(bid)] = sid
    return lookup


def build_shard_scorecards(scorecard: dict[str, Any], manifest: dict[str, Any], discovered_shards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    private_manifest = manifest.get("private_manifest", {})
    lookup = truth_shard_lookup(private_manifest)
    shard_ids = [str(s.get("shard_id")) for s in private_manifest.get("shards", [])]
    rows = {sid: {"shard_id": sid, "known_bug_instances": 0, "matches": 0, "false_positives": 0, "discovered_bugs": 0} for sid in shard_ids}
    for shard in private_manifest.get("shards", []) or []:
        sid = str(shard.get("shard_id"))
        rows[sid]["known_bug_instances"] = int(shard.get("bug_count") or 0)
    for i, payload in enumerate(discovered_shards):
        sid = f"shard_{i:03d}"
        rows.setdefault(sid, {"shard_id": sid, "known_bug_instances": 0, "matches": 0, "false_positives": 0, "discovered_bugs": 0})
        rows[sid]["discovered_bugs"] += len(payload.get("bugs", []) or [])
    for match in scorecard.get("matches", []) or []:
        gt = match.get("ground_truth", {})
        sid = lookup.get(str(gt.get("bug_id") or gt.get("bug_instance_id")))
        if sid in rows:
            rows[sid]["matches"] += 1
    for fp in scorecard.get("false_positives", []) or []:
        sid = f"shard_{assign_discovered_to_shard(fp, len(rows) or 1):03d}"
        rows.setdefault(sid, {"shard_id": sid, "known_bug_instances": 0, "matches": 0, "false_positives": 0, "discovered_bugs": 0})
        rows[sid]["false_positives"] += 1
    out = []
    for row in rows.values():
        known = row["known_bug_instances"]
        disc = row["discovered_bugs"]
        matches = row["matches"]
        fp = row["false_positives"]
        row["missed_bugs"] = max(known - matches, 0)
        row["recall"] = round(matches / max(known, 1), 4) if known else 0
        row["precision"] = round(matches / max(matches + fp, 1), 4) if (matches + fp) else 1.0
        row["false_positive_rate"] = round(fp / max(disc, 1), 4) if disc else 0
        out.append(row)
    return sorted(out, key=lambda r: r["shard_id"])


def build_html_report(payload: dict[str, Any]) -> str:
    summary = payload.get("distributed_summary", {})
    shard_rows = "".join(
        f"<tr><td>{r['shard_id']}</td><td>{r['known_bug_instances']}</td><td>{r['discovered_bugs']}</td><td>{r['matches']}</td><td>{r['missed_bugs']}</td><td>{r['recall']}</td><td>{r['precision']}</td></tr>"
        for r in payload.get("shard_scorecards", [])
    )
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Phase12 Distributed Benchmark Report</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{border:1px solid #d8dee9;border-radius:10px;padding:14px;background:#f8fafc}}table{{border-collapse:collapse;width:100%;margin-top:16px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.note{{background:#eef6ff;border:1px solid #bfdbfe;border-radius:8px;padding:12px}}</style></head><body><h1>Phase12 Distributed Benchmark Runner</h1><p class=\"note\">目标：将大规模 Benchmark 拆分为多个 shard，支持多 worker 执行、分片证据合并、分片评分和总报告。当前实现为本地文件级分片/合并框架，后续可接远程 worker。</p><div class=\"grid\"><div class=\"card\"><b>Shard count</b><br>{summary.get('shard_count')}</div><div class=\"card\"><b>Total known bugs</b><br>{summary.get('known_bug_instances')}</div><div class=\"card\"><b>Merged discovered</b><br>{summary.get('merged_discovered_bugs')}</div><div class=\"card\"><b>Merged evidence</b><br>{summary.get('merged_evidence_items')}</div></div><h2>Shard Scorecards</h2><table><tr><th>Shard</th><th>Known</th><th>Discovered</th><th>Matched</th><th>Missed</th><th>Recall</th><th>Precision</th></tr>{shard_rows}</table><h2>Anti-cheat</h2><ul><li>Public shard manifest redacts bug ids and ground truth refs.</li><li>Private shard manifest is evaluator-only.</li><li>AI discovery should still consume only PRD, OpenAPI, Base URL, test accounts and runtime responses.</li></ul></body></html>"""


def run_distributed_benchmark(
    ground_truth_path: Path = DEFAULT_GROUND_TRUTH,
    discovered_path: Path = DEFAULT_DISCOVERED,
    evidence_path: Path = DEFAULT_EVIDENCE,
    scorecard_path: Path = DEFAULT_SCORECARD,
    out_dir: Path = DEFAULT_OUT,
    shard_count: int = 4,
    strategy: str = "template_balanced",
) -> dict[str, Any]:
    truth_payload = read_json(ground_truth_path, {"bugs": []})
    discovered_payload = read_json(discovered_path, {"bugs": []})
    evidence_payload = read_json(evidence_path, {"evidence": []})
    scorecard = read_json(scorecard_path, {})

    manifest = create_shard_manifest(truth_payload, shard_count=shard_count, strategy=strategy)
    discovered_shards = split_discovered_by_shard(discovered_payload, manifest["shard_count"])
    evidence_shards = split_evidence_by_shard(evidence_payload, manifest["shard_count"])
    merged_discovered = merge_discovered_payloads(discovered_shards)
    merged_evidence = merge_evidence_payloads(evidence_shards)
    shard_scorecards = build_shard_scorecards(scorecard, manifest, discovered_shards)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "public_shard_manifest.json", manifest["public_manifest"])
    write_json(out_dir / "private_shard_manifest.json", manifest["private_manifest"])
    shard_root = out_dir / "shard_outputs"
    for i, payload in enumerate(discovered_shards):
        shard_id = f"shard_{i:03d}"
        write_json(shard_root / shard_id / "discovered_bugs.json", payload)
        write_json(shard_root / shard_id / "evidence_bundle.json", evidence_shards[i])
        write_json(shard_root / shard_id / "shard_scorecard.json", shard_scorecards[i] if i < len(shard_scorecards) else {"shard_id": shard_id})
    write_json(out_dir / "merged_discovered_bugs.json", merged_discovered)
    write_json(out_dir / "merged_evidence_bundle.json", merged_evidence)

    known = int(manifest["benchmark_summary"].get("bug_count") or 0)
    payload = {
        "phase": "phase12_distributed_benchmark_runner",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": {
            "ground_truth_path": str(ground_truth_path),
            "discovered_path": str(discovered_path),
            "evidence_path": str(evidence_path),
            "scorecard_path": str(scorecard_path),
        },
        "distributed_summary": {
            "shard_count": manifest["shard_count"],
            "strategy": strategy,
            "known_bug_instances": known,
            "merged_discovered_bugs": len(merged_discovered.get("bugs", [])),
            "merged_evidence_items": len(merged_evidence.get("evidence", [])),
            "public_manifest_has_private_identifiers": public_manifest_exposes_private_tokens(manifest["public_manifest"]),
            "private_manifest_for_evaluator_only": True,
        },
        "shard_scorecards": shard_scorecards,
        "anti_cheat": {
            "ai_discovery_reads_private_truth": False,
            "public_manifest_has_private_identifiers": public_manifest_exposes_private_tokens(manifest["public_manifest"]),
            "private_manifest_written_for_evaluator_only": True,
            "benchmark_compat_required": False,
        },
        "next_scale_targets": [10000, 100000, 1000000],
    }
    write_json(out_dir / "distributed_benchmark_scorecard.json", payload)
    (out_dir / "distributed_benchmark_report.html").write_text(build_html_report(payload), encoding="utf-8")
    return payload


def public_manifest_exposes_private_tokens(public_manifest: dict[str, Any]) -> bool:
    # Inspect JSON keys recursively so allowed aggregate fields such as
    # bug_count do not fail the anti-cheat check.
    forbidden_keys = {"bug_ids", "bug_instance_ids", "ground_truth_refs", "enabled_bugs", "current_bug_set"}

    def walk(obj: Any) -> bool:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if str(key) in forbidden_keys:
                    return True
                if walk(value):
                    return True
        elif isinstance(obj, list):
            return any(walk(item) for item in obj)
        return False

    return walk(public_manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", default=str(DEFAULT_GROUND_TRUTH))
    parser.add_argument("--discovered", default=str(DEFAULT_DISCOVERED))
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--scorecard", default=str(DEFAULT_SCORECARD))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--shards", type=int, default=4)
    parser.add_argument("--strategy", default="template_balanced", choices=["template_balanced", "hash"])
    args = parser.parse_args()
    payload = run_distributed_benchmark(
        ground_truth_path=Path(args.ground_truth),
        discovered_path=Path(args.discovered),
        evidence_path=Path(args.evidence),
        scorecard_path=Path(args.scorecard),
        out_dir=Path(args.out),
        shard_count=args.shards,
        strategy=args.strategy,
    )
    print(json.dumps({
        "shard_count": payload["distributed_summary"]["shard_count"],
        "known_bug_instances": payload["distributed_summary"]["known_bug_instances"],
        "merged_discovered_bugs": payload["distributed_summary"]["merged_discovered_bugs"],
        "report": str(Path(args.out) / "distributed_benchmark_report.html"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
