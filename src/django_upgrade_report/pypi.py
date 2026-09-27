"""A small, cached client for the PyPI JSON API."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from packaging.version import InvalidVersion, Version

from django_upgrade_report import __version__

USER_AGENT = (
    f"django-upgrade-report/{__version__} (+https://github.com/derblub/django-upgrade-report)"
)


@dataclass(frozen=True)
class Release:
    version: Version
    uploaded: datetime | None
    yanked: bool


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
        return [r for r in self.releases if not r.yanked and not r.version.is_prerelease]


class PyPI:
    def __init__(
        self,
        index_url: str = "https://pypi.org/pypi",
        cache_dir: Path | None = None,
        cache_ttl: float = 24 * 3600,
        timeout: float = 20,
    ):
        self.index_url = index_url.rstrip("/")
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl
        self.timeout = timeout
        self._memory: dict[str, dict | None] = {}
        self._lock = threading.Lock()

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
            uploads = [f["upload_time_iso_8601"] for f in files if f.get("upload_time_iso_8601")]
            uploaded = None
            if uploads:
                uploaded = datetime.fromisoformat(min(uploads).replace("Z", "+00:00"))
            yanked = bool(files) and all(f.get("yanked") for f in files)
            releases.append(Release(version, uploaded, yanked))
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
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if attempt == 2 or exc.code < 500:
                    raise
            except urllib.error.URLError:
                if attempt == 2:
                    raise
            time.sleep(2**attempt)
        return None

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


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "django-upgrade-report"


def _release_info(data: dict) -> ReleaseInfo:
    info = data["info"]
    return ReleaseInfo(
        name=info["name"],
        version=info["version"],
        classifiers=tuple(info.get("classifiers") or ()),
        requires_dist=tuple(info.get("requires_dist") or ()),
        requires_python=info.get("requires_python") or None,
    )


def _slim(data: dict) -> dict:
    info = data["info"]
    return {
        "info": {
            key: info.get(key)
            for key in ("name", "version", "classifiers", "requires_dist", "requires_python")
        },
        "releases": {
            version: [
                {"upload_time_iso_8601": f.get("upload_time_iso_8601"), "yanked": f.get("yanked")}
                for f in files[:1]
            ]
            for version, files in data.get("releases", {}).items()
        },
    }
