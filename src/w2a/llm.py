"""Single call-site LLM provider wrapper.

Used by the converter pipeline (Phase 2+) and copied into generated crews'
config.py (Phase 3+) so both sides go through the same retry/fallback/logging
path. Gemini 2.5 Flash (AI Studio free tier) is primary; Groq free tier is the
fallback on 429/5xx or any Gemini failure.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_GEMINI_MODEL = "gemini-flash-lite-latest"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
TOKEN_LOG_PATH = Path(os.environ.get("W2A_TOKEN_LOG", "logs/llm_calls.jsonl"))


class LLMError(Exception):
    """Raised when both primary and fallback providers fail, or retries are exhausted."""


class LLMResponseError(LLMError):
    """Raised when structured output could not be parsed into the response_model after retries."""

    def __init__(self, message: str, raw_output: str):
        super().__init__(message)
        self.raw_output = raw_output


def _log_call(provider: str, model: str, prompt_chars: int, response_chars: int, ok: bool, error: str | None = None) -> None:
    TOKEN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "provider": provider,
        "model": model,
        "prompt_chars": prompt_chars,
        "response_chars": response_chars,
        "ok": ok,
        "error": error,
    }
    with TOKEN_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


class LLM:
    """call(prompt, response_model=None) -> str | BaseModel

    Gemini primary, Groq fallback on 429/5xx/timeout/any exception.
    If response_model is given, the raw text is parsed as JSON and validated;
    on failure the validation error is appended to the prompt and re-asked,
    up to max_retries times, before raising LLMResponseError.
    """

    def __init__(
        self,
        gemini_model: str = DEFAULT_GEMINI_MODEL,
        groq_model: str = DEFAULT_GROQ_MODEL,
        timeout: float = 60.0,
        max_retries: int = 2,
    ):
        self.gemini_model = gemini_model
        self.groq_model = groq_model
        self.timeout = timeout
        self.max_retries = max_retries
        self._gemini_client = None
        self._groq_client = None

    # -- provider clients, built lazily so missing keys don't break import --

    def _get_gemini(self):
        if self._gemini_client is None:
            from google import genai

            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise LLMError("GEMINI_API_KEY not set")
            self._gemini_client = genai.Client(api_key=api_key)
        return self._gemini_client

    def _get_groq(self):
        if self._groq_client is None:
            from groq import Groq

            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise LLMError("GROQ_API_KEY not set")
            self._groq_client = Groq(api_key=api_key, timeout=self.timeout)
        return self._groq_client

    # -- raw single-shot calls per provider --

    def _call_gemini_raw(self, prompt: str, json_mode: bool) -> str:
        from google.genai import types

        client = self._get_gemini()
        config = types.GenerateContentConfig(
            response_mime_type="application/json" if json_mode else "text/plain",
            http_options=types.HttpOptions(timeout=int(self.timeout * 1000)),
        )
        result = client.models.generate_content(
            model=self.gemini_model, contents=prompt, config=config
        )
        return result.text

    def _call_groq_raw(self, prompt: str, json_mode: bool) -> str:
        client = self._get_groq()
        kw = {}
        if json_mode:
            kw["response_format"] = {"type": "json_object"}
        completion = client.chat.completions.create(
            model=self.groq_model,
            messages=[{"role": "user", "content": prompt}],
            **kw,
        )
        return completion.choices[0].message.content

    def _call_raw(self, prompt: str, json_mode: bool) -> str:
        """Try Gemini, fall back to Groq on any failure. No retries at this layer."""
        try:
            text = self._call_gemini_raw(prompt, json_mode)
            _log_call("gemini", self.gemini_model, len(prompt), len(text), ok=True)
            return text
        except Exception as gemini_exc:  # noqa: BLE001 - deliberately broad, this is the fallback trigger
            logger.warning("Gemini call failed (%s), falling back to Groq", gemini_exc)
            _log_call("gemini", self.gemini_model, len(prompt), 0, ok=False, error=str(gemini_exc))
            try:
                text = self._call_groq_raw(prompt, json_mode)
                _log_call("groq", self.groq_model, len(prompt), len(text), ok=True)
                return text
            except Exception as groq_exc:  # noqa: BLE001
                _log_call("groq", self.groq_model, len(prompt), 0, ok=False, error=str(groq_exc))
                raise LLMError(
                    f"Both providers failed. Gemini: {gemini_exc!r}. Groq: {groq_exc!r}."
                ) from groq_exc

    def call(self, prompt: str, response_model: type[T] | None = None, **_kw) -> "str | T":
        json_mode = response_model is not None
        last_raw = ""
        last_error: Exception | None = None

        attempts = self.max_retries + 1
        current_prompt = prompt
        for attempt in range(attempts):
            raw = self._call_raw(current_prompt, json_mode)
            last_raw = raw
            if response_model is None:
                return raw
            try:
                data = json.loads(raw)
                return response_model.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                logger.warning("Structured output parse failed (attempt %d/%d): %s", attempt + 1, attempts, exc)
                current_prompt = (
                    f"{prompt}\n\n"
                    f"Your previous output failed validation with this error:\n{exc}\n\n"
                    f"Your previous output was:\n{raw}\n\n"
                    "Return ONLY corrected JSON matching the schema."
                )

        debug_dir = Path("generated/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_file = debug_dir / f"failed_output_{int(time.time())}.txt"
        debug_file.write_text(last_raw, encoding="utf-8")
        raise LLMResponseError(
            f"Structured output failed after {attempts} attempts: {last_error}. Raw output saved to {debug_file}.",
            raw_output=last_raw,
        )
