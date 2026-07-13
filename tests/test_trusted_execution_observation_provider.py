from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_test_asset_center.trusted_execution_observation_provider import (
    TRUSTED_OBSERVATION_PACK_SCHEMA,
    TrustedObservationDirectoryProvider,
    TrustedObservationProviderError,
    seal_trusted_observation_pack,
)


SIGNING_KEY = "trusted-observation-gateway-key-0123456789"


def _fingerprint(value: dict) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _context() -> dict:
    return {
        "campaign_id": "campaign:one",
        "runtime_view": {
            "target": {
                "target_id": "target-1",
                "runtime": {"environment_ref": "http://127.0.0.1:8011"},
            }
        },
        "scan_output": {
            "run_id": "run-1",
            "process_boundary": {"result_fingerprint": "b" * 64},
        },
    }


def _write_pack(root: Path, *, campaign_id: str = "campaign:one") -> Path:
    label = "campaign_one"
    digest = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:16]
    path = root / f"{label}.{digest}.json"
    unsigned = {
        "schema_version": TRUSTED_OBSERVATION_PACK_SCHEMA,
        "campaign_id": campaign_id,
        "run_id": "run-1",
        "target_id": "target-1",
        "environment_id": "http://127.0.0.1:8011",
        "process_result_fingerprint": "b" * 64,
        "observations": [{"source_kind": "evaluator_http_proxy"}],
    }
    path.write_text(
        json.dumps(
            seal_trusted_observation_pack(
                unsigned,
                signing_key=SIGNING_KEY,
            )
        ),
        encoding="utf-8",
    )
    return path


def test_provider_reads_only_identity_bound_external_gateway_pack(
    tmp_path: Path,
) -> None:
    product = tmp_path / "product"
    trusted = tmp_path / "trusted"
    product.mkdir()
    trusted.mkdir()
    _write_pack(trusted)
    provider = TrustedObservationDirectoryProvider(
        observation_root=trusted,
        product_workspace_root=product,
        verification_key=SIGNING_KEY,
    )

    observations = provider(**_context())

    assert observations == [{"source_kind": "evaluator_http_proxy"}]


def test_provider_rejects_observation_directory_inside_product_workspace(
    tmp_path: Path,
) -> None:
    product = tmp_path / "product"
    trusted = product / "trusted"
    trusted.mkdir(parents=True)

    with pytest.raises(
        TrustedObservationProviderError,
        match="outside_product_workspace",
    ):
        TrustedObservationDirectoryProvider(
            observation_root=trusted,
            product_workspace_root=product,
            verification_key=SIGNING_KEY,
        )


def test_provider_rejects_pack_bound_to_another_process_result(
    tmp_path: Path,
) -> None:
    product = tmp_path / "product"
    trusted = tmp_path / "trusted"
    product.mkdir()
    trusted.mkdir()
    path = _write_pack(trusted)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["process_result_fingerprint"] = "c" * 64
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"pack_fingerprint", "pack_authentication"}
    }
    payload = seal_trusted_observation_pack(
        unsigned,
        signing_key=SIGNING_KEY,
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    provider = TrustedObservationDirectoryProvider(
        observation_root=trusted,
        product_workspace_root=product,
        verification_key=SIGNING_KEY,
    )

    with pytest.raises(
        TrustedObservationProviderError,
        match="process_result_fingerprint_mismatch",
    ):
        provider(**_context())


def test_provider_rejects_product_forged_pack_with_recomputed_public_hash(
    tmp_path: Path,
) -> None:
    product = tmp_path / "product"
    trusted = tmp_path / "trusted"
    product.mkdir()
    trusted.mkdir()
    path = _write_pack(trusted)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["observations"] = [{"source_kind": "product_forged_proxy"}]
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"pack_fingerprint", "pack_authentication"}
    }
    payload["pack_fingerprint"] = _fingerprint(unsigned)
    path.write_text(json.dumps(payload), encoding="utf-8")
    provider = TrustedObservationDirectoryProvider(
        observation_root=trusted,
        product_workspace_root=product,
        verification_key=SIGNING_KEY,
    )

    with pytest.raises(
        TrustedObservationProviderError,
        match="authentication",
    ):
        provider(**_context())
