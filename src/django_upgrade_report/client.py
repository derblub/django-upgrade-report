"""A small HTTP client for JSON APIs: retries, a disk cache, and no credentials in errors."""

from __future__ import annotations

import base64
import email.utils
import hashlib
import http.client
import json
import math
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django_upgrade_report import __version__

USER_AGENT = (
    f"django-upgrade-report/{__version__} (+https://github.com/derblub/django-upgrade-report)"
)
ATTEMPTS = 4
MAX_RETRY_AFTER = 30.0
MAX_CONNECTIONS = 8
"""Requests in flight at once per client, however many threads ask."""
_NOT_FOUND = {"not_found": True}
"""What the disk cache holds for a 404, so offline runs know it, too."""
ONLINE, PREFER_CACHE, OFFLINE = "online", "prefer-cache", "offline"
"""How a client uses its disk cache: fresh answers only, any answer before asking the
network, or never the network at all."""


class FetchError(OSError):
    """The server could not be reached or did not answer usefully, even after retrying.

    The message never contains credentials from the base URL.
    """


class NotCached(FetchError):
    """Offline, and the answer is not in the disk cache."""


class UnexpectedAnswer(ValueError):
    """Valid JSON, but not what the API sends. Retrying does not help."""


class JsonClient:
    """GET requests for JSON, with retries, an in-memory and an optional disk cache.

    Subclasses decide what a valid answer is (:meth:`_validate`), how long a cached answer
    stays fresh (:meth:`_ttl`) and what of it is worth caching (:meth:`_slim`).
    """

    hint = ""
    """Appended to errors about answers that are not what the API sends."""
    cache_format = ""
    """Part of every cache key: change it when :meth:`_slim` keeps something new."""

    def __init__(
        self,
        base_url: str,
        cache_dir: Path | None = None,
        timeout: float = 20,
        headers: dict[str, str] | None = None,
        connections: int = MAX_CONNECTIONS,
        mode: str = ONLINE,
    ):
        self.base_url, authorization, self._secrets = _split_credentials(base_url.rstrip("/"))
        self.cache_dir = cache_dir
        self.mode = mode
        self.oldest_cached: float | None = None
        """When the oldest answer read from the disk cache was stored (a timestamp)."""
        self.timeout = timeout
        self._headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if authorization:
            self._headers["Authorization"] = authorization
        self._headers.update(headers or {})
        self._memory: dict[str, object] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(connections)

    # --- what subclasses decide

    def _validate(self, data: object) -> None:
        """Raise :class:`UnexpectedAnswer` for an answer that would crash later."""
        if data is None:  # ``None`` means "not found" to the callers
            raise UnexpectedAnswer("unexpected answer, null")

    def _ttl(self, url: str) -> float | None:
        """Seconds a cached answer for ``url`` stays fresh; ``None`` means for good."""
        return 24 * 3600

    def _slim(self, data: object) -> object:
        """What of an answer is kept, in memory and on disk. Runs once per fetched answer."""
        return data

    # --- requests

    def _get(self, url: str) -> object | None:
        """The answer for ``url``, ``None`` for a 404. Once read, an answer is kept in memory."""
        with self._lock:
            if url in self._memory:
                return self._memory[url]
        data = self._read_cache(url, any_age=self.mode != ONLINE)
        if data is None:
            if self.mode == OFFLINE:
                raise NotCached(self.redact(f"could not fetch {url}: not in the cache"))
            data = self._fetch(url)
            if data is not None:
                data = self._slim(data)  # so a cold cache answers like a warm one
            self._write_cache(url, _NOT_FOUND if data is None else data)
        elif data == _NOT_FOUND:
            data = None
        with self._lock:
            self._memory[url] = data
        return data

    def _fetch(self, url: str) -> object | None:
        request = self._request(url)
        for attempt in range(1, ATTEMPTS + 1):
            delay = None
            try:
                with self._slots:
                    return self._fetch_once(request)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                problem = f"HTTP {exc.code} {exc.reason}"
                if exc.code == 403 and _rate_limited(exc.headers):
                    wait = _retry_after(exc.headers, limit=None) or _rate_limit_reset(exc.headers)
                    if wait is not None and wait > MAX_RETRY_AFTER:  # retrying cannot help
                        raise self._error(url, f"{problem}, rate limit exceeded") from None
                    delay = wait or None  # no wait at all would ask again at once
                elif exc.code == 429 or exc.code >= 500:
                    delay = _retry_after(exc.headers)
                else:
                    raise self._error(url, problem) from None
            except (OSError, http.client.HTTPException) as exc:
                if isinstance(exc, http.client.InvalidURL):
                    raise self._error(url, "invalid URL") from None
                problem = _describe(exc)
            except UnexpectedAnswer as exc:
                raise self._error(url, f"{exc}{self.hint}") from None
            except ValueError:
                problem = f"the answer is not JSON{self.hint}"
            if attempt == ATTEMPTS:
                raise self._error(url, f"{problem} (tried {ATTEMPTS} times)") from None
            time.sleep(delay if delay is not None else 2 ** (attempt - 1) + random.random())
        return None

    def _fetch_once(self, request: urllib.request.Request) -> object:
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read())
        self._validate(data)
        return data

    def _request(self, url: str) -> urllib.request.Request:
        try:
            return urllib.request.Request(url, headers=self._headers)
        except ValueError:
            raise self._error(url, "not an http(s) URL") from None

    def _error(self, url: str, problem: str) -> FetchError:
        return FetchError(self.redact(f"could not fetch {url}: {problem}"))

    def redact(self, text: str) -> str:
        """``text`` without the credentials of the base URL."""
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    # --- disk cache

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        key = f"{self.cache_format} {url}" if self.cache_format else url
        return self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def _read_cache(self, url: str, any_age: bool = False) -> object | None:
        path = self._cache_path(url)
        if path is None or not path.is_file():
            return None
        try:
            stored = path.stat().st_mtime
            ttl = self._ttl(url)
            if not any_age and ttl is not None and time.time() - stored > ttl:
                return None
            data = json.loads(path.read_text())
            if data != _NOT_FOUND:
                self._validate(data)  # a cache entry from another version, or a damaged one
        except (OSError, ValueError):
            return None
        with self._lock:
            if self.oldest_cached is None or stored < self.oldest_cached:
                self.oldest_cached = stored
        return data

    def _write_cache(self, url: str, data: object) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except OSError:
            pass


