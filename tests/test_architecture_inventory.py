from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


TEST_IMPORT_TRACE_KEY = "architecture-trace-test-key-0123456789abcdef"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _config(root: Path) -> Path:
    path = root / "architecture_roots.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "qualibug.architecture-roots.v1",
                "package": "samplepkg",
                "supported_roots": {
                    "product": ["samplepkg.main"],
                    "evaluation": ["samplepkg.evaluation"],
                    "tooling": [],
                },
                "module_class_overrides": {
                    "samplepkg.main": "core",
                    "samplepkg.evaluation": "diagnostic",
                },
                "discovery_entrypoints": [
                    {
                        "name": "canonical",
                        "module": "samplepkg.main",
                        "callable": "main",
                        "status": "canonical",
                    }
                ],
                "oversized_line_threshold": 20,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _sample_repo(
    tmp_path: Path,
    *,
    dynamic_import: bool = False,
    external_literal_import: bool = False,
    dependency_cycle: bool = False,
    forbidden_direction: bool = False,
) -> Path:
    _write(tmp_path / "samplepkg" / "__init__.py", "")
    dynamic = (
        "\nimport importlib as loader\n\ndef load(name):\n    return loader.import_module(name)\n"
        if dynamic_import
        else ""
    )
    if external_literal_import:
        dynamic += (
            "\nimport importlib as external_loader\n"
            "EXTERNAL_JSON = external_loader.import_module('json')\n"
        )
    if forbidden_direction:
        dynamic += "\nfrom . import adapter_bridge as forbidden_adapter\n"
    _write(
        tmp_path / "samplepkg" / "main.py",
        (
            "from . import used\n\n"
            "def main():\n    return used.VALUE\n\n"
            "def alternate():\n    return used.VALUE\n"
            + dynamic
        ),
    )
    _write(
        tmp_path / "samplepkg" / "used.py",
        "from . import adapter_bridge\nVALUE = 1\n"
        if dependency_cycle
        else "VALUE = 1\n",
    )
    _write(
        tmp_path / "samplepkg" / "evaluation.py",
        "from .adapter_bridge import evaluate\n",
    )
    _write(
        tmp_path / "samplepkg" / "adapter_bridge.py",
        (
            "from . import used\n\ndef evaluate():\n    return used.VALUE\n"
            if dependency_cycle
            else "def evaluate():\n    return True\n"
        ),
    )
    _write(tmp_path / "samplepkg" / "test_only.py", "VALUE = 2\n")
    _write(tmp_path / "samplepkg" / "dead_module.py", "VALUE = 3\n")
    _write(
        tmp_path / "samplepkg" / "external_root.py",
        "from . import external_dependency\n",
    )
    _write(
        tmp_path / "samplepkg" / "external_dependency.py",
        "VALUE = 4\n",
    )
    _write(
        tmp_path / "support_script.py",
        "from samplepkg import external_root\n",
    )
    _write(
        tmp_path / "tests" / "test_reference.py",
        "from samplepkg import test_only\n",
    )
    _write(
        tmp_path / "pyproject.toml",
        (
            "[project]\nname='sample'\nversion='0.0.0'\n"
            "[project.scripts]\n"
            "sample-main='samplepkg.main:main'\n"
            "sample-alternate='samplepkg.main:alternate'\n"
        ),
    )
    return tmp_path


def _by_module(inventory: dict) -> dict[str, dict]:
    return {row["module"]: row for row in inventory["modules"]}


def _runtime_trace_payload(
    inventory: dict,
    *,
    modules: list[str],
    covered_roots: list[str] | None = None,
    coverage_status: str = "COMPLETE",
) -> dict:
    required_roots = [row["root_id"] for row in inventory["trace_roots"]]
    roots = required_roots if covered_roots is None else covered_roots
    descriptors = {row["root_id"]: row for row in inventory["trace_roots"]}
    return {
        "schema_version": "qualibug.python-import-trace.v1",
        "coverage_status": coverage_status,
        "source_fingerprint": inventory["source_identity"][
            "python_source_fingerprint"
        ],
        "config_fingerprint": inventory["source_identity"][
            "config_fingerprint"
        ],
        "project_scripts_fingerprint": inventory["source_identity"][
            "project_scripts_fingerprint"
        ],
        "collector": {
            "name": "qualibug.import-trace",
            "version": "1",
            "session_id": "trace-session-1",
        },
        "root_sessions": [
            {
                "root_id": root,
                "module": descriptors[root]["module"],
                "callable": descriptors[root]["callable"],
                "status": "COMPLETE",
                "command_fingerprint": f"cmd:{root}",
                "environment_fingerprint": "env:test",
            }
            for root in roots
        ],
        "modules": modules,
    }


