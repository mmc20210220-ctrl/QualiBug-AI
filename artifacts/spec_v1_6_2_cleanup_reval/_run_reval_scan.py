"""Detached formal reval runner — keeps client alive outside Cursor tool timeout."""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).with_name("_formal_product_scan.py")
    runpy.run_path(str(target), run_name="__main__")
