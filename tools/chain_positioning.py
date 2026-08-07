"""Chain positioning CLI — 定位一次运行卡在哪（诊断工具）。

Usage:
    python tools/chain_positioning.py <run_result.json>
    python tools/chain_positioning.py --diff <runA.json> <runB.json>

Input is a scan run result JSON (the persisted scan result or
intelligence_report.json).  The positioning receipt is projected from the
receipts already inside the file, so old runs can be diagnosed without a
re-run.  All guidance is synthetic diagnostic text, never delivery evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_test_asset_center.chain_positioning import (  # noqa: E402
    build_chain_positioning,
    render_chain_diff_markdown,
    render_chain_positioning_markdown,
)


def _load(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path}: root must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="发现链路定位（诊断工具）")
    parser.add_argument("run_result", nargs="?", help="扫描运行结果 JSON")
    parser.add_argument("--diff", nargs=2, metavar=("RUN_A", "RUN_B"), help="对比两次运行的阶段级卡点差异")
    args = parser.parse_args()

    if args.diff:
        run_a = _load(args.diff[0])
        run_b = _load(args.diff[1])
        print(render_chain_diff_markdown(run_a, run_b))
        return 0
    if not args.run_result:
        parser.print_help()
        return 2
    run = _load(args.run_result)
    receipt = build_chain_positioning(run)
    print(render_chain_positioning_markdown(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
