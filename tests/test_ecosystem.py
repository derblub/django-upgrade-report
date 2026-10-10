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
    if not (ROOT / "ecosystem" / "build.py").exists():
        pytest.skip("ecosystem/ is left out of the sdist")
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
    assert rows["django-with"]["declared_since"]["5.2"] == "2026-01-01"
    # 1.5 declared 5.0, the newest release 2.1 no longer does: it still counts in the curve.
    assert rows["django-before"]["status"]["5.0"] == "check"
    assert rows["django-before"]["declared_since"]["5.0"] == "2026-01-01"


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
                "declared_since": {"5.2": "2025-04-10"},
            },
        ],
    }
    html = build.page(data)
    assert "&lt;x&gt;" in html and "<x>" not in html
    assert 'title="ready, first declared 2025-04-10"' in html
    assert "100% after 30 days" in html
    assert 'class="brand" href="https://pushingpixels.at"' in html
    assert '<link rel="icon" href="data:image/svg+xml,' in html


def test_highlights_lead_with_the_newest_lts_and_release(build, index):
    data = build.build(index, PACKAGES, today=date(2026, 1, 15))
    h = build.highlights(data)
    assert h["lts"] == {"version": "5.2", "ready": 3}
    assert h["newest"] == {"version": "6.0", "ready": 2, "blocked": 1, "days": 43}
    assert build.description(data) == (
        "3 of the 5 most downloaded Django packages declare Django 5.2 LTS; 2 declare "
        "Django 6.0, 43 days after its release. Updated every week."
    )


def test_page_has_a_preview_for_shared_links(build, index):
    data = build.build(index, PACKAGES, today=date(2026, 1, 15))
    html = build.page(data)
    assert '<meta name="description" content="3 of the 5 most' in html
    assert (
        '<meta property="og:image" content="https://derblub.github.io/django-upgrade-report/og.png">'
        in html
    )
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    card = build.card(data)
    assert "<b>3</b><span>of 5 declare Django 5.2 LTS</span>" in card
    assert "<b>1</b><span>packages block Django 6.0</span>" in card


def test_page_leads_with_figures_and_one_chart(build, index):
    data = build.build(index, PACKAGES, today=date(2026, 1, 15))
    html = build.page(data)
    assert html.index('<div class="tiles figures">') < html.index('<figure class="chart">')
    assert "<b>3</b><span>of 5 declare Django 5.2 LTS</span>" in html
    chart = html.split('<figure class="chart">', 1)[1].split("</figure>", 1)[0]
    assert chart.count("<polyline") == sum(len(v["curve"]) >= 2 for v in data["versions"])
    assert '<g class="lts">' in chart and '<g class="feature">' in chart
    assert ">4.2 LTS <tspan" in chart  # labelled at the end of its line


def snapshot(day, statuses):
    return {
        "generated": day,
        "versions": [{"version": "6.1"}, {"version": "6.2"}],
        "packages": [{"name": name, "status": status} for name, status in statuses.items()],
    }


def test_changes_since_last_week(build):
    before = snapshot("2026-10-05", {"a": {"6.1": "check"}, "b": {"6.1": "ready"}, "c": {}})
    now = snapshot(
        "2026-10-12",
        {"a": {"6.1": "ready"}, "b": {"6.1": "blocked"}, "d": {"6.1": "ready"}},
    )
    changed = build.changes(before, now)
    assert changed == {
        "since": "2026-10-05",
        "versions": {"6.1": {"ready": ["a"], "blocked": ["b"], "dropped": ["b"]}},
        "added": ["d"],  # new in the list: not "newly ready"
        "removed": ["c"],
    }
    assert build.changes(None, now) is None


def test_history_feeds_the_page_and_the_feed(build, tmp_path):
    (tmp_path / "2026-10-05.json").write_text(json.dumps(snapshot("2026-10-05", {})))
    assert build.previous(tmp_path, "2026-10-12")["generated"] == "2026-10-05"
    assert build.previous(tmp_path, "2026-10-05") is None  # not today's own

    week = snapshot("2026-10-12", {})
    week["changes"] = {
        "since": "2026-10-05",
        "versions": {"6.1": {"ready": ["a", "<b>"], "blocked": [], "dropped": []}},
    }
    section = build._changes(week)
    assert "Since 2026-10-05" in section
    assert "<li><b>Django 6.1</b> now ready: a, &lt;b&gt;</li>" in section
    atom = build.feed([week, snapshot("2026-10-05", {})])
    assert "<title>Week of 2026-10-12: 2 newly ready</title>" in atom
    assert atom.count("<entry>") == 1  # a week without changes has no entry
    assert "&lt;b&gt;" in atom and "<b>" not in atom.split("<entry>")[1].split("</title>")[1]

    assert "show up here from the next run on" in build._changes(snapshot("2026-10-12", {}))
    quiet = snapshot("2026-10-12", {}) | {"changes": {"since": "2026-10-05", "versions": {}}}
    assert "Nothing changed since 2026-10-05" in build._changes(quiet)
