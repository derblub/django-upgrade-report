"""An in-memory package index, so the tests never touch the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from django_upgrade_report.pypi import PyPI

BASE = "https://pypi.test/pypi"


def release(name, version, django=None, classifiers=(), extra=(), uploaded="2026-01-01"):
    requires = list(extra)
    if django is not None:
        requires.append(f"Django{django}")
    return {
        "name": name,
        "version": version,
        "classifiers": [f"Framework :: Django :: {v}" for v in classifiers],
        "requires_dist": requires,
        "requires_python": ">=3.10",
        "uploaded": uploaded,
    }


class FakePyPI(PyPI):
    def __init__(self, packages: dict[str, list[dict]]):
        super().__init__(BASE, cache_dir=None)
        self.packages = packages
        self.requests: list[str] = []
        self.no_files: set[tuple[str, str]] = set()

    def _fetch(self, url: str):
        self.requests.append(url)
        parts = url[len(BASE) + 1 :].split("/")
        name = parts[0]
        releases = self.packages.get(name)
        if releases is None:
            return None
        if len(parts) == 3:  # name/version/json
            match = [r for r in releases if r["version"] == parts[1]]
            return {"info": _info(match[0])} if match else None
        return {
            "info": _info(releases[-1]),
            "releases": {
                r["version"]: []
                if (name, r["version"]) in self.no_files
                else [{"upload_time_iso_8601": f"{r['uploaded']}T00:00:00Z"}]
                for r in releases
            },
        }


def _info(r: dict) -> dict:
    return {key: r[key] for key in ("name", "version", "classifiers", "requires_dist")} | {
        "requires_python": r["requires_python"]
    }


DJANGO = [
    release("Django", v, uploaded=date)
    for v, date in [
        ("4.2", "2023-04-03"),
        ("4.2.7", "2023-11-01"),
        ("5.0", "2023-12-04"),
        ("5.1", "2024-08-07"),
        ("5.2", "2025-04-02"),
        ("5.2.3", "2025-06-10"),
        ("6.0", "2025-12-03"),
    ]
]


@pytest.fixture
def index():
    return FakePyPI(
        {
            "django": DJANGO,
            # Already declares 5.2.
            "django-ready": [release("django-ready", "1.0", ">=4.2", ["4.2", "5.0", "5.1", "5.2"])],
            # 2.0 adds 5.2 and still supports 4.2: upgrade before Django.
            "django-before": [
                release("django-before", "1.0", ">=3.2", ["4.1", "4.2"]),
                release("django-before", "1.5", ">=3.2", ["4.2", "5.0"]),
                release("django-before", "2.0", ">=4.2", ["4.2", "5.0", "5.1", "5.2"]),
                release("django-before", "2.1", ">=4.2", ["4.2", "5.2", "6.0"]),
            ],
            # 3.0 declares 5.2 but dropped 4.2: upgrade together with Django.
            "django-with": [
                release("django-with", "2.0", ">=4.2,<5.0", ["4.2"]),
                release("django-with", "3.0", ">=5.2", ["5.2", "6.0"]),
            ],
            # Every release excludes 5.2.
            "django-blocked": [
                release("django-blocked", "1.0", "<5.0", ["4.2"], uploaded="2021-01-01"),
            ],
            # Classifiers lag behind, nothing excludes 5.2.
            "django-lagging": [release("django-lagging", "1.0", ">=3.2", ["4.1", "4.2"])],
            # Nothing at all.
            "django-silent": [
                release("django-silent", "0.1", classifiers=[]),
                release("django-silent", "0.2", ">=3.0", classifiers=[]),
            ],
            # Not Django related.
            "requests": [release("requests", "2.31.0", extra=["urllib3>=1.21"])],
        }
    )


FIXTURES = Path(__file__).parent / "fixtures" / "pypi"


class RecordedPyPI(PyPI):
    """Real PyPI metadata, recorded by ``fixtures/record.py``."""

    def __init__(self):
        super().__init__(BASE, cache_dir=None)
        self.recorded = {
            path.stem: json.loads(path.read_text()) for path in FIXTURES.glob("*.json")
        }

    def _fetch(self, url: str):
        name, *rest = url[len(BASE) + 1 :].split("/")
        data = self.recorded.get(name)
        if data is None:
            return None
        if len(rest) == 1:  # name/json
            return {
                "info": data["info"],
                "releases": {
                    version: [{"upload_time_iso_8601": time, "yanked": version in data["yanked"]}]
                    if time
                    else []
                    for version, time in data["uploaded"].items()
                },
            }
        version = rest[0]
        if version not in data["uploaded"]:
            return None
        if version not in data["releases"]:
            raise LookupError(f"{name} {version} is not recorded, extend fixtures/record.py")
        return {
            "info": {"name": data["info"]["name"], "version": version} | data["releases"][version]
        }


@pytest.fixture(scope="session")
def recorded():
    return RecordedPyPI()
