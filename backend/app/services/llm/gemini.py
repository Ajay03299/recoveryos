"""Google Gemini provider over the REST API (httpx — no extra SDK)."""
import json
from typing import TypeVar

import httpx
from pydantic import BaseModel

from app.core.config import settings
from app.services.llm import LLMError, LLMProvider
from app.services.llm.schema import to_gemini_schema

T = TypeVar("T", bound=BaseModel)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT = 25.0


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        self.model = settings.LLM_MODEL or "gemini-2.5-flash"
        self.api_key = settings.GEMINI_API_KEY
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY is not configured.")

    def _generate(self, system: str, prompt: str, schema: type[T]) -> T:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(schema),
            },
        }
        url = f"{BASE_URL}/{self.model}:generateContent"

        try:
            response = httpx.post(
                url, json=body, timeout=TIMEOUT,
                headers={"x-goog-api-key": self.api_key},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"Gemini returned {exc.response.status_code}: "
                f"{exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini request failed: {exc}") from exc

        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected Gemini response shape: {str(data)[:200]}") from exc

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Gemini did not return valid JSON: {text[:200]}") from exc

        return schema.model_validate(payload)
