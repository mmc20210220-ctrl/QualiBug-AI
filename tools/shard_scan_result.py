#!/usr/bin/env python3
"""离线流式转换：旧单文件 scan_result.json（可达 4GB）→ 分片 store。

用法:
  python tools/shard_scan_result.py <scan_result.json> [--threshold-bytes 4194304]
                                    [--no-backup] [--verify]

行为:
  * mmap 流式扫描，绝不整读入内存（4GB 文件可安全转换）；
  * 大键按递归分片规则移入 scan_result.parts/，scan_result.json 变为索引；
  * 默认把原文件保留为 scan_result.json.legacy（--no-backup 关闭）；
  * 转换后自动 verify（清单 vs 实际分片文件的大小/sha256）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from ai_test_asset_center.scan_result_store import (  # noqa: E402
    DEFAULT_SHARD_THRESHOLD_BYTES,
    shard_legacy_scan_result,
    verify_scan_result_store,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="convert legacy single-file scan_result to sharded store")
    parser.add_argument("scan_result", type=Path, help="path to legacy scan_result.json")
    parser.add_argument("--threshold-bytes", type=int, default=DEFAULT_SHARD_THRESHOLD_BYTES)
    parser.add_argument("--no-backup", action="store_true", help="do not keep scan_result.json.legacy")
    parser.add_argument("--verify", action="store_true", help="verify store integrity after conversion")
    args = parser.parse_args(argv)

    result = shard_legacy_scan_result(
        args.scan_result,
        threshold_bytes=args.threshold_bytes,
        keep_legacy=not args.no_backup,
    )
    print(result)
    if args.verify:
        check = verify_scan_result_store(args.scan_result, check_sha256=True)
        print(check)
        if not check.get("valid"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
