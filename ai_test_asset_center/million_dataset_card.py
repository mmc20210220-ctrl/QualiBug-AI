"""
[DEPRECATED] Million Dataset Card generator (Phase13)
Status: ZOMBIE MODULE -- 0 active cross-references in the mainline.
  Its dependency enterprise_bug_factory.tools.generate_million_dataset was
  removed from the repository, so dataset cards cannot be built.
Roadmap: fold dataset-card generation back into enterprise_bug_factory if the
  million-bug dataset tooling is revived; otherwise leave deprecated.
See DEPRECATED.md for architecture decisions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_INDEX = Path("enterprise_bug_factory/bug_sets/million_bug_index.json")
DEFAULT_OUT = Path("benchmark_outputs/million_dataset")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_dataset_card(index_path: Path = DEFAULT_INDEX, out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    try:
        from enterprise_bug_factory.tools.generate_million_dataset import (  # [DEPRECATED] see DEPRECATED.md
            build_dataset_card,
            build_dataset_card_html,
        )
    except ImportError as exc:
        raise RuntimeError(
            "million_dataset_card is deprecated: "
            "enterprise_bug_factory.tools.generate_million_dataset was removed."
        ) from exc
    index = read_json(index_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    card = build_dataset_card(index)
    (out_dir / "dataset_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "dataset_card.html").write_text(build_dataset_card_html(index), encoding="utf-8")
    return {"index": str(index_path), "dataset_card": str(out_dir / "dataset_card.html"), "summary": card}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Phase13 dataset card from million_bug_index.json")
    parser.add_argument("--index", default=str(DEFAULT_INDEX))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    payload = write_dataset_card(Path(args.index), Path(args.out))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
