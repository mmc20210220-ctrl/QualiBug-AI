"""Seal formal results and create archive."""
import json, hashlib, zipfile
from pathlib import Path

# Load results
results = json.loads(Path("project_c_post_tuning_oracle_v1_final.json").read_text(encoding="utf-8"))

# Create seal directory
seal_dir = Path("project_c_post_tuning_seal")
seal_dir.mkdir(exist_ok=True)

# Save individual components
(seal_dir / "formal_run_freeze.json").write_text(json.dumps(results["freeze"], indent=2), encoding="utf-8")
(seal_dir / "formal_findings.json").write_text(json.dumps(results["findings"], indent=2), encoding="utf-8")
(seal_dir / "formal_oracle_traces.json").write_text(json.dumps(results["oracle_stats"], indent=2), encoding="utf-8")
(seal_dir / "formal_reproduction_report.json").write_text(json.dumps(results["reproductions"], indent=2), encoding="utf-8")
(seal_dir / "formal_execution_funnel.json").write_text(json.dumps(results["counters"], indent=2), encoding="utf-8")

# Create zip
zip_path = Path("project_c_post_tuning_oracle_v1_final.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in seal_dir.glob("*.json"):
        zf.write(f, f.name)
    zf.write("project_c_post_tuning_oracle_v1_final.json", "full_results.json")

# Create sha256
sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
Path("project_c_post_tuning_oracle_v1_final.sha256").write_text(f"{sha}  {zip_path.name}\n")

print(f"Sealed: {zip_path.name}")
print(f"SHA256: {sha}")
print(f"Files: {[f.name for f in seal_dir.glob('*.json')]}")
