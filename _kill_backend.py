"""Kill backend process and restart."""
import ctypes, subprocess, time, sys

kernel32 = ctypes.windll.kernel32
PROCESS_TERMINATE = 0x0001

# Find the backend python process (largest memory)
r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV'],
                   capture_output=True, text=True)
lines = [l for l in r.stdout.split('\n') if 'python' in l.lower()]
print(f"Python processes: {len(lines)}")
for l in lines:
    print(f"  {l.strip()}")

# Kill all python processes that are likely the backend (large memory)
for l in lines:
    parts = l.split(',')
    if len(parts) >= 5:
        pid = int(parts[1].strip('"'))
        mem = parts[4].strip('"').replace(',', '').replace(' K', '').strip()
        try:
            mem_kb = int(mem)
        except:
            continue
        if mem_kb > 100000:  # > 100MB = likely backend
            print(f"Killing PID {pid} (mem={mem_kb}KB)...")
            h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if h:
                kernel32.TerminateProcess(h, 1)
                kernel32.CloseHandle(h)
                print(f"  Killed {pid}")

time.sleep(2)
print("Done. Start backend with: python -m ai_test_asset_center.private_pilot_entrypoint")
