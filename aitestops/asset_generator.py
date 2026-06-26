from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from aitestops.asset_semantic_guard import AssetSemanticGuard
from aitestops.dsl_to_pytest import DslToPytestGenerator
from aitestops.hybrid_ai_engine import EngineMode, HybridAIEngine
from aitestops.yaml_writer import dump_yaml


class AssetGenerator:
    """Generate controlled testing assets from requirement text."""

    def __init__(self, ai_engine: HybridAIEngine | None = None, engine_mode: EngineMode = "auto"):
        self.ai_engine = ai_engine or HybridAIEngine(mode=engine_mode)
        self.pytest_generator = DslToPytestGenerator()
        self.semantic_guard = AssetSemanticGuard()

    def generate_from_file(self, requirement_path: Path, out_dir: Path) -> Dict[str, Any]:
        requirement_text = requirement_path.read_text(encoding="utf-8")
        return self.generate(requirement_text=requirement_text, out_dir=out_dir, source_name=requirement_path.name)

    def generate(self, requirement_text: str, out_dir: Path, source_name: str = "requirement.md") -> Dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        audit_dir = out_dir / "ai_audit_logs"

        if isinstance(self.ai_engine, HybridAIEngine):
            self.ai_engine.audit_dir = audit_dir

        bundle = self.ai_engine.generate_bundle(requirement_text)
        analysis = bundle["analysis"]
        test_cases = bundle["test_cases"]
        data_profiles = bundle["test_data_profiles"]
        dsl_cases = bundle["test_dsl"]
        dsl_cases, semantic_notes = self.semantic_guard.normalize_dsl(data_profiles, dsl_cases)
        pytest_code = self.pytest_generator.render(dsl_cases)

        self._write_json(out_dir / "analysis.json", analysis)
        self._write_json(out_dir / "risks.json", analysis["risks"])
        self._write_json(out_dir / "test_cases.json", test_cases)
        self._write_json(out_dir / "test_data_profiles.json", data_profiles)
        (out_dir / "test_dsl.yaml").write_text(dump_yaml(dsl_cases), encoding="utf-8")
        (out_dir / "generated_pytest_test.py").write_text(pytest_code, encoding="utf-8")
        self._write_json(out_dir / "semantic_notes.json", semantic_notes)
        (out_dir / "generation_summary.md").write_text(
            self._summary(source_name, analysis, test_cases, data_profiles, dsl_cases, semantic_notes),
            encoding="utf-8",
        )

        engine_used = getattr(self.ai_engine, "last_engine_used", "unknown")
        fallback_reason = getattr(self.ai_engine, "last_error", None)
        meta = {
            "source": source_name,
            "engine_used": engine_used,
            "fallback_reason": fallback_reason,
            "out_dir": str(out_dir),
            "risk_count": len(analysis["risks"]),
            "test_case_count": len(test_cases),
            "data_profile_count": len(data_profiles),
            "dsl_case_count": len(dsl_cases),
            "schema_validation": "passed",
            "semantic_guard": "applied",
            "semantic_note_count": len(semantic_notes),
            "files": [
                "analysis.json",
                "risks.json",
                "test_cases.json",
                "test_data_profiles.json",
                "test_dsl.yaml",
                "generated_pytest_test.py",
                "generation_summary.md",
                "semantic_notes.json",
                "ai_audit_logs/",
            ],
        }
        self._write_json(out_dir / "generation_meta.json", meta)
        return meta

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _summary(
        source_name: str,
        analysis: Dict[str, Any],
        test_cases: list[dict],
        data_profiles: dict,
        dsl_cases: list[dict],
        semantic_notes: list[str],
    ) -> str:
        risks = "\n".join(f"- [{r['priority']}] {r['risk']}: {r['reason']}" for r in analysis["risks"])
        cases = "\n".join(f"- {c['case_id']} {c['title']} ({c['priority']}, {c['type']})" for c in test_cases)
        profiles = "\n".join(
            f"- {name}: role={value.get('role')}, status={value.get('status')}, strategy={value.get('create_strategy')}, privacy={value.get('privacy_level')}"
            for name, value in data_profiles.items()
        )
        semantic_text = "\n".join(f"- {note}" for note in semantic_notes) if semantic_notes else "- No semantic fixes required."
        return f"""# AI Test Asset Generation Summary

## Source

{source_name}

## Risks

{risks}

## Generated Test Cases

{cases}

## Test Data Profiles

{profiles}

## Automation DSL

{len(dsl_cases)} DSL cases generated.

## Schema Guard and Semantic Guard

Schema validation passed. Semantic guard result:

{semantic_text}

## Enterprise Control

The LLM or local engine only creates structured assets. Python tests are generated later by a template engine, so executable code remains auditable and deterministic.
"""
