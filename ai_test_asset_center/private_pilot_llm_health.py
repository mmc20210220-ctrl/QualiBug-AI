"""LLM health helpers for PrivatePilotHandler."""
from __future__ import annotations

import os
from typing import Any

class LlmHealthMixin:
    def _llm_available(self) -> bool:
        return self._llm_health()["available"]

    def _llm_health(self) -> dict[str, Any]:
        try:
            from .llm_reasoning import ReasoningConfig
            cfg = ReasoningConfig.from_env()
            configured = cfg.enabled
        except Exception as exc:
            return {"configured": False, "available": False, "status": "failed", "label": "Failed", "error": str(exc)[:160]}
        if not configured:
            return {"configured": False, "available": False, "status": "offline", "label": "Not configured"}
        forced_status = os.environ.get("QUALIBUG_LLM_HEALTH_STATUS", "").strip().lower()
        if forced_status in {"online", "failed"}:
            return {"configured": True, "available": forced_status == "online", "status": forced_status, "label": "Verified online" if forced_status == "online" else "Verification failed"}
        last_status = os.environ.get("QUALIBUG_LLM_LAST_HEALTH_STATUS", "").strip().lower()
        if last_status in {"online", "failed"}:
            return {
                "configured": True,
                "available": last_status == "online",
                "status": last_status,
                "label": os.environ.get("QUALIBUG_LLM_LAST_HEALTH_LABEL", "Verified online" if last_status == "online" else "Verification failed"),
                "error": os.environ.get("QUALIBUG_LLM_LAST_HEALTH_ERROR", ""),
            }
        return self._verify_llm_connectivity()

    def _verify_llm_connectivity(self) -> dict[str, Any]:
        try:
            from .llm_reasoning import ReasoningClient, ReasoningConfig

            cfg = ReasoningConfig.from_env()
            if not cfg.enabled:
                result = {"configured": False, "available": False, "status": "offline", "label": "Not configured", "error": "Missing LLM_BASE_URL, LLM_API_KEY or LLM_MODEL."}
            else:
                cfg.timeout_seconds = int(os.environ.get("LLM_HEALTH_TIMEOUT_SECONDS", "15"))
                client = ReasoningClient(cfg)
                client.health_check()
                result = {"configured": True, "available": True, "status": "online", "label": "Verified online"}
        except Exception as exc:
            result = {"configured": True, "available": False, "status": "failed", "label": "Verification failed", "error": str(exc)[:300]}
        os.environ["QUALIBUG_LLM_LAST_HEALTH_STATUS"] = str(result["status"])
        os.environ["QUALIBUG_LLM_LAST_HEALTH_LABEL"] = str(result["label"])
        if result.get("error"):
            os.environ["QUALIBUG_LLM_LAST_HEALTH_ERROR"] = str(result["error"])
        else:
            os.environ.pop("QUALIBUG_LLM_LAST_HEALTH_ERROR", None)
        return result
