"""
app/llm/base.py

Abstract base class for LLM provider implementations.

Design constraints
------------------
- Synchronous interface only: RQ workers run outside the FastAPI asyncio
  event loop, so async clients cannot be used.  This mirrors the existing
  PyMongo-not-Motor pattern in email_tasks.py and resume_tasks.py.
- Provider-agnostic: callers depend only on this interface.  OpenAI, Gemini,
  Claude, Azure OpenAI are all concrete subclasses — adding one requires
  implementing this ABC and registering it in factory.py.
- No FastAPI / Motor imports: this module must be importable by RQ workers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """
    Abstract interface for a synchronous LLM chat completion provider.

    All implementations must be synchronous — RQ worker threads have no
    asyncio event loop.

    Contract
    --------
    ``complete_sync`` receives a structured system prompt and user content,
    calls the underlying LLM, and returns a parsed Python dict.  Retry
    logic, timeout handling, and token accounting are the responsibility of
    the concrete implementation.

    Raises
    ------
    LLMProviderError
        Raised by implementations after all retry attempts are exhausted or
        on unrecoverable errors (e.g. invalid API key).  Callers should
        catch this to set ``jd_analysis.status = "failed"``.
    """

    @abstractmethod
    def complete_sync(self, system_prompt: str, user_content: str) -> dict:
        """
        Execute a synchronous chat completion and return the result as a dict.

        Args:
            system_prompt: The system-role message (your analysis instructions).
            user_content:  The user-role message (the JD text to analyze).

        Returns:
            A Python dict parsed from the LLM response.  The exact schema is
            determined by the prompt — callers treat this as opaque and store
            it verbatim in ``jd_analysis.result``.

        Raises:
            LLMProviderError: After retry exhaustion or unrecoverable failure.
        """
        ...


class LLMProviderError(Exception):
    """
    Raised by LLMProvider implementations when a completion cannot be
    delivered after all retry attempts.

    Attributes
    ----------
    message : str
        Human-readable description of the failure.
    original_exc : Exception | None
        The underlying exception that caused the failure, if available.
    """

    def __init__(self, message: str, original_exc: Exception | None = None) -> None:
        super().__init__(message)
        self.original_exc = original_exc
