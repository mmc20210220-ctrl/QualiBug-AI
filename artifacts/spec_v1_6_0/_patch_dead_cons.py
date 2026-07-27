from pathlib import Path

p = Path(r"d:/QualiBug-AI/QualiBug-AI-main/ai_test_asset_center/experiment_protocols_base.py")
text = p.read_text(encoding="utf-8")
marker = '    if family == "conservation":\n        # ── Phase 4:'
start = text.find(marker)
end = text.find("    write_body: dict[str, Any] = {}", start)
assert start > 0 and end > start, (start, end)
replacement = '''    if family == "conservation":
        # Dead-path safeguard: the live conservation branch returns earlier.
        # Keep fail-closed here so reordering cannot reintroduce NL guessing.
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_EMPTY_CONSERVATION_TERMS",
            "detail": "conservation_requires_non_empty_equation_terms",
        }

'''
p.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
print("replaced", end - start, "chars")
