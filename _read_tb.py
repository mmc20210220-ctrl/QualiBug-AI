"""Read traceback log."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
t = Path("_scan_traceback.log").read_text("utf-8", errors="replace")
print(t[-5000:] if len(t) > 5000 else t)
