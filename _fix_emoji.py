"""Remove remaining emojis for GBK compatibility."""
import re, os

FILES = [
    r"C:\Users\Test\Desktop\QualiBug_AI_Enterprise_Edition_Phase61_Complete\ai_test_asset_center\enterprise_pilot_runtime.py",
    r"C:\Users\Test\Desktop\QualiBug_AI_Enterprise_Edition_Phase61_Complete\ai_test_asset_center\private_pilot_service.py",
    r"C:\Users\Test\Desktop\QualiBug_AI_Enterprise_Edition_Phase61_Complete\ai_test_asset_center\self_improving_loop.py",
    r"C:\Users\Test\Desktop\QualiBug_AI_Enterprise_Edition_Phase61_Complete\ai_test_asset_center\semantic_diff.py",
]

REPLACES = [
    ('⚙️', '[CONFIG]'),
    ('🔍', '[SCAN]'),
    ('⚡', '[RUN]'),
    ('📊', '[STAT]'),
    ('🔧', '[TOOL]'),
    ('✅', '[OK]'),
    ('📋', '[LIST]'),
    ('🔌', '[CONN]'),
    ('💡', '[TIP]'),
    ('📌', '[PIN]'),
    ('⚠️', '[WARN]'),
    ('⚠', '[WARN]'),
    ('🎯', '[TARGET]'),
]

count = 0
for path in FILES:
    if not os.path.exists(path):
        continue
    content = open(path, encoding='utf-8').read()
    for emoji, ascii in REPLACES:
        if emoji in content:
            content = content.replace(emoji, ascii)
            count += 1
    open(path, 'w', encoding='utf-8').write(content)
    print(f'Fixed: {os.path.basename(path)}')

print(f'Total replacements: {count}')