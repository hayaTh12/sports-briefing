"""
Thin HTTP wrapper around httpx with structured error handling.

All league fetchers that hit REST APIs use ``get_json`` so error-handling
and logging are consistent across the codebase.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


def get_json(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    params: Optional[dict[str, Any]] = None,
    timeout: int = 30,
) -> Optional[Any]:
    """Perform a GET request and return the parsed JSON body.

    Returns ``None`` on any error (network failure, non-2xx status, invalid
    JSON) so callers can treat ``None`` as "data unavailable" without
    wrapping every call in try/except.

    Args:
        url:     Full URL to request.
        headers: Optional request headers.
        params:  Optional query parameters.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON value, or ``None`` on failure.
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers or {}, params=params or {})
            response.raise_for_status()
            return response.json()

    except httpx.HTTPStatusError as exc:
        logger.error(
            "HTTP %s for %s — %s",
            exc.response.status_code,
            url,
            exc.response.text[:200],
        )
    except httpx.TimeoutException:
        logger.error("Request timed out after %ss: %s", timeout, url)
    except httpx.RequestError as exc:
        logger.error("Network error for %s: %s", url, exc)
    except (ValueError, Exception) as exc:
        logger.error("Unexpected error fetching %s: %s", url, exc)

    return None
