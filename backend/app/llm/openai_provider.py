"""
app/llm/openai_provider.py

Synchronous OpenAI chat completion provider.

Uses ``openai.OpenAI`` (the synchronous client) — NOT ``AsyncOpenAI`` —
because RQ workers have no asyncio event loop.  This follows the same
PyMongo-not-Motor convention used throughout the worker layer.

Retry strategy
--------------
tenacity retries on:
  - openai.RateLimitError         (HTTP 429)
  - openai.APITimeoutError        (request timed out)
  - openai.APIConnectionError     (network failure)
  - openai.InternalServerError    (HTTP 500/503)

Non-retried errors (re-raise immediately):
  - openai.AuthenticationError    (bad API key — not transient)
  - openai.BadRequestError        (malformed request — not transient)

Observability
-------------
Every completion logs: provider, model, prompt_tokens, completion_tokens,
total_tokens, and wall-clock latency.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import openai
import tenacity

from app.llm.base import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)

# ── Transient error types that warrant a retry ────────────────────────────────
_RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


class OpenAIProvider(LLMProvider):
    """
    Synchronous OpenAI chat completion provider with retry and observability.

    Args:
        api_key:     OpenAI secret key (``sk-...``).
        model:       Completion model, e.g. ``"gpt-4o-mini"``.
        timeout:     Per-request timeout in seconds.
        max_tokens:  Upper bound on completion tokens.
        max_retries: Maximum number of retry attempts (tenacity).
    """

    def __init__(
        self,
        *,
        api_key:     str,
        model:       str   = "gpt-4o-mini",
        timeout:     float = 30.0,
        max_tokens:  int   = 2000,
        max_retries: int   = 3,
    ) -> None:
        self._model       = model
        self._max_tokens  = max_tokens
        self._max_retries = max_retries
        self._client      = openai.OpenAI(api_key=api_key, timeout=timeout)

    # ── Public interface ──────────────────────────────────────────────────────

    def complete_sync(self, system_prompt: str, user_content: str) -> dict:
        """
        Call OpenAI chat completions and return a parsed dict.

        The LLM is instructed to respond with valid JSON via ``response_format``.
        The raw JSON string is parsed and returned as a Python dict.

        Args:
            system_prompt: Analysis instructions (your prompt).
            user_content:  The job description text to analyze.

        Returns:
            dict parsed from the LLM JSON response.

        Raises:
            LLMProviderError: After max retries or on a non-retryable error.
        """
        try:
            return self._complete_with_retry(system_prompt, user_content)
        except LLMProviderError:
            raise
        except Exception as exc:
            # Catch-all safety net — should not normally be reached.
            raise LLMProviderError(
                f"Unexpected error from OpenAI provider: {exc}", original_exc=exc
            ) from exc

    # ── Internal ──────────────────────────────────────────────────────────────

    def _complete_with_retry(self, system_prompt: str, user_content: str) -> dict:
        """Wrap ``_call_api`` with tenacity retry."""

        @tenacity.retry(
            retry=tenacity.retry_if_exception_type(_RETRYABLE),
            wait=tenacity.wait_exponential(multiplier=1, min=2, max=30),
            stop=tenacity.stop_after_attempt(self._max_retries),
            reraise=False,
            before_sleep=lambda rs: logger.warning(
                "LLM transient error — retrying (attempt %d/%d): %s",
                rs.attempt_number, self._max_retries, rs.outcome.exception(),
            ),
        )
        def _inner() -> dict:
            return self._call_api(system_prompt, user_content)

        try:
            return _inner()
        except _RETRYABLE as exc:
            raise LLMProviderError(
                f"OpenAI call failed after {self._max_retries} retries: {exc}",
                original_exc=exc,
            ) from exc
        except (openai.AuthenticationError, openai.BadRequestError) as exc:
            # Non-retryable — surface immediately.
            raise LLMProviderError(
                f"OpenAI non-retryable error: {exc}", original_exc=exc
            ) from exc

    def _call_api(self, system_prompt: str, user_content: str) -> dict[str, Any]:
        """Execute one OpenAI chat completion call."""
        t0 = time.monotonic()

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ],
            max_tokens=self._max_tokens,
            response_format={"type": "json_object"},
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        usage      = response.usage

        logger.info(
            "event=llm.completion_done model=%s "
            "prompt_tokens=%d completion_tokens=%d total_tokens=%d latency_ms=%d",
            self._model,
            usage.prompt_tokens     if usage else -1,
            usage.completion_tokens if usage else -1,
            usage.total_tokens      if usage else -1,
            latency_ms,
        )

        raw: str = response.choices[0].message.content or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                f"LLM returned non-JSON content: {raw[:200]!r}", original_exc=exc
            ) from exc
