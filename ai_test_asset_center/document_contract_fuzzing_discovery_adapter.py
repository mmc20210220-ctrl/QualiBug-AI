from __future__ import annotations

from pathlib import Path
from typing import Any

from .defect_signal_schema import normalize_defect_signal
from .document_contract_fuzzing import compile_document_contracts
from .real_project_onboarding import config_paths


def _pick_api_markdown(input_dir: Path) -> Path | None:
    candidates = [path for path in input_dir.glob("*.md") if path.name.lower() not in {"prd.md", "readme.md"}]
    if not candidates:
        return None

    def api_score(path: Path) -> tuple[int, int]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return (sum(text.upper().count(method) for method in ("GET", "POST", "PUT", "PATCH", "DELETE")), len(text))

    api_path = max(candidates, key=api_score)
    return api_path if api_score(api_path)[0] > 0 else None


def generate_document_contract_fuzzing_probes(
    project_id: str,
    root: Path | None = None,
    *,
    max_count: int = 24,
) -> list[dict[str, Any]]:
    root = root or Path(".").resolve()
    paths = config_paths(project_id, root)
    input_dir = paths["input_dir"]
    prd_path = input_dir / "prd.md"
    prd_text = prd_path.read_text(encoding="utf-8", errors="replace") if prd_path.exists() else ""
    api_path = _pick_api_markdown(input_dir)
    if not api_path:
        return []
    api_text = api_path.read_text(encoding="utf-8", errors="replace")
    compiled = compile_document_contracts(prd_text, api_text)

    probes: list[dict[str, Any]] = []
    for contract in compiled.get("contracts") or []:
        if not isinstance(contract, dict):
            continue
        if len(probes) >= max_count:
            break
        method = str(contract.get("method") or "POST").upper()
        path = str(contract.get("path") or "")
        kind = str(contract.get("kind") or "document_business_constraint")
        severity = str(contract.get("severity") or "P2")
        title = str(contract.get("title") or f"{method} {path} 文档约束验证")[:500]
        probes.append(
            normalize_defect_signal(
                {
                    "probe_id": f"DOC_FUZZ_{len(probes)+1:04d}",
                    "title": f"文档约束模糊验证：{title}",
                    "defect_family": "api_contract",
                    "risk_type": kind,
                    "severity": severity,
                    "source": "document_contract_fuzzing",
                    "method": method,
                    "path": path,
                    "expected": str(contract.get("expected") or "文档约束应被系统拒绝或满足"),
                    "actual": "待在可销毁沙箱中执行验证",
                    "status": "planned_probe",
                    "confidence": 0.32,
                    "execution_policy": "candidate_only",
                    "evidence": {
                        "project_id": project_id,
                        "document_source": api_path.name,
                        "contract_id": contract.get("contract_id"),
                        "kind": kind,
                        "execution_policy": str(contract.get("execution_policy") or "sandbox_required"),
                        "mutation": contract.get("mutation") or {},
                        "sample_body": contract.get("sample_body") or {},
                    },
                },
                signal_kind="probe",
                default_source="document_contract_fuzzing",
                default_status="planned_probe",
                default_confidence=0.32,
            )
        )
    return probes

