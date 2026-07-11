"""Emit honest BLOCKED receipts for Gate D prerequisites that cannot run yet.

Does NOT invent evaluate metrics. Distinct held-out / clean live environments and
four champion/challenger reports remain unavailable while commercial/diagnostic
manifests scaffold multiple industries onto the single ecommerce mall at :8080.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from ai_test_asset_center.artifact_redactor import write_json_redacted  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def main() -> None:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = ROOT / "_audit_packs" / f"blocked_receipts_{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    commercial = ROOT / "_private_eval" / "commercial_v1" / "evaluation_manifest.json"
    diagnostic = ROOT / "_private_eval" / "diagnostic_benchmark_mall_v1" / "evaluation_manifest.json"
    commercial_raw = json.loads(commercial.read_text(encoding="utf-8")) if commercial.exists() else {}
    env_refs = sorted(
        {
            str(((t.get("runtime") or {}).get("environment_ref") or "")).strip()
            for t in list(commercial_raw.get("targets") or [])
            if isinstance(t, dict)
        }
        - {""}
    )

    shared = {
        "schema_version": "qualibug.breakthrough-blocked-receipt.v1",
        "created_at_utc": stamp,
        "measurement_status": "BLOCKED",
        "claim_status": "NOT_MEASURED",
        "commercial_promotion_evidence": False,
        "capability_breakthrough_claim": False,
        "manifest_fingerprints": {
            "commercial_manifest": str(commercial),
            "commercial_manifest_sha256": _sha(commercial),
            "diagnostic_manifest": str(diagnostic),
            "diagnostic_manifest_sha256": _sha(diagnostic),
        },
        "observed_environment_refs": env_refs,
    }

    held_out = {
        **shared,
        "receipt_id": "held-out-live-multi-industry",
        "blocked_reason": "BLOCKED_MISSING_DISTINCT_LIVE_TARGETS",
        "detail": (
            "Held-out industries in the commercial/diagnostic manifests currently share "
            "the same live environment_ref (ecommerce mall on localhost:8080). Running "
            "discovery against that single API with foreign OpenAPI docs would produce "
            "invalid cross-industry evidence. Distinct non-production held-out runtimes "
            "with working reset/observation are required before MEASURED held-out metrics."
        ),
        "required_next_step": "Stand up >=3 distinct non-prod held-out industry targets with fixture reset.",
    }

    clean = {
        **shared,
        "receipt_id": "clean-target-live",
        "blocked_reason": "BLOCKED_MISSING_CLEAN_LIVE_TARGET",
        "detail": (
            "clean-healthcare / clean-1 are declared in manifests but no distinct clean "
            "live system is available. Evaluating an empty synthetic envelope would invent "
            "FP=0 and is forbidden. A real intentionally-clean non-prod target with reset "
            "receipts is required."
        ),
        "required_next_step": "Provide an intentionally clean non-prod target and run a live scan + evaluate.",
    }

    champion = {
        **shared,
        "receipt_id": "champion-challenger-four-pass",
        "blocked_reason": "BLOCKED_MISSING_MULTI_TARGET_LIVE_BASELINE",
        "detail": (
            "tools/run_observed_discovery_policy_evaluation.py requires a frozen commercial "
            "manifest with live held-in, held-out, and clean targets. Until distinct live "
            "targets exist, the four replay+shadow champion/challenger reports cannot be "
            "produced without fabricating promotion evidence."
        ),
        "required_reports": [
            "champion_replay",
            "challenger_replay",
            "champion_shadow",
            "challenger_shadow",
        ],
        "required_next_step": "After multi-target live readiness, run observed policy evaluation without --activate.",
    }

    pack = {
        **shared,
        "receipts": {
            "held_out_live": held_out,
            "clean_target_live": clean,
            "champion_challenger": champion,
        },
        "final_status": "INCOMPLETE",
        "note": "These BLOCKED receipts are evidence of honest non-measurement, not Gate D progress.",
    }
    write_json_redacted(out / "BLOCKED_RECEIPTS.json", pack)
    write_json_redacted(out / "held_out_live.BLOCKED.json", held_out)
    write_json_redacted(out / "clean_target_live.BLOCKED.json", clean)
    write_json_redacted(out / "champion_challenger.BLOCKED.json", champion)
    print(json.dumps({"out": str(out), "status": "BLOCKED"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
