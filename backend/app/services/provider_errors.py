"""
app/services/provider_errors.py

Shared base exceptions for all email provider service implementations.

Purpose
-------
Allows ``ingestion_service.py`` to catch provider-agnostic auth and
rate-limit errors without accumulating a growing tuple of provider-specific
exception classes as new providers are added.

Inheritance pattern
-------------------
Each provider's exception classes use multiple inheritance to remain
catchable by both provider-specific handlers (existing tests / internal
code) and the provider-agnostic ingestion layer:

    # In gmail_service.py:
    class GmailAuthError(GmailServiceError, EmailProviderAuthError): ...
    class GmailRateLimitError(GmailServiceError, EmailProviderRateLimitError): ...

    # In outlook_service.py:
    class OutlookAuthError(OutlookServiceError, EmailProviderAuthError): ...
    class OutlookRateLimitError(OutlookServiceError, EmailProviderRateLimitError): ...

Usage in ingestion_service.py
------------------------------
    except EmailProviderAuthError as exc:
        # Catches GmailAuthError AND OutlookAuthError — no change needed
        # when a third provider is added.
        logger.error(...)

    except EmailProviderRateLimitError as exc:
        # Catches GmailRateLimitError AND OutlookRateLimitError.
        logger.warning(...)

Extensibility
-------------
This file never changes when a new provider is added.
Only the new provider's exception classes need to inherit from these bases.
"""


class EmailProviderError(Exception):
    """Base exception for all email provider errors."""


class EmailProviderAuthError(EmailProviderError):
    """
    Authentication cannot be recovered by token refresh.

    The recruiter must re-authenticate via the OAuth login flow.
    Raised by any provider when a 401 persists after the single refresh
    attempt, or when the refresh token itself is expired/revoked.
    """


class EmailProviderRateLimitError(EmailProviderError):
    """
    Provider API rate limit exceeded.

    The ingestion layer should log the event, skip this recruiter's
    batch, and retry on the next scheduled cycle.  Raised by any
    provider when an HTTP 429 response is received and the retry
    budget is exhausted.
    """
