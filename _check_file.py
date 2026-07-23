from pathlib import Path
import time
p = Path('_scan_result_latest.json')
print(f'exists={p.exists()}')
if p.exists():
    print(f'mtime={time.ctime(p.stat().st_mtime)}')
    print(f'size={p.stat().st_size}')
