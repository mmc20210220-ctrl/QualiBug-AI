from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from aitestops.env_loader import load_dotenv


class LLMClientError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.1
    timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> "LLMConfig":
        load_dotenv()
        provider = os.getenv("LLM_PROVIDER", "openai_compatible")
        base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        api_key = os.getenv("LLM_API_KEY", "")
        model = os.getenv("LLM_MODEL", "")
        temperature = float(os.getenv("LLM_TEMPERATURE", "0.1"))
        timeout_seconds = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
        return cls(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


class OpenAICompatibleLLMClient:
    """Minimal OpenAI-compatible Chat Completions client using stdlib only."""

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig.from_env()

    def complete_json(self, system_prompt: str, user_prompt: str, audit_dir: Path | None = None) -> Dict[str, Any]:
        if not self.config.enabled:
            raise LLMClientError("LLM is not configured. Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL.")

        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
        }

        # Some OpenAI-compatible services support this. Services that do not support it may ignore it or return an error.
        if os.getenv("LLM_USE_JSON_RESPONSE_FORMAT", "1") == "1":
            payload["response_format"] = {"type": "json_object"}

        self._audit(audit_dir, "request", payload)
        response_text = self._post_json(payload)
        self._audit(audit_dir, "response_raw", {"text": response_text})

        data = self._extract_message_json(response_text)
        self._audit(audit_dir, "response_json", data)
        return data

    def _post_json(self, payload: Dict[str, Any]) -> str:
        url = f"{self.config.base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise LLMClientError(f"LLM HTTP {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise LLMClientError(f"LLM network error: {exc}") from exc

    def _extract_message_json(self, response_text: str) -> Dict[str, Any]:
        try:
            raw = json.loads(response_text)
            content = raw["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LLMClientError(f"Unexpected LLM response envelope: {exc}") from exc

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Basic repair for models that wrap JSON in ```json fences.
            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                cleaned = cleaned.replace("json\n", "", 1).replace("JSON\n", "", 1).strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as exc:
                raise LLMClientError(f"LLM content is not valid JSON: {content[:500]}") from exc

    def _audit(self, audit_dir: Path | None, name: str, payload: Dict[str, Any]) -> None:
        if audit_dir is None:
            return
        audit_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        path = audit_dir / f"{timestamp}_{name}.json"
        # Mask API key if present in request-like payloads.
        safe_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")
