"""Retry decorator for Google API calls with exponential backoff + jitter.

Retries on:
    - HTTP 429 (rate limit)
    - HTTP 5xx (transient server errors)
    - socket errors / timeouts

Does NOT retry on:
    - 4xx other than 429 (your input is wrong, retrying won't help)
    - AuthError (token broken — fail fast so user can fix)

Usage:
    @with_retry
    async def my_api_call(...): ...
"""

from __future__ import annotations

import asyncio
import random
from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

from googleapiclient.errors import HttpError

import config
from logging_util import log

T = TypeVar("T")


def _should_retry(e: Exception) -> bool:
    if isinstance(e, HttpError):
        status = e.resp.status if e.resp is not None else 0
        return status == 429 or 500 <= status < 600
    # Network-ish errors. Don't over-match — only obvious transient stuff.
    if isinstance(e, (TimeoutError, ConnectionError)):
        return True
    return False


def retry_call(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Sync retry helper. Call a synchronous function with the same backoff policy.

    Example:
        resp = retry_call(lambda: svc.users().messages().list(userId="me", q=q).execute())
    """
    import time

    settings = config.retry_settings()
    max_attempts: int = int(settings.get("max_attempts", 4))
    initial: float = float(settings.get("initial_backoff_seconds", 1.0))
    cap: float = float(settings.get("max_backoff_seconds", 30.0))

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if not _should_retry(e) or attempt == max_attempts:
                raise
            delay = min(cap, initial * (2 ** (attempt - 1)))
            delay = random.uniform(0, delay)
            log.warning(
                "retry_call (attempt %d/%d) after %.2fs: %s",
                attempt,
                max_attempts,
                delay,
                e,
            )
            time.sleep(delay)
            last_exc = e
    assert last_exc is not None
    raise last_exc


def with_retry(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Async decorator. Reads settings from config.retry_settings() at call time."""

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        settings = config.retry_settings()
        max_attempts: int = int(settings.get("max_attempts", 4))
        initial: float = float(settings.get("initial_backoff_seconds", 1.0))
        cap: float = float(settings.get("max_backoff_seconds", 30.0))

        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                if not _should_retry(e) or attempt == max_attempts:
                    raise
                # Exponential backoff with jitter: sleep in [0, min(cap, initial * 2**(n-1))].
                delay = min(cap, initial * (2 ** (attempt - 1)))
                delay = random.uniform(0, delay)
                log.warning(
                    "retrying %s (attempt %d/%d) after %.2fs: %s",
                    fn.__name__,
                    attempt,
                    max_attempts,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)
                last_exc = e

        # Unreachable — loop either returns or raises — but satisfy type checker.
        assert last_exc is not None
        raise last_exc

    return wrapper