def test_inventory_classifies_reachability_without_auto_deleting(tmp_path: Path) -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )

    root = _sample_repo(tmp_path)
    inventory = build_architecture_inventory(
        repo_root=root,
        config_path=_config(root),
    )
    modules = _by_module(inventory)

    assert modules["samplepkg.main"]["reachable_from_product"] is True
    assert modules["samplepkg.used"]["reachable_from_product"] is True
    assert modules["samplepkg.evaluation"]["reachable_from_evaluation"] is True
    assert modules["samplepkg.adapter_bridge"]["responsibility"] == "adapter"
    assert modules["samplepkg.test_only"]["reachable_from_tests"] is True
    assert modules["samplepkg.test_only"]["responsibility"] != "retirement_candidate"
    assert modules["samplepkg.external_root"][
        "reachable_from_external_reference"
    ] is True
    assert modules["samplepkg.external_dependency"][
        "reachable_from_external_reference"
    ] is True
    assert modules["samplepkg.external_dependency"]["responsibility"] != (
        "retirement_candidate"
    )
    assert modules["samplepkg.dead_module"]["responsibility"] == "retirement_candidate"
    assert modules["samplepkg.dead_module"]["removal_gate"] == (
        "BLOCKED_RUNTIME_TRACE_REQUIRED"
    )
    assert inventory["auto_delete_performed"] is False
    assert inventory["quality_claim_status"] == "ARCHITECTURE_DIAGNOSTIC_ONLY"
    assert inventory["external_discovery_quality"] == "NOT_MEASURED"


def test_complete_runtime_trace_advances_but_never_auto_approves_deletion(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )

    root = _sample_repo(tmp_path)
    baseline = build_architecture_inventory(
        repo_root=root,
        config_path=_config(root),
    )
    trace = root / "runtime_trace.json"
    trace.write_text(
        json.dumps(
            _runtime_trace_payload(
                baseline,
                modules=[
                    *[row["module"] for row in baseline["trace_roots"]],
                    "samplepkg.used",
                    "samplepkg.adapter_bridge",
                ],
            )
        ),
        encoding="utf-8",
    )

    inventory = build_architecture_inventory(
        repo_root=root,
        config_path=_config(root),
        runtime_trace_path=trace,
    )
    dead = _by_module(inventory)["samplepkg.dead_module"]

    assert inventory["runtime_trace"]["status"] == "UNAUTHENTICATED_COMPLETE"
    assert inventory["runtime_trace"]["trusted_for_deletion"] is False
    assert dead["runtime_observed"] is False
    assert dead["removal_gate"] == (
        "BLOCKED_RUNTIME_TRACE_AUTHENTICATION_REQUIRED"
    )
    assert inventory["auto_delete_performed"] is False


def test_evaluator_signed_complete_trace_is_trusted_but_still_manual(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )
    from benchmark_evaluator.architecture_import_trace import (
        seal_architecture_import_trace,
    )

    root = _sample_repo(tmp_path)
    config = _config(root)
    baseline = build_architecture_inventory(
        repo_root=root,
        config_path=config,
    )
    payload = _runtime_trace_payload(
        baseline,
        modules=[
            "samplepkg",
            *[row["module"] for row in baseline["trace_roots"]],
        ],
    )
    signed = seal_architecture_import_trace(
        payload,
        signing_key=TEST_IMPORT_TRACE_KEY,
    )
    trace = root / "runtime_trace.signed.json"
    trace.write_text(json.dumps(signed), encoding="utf-8")

    inventory = build_architecture_inventory(
        repo_root=root,
        config_path=config,
        runtime_trace_path=trace,
        runtime_trace_signing_key=TEST_IMPORT_TRACE_KEY,
    )

    assert inventory["runtime_trace"]["status"] == "AUTHENTICATED_COMPLETE"
    assert inventory["runtime_trace"]["trusted_for_deletion"] is True
    assert inventory["runtime_trace"]["authentication"]["status"] == "VERIFIED"
    assert _by_module(inventory)["samplepkg.dead_module"]["removal_gate"] == (
        "MANUAL_DELETION_REVIEW_REQUIRED"
    )
    assert inventory["auto_delete_performed"] is False


