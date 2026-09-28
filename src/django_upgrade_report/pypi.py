"""A small, cached client for the PyPI JSON API."""

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from packaging.version import InvalidVersion, Version

from django_upgrade_report import __version__

USER_AGENT = (
    f"django-upgrade-report/{__version__} (+https://github.com/derblub/django-upgrade-report)"
)
ATTEMPTS = 4
MAX_RETRY_AFTER = 30.0
MAX_CONNECTIONS = 8
"""Requests in flight at once, however many threads ask."""


class PyPIError(OSError):
    """The package index could not be reached or did not answer usefully, even after retrying.

    The message never contains credentials from the index URL.
    """


@dataclass(frozen=True)
class Release:
    version: Version
    uploaded: datetime | None
    """The earliest upload time of any of the release's files."""
    yanked: bool
    has_files: bool = True
    """Releases without files cannot be installed."""


@dataclass(frozen=True)
class ReleaseInfo:
    """The metadata of one release that matters for Django compatibility."""

    name: str
    version: str
    classifiers: tuple[str, ...]
    requires_dist: tuple[str, ...]
    requires_python: str | None


@dataclass(frozen=True)
class Project:
    name: str
    latest: ReleaseInfo
    releases: tuple[Release, ...]
    """All releases, oldest first."""

    def stable_releases(self) -> list[Release]:
        return [
            r for r in self.releases if r.has_files and not r.yanked and not r.version.is_prerelease
        ]


class PyPI:
    def __init__(
        self,
        index_url: str = "https://pypi.org/pypi",
        cache_dir: Path | None = None,
        cache_ttl: float = 24 * 3600,
        timeout: float = 20,
    ):
        self.index_url, self._authorization, self._secrets = _split_credentials(
            index_url.rstrip("/")
        )
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl
        self.timeout = timeout
        self._memory: dict[str, dict | None] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def project(self, name: str) -> Project | None:
        data = self._get(f"{self.index_url}/{name}/json")
        if data is None:
            return None
        releases = []
        for raw_version, files in data.get("releases", {}).items():
            try:
                version = Version(raw_version)
            except InvalidVersion:
                continue
            uploads = [
                t for f in files if isinstance(t := f.get("upload_time_iso_8601"), str) and t
            ]
            uploaded = _timestamp(min(uploads)) if uploads else None
            yanked = bool(files) and all(f.get("yanked") for f in files)
            releases.append(Release(version, uploaded, yanked, has_files=bool(files)))
        releases.sort(key=lambda r: r.version)
        return Project(data["info"]["name"], _release_info(data), tuple(releases))

    def release(self, name: str, version: str) -> ReleaseInfo | None:
        data = self._get(f"{self.index_url}/{name}/{version}/json")
        return _release_info(data) if data is not None else None

    def _get(self, url: str) -> dict | None:
        with self._lock:
            if url in self._memory:
                return self._memory[url]
        data = self._read_cache(url)
        if data is None:
            data = self._fetch(url)
            if data is not None:
                self._write_cache(url, data)
        with self._lock:
            self._memory[url] = data
        return data

    def _fetch(self, url: str) -> dict | None:
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
                if exc.code != 429 and exc.code < 500:
                    raise self._error(url, problem) from None
                delay = _retry_after(exc.headers)
            except (OSError, http.client.HTTPException) as exc:
                if isinstance(exc, http.client.InvalidURL):
                    raise self._error(url, "invalid URL") from None
                problem = _describe(exc)
            except _UnexpectedAnswer as exc:
                raise self._error(url, f"{exc} (is this a PyPI JSON API URL?)") from None
            except ValueError:
                problem = "the answer is not JSON (is this a PyPI JSON API URL?)"
            if attempt == ATTEMPTS:
                raise self._error(url, f"{problem} (tried {ATTEMPTS} times)") from None
            time.sleep(delay if delay is not None else 2 ** (attempt - 1) + random.random())
        return None

    def _fetch_once(self, request: urllib.request.Request) -> dict:
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read())
        if not isinstance(data, dict):
            raise _UnexpectedAnswer("unexpected answer, not a JSON object")
        _validate(data)
        return data

    def _request(self, url: str) -> urllib.request.Request:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self._authorization:
            headers["Authorization"] = self._authorization
        try:
            return urllib.request.Request(url, headers=headers)
        except ValueError:
            raise self._error(url, "not an http(s) URL") from None

    def _error(self, url: str, problem: str) -> PyPIError:
        return PyPIError(self.redact(f"could not fetch {url}: {problem}"))

    def redact(self, text: str) -> str:
        """``text`` without the credentials of the index URL."""
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def _read_cache(self, url: str) -> dict | None:
        path = self._cache_path(url)
        if path is None or not path.is_file():
            return None
        # Metadata of a specific release never changes, the project index does.
        is_release = url.count("/") > f"{self.index_url}/x/json".count("/")
        if not is_release and time.time() - path.stat().st_mtime > self.cache_ttl:
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def _write_cache(self, url: str, data: dict) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            # Only the fields we use, the full release list can be megabytes.
            tmp.write_text(json.dumps(_slim(data)))
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


class _UnexpectedAnswer(ValueError):
    """Valid JSON, but not what the PyPI JSON API sends. Retrying does not help."""


def _validate(data: dict) -> None:
    """Reject answers that would crash later, e.g. an error object from a proxy."""
    info = data.get("info")
    if not isinstance(info, dict) or not isinstance(info.get("name"), str):
        raise _UnexpectedAnswer("unexpected answer, no package info")
    if not isinstance(info.get("version"), str):
        raise _UnexpectedAnswer("unexpected answer, no version")
    releases = data.get("releases", {})
    if not isinstance(releases, dict) or not all(
        isinstance(files, list) and all(isinstance(f, dict) for f in files)
        for files in releases.values()
    ):
        raise _UnexpectedAnswer("unexpected answer, malformed release list")


def _timestamp(text: str) -> datetime | None:
    """An upload time; mirrors that leave out the time zone mean UTC, like PyPI."""
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def _retry_after(headers) -> float | None:
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
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


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


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "django-upgrade-report"


def _release_info(data: dict) -> ReleaseInfo:
    info = data["info"]
    return ReleaseInfo(
        name=info["name"],
        version=info["version"],
        classifiers=_strings(info.get("classifiers")),
        requires_dist=_strings(info.get("requires_dist")),
        requires_python=_string(info.get("requires_python")),
    )


def _strings(value: object) -> tuple[str, ...]:
    """A list of strings from index metadata; anything else in it is ignored."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _slim(data: dict) -> dict:
    info = data["info"]
    return {
        "info": {
            key: info.get(key)
            for key in ("name", "version", "classifiers", "requires_dist", "requires_python")
        },
        "releases": {
            version: _slim_files(files) for version, files in data.get("releases", {}).items()
        },
    }


def _slim_files(files: list[dict]) -> list[dict]:
    """One stand-in file that keeps what ``PyPI.project`` reads from all of them.

    The earliest upload time dates the release, so a warm cache must agree with a cold one.
    """
    if not files:
        return []
    uploads = [t for f in files if isinstance(t := f.get("upload_time_iso_8601"), str) and t]
    return [
        {
            "upload_time_iso_8601": min(uploads) if uploads else None,
            "yanked": all(f.get("yanked") for f in files),
        }
    ]
