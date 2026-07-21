# -*- coding: utf-8 -*-
import re
common = open('ai_test_asset_center/grounded_probe_executor/_common.py', encoding='utf-8').read()
funcs = ['_redact', '_safe_payload_summary', '_runtime_response_status', '_runtime_response_summary']
for f in funcs:
    found = f"def {f}" in common
    print(f"{f}: {'YES' if found else 'NO'}")