def test_signed_runtime_trace_tampering_fails_closed(tmp_path: Path) -> None:
    from ai_test_asset_center.architecture_inventory import (
        ArchitectureInventoryError,
        build_architecture_inventory,
    )
    from benchmark_evaluator.architecture_import_trace import (
        seal_architecture_import_trace,
    )

    root = _sample_repo(tmp_path)
    config = _config(root)
    baseline = build_architecture_inventory(
        repo_root=root,
        config_path=config,
    )
    signed = seal_architecture_import_trace(
        _runtime_trace_payload(
            baseline,
            modules=[
                "samplepkg",
                *[row["module"] for row in baseline["trace_roots"]],
            ],
        ),
        signing_key=TEST_IMPORT_TRACE_KEY,
    )
    signed["modules"].append("samplepkg.unused")
    trace = root / "runtime_trace.tampered.json"
    trace.write_text(json.dumps(signed), encoding="utf-8")

    with pytest.raises(
        ArchitectureInventoryError,
        match="runtime_trace_authentication_invalid",
    ):
        build_architecture_inventory(
            repo_root=root,
            config_path=config,
            runtime_trace_path=trace,
            runtime_trace_signing_key=TEST_IMPORT_TRACE_KEY,
        )


def test_dynamic_import_uncertainty_blocks_retirement_even_with_complete_trace(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )

    root = _sample_repo(tmp_path, dynamic_import=True)
    baseline = build_architecture_inventory(
        repo_root=root,
        config_path=_config(root),
    )
    trace = root / "runtime_trace.json"
    trace.write_text(
        json.dumps(
            _runtime_trace_payload(
                baseline,
                modules=[
                    row["module"] for row in baseline["trace_roots"]
                ],
            )
        ),
        encoding="utf-8",
    )

    inventory = build_architecture_inventory(
        repo_root=root,
        config_path=_config(root),
        runtime_trace_path=trace,
    )

    assert inventory["dynamic_import_uncertainty"]["present"] is True
    assert _by_module(inventory)["samplepkg.dead_module"]["removal_gate"] == (
        "BLOCKED_DYNAMIC_IMPORT_REVIEW_REQUIRED"
    )


def test_literal_external_import_does_not_create_project_deletion_uncertainty(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )

    root = _sample_repo(tmp_path, external_literal_import=True)
    inventory = build_architecture_inventory(
        repo_root=root,
        config_path=_config(root),
    )

    assert inventory["dynamic_import_uncertainty"]["present"] is False
    assert _by_module(inventory)["samplepkg.main"][
        "dynamic_import_uncertainty"
    ] == []


def test_inventory_reports_cycles_hubs_and_forbidden_dependency_directions(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )

    root = _sample_repo(
        tmp_path,
        dependency_cycle=True,
        forbidden_direction=True,
    )
    inventory = build_architecture_inventory(
        repo_root=root,
        config_path=_config(root),
    )
    graph = inventory["dependency_graph"]

    assert graph["cyclic_scc_count"] == 1
    assert graph["largest_cyclic_scc_size"] == 2
    assert graph["cyclic_sccs"][0]["modules"] == [
        "samplepkg.adapter_bridge",
        "samplepkg.used",
    ]
    assert any(
        row["source"] == "samplepkg.main"
        and row["target"] == "samplepkg.adapter_bridge"
        for row in graph["forbidden_dependency_directions"]
    )
    assert graph["fan_out_hubs"][0]["count"] >= 1


def test_complete_runtime_trace_fails_closed_without_exact_root_and_source_proof(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.architecture_inventory import (
        ArchitectureInventoryError,
        build_architecture_inventory,
    )

    root = _sample_repo(tmp_path)
    config = _config(root)
    baseline = build_architecture_inventory(repo_root=root, config_path=config)
    trace = root / "runtime_trace.json"
    trace.write_text(
        json.dumps(
            _runtime_trace_payload(
                baseline,
                modules=[],
                covered_roots=[],
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ArchitectureInventoryError,
        match="runtime_trace_root_coverage_missing",
    ):
        build_architecture_inventory(
            repo_root=root,
            config_path=config,
            runtime_trace_path=trace,
        )

    payload = _runtime_trace_payload(
        baseline,
        modules=[
            row["module"] for row in baseline["trace_roots"]
        ],
    )
    payload["source_fingerprint"] = "stale-source"
    trace.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        ArchitectureInventoryError,
        match="runtime_trace_source_fingerprint_mismatch",
    ):
        build_architecture_inventory(
            repo_root=root,
            config_path=config,
            runtime_trace_path=trace,
        )


def test_configuration_schema_fails_fast_for_malformed_authority(tmp_path: Path) -> None:
    from ai_test_asset_center.architecture_inventory import (
        ArchitectureInventoryError,
        build_architecture_inventory,
    )

    root = _sample_repo(tmp_path)
    config = _config(root)
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["module_class_overrides"] = []
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        ArchitectureInventoryError,
        match="architecture_module_class_overrides_invalid",
    ):
        build_architecture_inventory(repo_root=root, config_path=config)

    payload["module_class_overrides"] = {}
    payload["discovery_entrypoints"] = [
        {
            "name": "canonical",
            "module": "samplepkg.main",
            "callable": "missing_callable",
            "status": "canonical",
        }
    ]
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        ArchitectureInventoryError,
        match="architecture_entrypoint_callable_missing",
    ):
        build_architecture_inventory(repo_root=root, config_path=config)


