#!/usr/bin/env python
"""Cron wrapper: run watchdog and only report when something is wrong."""
import sys, os, json

# Ensure we run from the project root
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from ai_test_asset_center.loop_watchdog import run_watchdog

events = run_watchdog(once=True)

# Only produce output for non-OK events (silent when everything is fine)
errors = [e for e in events if e.level == "ERROR"]
warns = [e for e in events if e.level == "WARN"]

if errors or warns:
    print("QualiBug Watchdog Alert")
    print("=" * 40)
    for e in errors:
        print(f"  [ERROR] {e.category}: {e.detail}")
        if e.suggestion:
            print(f"          -> {e.suggestion}")
    for e in warns:
        print(f"  [WARN]  {e.category}: {e.detail}")
        if e.suggestion:
            print(f"          -> {e.suggestion}")
    print("=" * 40)
    # Also dump latest events file for context
    events_file = "platform_outputs/real_project_demo/.loop_events.jsonl"
    if os.path.exists(events_file):
        lines = open(events_file).readlines()
        print(f"\nRecent events ({len(lines)} total):")
        for line in lines[-5:]:
            e = json.loads(line)
            print(f"  [{e['level']}] {e['category']}: {e['detail'][:120]}")
else:
    # Silent — everything OK
    pass
