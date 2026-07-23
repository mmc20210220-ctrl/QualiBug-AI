"""Convert OpenAPI 3.1 to 3.0 compatible format for QualiBug parser.

This is INPUT PREPARATION, not Project C code modification.
The original openapi.yaml remains unchanged.
"""
import re
from pathlib import Path

INPUT = Path("projects/contractflow_c/input/openapi.yaml")
OUTPUT = Path("projects/contractflow_c/input/openapi_compat.yaml")

content = INPUT.read_text(encoding="utf-8")

# Convert version
content = content.replace("openapi: 3.1.0", "openapi: 3.0.3")

# Convert type: [string, 'null'] to type: string + nullable: true
# Pattern: type: [string, 'null'] or type: [string, "null"]
def convert_nullable_type(match):
    indent = match.group(1)
    type_name = match.group(2)
    return f"{indent}type: {type_name}\n{indent}nullable: true"

# Handle various nullable type patterns
patterns = [
    (r"(\s*)type:\s*\[string,\s*['\"]null['\"]\]", r"\1type: string\n\1nullable: true"),
    (r"(\s*)type:\s*\[number,\s*['\"]null['\"]\]", r"\1type: number\n\1nullable: true"),
    (r"(\s*)type:\s*\[integer,\s*['\"]null['\"]\]", r"\1type: integer\n\1nullable: true"),
    (r"(\s*)type:\s*\[object,\s*['\"]null['\"]\]", r"\1type: object\n\1nullable: true"),
]

for pattern, replacement in patterns:
    content = re.sub(pattern, replacement, content)

# Also handle inline format like {type: [string, 'null']}
inline_patterns = [
    (r"\{type:\s*\[string,\s*['\"]null['\"]\]\}", "{type: string, nullable: true}"),
    (r"\{type:\s*\[number,\s*['\"]null['\"]\]\}", "{type: number, nullable: true}"),
    (r"\{type:\s*\[integer,\s*['\"]null['\"]\]\}", "{type: integer, nullable: true}"),
]

for pattern, replacement in inline_patterns:
    content = re.sub(pattern, replacement, content)

OUTPUT.write_text(content, encoding="utf-8")
print(f"Converted: {INPUT} -> {OUTPUT}")
print(f"Original size: {INPUT.stat().st_size:,} bytes")
print(f"Converted size: {OUTPUT.stat().st_size:,} bytes")

# Verify no remaining array types
remaining = re.findall(r"type:\s*\[", content)
print(f"Remaining array types: {len(remaining)}")
if remaining:
    for m in remaining[:5]:
        print(f"  {m}")
