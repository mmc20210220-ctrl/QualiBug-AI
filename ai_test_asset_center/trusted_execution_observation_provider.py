from __future__ import annotations

"""Evaluator-side loader for independently captured target-I/O observations."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .evaluator_receipt_auth import (
    EvaluatorReceiptAuthError,
    resolve_evaluator_hmac_keyring,
    seal_evaluator_artifact,
    verify_evaluator_artifact,
)


TRUSTED_OBSERVATION_PACK_SCHEMA = (
    "qualibug.trusted-execution-observation-pack.v2"
)
TRUSTED_OBSERVATION_PACK_FINGERPRINT_FIELD = "pack_fingerprint"
TRUSTED_OBSERVATION_PACK_AUTHENTICATION_FIELD = "pack_authentication"
_TRUSTED_OBSERVATION_UNSIGNED_FIELDS = {
    "schema_version",
    "campaign_id",
    "run_id",
    "target_id",
    "environment_id",
    "process_result_fingerprint",
    "observations",
}


class TrustedObservationProviderError(ValueError):
    """The external gateway observation pack is missing or inconsistent."""


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_campaign_name(campaign_id: str) -> str:
    value = str(campaign_id or "").strip()
    if not value:
        raise TrustedObservationProviderError("campaign_id_missing")
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:80].strip("._")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{label or 'campaign'}.{digest}.json"


def seal_trusted_observation_pack(
    payload: dict[str, Any],
    *,
    signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Seal a gateway-created observation pack with evaluator-owned HMAC."""

    if not isinstance(payload, dict) or set(payload) != _TRUSTED_OBSERVATION_UNSIGNED_FIELDS:
        raise TrustedObservationProviderError(
            "trusted_observation_pack_unsigned_fields_invalid"
        )
    if payload.get("schema_version") != TRUSTED_OBSERVATION_PACK_SCHEMA:
        raise TrustedObservationProviderError(
            "trusted_observation_pack_schema_invalid"
        )
    try:
        return seal_evaluator_artifact(
            payload,
            signing_key=signing_key,
            domain=TRUSTED_OBSERVATION_PACK_SCHEMA,
            fingerprint_field=TRUSTED_OBSERVATION_PACK_FINGERPRINT_FIELD,
            authentication_field=TRUSTED_OBSERVATION_PACK_AUTHENTICATION_FIELD,
        )
    except EvaluatorReceiptAuthError as exc:
        raise TrustedObservationProviderError(
            f"trusted_observation_pack_authentication_failed:{exc}"
        ) from exc


class TrustedObservationDirectoryProvider:
    """Load one atomic, evaluator-owned gateway pack for each completed scan.

    The root must be outside the product workspace. The product child never
    receives this path or the evaluator signing key.
    """

    def __init__(
        self,
        *,
        observation_root: Path | str,
        product_workspace_root: Path | str,
        verification_key: str | bytes | bytearray | None = None,
    ) -> None:
        self.observation_root = Path(observation_root).resolve()
        self.product_workspace_root = Path(product_workspace_root).resolve()
        try:
            self.observation_root.relative_to(self.product_workspace_root)
        except ValueError:
            pass
        else:
            raise TrustedObservationProviderError(
                "trusted_observation_root_must_be_outside_product_workspace"
            )
        if not self.observation_root.is_dir():
            raise TrustedObservationProviderError(
                f"trusted_observation_root_not_found:{self.observation_root}"
            )
        try:
            resolve_evaluator_hmac_keyring(verification_key)
        except EvaluatorReceiptAuthError as exc:
            raise TrustedObservationProviderError(
                f"trusted_observation_verification_key_invalid:{exc}"
            ) from exc
        self.verification_key = verification_key

    def __call__(self, **kwargs: Any) -> list[dict[str, Any]]:
        scan_output = kwargs.get("scan_output")
        runtime_view = kwargs.get("runtime_view")
        if not isinstance(scan_output, dict) or not isinstance(runtime_view, dict):
            raise TrustedObservationProviderError(
                "trusted_observation_context_invalid"
            )
        campaign_id = str(kwargs.get("campaign_id") or "").strip()
        path = self.observation_root / _safe_campaign_name(campaign_id)
        if not path.is_file():
            raise TrustedObservationProviderError(
                f"trusted_observation_pack_missing:{path}"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrustedObservationProviderError(
                f"trusted_observation_pack_invalid:{path}:{exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise TrustedObservationProviderError(
                "trusted_observation_pack_must_be_object"
            )
        expected_fields = _TRUSTED_OBSERVATION_UNSIGNED_FIELDS | {
            TRUSTED_OBSERVATION_PACK_FINGERPRINT_FIELD,
            TRUSTED_OBSERVATION_PACK_AUTHENTICATION_FIELD,
        }
        if set(payload) != expected_fields:
            raise TrustedObservationProviderError(
                "trusted_observation_pack_fields_invalid"
            )
        if payload.get("schema_version") != TRUSTED_OBSERVATION_PACK_SCHEMA:
            raise TrustedObservationProviderError(
                "trusted_observation_pack_schema_invalid"
            )
        try:
            verify_evaluator_artifact(
                payload,
                signing_key=self.verification_key,
                domain=TRUSTED_OBSERVATION_PACK_SCHEMA,
                fingerprint_field=TRUSTED_OBSERVATION_PACK_FINGERPRINT_FIELD,
                authentication_field=TRUSTED_OBSERVATION_PACK_AUTHENTICATION_FIELD,
            )
        except EvaluatorReceiptAuthError as exc:
            raise TrustedObservationProviderError(
                f"trusted_observation_pack_authentication_invalid:{exc}"
            ) from exc
        target = runtime_view.get("target")
        target = target if isinstance(target, dict) else {}
        runtime = target.get("runtime")
        runtime = runtime if isinstance(runtime, dict) else {}
        boundary = scan_output.get("process_boundary")
        boundary = boundary if isinstance(boundary, dict) else {}
        expected = {
            "campaign_id": campaign_id,
            "run_id": str(scan_output.get("run_id") or "").strip(),
            "target_id": str(target.get("target_id") or "").strip(),
            "environment_id": str(runtime.get("environment_ref") or "")
            .strip()
            .rstrip("/"),
            "process_result_fingerprint": str(
                boundary.get("result_fingerprint") or ""
            ).strip(),
        }
        for field, value in expected.items():
            if payload.get(field) != value:
                raise TrustedObservationProviderError(
                    f"trusted_observation_pack_{field}_mismatch"
                )
        observations = payload.get("observations")
        if not isinstance(observations, list) or any(
            not isinstance(row, dict) for row in observations
        ):
            raise TrustedObservationProviderError(
                "trusted_observation_pack_observations_invalid"
            )
        return [dict(row) for row in observations]
