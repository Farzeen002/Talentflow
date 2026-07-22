"""
app/llm/__init__.py

Public surface of the LLM module.

Import from here to stay decoupled from internal file structure.
"""

from app.llm.base import LLMProvider, LLMProviderError
from app.llm.factory import get_llm_provider
from app.llm.jd_analyzer import analyze_jd

__all__ = [
    "LLMProvider",
    "LLMProviderError",
    "get_llm_provider",
    "analyze_jd",
]
