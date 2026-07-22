"""
app/llm/factory.py

Provider factory for LLM implementations.

Reads the LLM_PROVIDER setting and returns the appropriate LLMProvider
instance, fully configured from Settings.

Extending with a new provider
-----------------------------
1. Implement LLMProvider in a new module, e.g. app/llm/gemini_provider.py
2. Add a branch in get_llm_provider() below.
3. Update LLM_PROVIDER in .env.example.

No other files need to change.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)


def get_llm_provider(settings: Settings) -> LLMProvider:
    """
    Return a fully configured LLMProvider based on ``settings.LLM_PROVIDER``.

    Args:
        settings: Application settings instance (from ``get_settings()``).

    Returns:
        A concrete LLMProvider ready to call.

    Raises:
        ValueError: If ``LLM_PROVIDER`` names an unsupported provider.
        ValueError: If required credentials for the selected provider are
                    missing from settings (e.g. ``OPENAI_API_KEY`` is None).
    """
    provider_name = (settings.LLM_PROVIDER or "openai").lower().strip()

    if provider_name == "openai":
        return _make_openai_provider(settings)

    # ── Future providers (not yet implemented) ────────────────────────────────
    # elif provider_name == "gemini":
    #     return _make_gemini_provider(settings)
    # elif provider_name == "claude":
    #     return _make_claude_provider(settings)
    # elif provider_name == "azure_openai":
    #     return _make_azure_openai_provider(settings)

    raise ValueError(
        f"Unsupported LLM_PROVIDER={provider_name!r}. "
        "Supported values: openai"
    )


def _make_openai_provider(settings: Settings) -> "LLMProvider":
    """Construct an OpenAIProvider from settings."""
    from app.llm.openai_provider import OpenAIProvider  # local import avoids circular

    if not settings.OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY is not set. "
            "Add it to .env to enable JD analysis."
        )

    logger.debug(
        "LLM factory: creating OpenAIProvider model=%s timeout=%ss max_retries=%d",
        settings.OPENAI_MODEL,
        settings.OPENAI_REQUEST_TIMEOUT,
        settings.LLM_MAX_RETRIES,
    )

    return OpenAIProvider(
        api_key=     settings.OPENAI_API_KEY,
        model=       settings.OPENAI_MODEL,
        timeout=     settings.OPENAI_REQUEST_TIMEOUT,
        max_tokens=  settings.LLM_MAX_TOKENS,
        max_retries= settings.LLM_MAX_RETRIES,
    )
