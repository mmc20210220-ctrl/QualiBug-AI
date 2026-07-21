# -*- coding: utf-8 -*-
"""Find files using _safe_project_id without importing it."""
import os

files = [f for f in os.listdir('ai_test_asset_center') if f.endswith('.py')]
for f in files:
    path = f'ai_test_asset_center/{f}'
    try:
        content = open(path, encoding='utf-8', errors='ignore').read()
    except:
        continue
    if '_safe_project_id(' not in content:
        continue
    if 'def _safe_project_id' in content:
        continue
    # Check if imported
    lines = content.split('\n')
    imported = False
    for line in lines[:100]:  # Check first 100 lines for imports
        if '_safe_project_id' in line and ('import' in line or 'from' in line):
            imported = True
            break
    if not imported:
        print(f"MISSING IMPORT: {f}")
