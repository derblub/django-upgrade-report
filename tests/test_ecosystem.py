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


def test_page_leads_with_figures_and_two_charts(build, index):
    data = build.build(index, PACKAGES, today=date(2026, 1, 15), downloads=DOWNLOADS)
    html = build.page(data)
    assert html.index('<div class="figures">') < html.index('<section class="charts"')
    assert '<div class="figure ready"><b>3</b><span>of 5 declare Django 5.2 LTS</span>' in html
    for kind in ("release", "calendar"):
        chart = html.split(f'data-chart="{kind}"', 1)[1].split("</figure>", 1)[0]
        assert '<g class="layer packages">' in chart and '<g class="layer downloads">' in chart
        assert 'class="crosshair"' in chart
    release = html.split('data-chart="release"', 1)[1].split("</figure>", 1)[0]
    # Newest first takes the brand teal; older than the fifth would step back to the neutral.
    assert '<g class="series" data-v="6.0" style="--c:var(--s1)">' in release
    assert '<g class="series" data-v="4.2" style="--c:var(--s5)">' in release
    assert ">4.2 LTS <tspan" in release  # labelled at the end of its line
    assert '<button type="button" data-v="5.2" aria-pressed="false" style="--c:var(--s2)">' in html
    spec = json.loads(html.split('id="chart-data">', 1)[1].split("</script>", 1)[0])
    assert set(spec) == {"release", "calendar"}
    assert set(spec["release"]["modes"]) == {"packages", "downloads"}


def test_timeline_and_weighted_curves(build, index):
    data = build.build(index, PACKAGES, today=date(2026, 1, 15), downloads=DOWNLOADS)
    v60 = next(v for v in data["versions"] if v["version"] == "6.0")
    assert v60["timeline"][0] == ["2025-12-03", 0.0]  # from the release
    assert v60["timeline"][1][0] == "2026-01-01"  # then the first of every month
    assert v60["timeline"][-1] == ["2026-01-15", 0.4]  # and today: 2 of 5 declared it
    # Weighted: django-before (5,000,000 downloads) declared 6.0 on 2026-01-01.
    assert v60["curve_downloads"][-1][1] > 0.99
    assert build._chart_series(data)[0]["c"] == "var(--s1)"


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


DOWNLOADS = {"django-ready": 900, "django-before": 5_000_000, "django-blocked": 1_200}


def test_rows_carry_downloads_releases_and_requirements(build, index):
    data = build.build(index, PACKAGES, today=date(2026, 1, 15), downloads=DOWNLOADS)
    rows = {p["name"]: p for p in data["packages"]}
    assert rows["django-before"]["downloads"] == 5_000_000
    assert rows["django-lagging"]["downloads"] is None
    assert rows["django-blocked"]["released"] == "2021-01-01"
    assert rows["django-blocked"]["stale"] and not rows["django-ready"]["stale"]
    assert rows["django-blocked"]["requires"] == "<5.0"


def test_page_links_versions_and_packages_and_lists_blockers(build, index):
    data = build.build(index, PACKAGES, today=date(2026, 1, 15), downloads=DOWNLOADS)
    html = build.page(data)
    assert 'uvx django-upgrade-report</code><button type="button" class="copy"' in html
    assert '<a href="#5.2">Django 5.2</a>' in html
    assert '<details class="version" id="6.0" open>' in html  # the newest release
    assert '<details class="version" id="6.1" open>' in html  # the next one
    assert '<details class="version" id="5.0">' in html
    blockers = html.split('id="5.0">', 1)[1].split("</details>", 1)[0]
    assert "Django 5.0: 2 packages exclude it" in blockers
    assert blockers.index("django-blocked") < blockers.index("django-with")  # by downloads
    assert "<code>&lt;5.0</code>" in blockers
    matrix = html.split('<tbody id="rows">', 1)[1]
    assert matrix.index('id="django-before"') < matrix.index('id="django-blocked"')
    assert 'data-status="4.2:ready 5.0:blocked' in html
    assert '<span class="flag">no release in 2 years</span>' in html
    assert '<div class="toolbar" id="toolbar" hidden>' in html  # shown by the script only
    assert build._compact(24_512_000) == "24.5M" and build._compact(1_200) == "1.2K"


class FakeFiles:
    """GitHub with one repository whose tox.ini runs the next Django."""

    def workflows(self, owner, repo):
        return []

    def text(self, owner, repo, path):
        return "[tox]\nenvlist = py312-django{52,61}\n" if path == "tox.ini" else None


def test_signs_downloads_and_badges(build, index):
    index.packages["django-lagging"][0]["project_urls"] = {
        "Source": "https://github.com/acme/django-lagging"
    }
    data = build.build(
        index, PACKAGES, today=date(2026, 1, 15), downloads=DOWNLOADS, files=FakeFiles()
    )
    columns = {v["version"]: v for v in data["versions"]}
    rows = {p["name"]: p for p in data["packages"]}
    # Looked at for the newest release (6.0) and the next one (6.1) only.
    assert rows["django-lagging"]["signs"] == {"6.1": ["main branch tests Django 6.1 (tox.ini)"]}
    assert columns["6.1"]["counts"]["signs"] == 1 and columns["5.2"]["counts"]["signs"] == 0
    # Weighted by downloads: django-before alone carries almost all of them.
    assert columns["5.0"]["downloads"] == {"ready": 0.0, "check": 1.0, "blocked": 0.0}
    assert columns["5.2"]["downloads"]["ready"] == 1.0  # 5,000,900 of 5,002,100

    html = build.page(data)
    assert '<td class="s check signed" title="check: main branch tests Django 6.1' in html
    assert '<option value="check-signed">to check, with a sign</option>' in html
    assert "6.1:check-signed" in html

    found = build.badges(data)
    assert found["django-ready/5.2.json"] == {
        "schemaVersion": 1,
        "label": "Django 5.2",
        "message": "declared",
        "color": "brightgreen",
    }
    assert found["django-blocked.json"]["message"] == "excluded"  # the newest release, 6.0
    assert found["django-blocked.json"]["label"] == "Django 6.0"
    snippet = build.badge_snippet("django-ready")
    assert snippet.startswith("[![Django support](https://img.shields.io/endpoint?url=https%3A")
    assert snippet.endswith("(https://derblub.github.io/django-upgrade-report/#django-ready)")