def _split_credentials(url: str) -> tuple[str, str | None, tuple[str, ...]]:
    """Move ``user:token@`` out of the URL, into a Basic auth header, like pip does.

    Returns the URL without credentials, the header value and the strings to redact.
    """
    parts = urllib.parse.urlsplit(url)
    userinfo, at, host = parts.netloc.rpartition("@")
    if not at:
        return url, None, ()
    user, _, password = userinfo.partition(":")
    plain_user, plain_password = urllib.parse.unquote(user), urllib.parse.unquote(password)
    token = base64.b64encode(f"{plain_user}:{plain_password}".encode()).decode()
    clean = urllib.parse.urlunsplit(parts._replace(netloc=host))
    # ``https://TOKEN@host`` carries the secret in the user part.
    secret = (password, plain_password) if password else (user, plain_user)
    secrets = sorted({userinfo, token, *secret} - {""}, key=len, reverse=True)
    return clean, f"Basic {token}", tuple(secrets)


def _retry_after(headers, limit: float | None = MAX_RETRY_AFTER) -> float | None:
    value = headers.get("Retry-After") if headers else None
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = when.timestamp() - time.time()
    if not math.isfinite(seconds):
        return None
    seconds = max(seconds, 0.0)
    return seconds if limit is None else min(seconds, limit)


def _rate_limited(headers) -> bool:
    """Whether a 403 means "slow down" (GitHub) rather than "not allowed".

    The primary rate limit says so with ``X-RateLimit-Remaining: 0``, the secondary one with
    ``Retry-After``.
    """
    return bool(headers) and (
        headers.get("X-RateLimit-Remaining") == "0" or bool(headers.get("Retry-After"))
    )


def _rate_limit_reset(headers) -> float | None:
    """Seconds until ``X-RateLimit-Reset``; ``None`` when unknown or already passed."""
    try:
        seconds = float(headers.get("X-RateLimit-Reset", "")) - time.time()
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _describe(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.URLError):
        if not isinstance(exc.reason, BaseException):
            return str(exc.reason)
        exc = exc.reason
    if isinstance(exc, TimeoutError):
        return "timed out"
    if isinstance(exc, http.client.IncompleteRead):
        return "the connection closed before the answer was complete"
    if isinstance(exc, ConnectionError):
        return f"connection lost ({type(exc).__name__})"
    return str(exc) or type(exc).__name__
