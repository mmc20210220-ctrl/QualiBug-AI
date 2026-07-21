# -*- coding: utf-8 -*-
"""Find all underscore functions used in _api.py and their source modules."""
import re, os

base = 'ai_test_asset_center/enterprise_knowledge_center'
api_content = open(f'{base}/_api.py', encoding='utf-8').read()

# All underscore function calls in _api.py
used = set(re.findall(r'\b(_[a-z][a-z_0-9]+)\s*\(', api_content))
# Defined locally in _api.py
local = set(re.findall(r'^def (_[a-z][a-z_0-9]+)\s*\(', api_content, re.M))
# Also _add_rel is defined as nested function
local.add('_add_rel')

external = used - local
print(f"Used: {len(used)}, Local: {len(local)}, External needed: {len(external)}")

# Find source for each
files = [f for f in os.listdir(base) if f.endswith('.py') and f != '_api.py']
file_contents = {}
for f in files:
    try:
        file_contents[f] = open(os.path.join(base, f), encoding='utf-8', errors='ignore').read()
    except:
        pass

# Also check parent package
parent_files = [f for f in os.listdir('ai_test_asset_center') if f.endswith('.py')]
for f in parent_files:
    try:
        file_contents[f'../{f}'] = open(f'ai_test_asset_center/{f}', encoding='utf-8', errors='ignore').read()
    except:
        pass

by_module = {}
for name in sorted(external):
    found = []
    for fname, content in file_contents.items():
        if re.search(r'^def ' + re.escape(name) + r'\s*\(', content, re.M):
            found.append(fname)
        # Also check "import X as name" pattern
        if re.search(r'import\s+\w+\s+as\s+' + re.escape(name), content):
            found.append(f'{fname} (re-export)')
    by_module.setdefault(tuple(found) if found else ('NOT_FOUND',), []).append(name)

for sources, names in sorted(by_module.items()):
    print(f"\n  From {sources}:")
    for n in names:
        print(f"    {n}")
