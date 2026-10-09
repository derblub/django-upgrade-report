"""ecosystem/build.py: the public readiness data, against the fake index."""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SNAPSHOT = Path(__file__).parent / "data" / "ecosystem" / "data.json"
PACKAGES = ["django-ready", "django-before", "django-with", "django-blocked", "django-lagging"]


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location(
        "ecosystem_build", ROOT / "ecosystem" / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_data_matches_the_snapshot(build, index):
    data = build.build(index, PACKAGES, today=date(2026, 1, 15))
    data["tool"] = "django-upgrade-report"  # not the version
    if not SNAPSHOT.exists():  # pragma: no cover - writes the first snapshot
        SNAPSHOT.write_text(json.dumps(data, indent=1) + "\n")
    assert data == json.loads(SNAPSHOT.read_text())


def test_versions_from_4_2_and_the_next_one(build, index):
    data = build.build(index, PACKAGES, today=date(2026, 1, 15))
    columns = [(v["version"], v["released"]) for v in data["versions"]]
    assert columns == [
        ("4.2", True),
        ("5.0", True),
        ("5.1", True),
        ("5.2", True),
        ("6.0", True),
        ("6.1", False),
    ]
    rows = {p["name"]: p for p in data["packages"]}
    assert rows["django-blocked"]["status"]["5.2"] == "blocked"
    assert rows["django-ready"]["status"]["5.2"] == "ready"
    assert rows["django-with"]["ready_since"]["5.2"] == "2026-01-01"


def test_select_keeps_django_related_names(build, index):
    top = [
        "requests",
        "django",
        "django-ready",
        "django-lagging",
        "django-missing",
        "django-before",
    ]
    assert build.select(index, top, count=2) == ["django-ready", "django-lagging"]


def test_page_escapes_and_links(build):
    data = {
        "generated": "2026-01-15",
        "tool": "django-upgrade-report 0.4.0",
        "packages_count": 1,
        "versions": [
            {
                "version": "5.2",
                "released": True,
                "ga": "2025-04-02",
                "counts": {"ready": 1, "check": 0, "blocked": 0},
                "curve": [[0, 0.5], [30, 1.0]],
            },
        ],
        "packages": [
            {
                "name": "<x>",
                "version": "1.0",
                "status": {"5.2": "ready"},
                "ready_since": {"5.2": "2025-04-10"},
            },
        ],
    }
    html = build.page(data)
    assert "&lt;x&gt;" in html and "<x>" not in html
    assert 'title="ready since 2025-04-10"' in html
    assert "100% after 30 days" in html
