"""A small, cached client for the PyPI JSON API."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from packaging.utils import parse_wheel_filename
from packaging.version import InvalidVersion, Version

from django_upgrade_report.client import (  # noqa: F401  (re-exported for callers)
    ATTEMPTS,
    MAX_CONNECTIONS,
    MAX_RETRY_AFTER,
    OFFLINE,
    ONLINE,
    PREFER_CACHE,
    USER_AGENT,
    FetchError,
    JsonClient,
    NotCached,
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
    requires_python: str | None = None
    wheel_tags: tuple[str, ...] = ()
    """Tags of the release's Linux wheels, e.g. ``cp312-cp312-manylinux_2_17_x86_64``."""
    has_sdist: bool = False


@dataclass(frozen=True)
class ReleaseInfo:
    """The metadata of one release that matters for Django compatibility."""

    name: str
    version: str
    classifiers: tuple[str, ...]
    requires_dist: tuple[str, ...]
    requires_python: str | None
    project_urls: tuple[tuple[str, str], ...] = ()
    """``Project-URL`` labels and URLs, e.g. ``("Changelog", "https://...")``."""
    home_page: str | None = None
    django_mentions: tuple[str, ...] = ()
    """Django feature versions the description (the README on PyPI) names, e.g. ``"5.2"``."""
    wheel_tags: tuple[str, ...] = ()
    has_sdist: bool | None = None
    """``None`` when the index did not list the release's files."""


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
    cache_format = "v2"

    def __init__(
        self,
        index_url: str = "https://pypi.org/pypi",
        cache_dir: Path | None = None,
        cache_ttl: float = 24 * 3600,
        timeout: float = 20,
        mode: str = ONLINE,
    ):
        super().__init__(index_url, cache_dir=cache_dir, timeout=timeout, mode=mode)
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
            # One stand-in file per release, see _slim_files.
            stand_in = files[0] if files else {}
            when = stand_in.get("upload_time_iso_8601")
            releases.append(
                Release(
                    version,
                    _timestamp(when) if isinstance(when, str) and when else None,
                    bool(stand_in.get("yanked")),
                    has_files=bool(files),
                    requires_python=_string(stand_in.get("requires_python")),
                    wheel_tags=_strings(stand_in.get("wheel_tags")),
                    has_sdist=stand_in.get("has_sdist") is True,
                )
            )
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
        # Only the fields we use: the full release list can be megabytes, a README 100 KB.
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
    """``data`` as :meth:`PyPI._slim` left it."""
    info = data["info"]
    return ReleaseInfo(
        name=info["name"],
        version=info["version"],
        classifiers=_strings(info.get("classifiers")),
        requires_dist=_strings(info.get("requires_dist")),
        requires_python=_string(info.get("requires_python")),
        project_urls=tuple(
            (label, url)
            for label, url in (_table(info.get("project_urls"))).items()
            if isinstance(label, str) and label and _url(url)
        ),
        home_page=_url(info.get("home_page")),
        django_mentions=_strings(info.get("django_mentions")),
        wheel_tags=_strings(info.get("wheel_tags")),
        has_sdist=has_sdist if isinstance(has_sdist := info.get("has_sdist"), bool) else None,
    )


def _url(value: object) -> str | None:
    """A web address; old setuptools wrote "UNKNOWN" where there was none."""
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return value
    return None


def _table(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _strings(value: object) -> tuple[str, ...]:
    """A list of strings from index metadata; anything else in it is ignored."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


# "Django 5.2", "Django >= 4.2", "django~=5.0", "Django: 5.1", "Django version 4.2", but not
# "Django<5.0" (that excludes it) or "python-django 3.2" (another package).
_MENTION = re.compile(r"(?i)(?<![\w.-])django\s*(?:(?:==|>=|~=|:)\s*|v|version\s+)?(\d+\.\d+)\b")


def django_mentions(text: object) -> list[str]:
    """Django feature versions a text names as supported, e.g. "Django 5.2" or "Django>=4.2"."""
    if not isinstance(text, str):
        return []
    found = {v for v in map(Version, _MENTION.findall(text)) if 1 <= v.major <= 9}
    return [str(v) for v in sorted(found)]


def _slim_project(data: dict) -> dict:
    """What the engine reads of a project or release answer from the index."""
    info = data["info"]
    files = data.get("urls")
    slim = {
        key: info.get(key)
        for key in (
            "name",
            "version",
            "classifiers",
            "requires_dist",
            "requires_python",
            "project_urls",
            "home_page",
        )
    }
    slim["django_mentions"] = django_mentions(info.get("description"))
    if isinstance(files, list):  # the files of this release (or the latest)
        files = [f for f in files if isinstance(f, dict)]
        slim["wheel_tags"] = _wheel_tags(files)
        slim["has_sdist"] = any(_is_sdist(f) for f in files)
    return {
        "info": slim,
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
    stand_in = {
        "upload_time_iso_8601": min(uploads) if uploads else None,
        "yanked": all(f.get("yanked") for f in files),
    }
    # Left out when empty: botocore has thousands of releases.
    python = next((p for f in files if (p := _string(f.get("requires_python")))), None)
    if python:
        stand_in["requires_python"] = python
    if tags := _wheel_tags(files):
        stand_in["wheel_tags"] = tags
    if any(_is_sdist(f) for f in files):
        stand_in["has_sdist"] = True
    return [stand_in]


def _is_sdist(file: dict) -> bool:
    filename = file.get("filename")
    return file.get("packagetype") == "sdist" or (
        isinstance(filename, str) and filename.endswith((".tar.gz", ".zip"))
    )


def _wheel_tags(files: list[dict]) -> list[str]:
    """The tags of the wheels among ``files`` that install on Linux, where Django apps run.

    Other platforms' wheels are left out: numpy has some 50 tags per release. File names that
    are not valid are skipped.
    """
    tags = set()
    for f in files:
        filename = f.get("filename")
        if not isinstance(filename, str) or not filename.endswith(".whl"):
            continue
        try:
            tags.update(
                str(tag)
                for tag in parse_wheel_filename(filename)[3]
                if tag.platform == "any" or "linux" in tag.platform
            )
        except ValueError:  # InvalidWheelFilename, or InvalidVersion on older packaging
            continue
    return sorted(tags)