def test_windows_ignore_names_are_case_insensitive(tmp_path: Path) -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )

    root = _sample_repo(tmp_path)
    _write(root / "VENV" / "broken.py", "not valid python [")
    inventory = build_architecture_inventory(
        repo_root=root,
        config_path=_config(root),
    )
    assert inventory["diagnostics"]["module_count"] >= 1


def test_gitignored_python_scratch_files_are_not_architecture_modules(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )

    root = _sample_repo(tmp_path)
    (root / ".gitignore").write_text("_tmp_*.py\n", encoding="utf-8")
    (root / "_tmp_old.py").write_bytes(b"\xff\xfeignored scratch")
    initialized = subprocess.run(
        ["git", "init", "--quiet"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert initialized.returncode == 0, initialized.stderr

    inventory = build_architecture_inventory(
        repo_root=root,
        config_path=_config(root),
    )

    assert "_tmp_old" not in {
        row["module"] for row in inventory["modules"]
    }


def test_trace_roots_preserve_distinct_script_callable_identities(
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )

    root = _sample_repo(tmp_path)
    inventory = build_architecture_inventory(
        repo_root=root,
        config_path=_config(root),
    )
    scripts = [
        row for row in inventory["trace_roots"]
        if row["category"] == "project_script"
    ]
    assert len(scripts) == 2
    assert {row["module"] for row in scripts} == {"samplepkg.main"}
    assert {row["callable"] for row in scripts} == {"main", "alternate"}
    assert len({row["root_id"] for row in scripts}) == 2
    assert len(inventory["source_identity"]["project_scripts_fingerprint"]) == 64


def test_module_graph_and_source_hash_use_one_byte_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center.architecture_inventory import _parse_module

    path = tmp_path / "samplepkg" / "main.py"
    original_source = b"VALUE = 1\n"
    changed_source = b"VALUE = 2\nEXTRA = True\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(original_source)
    original_read_text = Path.read_text

    def mutate_after_text_read(self: Path, *args: object, **kwargs: object) -> str:
        text = original_read_text(self, *args, **kwargs)
        if self == path:
            self.write_bytes(changed_source)
        return text

    monkeypatch.setattr(Path, "read_text", mutate_after_text_read)
    parsed = _parse_module(
        module="samplepkg.main",
        path=path,
        known_modules={"samplepkg.main"},
    )

    assert parsed["line_count"] == 1
    assert parsed["source_sha256"] == hashlib.sha256(original_source).hexdigest()


def test_inventory_cli_persists_diagnostics_without_mutating_sources(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    root = _sample_repo(tmp_path)
    config = _config(root)
    output = root / "inventory.json"
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*.py")
    }

    completed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "tools" / "architecture_inventory.py"),
            "--root",
            str(root),
            "--config",
            str(config),
            "--output",
            str(output),
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "qualibug.architecture-inventory.v1"
    assert payload["auto_delete_performed"] is False
    assert payload["source_identity"]["scope"] == "WORKTREE_CONTENT_ADDRESSED"
    assert len(payload["source_identity"]["python_source_fingerprint"]) == 64
    assert len(payload["source_identity"]["config_fingerprint"]) == 64
    assert all(len(row["source_sha256"]) == 64 for row in payload["modules"])
    summary = json.loads(completed.stdout)
    assert summary["output"] == str(output.resolve())
    assert summary["runtime_trace"]["missing_required_root_count"] >= 1
    assert "missing_required_roots" not in summary["runtime_trace"]
    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*.py")
    }
    assert after == before


