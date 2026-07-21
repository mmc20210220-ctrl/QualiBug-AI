# -*- coding: utf-8 -*-
"""Check where Behavior IR operations are in scan result."""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

d = json.load(open('scan_fresh_result.json', 'r', encoding='utf-8'))

# Check layers
layers = d.get('layers', {})
print(f"layers keys: {list(layers.keys())[:20]}")

# Check spectrum
spectrum = d.get('spectrum', {})
print(f"spectrum keys: {list(spectrum.keys())[:20]}")

# Check if behavior_ir is anywhere
def find_key(obj, target, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if target in k.lower():
                print(f"  Found '{k}' at {path}.{k} (type={type(v).__name__}, len={len(v) if hasattr(v,'__len__') else 'n/a'})")
            if isinstance(v, (dict, list)) and path.count('.') < 3:
                find_key(v, target, f"{path}.{k}")
    elif isinstance(obj, list) and len(obj) > 0 and path.count('.') < 2:
        find_key(obj[0], target, f"{path}[0]")

print("\nSearching for 'behavior_ir':")
find_key(d, 'behavior_ir')

print("\nSearching for 'operations':")
find_key(d, 'operations')

print("\nSearching for 'obligation':")
find_key(d, 'obligation')
