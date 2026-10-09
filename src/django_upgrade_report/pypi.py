"""A small, cached client for the PyPI JSON API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from packaging.version import InvalidVersion, Version

from django_upgrade_report.client import (  # noqa: F401  (re-exported for callers)
    ATTEMPTS,
    MAX_CONNECTIONS,
    MAX_RETRY_AFTER,
    USER_AGENT,
    FetchError,
    JsonClient,
    UnexpectedAnswer,
)

PyPIError = FetchError
"""The package index could not be reached or did not answer usefully, even after retrying."""


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


class PyPI(JsonClient):
    hint = " (is this a PyPI JSON API URL?)"

    def __init__(
        self,
        index_url: str = "https://pypi.org/pypi",
        cache_dir: Path | None = None,
        cache_ttl: float = 24 * 3600,
        timeout: float = 20,
    ):
        super().__init__(index_url, cache_dir=cache_dir, timeout=timeout)
        self.cache_ttl = cache_ttl

    @property
    def index_url(self) -> str:
        return self.base_url

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

    def _ttl(self, url: str) -> float | None:
        # Metadata of a specific release never changes, the project index does.
        is_release = url.count("/") > f"{self.index_url}/x/json".count("/")
        return None if is_release else self.cache_ttl

    def _slim(self, data: dict) -> dict:
        # Only the fields we use, the full release list can be megabytes.
        return _slim_project(data)

    def _validate(self, data: object) -> None:
        """Reject answers that would crash later, e.g. an error object from a proxy."""
        if not isinstance(data, dict):
            raise UnexpectedAnswer("unexpected answer, not a JSON object")
        info = data.get("info")
        if not isinstance(info, dict) or not isinstance(info.get("name"), str):
            raise UnexpectedAnswer("unexpected answer, no package info")
        if not isinstance(info.get("version"), str):
            raise UnexpectedAnswer("unexpected answer, no version")
        releases = data.get("releases", {})
        if not isinstance(releases, dict) or not all(
            isinstance(files, list) and all(isinstance(f, dict) for f in files)
            for files in releases.values()
        ):
            raise UnexpectedAnswer("unexpected answer, malformed release list")


def _timestamp(text: str) -> datetime | None:
    """An upload time; mirrors that leave out the time zone mean UTC, like PyPI."""
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


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


def _slim_project(data: dict) -> dict:
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