def test_inventory_cli_returns_structured_nonzero_failure(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    root = _sample_repo(tmp_path)
    config = _config(root)
    config.write_text('{"schema_version":', encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "tools" / "architecture_inventory.py"),
            "--root",
            str(root),
            "--config",
            str(config),
            "--output",
            str(root / "inventory.json"),
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    failure = json.loads(completed.stderr)
    assert failure["schema_version"] == (
        "qualibug.architecture-inventory-error.v1"
    )
    assert failure["status"] == "FAILED"
    assert "architecture_roots_invalid" in failure["detail"]


def test_external_seal_cli_produces_inventory_trusted_trace(tmp_path: Path) -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )

    repository_root = Path(__file__).resolve().parents[1]
    product = _sample_repo(tmp_path / "product")
    config = _config(product)
    baseline = build_architecture_inventory(
        repo_root=product,
        config_path=config,
    )
    evaluator = tmp_path / "evaluator"
    evaluator.mkdir()
    observed = evaluator / "observed.json"
    observed.write_text(
        json.dumps(
            _runtime_trace_payload(
                baseline,
                modules=[
                    "samplepkg",
                    *[row["module"] for row in baseline["trace_roots"]],
                ],
            )
        ),
        encoding="utf-8",
    )
    key = evaluator / "trace.key"
    key.write_bytes(TEST_IMPORT_TRACE_KEY.encode("utf-8"))
    signed = evaluator / "runtime_trace.signed.json"

    sealed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "tools" / "seal_architecture_import_trace.py"),
            "--input",
            str(observed),
            "--output",
            str(signed),
            "--key-file",
            str(key),
            "--product-workspace",
            str(product),
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert sealed.returncode == 0, sealed.stderr
    assert json.loads(sealed.stdout)["status"] == "SEALED"

    output = product / "inventory.json"
    inventoried = subprocess.run(
        [
            sys.executable,
            str(repository_root / "tools" / "architecture_inventory.py"),
            "--root",
            str(product),
            "--config",
            str(config),
            "--runtime-trace",
            str(signed),
            "--evaluator-key-file",
            str(key),
            "--output",
            str(output),
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert inventoried.returncode == 0, inventoried.stderr
    summary = json.loads(inventoried.stdout)
    assert summary["runtime_trace"]["authentication"]["status"] == "VERIFIED"
    assert summary["runtime_trace"]["trusted_for_deletion"] is True
    inventory = json.loads(output.read_text(encoding="utf-8"))
    assert inventory["runtime_trace"]["status"] == "AUTHENTICATED_COMPLETE"
    assert inventory["runtime_trace"]["trusted_for_deletion"] is True
    assert inventory["auto_delete_performed"] is False


def test_inventory_cli_rejects_product_owned_evaluator_key(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    product = _sample_repo(tmp_path / "product")
    config = _config(product)
    key = product / "trace.key"
    key.write_bytes(TEST_IMPORT_TRACE_KEY.encode("utf-8"))

    completed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "tools" / "architecture_inventory.py"),
            "--root",
            str(product),
            "--config",
            str(config),
            "--evaluator-key-file",
            str(key),
            "--output",
            str(product / "inventory.json"),
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    failure = json.loads(completed.stderr)
    assert failure["status"] == "FAILED"
    assert failure["detail"] == (
        "evaluator_key_file_must_be_outside_product_workspace"
    )


def test_repository_inventory_reports_architecture_metrics_not_quality() -> None:
    from ai_test_asset_center.architecture_inventory import (
        build_architecture_inventory,
    )

    root = Path(__file__).resolve().parents[1]
    inventory = build_architecture_inventory(
        repo_root=root,
        config_path=root / "ai_test_asset_center" / "architecture_roots.json",
    )

    diagnostics = inventory["diagnostics"]
    assert diagnostics["module_count"] > 0
    assert diagnostics["python_line_count"] > 0
    assert diagnostics["architecture_budget"]["status"] in {
        "WITHIN_BUDGET",
        "OVER_BUDGET",
    }
    budget = diagnostics["architecture_budget"]
    if budget["status"] == "WITHIN_BUDGET":
        assert diagnostics["module_count"] <= budget["max_module_count"]
        assert diagnostics["python_line_count"] <= budget[
            "max_python_line_count"
        ]
    else:
        assert budget["exceeded_metrics"]
        assert (
            diagnostics["module_count"] > budget["max_module_count"]
            or diagnostics["python_line_count"]
            > budget["max_python_line_count"]
        )
    assert diagnostics["discovery_entrypoint_count"] >= 1
    assert "duplicate_discovery_entrypoint_count" in diagnostics
    assert "monkeypatch_authority_count" in diagnostics
    assert "oversized_boundary_count" in diagnostics
    assert inventory["dependency_graph"]["largest_cyclic_scc_size"] < 64
    assert inventory["dependency_graph"]["cyclic_scc_count"] == len(
        inventory["dependency_graph"]["cyclic_sccs"]
    )
    assert inventory["auto_delete_performed"] is False
    assert inventory["external_discovery_quality"] == "NOT_MEASURED"
