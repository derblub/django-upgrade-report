"""How ready the Django ecosystem is for each Django version, published every week.

    PYTHONPATH=src python3 ecosystem/build.py select   # the packages, once a month or so
    PYTHONPATH=src python3 ecosystem/build.py build    # data.json and index.html in site/

``select`` takes the most downloaded Django-related packages from the public top-pypi-packages
data set and writes them to ``packages.json``, which is checked in, so runs stay comparable.
``build`` judges the newest release of each against every Django version from 4.2 on, plus the
next one, with the same rules as the report, and writes ``site/data.json`` and
``site/index.html``. Lives outside the package: the tool itself never talks to a server.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import Version

from django_upgrade_report import AUTHOR, ECOSYSTEM_URL, REPO_URL, __version__
from django_upgrade_report.analysis import (
    _INACTIVE,
    STALE_AFTER_DAYS,
    Status,
    Verdict,
    _build_target,
    _series,
    analyse,
    django_requirement,
    is_django_related,
    supports,
)
from django_upgrade_report.evidence import FetchError, GitHubFiles, test_matrix
from django_upgrade_report.frameworks import DJANGO
from django_upgrade_report.pypi import USER_AGENT, PyPI, PyPIError, default_cache_dir
from django_upgrade_report.render.html import FAVICON, brand
from django_upgrade_report.sources import Dependency, DependencySet

HERE = Path(__file__).parent
PACKAGES = HERE / "packages.json"
SITE = HERE / "site"
TOP = "https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/top-pypi-packages.min.json"
COUNT = 300
FIRST = Version("4.2")
WORKERS = 8
# Names worth asking PyPI about. Django-related packages without such a name (whitenoise,
# channels) are missed: asking about all 15,000 top packages is not worth that load.
CANDIDATE = re.compile(r"django|wagtail|^drf[-_]|^dj[-_]|djangorestframework|^channels")
CURVE_DAYS = (0, 30, 60, 90, 180, 270, 365, 540, 730)
TIMELINE_MONTHS = 36
EARLY = 365
"""Days before a release from which a package's releases can declare it."""


# --- the packages ---------------------------------------------------------------------------


def select(pypi: PyPI, top: list[str], count: int = COUNT) -> list[str]:
    """The first ``count`` names of ``top`` (most downloaded first) that are Django-related."""
    candidates = [name for name in top if CANDIDATE.search(name) and name != "django"]
    with ThreadPoolExecutor(WORKERS) as pool:
        projects = list(pool.map(lambda name: _project(pypi, name), candidates))
    chosen = []
    for name, project in zip(candidates, projects, strict=True):
        if project is not None and is_django_related(project.latest):
            chosen.append(canonicalize_name(name))
        if len(chosen) == count:
            break
    return chosen


def _project(pypi: PyPI, name: str):
    try:
        return pypi.project(name)
    except PyPIError:
        return None


def _top() -> tuple[str, list[str], dict[str, int]]:
    """When the data set was made, the names most downloaded first, and the downloads of the
    last 30 days per canonical name."""
    request = urllib.request.Request(TOP, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)
    rows = data["rows"]
    downloads = {canonicalize_name(row["project"]): row["download_count"] for row in rows}
    return data.get("last_update", ""), [row["project"] for row in rows], downloads


# --- the readiness --------------------------------------------------------------------------


def versions(django) -> list[tuple[Version, Version | None]]:
    """``(target, the version before it)`` for every Django from 4.2 on, plus the next one."""
    series = sorted(v for v in _series(django) if v >= Version("4.1"))
    released = [v for v in series if v >= FIRST]
    newest = released[-1]
    pairs = [(v, series[i - 1] if i else None) for i, v in enumerate(series) if v >= FIRST]
    return [*pairs, (DJANGO.next_feature(newest), newest)]


def first_declared(pypi: PyPI, name: str, target) -> datetime | None:
    """When the first release that declares ``target`` came out, whether or not the newest
    release still does: a package that dropped 4.2 since still declared it once.

    Releases from a year before the target on count. Support comes in once and goes away
    once, so the newest release, or else the newest of each series, finds one that declares
    it, and a binary search before that finds the first."""
    project = pypi.project(name)
    if project is None:
        return None
    since = target.ga - timedelta(days=EARLY) if target.ga else None
    releases = [
        r
        for r in project.stable_releases()
        if since is None or (r.uploaded is not None and r.uploaded >= since)
    ]

    def declares(index: int) -> bool:
        r = releases[index]
        info = pypi.release(name, str(r.version))
        return info is not None and supports(info, target, r.uploaded).verdict is Verdict.YES

    if not releases:
        return None
    newest_of_series = {}
    for index, r in enumerate(releases):
        newest_of_series[(r.version.major, r.version.minor)] = index
    found = next((i for i in sorted(newest_of_series.values(), reverse=True) if declares(i)), None)
    if found is None:
        return None
    low, high = 0, found
    while low < high:
        middle = (low + high) // 2
        if declares(middle):
            high = middle
        else:
            low = middle + 1
    return releases[low].uploaded


def build(
    pypi: PyPI,
    packages: list[str],
    today: date | None = None,
    downloads: dict[str, int] | None = None,
    files=None,
) -> dict:
    """The status of the newest release of each package for every Django version.

    With ``files`` (GitHub, ``--evidence``), packages to check for the newest release and the
    next one also show whether the test matrix on their default branch runs it."""
    today = today or datetime.now(timezone.utc).date()
    django = pypi.project("django")
    if django is None:
        raise RuntimeError("Could not read Django's release history")
    series = _series(django)
    rows = {
        name: {"name": name, "version": None, "status": {}, "declared_since": {}, "signs": {}}
        for name in packages
    }
    for name, row in rows.items():
        project = _project(pypi, name)
        if project is None:
            continue
        last = max((r.uploaded for r in project.releases if r.uploaded), default=None)
        spec = django_requirement(project.latest)
        row.update(
            downloads=(downloads or {}).get(name),
            released=last.date().isoformat() if last else None,
            stale=bool(last) and (today - last.date()).days > STALE_AFTER_DAYS,
            inactive=_INACTIVE in project.latest.classifiers,
            requires=str(spec) if spec else None,
        )
    columns = []
    pairs = versions(django)
    released_ = [t for t, _ in pairs if t in series]
    looked_at = {pairs[-1][0], *released_[-1:]}  # where signs from GitHub are worth the requests
    for target, before in pairs:
        label = f"{target.major}.{target.minor}"
        # From the newest patch of the version before: what an upgrade to it would see.
        deps = {"django": Dependency("django", str(series[before][-1]))} if before else {}
        for name in packages:
            project = _project(pypi, name)
            if project is not None:
                deps[name] = Dependency(name, project.latest.version)
                rows[name]["version"] = project.latest.version
        report = analyse(DependencySet("ecosystem", deps), pypi, label, workers=WORKERS)
        goal = _build_target(django, pypi, target, None)
        counts = {status.value: 0 for status in (Status.READY, Status.CHECK, Status.BLOCKED)}
        declared = []
        judged = {p.name: p for p in report.packages}
        names = list(judged)
        with ThreadPoolExecutor(WORKERS) as pool:
            found = pool.map(first_declared, [pypi] * len(names), names, [goal] * len(names))
            since = dict(zip(names, found, strict=True))
        if files is not None and target in looked_at:
            _look_on_github(files, report, label)
        counts["signs"] = 0
        for name, p in judged.items():
            status = Status.CHECK if p.status is Status.UPGRADE else p.status  # a pre-release
            counts[status.value] += 1
            rows[name]["status"][label] = status.value
            if status is Status.CHECK and p.evidence:
                counts["signs"] += 1
                rows[name]["signs"][label] = [e.text for e in p.evidence]
            if since.get(name) is not None:
                rows[name]["declared_since"][label] = since[name].date().isoformat()
                declared.append((since[name].date(), rows[name].get("downloads") or 0))
        ga = goal.ga.date() if goal.ga else None
        columns.append(
            {
                "version": label,
                "released": goal.released,
                "ga": ga.isoformat() if ga else None,
                "counts": counts,
                "downloads": _download_shares(rows, label),
                "curve": _curve(declared, ga, today, len(judged)),
                "curve_downloads": _curve(declared, ga, today, _weight(rows, judged), True),
                "timeline": _timeline(declared, ga, today, len(judged)),
                "timeline_downloads": _timeline(declared, ga, today, _weight(rows, judged), True),
            }
        )
    return {
        "generated": today.isoformat(),
        "tool": f"django-upgrade-report {__version__}",
        "packages_count": len(packages),
        "versions": columns,
        "packages": sorted(
            (row for row in rows.values() if row["status"]), key=lambda row: row["name"]
        ),
    }


def _look_on_github(files, report, label: str) -> None:
    """Signs from the test matrix on the default branch, for packages to check without one."""

    def look(p) -> None:
        try:
            sign = test_matrix(files, p.repository_url, label)
        except FetchError:
            return
        if sign is not None:
            p.evidence.append(sign)

    wanted = [
        p
        for p in report.packages
        if p.status is Status.CHECK and not p.evidence and p.repository_url
    ]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(look, wanted))


def _download_shares(rows: dict, label: str) -> dict | None:
    """The share of downloads behind each status: packages weighted by how much they are
    used. ``None`` without download numbers."""
    total = {"ready": 0, "check": 0, "blocked": 0}
    for row in rows.values():
        status = row["status"].get(label)
        if status in total and row.get("downloads"):
            total[status] += row["downloads"]
    whole = sum(total.values())
    return {key: round(value / whole, 3) for key, value in total.items()} if whole else None


def _weight(rows: dict, judged) -> int:
    """All downloads of the judged packages: what the weighted shares divide by."""
    return sum(rows[name].get("downloads") or 0 for name in judged)


def _declared_share(
    declared: list[tuple[date, int]], day: date, total: int, weighted: bool
) -> float:
    done = sum(weight if weighted else 1 for when, weight in declared if when <= day)
    return round(done / total, 3)


def _curve(
    declared: list[tuple[date, int]],
    ga: date | None,
    today: date,
    total: int,
    weighted: bool = False,
) -> list[list]:
    """``[days after the release, share that had declared it]``, up to today: how fast the
    ecosystem caught up, counting packages that dropped the version since, too. Weighted, each
    package counts with its downloads."""
    if ga is None or not total:
        return []
    points = []
    for days in CURVE_DAYS:
        day = ga + timedelta(days=days)
        if day > today:
            break
        points.append([days, _declared_share(declared, day, total, weighted)])
    return points


def _timeline(
    declared: list[tuple[date, int]],
    ga: date | None,
    today: date,
    total: int,
    weighted: bool = False,
) -> list[list]:
    """``[date, share that had declared it]`` on the first of every month of the last
    ``TIMELINE_MONTHS`` from the release on, and today: the versions side by side in time."""
    if ga is None or not total:
        return []
    start = max(ga, today - timedelta(days=TIMELINE_MONTHS * 31))
    days = [start]
    month = date(start.year, start.month, 1)
    while True:
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
        if month >= today:
            break
        days.append(month)
    days.append(today)
    return [[day.isoformat(), _declared_share(declared, day, total, weighted)] for day in days]


# --- the weeks before ------------------------------------------------------------------------


def previous(history: Path, today: str) -> dict | None:
    """The newest snapshot in ``history`` from before ``today``."""
    older = sorted(p for p in history.glob("*.json") if p.stem < today)
    return json.loads(older[-1].read_text()) if older else None


def changes(before: dict | None, now: dict) -> dict | None:
    """What changed since ``before``, per Django version: packages that are ready now and
    were not, that block now and did not, and that are no longer ready."""
    if before is None:
        return None
    old = {p["name"]: p["status"] for p in before.get("packages", [])}
    versions = {}
    for v in now["versions"]:
        label = v["version"]
        moved = {"ready": [], "blocked": [], "dropped": []}
        for p in now["packages"]:
            was, is_ = old.get(p["name"], {}).get(label), p["status"].get(label)
            if p["name"] not in old or was == is_:
                continue
            if is_ == "ready":
                moved["ready"].append(p["name"])
            elif is_ == "blocked":
                moved["blocked"].append(p["name"])
            if was == "ready":
                moved["dropped"].append(p["name"])
        if any(moved.values()):
            versions[label] = moved
    names = {p["name"] for p in now["packages"]}
    return {
        "since": before["generated"],
        "versions": versions,
        "added": sorted(names - set(old)),
        "removed": sorted(set(old) - names),
    }


# --- what the page says first ---------------------------------------------------------------


def highlights(data: dict) -> dict:
    """The numbers the page, its preview and its description lead with: the newest LTS, the
    newest release and how long it has been out, and what blocks the newest release."""
    released = [v for v in data["versions"] if v["released"] and v["ga"]]
    newest = released[-1]
    lts = [v for v in released[:-1] if DJANGO.is_lts(Version(v["version"]))]
    days = (date.fromisoformat(data["generated"]) - date.fromisoformat(newest["ga"])).days
    return {
        "total": data["packages_count"],
        "lts": {"version": lts[-1]["version"], "ready": lts[-1]["counts"]["ready"]}
        if lts
        else None,
        "newest": {
            "version": newest["version"],
            "ready": newest["counts"]["ready"],
            "blocked": newest["counts"]["blocked"],
            "days": days,
        },
    }


def description(data: dict) -> str:
    """One sentence for search results and link previews."""
    h = highlights(data)
    new = h["newest"]
    parts = []
    if h["lts"]:
        parts.append(
            f"{h['lts']['ready']} of the {h['total']} most downloaded Django packages "
            f"declare Django {h['lts']['version']} LTS"
        )
    parts.append(
        f"{new['ready']} declare Django {new['version']}, {new['days']} days after its release"
    )
    return "; ".join(parts) + ". Updated every week."


def card(data: dict) -> str:
    """The preview image as a page, 1200 by 630: what a shared link shows. Pushing Pixels ink,
    the brand typeface carried inside, so it renders the same anywhere."""
    h = highlights(data)
    new = h["newest"]
    figures = []
    if h["lts"]:
        lts = h["lts"]
        figures.append(
            ("ready", lts["ready"], f"of {h['total']} declare Django {lts['version']} LTS")
        )
    figures.append(
        (
            "ready",
            new["ready"],
            f"declare Django {new['version']}, {new['days']} days after its release",
        )
    )
    figures.append(("blocked", new["blocked"], f"packages block Django {new['version']}"))
    tiles = "".join(
        f'<div class="figure {css}"><b>{value}</b><span>{escape(label)}</span></div>'
        for css, value, label in figures
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><style>{_card_fonts()}{_PAGE_CSS}{_CARD_CSS}</style>
</head><body><div class="card">
<p class="label">Django ecosystem · {escape(data["generated"])}</p>
<h1>How ready is the Django ecosystem?</h1>
<div class="figures">{tiles}</div>
<p class="foot"><span>derblub.github.io/django-upgrade-report · updated every week</span>
<span>{brand()}</span></p>
</div></body></html>
"""


def _card_fonts() -> str:
    """The brand typeface as data, for a page rendered without the site around it."""
    import base64

    font = (HERE / "fonts" / "google-sans-flex-latin-wght-normal.woff2").read_bytes()
    encoded = base64.b64encode(font).decode()
    return (
        '@font-face { font-family: "Google Sans Flex"; font-weight: 1 1000; '
        f'src: url(data:font/woff2;base64,{encoded}) format("woff2"); }}'
    )


_CARD_CSS = """
body { width: 1200px; height: 630px; }
.card { width: 1200px; height: 630px; padding: 64px 72px; display: flex;
  flex-direction: column; background: var(--ink); }
.card .label { font-size: 20px; line-height: 24px; }
.card h1 { font-size: 64px; margin-top: 16px; }
.card .figures { margin-top: 44px; grid-template-columns: repeat(3, 1fr); }
.card .figure { padding: 28px; }
.card .figure b { font-size: 88px; }
.card .figure span { font-size: 24px; line-height: 32px; }
.foot { margin-top: auto; display: flex; justify-content: space-between; color: var(--muted);
  font-size: 22px; }
.foot a { color: var(--text); text-decoration: none; }
"""


def render_card(html: str, out: Path) -> bool:
    """The preview as a PNG, with Playwright's Chromium; False when it is not installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        page_ = browser.new_page(viewport={"width": 1200, "height": 630})
        page_.set_content(html)
        page_.screenshot(path=str(out))
        browser.close()
    return True


# --- the page -------------------------------------------------------------------------------

_PAGE_CSS = """
@font-face { font-family: "Google Sans Flex"; font-style: normal; font-weight: 1 1000;
  font-display: swap; src: url(fonts/google-sans-flex-latin-wght-normal.woff2) format("woff2");
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC,
    U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215,
    U+FEFF, U+FFFD; }
@font-face { font-family: "Google Sans Flex"; font-style: normal; font-weight: 1 1000;
  font-display: swap;
  src: url(fonts/google-sans-flex-latin-ext-wght-normal.woff2) format("woff2");
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304,
    U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0,
    U+2113, U+2C60-2C7F, U+A720-A7FF; }
:root {
  color-scheme: dark;
  /* Pushing Pixels, Ink: the page ground, surfaces on it, raised elements and hairlines. */
  --ink: #1d1d20; --surface: #131315; --raised: #27272b; --hover: #202024;
  --text: #ffffff; --muted: #99a1af; --muted-hi: #d1d5dc; --accent: #2dd4bf;
  /* Status: declared, not declared, excluded. */
  --ready: #5ee9b5; --check: #99a1af; --blocked: #fb2c36; --signed: #2dd4bf;
  /* Chart series, newest release first; older ones step back to the neutral. */
  --s1: #2dd4bf; --s2: #ee5e23; --s3: #824ae4; --s4: #f7e04f; --s5: #1c8fe0; --s-old: #6b7280;
  --sans: "Google Sans Flex", ui-sans-serif, system-ui, sans-serif;
  --mono: "DejaVu Sans Mono", "Liberation Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  --ease-ui: cubic-bezier(0.4, 0, 0.2, 1);
}
* { box-sizing: border-box; }
html { background: var(--ink); }
body { margin: 0; background: var(--ink); color: var(--text); font-family: var(--sans);
  font-size: 16px; line-height: 1.6; font-weight: 350; -webkit-font-smoothing: antialiased; }
main { max-width: 1040px; margin: 0 auto; padding: 64px 24px 80px; }
a { color: var(--accent); text-underline-offset: 3px; }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible,
[tabindex]:focus-visible, summary:focus-visible { outline: 2px solid var(--accent);
  outline-offset: 3px; }
::selection { background: rgba(45, 212, 191, 0.45); }
code { font-family: var(--mono); font-size: 0.92em; }
.label { margin: 0; font-size: 12px; line-height: 16px; letter-spacing: 0.2em;
  text-transform: uppercase; font-weight: 550; color: var(--muted); }
h1 { margin: 12px 0 0 -0.05em; font-size: clamp(2rem, 1rem + 4vw, 4.5rem); line-height: 1;
  font-weight: 650; text-transform: uppercase; text-wrap: balance; }
header .intro { margin: 24px 0 0; max-width: 65ch; font-size: 20px; line-height: 30px;
  font-weight: 300; color: var(--muted-hi); text-wrap: pretty; }
h2 { margin: 80px 0 8px; font-size: 24px; line-height: 32px; font-weight: 600;
  text-transform: uppercase; }
@media (min-width: 48rem) { h2 { font-size: 30px; line-height: 36px; } }
h3 { margin: 32px 0 8px; font-size: 16px; font-weight: 550; text-transform: uppercase;
  letter-spacing: 0.08em; }
.hint { margin: 0 0 16px; max-width: 65ch; color: var(--muted); text-wrap: pretty; }
.hint a { color: inherit; }
.panel { background: var(--surface); border: 1px solid var(--raised); }

/* The numbers the page leads with, each closed by the pushed pixel. */
.figures { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1px; margin: 48px 0 0; background: var(--raised); border: 1px solid var(--raised); }
.figure { background: var(--surface); padding: 24px; }
.figure b { display: block; font-size: 64px; line-height: 1; font-weight: 900;
  font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
.figure b::after { content: ""; display: inline-block; width: 0.16em; height: 0.16em;
  margin-left: 0.06em; border-radius: 0.03em; background: var(--pixel, var(--accent)); }
.figure.ready { --pixel: var(--ready); } .figure.blocked { --pixel: var(--blocked); }
.figure span { display: block; margin-top: 12px; color: var(--muted-hi); }

.try { margin: 24px 0 0; padding: 24px; }
.try p { margin: 0 0 12px; max-width: 65ch; }
.command { display: flex; gap: 8px; align-items: stretch; flex-wrap: wrap; }
.command code { overflow-wrap: anywhere; padding: 8px 12px; background: var(--ink);
  border: 1px solid var(--raised); color: var(--text); font-size: 15px; }
button, .copy { font: inherit; font-size: 14px; font-weight: 550; cursor: pointer;
  color: var(--text); background: var(--raised); border: 1px solid var(--raised);
  border-radius: 2px; padding: 6px 14px; min-height: 32px;
  transition: border-color 150ms var(--ease-ui), color 150ms var(--ease-ui); }
button:hover, .copy:hover { border-color: var(--accent); }
.command.copyable { cursor: pointer; }
.command.copyable code { transition: border-color 150ms var(--ease-ui); }
.command.copyable:hover code, .command.copyable:hover .copy { border-color: var(--accent); }
.badge-preview { display: grid; gap: 8px; justify-items: start; padding: 20px 24px;
  margin: 0 0 12px; }
.badge-preview img { display: block; }
.badge-states { display: flex; flex-wrap: wrap; gap: 8px; }
.changes-list { margin: 8px 0 0; padding-left: 20px; }
.changes-list li { margin: 4px 0; }

/* Charts. */
.chart-controls { display: flex; flex-wrap: wrap; gap: 16px 24px; align-items: center;
  margin: 8px 0 16px; }
.toggle { display: inline-flex; border: 1px solid var(--raised); }
.toggle button { border: 0; border-radius: 0; background: transparent; color: var(--muted); }
.toggle button[aria-pressed=true] { background: var(--raised); color: var(--text); }
.legend { display: flex; flex-wrap: wrap; gap: 4px 6px; }
.legend button { display: inline-flex; align-items: center; gap: 8px; background: transparent;
  border-color: transparent; color: var(--muted-hi); font-weight: 450; padding: 4px 8px; }
.legend button[aria-pressed=true] { border-color: var(--text); color: var(--text); }
.key { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  background: var(--c); }
.chart { position: relative; margin: 0 0 8px; padding: 16px 16px 8px; overflow-x: auto; }
.chart svg { display: block; width: 100%; min-width: 620px; height: auto; overflow: visible; }
.chart .grid { stroke: var(--raised); stroke-width: 1; }
.chart .tick { fill: var(--muted); font-size: 12px; font-family: var(--sans); }
.chart polyline { fill: none; stroke: var(--c); stroke-width: 2; stroke-linejoin: round;
  stroke-linecap: round; }
.chart .end { fill: var(--c); stroke: var(--surface); stroke-width: 2; }
.chart .end-label { fill: var(--text); font-size: 13px; font-weight: 550;
  font-family: var(--sans); }
.chart .end-label .value { fill: var(--muted); font-weight: 350; }
.chart .leader { stroke: var(--muted); stroke-width: 1; }
.chart .series { transition: opacity 150ms var(--ease-ui); }
.charts[data-focus] .series { opacity: 0.15; }
.charts[data-focus] .series.focus { opacity: 1; }
.charts .layer.downloads { display: none; }
.charts[data-mode=downloads] .layer.downloads { display: inline; }
.charts[data-mode=downloads] .layer.packages { display: none; }
.chart .crosshair { stroke: var(--muted-hi); stroke-width: 1; visibility: hidden; }
.chart.hover .crosshair { visibility: visible; }
.tooltip { position: absolute; top: 12px; pointer-events: none; background: var(--ink);
  border: 1px solid var(--raised); padding: 8px 12px; font-size: 13px; line-height: 20px;
  min-width: 170px; white-space: nowrap; z-index: 5; }
.tooltip b { display: block; font-weight: 550; margin-bottom: 4px; }
.tooltip span { display: flex; align-items: center; gap: 8px; }
.tooltip span .val { margin-left: auto; font-style: normal; font-variant-numeric: tabular-nums;
  color: var(--muted-hi); padding-left: 12px; }

/* Tables. */
.table { background: var(--surface); border: 1px solid var(--raised); overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 10px 14px; border-top: 1px solid var(--raised);
  vertical-align: top; }
thead th { border-top: 0; font-size: 12px; letter-spacing: 0.2em; text-transform: uppercase;
  color: var(--muted); font-weight: 550; }
td.name, th.name { font-weight: 550; white-space: nowrap; }
.name a { color: var(--text); }
.name a:hover { color: var(--accent); }
.overview td:nth-child(2) { white-space: nowrap; color: var(--muted-hi); }
.bar { display: flex; gap: 2px; height: 10px; min-width: 160px; margin-top: 7px; }
.bar span { display: block; }
.bar .ready { background: var(--ready); } .bar .check { background: var(--raised); }
.bar .blocked { background: var(--blocked); }
.sub { display: block; color: var(--muted); font-size: 12px; }
td.s { text-align: center; font-weight: 650; }
td.s.ready { color: var(--ready); } td.s.check { color: var(--check); }
td.s.blocked { color: var(--blocked); }
td.s.signed { color: var(--signed); box-shadow: inset 0 -2px 0 var(--signed); }
details.version { border-top: 1px solid var(--raised); }
details.version:last-of-type { border-bottom: 1px solid var(--raised); }
details.version summary { cursor: pointer; padding: 12px 0; list-style-position: inside; }
details.version summary h3 { display: inline; margin: 0; }
details.version .table { margin: 0 0 16px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 16px 0 8px; }
.toolbar input, .toolbar select { font: inherit; font-size: 15px; padding: 6px 10px;
  min-height: 36px; border: 1px solid var(--raised); border-radius: 2px;
  background: var(--surface); color: var(--text); }
.toolbar input { flex: 1 1 240px; }
#shown { color: var(--muted); font-size: 13px; }
.legend-row { display: flex; flex-wrap: wrap; gap: 6px 20px; margin: 0 0 12px; padding: 0;
  list-style: none; color: var(--muted); font-size: 13px; }
.legend-row li { display: flex; align-items: baseline; gap: 6px; }
.legend-row b { color: var(--muted-hi); font-weight: 550; }
.legend-row .s { font-weight: 650; width: 1.1em; text-align: center; }
.legend-row .s.ready { color: var(--ready); } .legend-row .s.check { color: var(--check); }
.legend-row .s.blocked { color: var(--blocked); }
.legend-row .s.signed { color: var(--signed); box-shadow: inset 0 -2px 0 var(--signed); }
.matrix { max-height: 80vh; overflow: auto; }
.matrix table { width: max-content; min-width: 100%; }
.matrix thead th { position: sticky; top: 0; z-index: 2; background: var(--surface); }
.matrix .name { position: sticky; left: 0; z-index: 1; background: var(--surface); }
.matrix thead th.name { z-index: 3; }
.matrix td { white-space: nowrap; }
/* Rows light up under the pointer; a clicked or linked package row stays marked. */
tbody tr > * { transition: background-color 0.12s; }
tbody tr:hover > *, .matrix tbody tr:hover > .name { background: var(--hover); }
.matrix tbody tr { cursor: pointer; }
.matrix tr.active > *, .matrix tr:target > * { background: var(--raised); }
.matrix tr.active > .name, .matrix tr:target > .name { box-shadow: inset 2px 0 0 var(--accent); }
.reach { display: block; width: 100%; max-width: 72px; height: 4px; margin: 6px 0 0;
  background: var(--raised); }
.reach i { display: block; height: 100%; background: var(--muted-hi); }
.legend-row .reach { width: 24px; margin: 0; align-self: center; }
.flag { display: block; width: fit-content; margin: 4px 0 0; font-size: 11px; font-weight: 450;
  color: var(--muted-hi); border: 1px solid var(--raised); padding: 0 6px; }
/* On a desktop the list fits the page: no sideways scrolling, only down. */
.matrix th, .matrix td { padding: 10px 10px; }
.matrix thead th { letter-spacing: 0.08em; }
.matrix th.s, .matrix td.s { padding-left: 4px; padding-right: 4px; text-align: center;
  min-width: 36px; }
@media (min-width: 641px) { .matrix table { width: 100%; } }
.table-note { margin: 10px 0 0; max-width: 80ch; color: var(--muted); font-size: 13px;
  line-height: 20px; }
sup { font-size: 0.7em; line-height: 0; margin-left: 2px; color: var(--accent); }
ol.table-note { padding-left: 18px; }
.table-note li::marker { color: var(--accent); font-size: 0.85em; }
.site-footer { margin-top: 96px; border-top: 1px solid var(--raised); color: var(--muted);
  font-size: 14px; line-height: 22px; }
.footer-columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 24px 40px; padding: 32px 0; }
.footer-columns p { margin: 0; }
.footer-columns .label { margin-bottom: 8px; }
.site-footer a { color: var(--muted-hi); }
.site-footer a:hover { color: var(--accent); }
.footer-bottom { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px 24px;
  padding: 16px 0 0; border-top: 1px solid var(--raised); }
.footer-links { display: flex; gap: 20px; }
.brand { white-space: nowrap; }
.pp-mark { width: 1.15em; height: 1.15em; vertical-align: -0.22em; margin-right: 0.3em; }
@media (max-width: 640px) {
  main { padding: 40px 16px 64px; }
  .figure b { font-size: 48px; }
  .matrix .name { white-space: normal; min-width: 130px; max-width: 160px;
    overflow-wrap: anywhere; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: 1ms !important;
    animation-duration: 1ms !important; }
}
"""
_ICON = {"ready": "✓", "check": "?", "blocked": "✗"}


def page(data: dict) -> str:
    """A static page with the same look as the HTML report. Without JavaScript everything is
    shown; the script adds search, filters, sorting and copying, kept in the address."""
    inner = f"""<header>
<p class="label">Django ecosystem · updated {escape(data["generated"])}</p>
<h1>How ready is the Django ecosystem?</h1>
<p class="intro">The newest release of the {data["packages_count"]} most downloaded
Django-related packages on PyPI, judged against every Django version by what its maintainers
declare: the <code>Framework :: Django</code> classifiers and the Django requirement. Updated
every week.</p>
</header>
<div class="ecosystem">
{_figures(data)}
{_try()}
{_changes(data)}
{_charts(data)}
<h2>Every Django version</h2>
{_overview(data)}
<ol class="table-note">
<li>The share of the downloads of the last 30 days that goes to packages that are ready.</li>
<li>The share of packages with a release that declared the version by then, counting packages
whose newest release has dropped it since.</li>
</ol>
{_blockers(data)}
<h2 id="packages">Every package</h2>
{_matrix(data)}
{_for_maintainers(data)}
</div>
{_footer(data)}"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Django ecosystem readiness</title>
{_meta_tags(data)}
<link rel="alternate" type="application/atom+xml" title="Weekly changes" href="feed.xml">
{FAVICON}
<style>{_PAGE_CSS}</style>
</head>
<body>
<main>
{inner}
</main>
<script>{_SCRIPT}</script>
</body>
</html>
"""


def _try() -> str:
    """The command for your own project, near the top, with a copy button."""
    return (
        '<div class="try panel"><p><b>Your own project:</b> which of your dependencies block the '
        "upgrade, which you can upgrade today, and in which order. Reads your lockfile, sends "
        "only package names to PyPI.</p>"
        '<div class="command"><code>uvx django-upgrade-report</code>'
        '<button type="button" class="copy" data-copy="uvx django-upgrade-report" hidden>'
        "Copy</button></div></div>"
    )


def _overview(data: dict) -> str:
    rows = []
    for v in data["versions"]:
        counts = v["counts"]
        total = counts["ready"] + counts["check"] + counts["blocked"] or 1
        bar = "".join(
            f'<span class="{key}" style="width:{counts[key] / total * 100:.1f}%"></span>'
            for key in ("ready", "check", "blocked")
        )
        when = f"released {v['ga']}" if v["released"] else "not released yet"
        label = escape(v["version"])
        rows.append(
            f'<tr><td class="name"><a href="#{label}">Django {label}</a></td>'
            f"<td>{escape(when)}</td>"
            f'<td><div class="bar" title="{counts["ready"]} ready, {counts["check"]} to check, '
            f'{counts["blocked"]} blocked">{bar}</div></td>'
            f"<td>{counts['ready']}</td><td>{counts['check']}{_signs(counts)}</td>"
            f"<td>{counts['blocked']}</td><td>{_share(v.get('downloads'))}</td>"
            f"<td>{_curve_text(v['curve'])}</td></tr>"
        )
    return (
        '<div class="table overview"><table><thead><tr><th>Version</th><th></th><th>Share</th>'
        "<th>ready</th><th>to check</th><th>blocked</th><th>Ready by downloads<sup>1</sup></th>"
        "<th>Declared after release<sup>2</sup></th></tr>"
        f"</thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _signs(counts: dict) -> str:
    signs = counts.get("signs")
    return f'<span class="sub">{signs} with a sign</span>' if signs else ""


def _share(shares: dict | None) -> str:
    """The share of downloads that goes to packages that are ready."""
    return f"{shares['ready'] * 100:.0f}%" if shares else ""


def _blockers(data: dict) -> str:
    """Per Django version, the packages whose newest release excludes it, most downloaded
    first: the question most visitors come with. The newest and the next version are open."""
    released = [v["version"] for v in data["versions"] if v["released"]]
    open_ = {released[-1], data["versions"][-1]["version"]} if released else set()
    span = _download_span(data["packages"])
    sections = []
    for v in reversed(data["versions"]):
        label = v["version"]
        blocked = sorted(
            (p for p in data["packages"] if p["status"].get(label) == "blocked"),
            key=lambda p: (-(p.get("downloads") or 0), p["name"]),
        )
        count = len(blocked)
        title = (
            f"Django {label}: {count} package{'s exclude' if count != 1 else ' excludes'} it"
            if count
            else f"Django {label}: no package excludes it"
        )
        rows = "".join(
            f'<tr><td class="name"><a href="#{escape(p["name"])}">{escape(p["name"])}</a></td>'
            f'<td class="dl">{_compact(p.get("downloads"))}{_reach(p.get("downloads"), span)}</td>'
            f"<td><code>{escape(p.get('requires') or '')}"
            f"</code></td><td>{escape(str(p['version']))}</td></tr>"
            for p in blocked
        )
        table = (
            '<div class="table"><table><thead><tr><th>Package</th>'
            f'<th title="{_DOWNLOADS_TITLE}">Downloads</th>'
            f"<th>Requires</th><th>Newest</th></tr></thead><tbody>{rows}</tbody></table></div>"
            if blocked
            else ""
        )
        sections.append(
            f'<details class="version" id="{escape(label)}"{" open" if label in open_ else ""}>'
            f"<summary><h3>{escape(title)}</h3></summary>{table}</details>"
        )
    return '<h2 id="blockers">What blocks each version</h2>' + "".join(sections)


def _matrix(data: dict) -> str:
    """Every package against every version, most downloaded first, with a search field and
    filters that the script fills in."""
    versions = [v["version"] for v in data["versions"]]
    released = [v["version"] for v in data["versions"] if v["released"]]
    default = released[-1] if released else versions[-1]
    heads = "".join(f'<th class="s">{escape(v)}</th>' for v in versions)
    rows = []
    packages = sorted(data["packages"], key=lambda p: (-(p.get("downloads") or 0), p["name"]))
    span = _download_span(packages)
    for p in packages:
        cells = "".join(
            f'<td class="s {_cell(p, v)}" title="{escape(_title(p, v))}">'
            f"{_ICON.get(p['status'].get(v), '')}</td>"
            for v in versions
        )
        name = escape(p["name"])
        flags = "".join(
            f'<span class="flag">{text}</span>'
            for key, text in (("inactive", "inactive"), ("stale", "no release in 2 years"))
            if p.get(key)
        )
        statuses = " ".join(f"{escape(v)}:{_cell(p, v).replace(' ', '-')}" for v in versions)
        rows.append(
            f'<tr id="{name}" data-name="{name}" data-downloads="{p.get("downloads") or 0}" '
            f'data-released="{escape(p.get("released") or "")}" data-status="{statuses}">'
            f'<th class="name" scope="row"><a href="https://pypi.org/project/{name}/">{name}</a>'
            f'{flags}</th><td class="dl">{_compact(p.get("downloads"))}'
            f"{_reach(p.get('downloads'), span)}</td>"
            f"<td>{escape(p.get('released') or '')}</td>"
            f"<td>{escape(str(p['version']))}</td>{cells}</tr>"
        )
    options = "".join(
        f'<option value="{escape(v)}"{" selected" if v == default else ""}>Django {escape(v)}'
        "</option>"
        for v in versions
    )
    toolbar = (
        '<div class="toolbar" id="toolbar" hidden><input type="search" id="q" '
        'placeholder="Search packages  ( / )" aria-label="Search packages">'
        f'<select id="v" aria-label="Django version">{options}</select>'
        '<select id="status" aria-label="Status"><option value="">any status</option>'
        '<option value="ready">ready</option><option value="check">to check</option>'
        '<option value="check-signed">to check, with a sign</option>'
        '<option value="blocked">blocked</option></select>'
        '<select id="sort" aria-label="Sort"><option value="downloads">most downloaded</option>'
        '<option value="name">name</option><option value="released">last release</option>'
        '</select><span id="shown" aria-live="polite"></span></div>'
    )
    legend = (
        '<ul class="legend-row" aria-label="What the marks mean">'
        '<li><span class="s ready">✓</span><b>Ready</b> the newest release declares it</li>'
        '<li><span class="s check">?</span><b>To check</b> it does not say</li>'
        '<li><span class="s check signed">?</span><b>With a sign</b> its README names the '
        "version, or its main branch tests it</li>"
        '<li><span class="s blocked">✗</span><b>Blocked</b> it excludes the version</li>'
        '<li><span class="reach"><i style="width:60%"></i></span><b>Downloads</b> of the last '
        "30 days, the bar on a log scale</li></ul>"
    )
    return (
        f"{toolbar}{legend}"
        '<div class="table matrix"><table><thead><tr><th class="name">Package</th>'
        f'<th title="{_DOWNLOADS_TITLE}">Downloads</th>'
        f"<th>Last release</th><th>Newest</th>{heads}</tr></thead>"
        f'<tbody id="rows">{"".join(rows)}</tbody></table></div>'
    )


_DOWNLOADS_TITLE = "Downloads of the last 30 days; the bar is on a log scale"


def _download_span(packages: list[dict]) -> tuple[int, int]:
    """The least and the most downloads among all packages: one scale for every table."""
    counts = [p["downloads"] for p in packages if p.get("downloads")]
    return (min(counts), max(counts)) if counts else (0, 0)


def _reach(downloads: int | None, span: tuple[int, int]) -> str:
    """A bar under the downloads: on a log scale, since the most downloaded package has a
    few hundred times the downloads of the 300th; a linear bar would leave most rows empty."""
    low, high = span
    if not downloads or high <= low:
        return ""
    share = math.log(downloads / low) / math.log(high / low)
    width = 4 + 96 * share
    return f'<span class="reach" aria-hidden="true"><i style="width:{width:.0f}%"></i></span>'


def _for_maintainers(data: dict) -> str:
    """The badge a package can show in its README, drawn as it will look, linked back to its
    row, with the three messages it can carry."""
    example = next((p["name"] for p in data["packages"] if p["name"] == "django-filter"), None)
    example = example or (data["packages"][0]["name"] if data["packages"] else "your-package")
    released = [v["version"] for v in data["versions"] if v["released"]]
    newest = released[-1] if released else "6.1"
    snippet = badge_snippet(example)
    states = "".join(
        f'<img src="https://img.shields.io/badge/{urllib.parse.quote(f"Django {newest}")}-'
        f'{urllib.parse.quote(message)}-{color}" alt="Django {escape(newest)}: '
        f'{escape(message)}" height="20">'
        for message, color in _BADGE.values()
    )
    return (
        '<h2 id="badges">A badge for your README</h2>'
        '<p class="hint">For maintainers: what this page says about the newest Django release, '
        "updated every week. Replace the package name; for one version, use "
        f"<code>badges/{escape(example)}/{escape(newest)}.json</code>.</p>"
        '<div class="badge-preview panel"><p class="label">Preview</p>'
        f'<a href="#{escape(example)}"><img src="{escape(badge_image(example))}" '
        f'alt="Django support badge of {escape(example)}" height="20"></a>'
        f'<p class="label">It says one of</p><div class="badge-states">{states}</div></div>'
        f'<div class="command"><code>{escape(snippet)}</code><button type="button" '
        f'class="copy" data-copy="{escape(snippet)}" hidden>Copy</button></div>'
    )


def _footer(data: dict) -> str:
    """How the page is made, where its numbers come from and how to read them: three short
    columns, then the signature."""
    columns = (
        (
            "The data",
            f"Generated {escape(data['generated'])} by "
            f'<a href="{REPO_URL}">{escape(data["tool"])}</a>, every Monday, from what '
            f"the {data['packages_count']} most downloaded Django-related packages declare on "
            "PyPI.",
        ),
        (
            "Sources",
            "Package metadata from PyPI. Downloads of the last 30 days from "
            '<a href="https://github.com/hugovk/top-pypi-packages">top-pypi-packages</a>. '
            "Signs of support from READMEs and test matrices on GitHub.",
        ),
        (
            "Reading it",
            "To check is not blocked: the metadata does not say either way. The rules are "
            f'the report\'s: <a href="{REPO_URL}#how-it-decides">how it decides</a>.',
        ),
    )
    cells = "".join(
        f'<div><p class="label">{title}</p><p>{text}</p></div>' for title, text in columns
    )
    return (
        f'<footer class="site-footer"><div class="footer-columns">{cells}</div>'
        f'<div class="footer-bottom"><span>By {escape(AUTHOR)}, {brand()}</span>'
        '<span class="footer-links"><a href="feed.xml">Weekly feed</a>'
        '<a href="data.json">data.json</a>'
        f'<a href="{REPO_URL}">GitHub</a></span></div></footer>'
    )


def _cell(package: dict, version: str) -> str:
    """The status, and ``signed`` for a package to check with a sign of support."""
    status = package["status"].get(version, "")
    return f"{status} signed" if package.get("signs", {}).get(version) else status


def _compact(number: int | None) -> str:
    """``12.3M``, ``456K``: downloads at a glance."""
    if not number:
        return ""
    for size, unit in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if number >= size:
            return f"{number / size:.1f}".rstrip("0").rstrip(".") + unit
    return str(number)


# Search, filters and sorting for the package list, and the copy button. The state lives in
# the address (#q=allauth&v=6.1&status=blocked), so a filtered view can be passed on; an
# address without "=" is a plain anchor, which opens its version's section.
_SCRIPT = r"""
(() => {
  const $ = (id) => document.getElementById(id);
  const bar = $("toolbar"), body = $("rows");
  const q = $("q"), v = $("v"), st = $("status"), sort = $("sort");
  bar.hidden = false;
  const rows = [...body.rows];
  function read() {
    const h = location.hash.slice(1);
    if (!h.includes("=")) {
      const el = h && document.getElementById(decodeURIComponent(h));
      if (el && el.tagName === "DETAILS") el.open = true;
      return;
    }
    const p = new URLSearchParams(h);
    q.value = p.get("q") || "";
    if (p.get("v")) v.value = p.get("v");
    st.value = p.get("status") || "";
    sort.value = p.get("sort") || "downloads";
  }
  function apply(write) {
    const term = q.value.trim().toLowerCase(), want = st.value, ver = v.value;
    let shown = 0;
    for (const r of rows) {
      const status = (r.dataset.status.split(" ").find((s) => s.startsWith(ver + ":")) || "")
        .split(":")[1];
      const ok = (!term || r.dataset.name.includes(term)) &&
        (!want || status === want || (want === "check" && status === "check-signed"));
      r.hidden = !ok;
      shown += ok;
    }
    const key = sort.value;
    const sorted = [...rows].sort((a, b) =>
      key === "name" ? a.dataset.name.localeCompare(b.dataset.name)
      : key === "released" ? b.dataset.released.localeCompare(a.dataset.released)
      : b.dataset.downloads - a.dataset.downloads);
    body.append(...sorted);
    $("shown").textContent = shown + " of " + rows.length;
    if (write) {
      const p = new URLSearchParams();
      if (term) p.set("q", term);
      if (want) p.set("status", want);
      if (term || want) p.set("v", ver);
      if (key !== "downloads") p.set("sort", key);
      history.replaceState(null, "", p.toString() ? "#" + p : location.pathname);
    }
  }
  for (const el of [q, v, st, sort]) el.addEventListener("input", () => apply(true));
  // A click on a row marks it, so it is easy to follow across the columns; a second click
  // clears it. Links and selected text keep their own behaviour.
  body.addEventListener("click", (e) => {
    const r = e.target.closest("tr");
    if (!r || e.target.closest("a") || String(getSelection())) return;
    const was = r.classList.contains("active");
    for (const other of body.querySelectorAll("tr.active")) other.classList.remove("active");
    r.classList.toggle("active", !was);
  });
  addEventListener("hashchange", () => { read(); apply(false); });
  addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== q) { e.preventDefault(); q.focus(); }
  });
  // The command field copies like its button, and the button says so.
  for (const b of document.querySelectorAll("button.copy")) {
    b.hidden = false;
    const field = b.closest(".command");
    field.classList.add("copyable");
    field.addEventListener("click", () => navigator.clipboard.writeText(b.dataset.copy).then(() => {
      b.textContent = "Copied";
      clearTimeout(b.timer);
      b.timer = setTimeout(() => (b.textContent = "Copy"), 1500);
    }));
  }
  read();
  apply(false);
})();
(() => {
  const charts = document.querySelector(".charts");
  if (!charts) return;
  const spec = JSON.parse(document.getElementById("chart-data").textContent);
  const toggle = charts.querySelector(".toggle");
  toggle.hidden = false;
  toggle.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    charts.dataset.mode = b.dataset.mode;
    for (const x of toggle.querySelectorAll("button")) x.setAttribute("aria-pressed", x === b);
    for (const f of figures) if (f.classList.contains("hover")) f.refresh();
  });
  const legend = charts.querySelectorAll(".legend button");
  function focusOn(v) {
    if (v) charts.dataset.focus = v; else delete charts.dataset.focus;
    for (const b of legend) b.setAttribute("aria-pressed", b.dataset.v === v);
    for (const g of charts.querySelectorAll(".series")) {
      g.classList.toggle("focus", g.dataset.v === v);
    }
  }
  for (const b of legend) b.addEventListener("click", () =>
    focusOn(charts.dataset.focus === b.dataset.v ? null : b.dataset.v));
  const months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
  const when = (kind, x) => {
    if (kind === "release") return x ? x + " days after release" : "release day";
    const d = new Date((x - 719163) * 86400000);  // ordinal day to a date
    return d.getUTCDate() + " " + months[d.getUTCMonth()] + " " + d.getUTCFullYear();
  };
  const figures = [...charts.querySelectorAll("figure.chart")];
  for (const fig of figures) {
    const kind = fig.dataset.chart, c = spec[kind];
    const svg = fig.querySelector("svg"), tip = fig.querySelector(".tooltip");
    const line = fig.querySelector(".crosshair");
    const [x0, x1] = c.x, [left, top, w, h] = c.plot;
    const width = svg.viewBox.baseVal.width;
    let at = null;
    const mode = () => c.modes[charts.dataset.mode];
    const xs = () => [...new Set(mode().series.flatMap((s) => s.p.map((p) => p[0])))]
      .sort((a, b) => a - b);
    const px = (x) => left + (x - x0) / ((x1 - x0) || 1) * w;
    function value(points, x) {  // the share at x, or the last one before it
      if (!points.length || x < points[0][0]) return null;
      let found = null;
      for (const p of points) if (p[0] <= x) found = p[1];
      return x > points[points.length - 1][0] ? null : found;
    }
    function show(x) {
      at = x;
      const pos = px(x);
      line.setAttribute("x1", pos); line.setAttribute("x2", pos);
      fig.classList.add("hover");
      const rows = mode().series.map((s) => [s, value(s.p, x)]).filter((r) => r[1] !== null);
      tip.replaceChildren();
      const head = document.createElement("b");
      head.textContent = when(kind, x);
      tip.append(head);
      for (const [s, v] of rows) {
        const row = document.createElement("span");
        row.style.setProperty("--c", s.c);
        const key = document.createElement("i");
        key.className = "key";
        const val = document.createElement("i");
        val.className = "val";
        val.textContent = Math.round(v * 100) + "%";
        row.append(key, "Django " + s.name, val);
        tip.append(row);
      }
      tip.hidden = false;
      const scale = svg.getBoundingClientRect().width / width;
      const offset = svg.getBoundingClientRect().left - fig.getBoundingClientRect().left;
      const room = pos * scale + offset;
      tip.style.left = (room + 16 + tip.offsetWidth > fig.clientWidth
        ? room - 16 - tip.offsetWidth : room + 16) + "px";
    }
    function hide() { fig.classList.remove("hover"); tip.hidden = true; }
    fig.refresh = () => at !== null && show(nearest(at));
    function nearest(x) {
      return xs().reduce((a, b) => (Math.abs(b - x) < Math.abs(a - x) ? b : a));
    }
    svg.addEventListener("pointermove", (e) => {
      const r = svg.getBoundingClientRect();
      const vx = (e.clientX - r.left) * width / r.width;
      if (vx < left - 8 || vx > left + w + 8) return hide();
      show(nearest(x0 + (vx - left) / w * (x1 - x0)));
    });
    svg.addEventListener("pointerleave", hide);
    fig.addEventListener("focus", () => show(at ?? xs().at(-1)));
    fig.addEventListener("blur", hide);
    fig.addEventListener("keydown", (e) => {
      const all = xs(), i = Math.max(0, all.indexOf(at ?? all.at(-1)));
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        show(all[Math.min(all.length - 1, Math.max(0, i + (e.key === "ArrowRight" ? 1 : -1)))]);
      } else if (e.key === "Escape") hide();
    });
  }
})();
"""


def _meta_tags(data: dict) -> str:
    """What search engines and link previews show: the numbers, and the weekly image."""
    text = escape(description(data))
    title = "How ready is the Django ecosystem?"
    image = f"{ECOSYSTEM_URL}og.png"
    return "\n".join(
        [
            f'<meta name="description" content="{text}">',
            f'<link rel="canonical" href="{ECOSYSTEM_URL}">',
            '<meta property="og:type" content="website">',
            f'<meta property="og:url" content="{ECOSYSTEM_URL}">',
            f'<meta property="og:title" content="{title}">',
            f'<meta property="og:description" content="{text}">',
            f'<meta property="og:image" content="{image}">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            f'<meta property="og:image:alt" content="{text}">',
            '<meta name="twitter:card" content="summary_large_image">',
        ]
    )


def _figures(data: dict) -> str:
    """The three numbers the page leads with, each closed by the pushed pixel in its status
    colour."""
    h = highlights(data)
    new = h["newest"]
    tiles = []
    if h["lts"]:
        lts = h["lts"]
        tiles.append(
            ("ready", lts["ready"], f"of {h['total']} declare Django {lts['version']} LTS")
        )
    tiles.append(
        (
            "ready",
            new["ready"],
            f"declare Django {new['version']}, {new['days']} days after its release",
        )
    )
    tiles.append(("blocked", new["blocked"], f"packages block Django {new['version']}"))
    inner = "".join(
        f'<div class="figure {css}"><b>{value}</b><span>{escape(label)}</span></div>'
        for css, value, label in tiles
    )
    return f'<div class="figures">{inner}</div>'


_CHART = {"width": 860, "height": 340, "left": 48, "right": 104, "top": 16, "bottom": 40}
_SLOTS = ("s1", "s2", "s3", "s4", "s5")
"""Colours for the newest five released versions; older ones take the neutral ``s-old``."""
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _chart_series(data: dict) -> list[dict]:
    """Every version with a curve, newest first, with its colour and its points for both
    charts and both ways of counting. A version keeps its colour until a newer one comes out."""
    shown = [v for v in data["versions"] if len(v["curve"]) >= 2]
    found = []
    for i, v in enumerate(reversed(shown)):
        lts = DJANGO.is_lts(Version(v["version"]))

        def dated(points):
            return [[date.fromisoformat(day).toordinal(), share] for day, share in points or []]

        found.append(
            {
                "v": v["version"],
                "name": f"{v['version']}{' LTS' if lts else ''}",
                "c": f"var(--{_SLOTS[i]})" if i < len(_SLOTS) else "var(--s-old)",
                "release": {
                    "packages": v["curve"],
                    "downloads": v.get("curve_downloads") or [],
                },
                "calendar": {
                    "packages": dated(v.get("timeline")),
                    "downloads": dated(v.get("timeline_downloads")),
                },
            }
        )
    return found


def _ticks(kind: str, x0: int, x1: int) -> list[tuple[int, str]]:
    if kind == "release":
        return [
            (0, "release"),
            (90, "3 months"),
            (180, "6 months"),
            (365, "1 year"),
            (540, "18 months"),
            (730, "2 years"),
        ]
    ticks = []
    first = date.fromordinal(x0)
    for year in range(first.year, date.fromordinal(x1).year + 1):
        for month in (1, 7):
            day = date(year, month, 1).toordinal()
            if x0 <= day <= x1:
                ticks.append((day, f"{_MONTHS[month - 1]} {year}"))
    return ticks


def _charts(data: dict) -> str:
    """How fast packages declare a new Django, two ways: by days after each release, and side
    by side in calendar time. Both count packages or their downloads; the script switches,
    highlights a version and shows the values under the pointer. Without it, both charts show
    the share of packages, labelled at the end of each line."""
    series = _chart_series(data)
    if not series:
        return ""
    spec = {}
    figures = {}
    for kind in ("release", "calendar"):
        xs = [x for s in series for mode in s[kind].values() for x, _ in mode]
        if not xs:
            continue
        x0, x1 = (0, CURVE_DAYS[-1]) if kind == "release" else (min(xs), max(xs))
        layers, modes = [], {}
        for mode in ("packages", "downloads"):
            layer, ceiling = _layer(series, kind, mode, x0, x1)
            layers.append(layer)
            modes[mode] = {
                "ceiling": ceiling,
                "series": [
                    {"v": s["v"], "name": s["name"], "c": s["c"], "p": s[kind][mode]}
                    for s in series
                    if s[kind][mode]
                ],
            }
        w, h = _CHART["width"], _CHART["height"]
        left, top = _CHART["left"], _CHART["top"]
        plot_w = w - left - _CHART["right"]
        plot_h = h - top - _CHART["bottom"]
        spec[kind] = {"x": [x0, x1], "plot": [left, top, plot_w, plot_h]}
        spec[kind]["modes"] = modes
        label = (
            "Share of packages that declared each Django version, by days after its release"
            if kind == "release"
            else "Share of packages that declared each Django version, by date"
        )
        figures[kind] = (
            f'<figure class="chart panel" data-chart="{kind}" tabindex="0" role="group" '
            f'aria-label="{label}. Arrow keys move through the points.">'
            f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{label}">{"".join(layers)}'
            f'<line class="crosshair" x1="0" x2="0" y1="{top}" y2="{top + plot_h}"/></svg>'
            '<div class="tooltip" hidden></div></figure>'
        )
    legend = "".join(
        f'<button type="button" data-v="{escape(s["v"])}" aria-pressed="false" '
        f'style="--c:{s["c"]}"><i class="key"></i>Django {escape(s["name"])}</button>'
        for s in series
    )
    data_json = json.dumps(spec, separators=(",", ":")).replace("<", "\\u003c")
    total = data["packages_count"]
    return f"""<section class="charts" data-mode="packages">
<h2>How fast packages catch up</h2>
<p class="hint">Share of the {total} packages with a release that declared each version, by days
after its release. Downloads counts each package with its downloads of the last 30 days.</p>
<div class="chart-controls">
<div class="toggle" role="group" aria-label="Count" hidden><button type="button"
data-mode="packages" aria-pressed="true">Packages</button><button type="button"
data-mode="downloads" aria-pressed="false">Downloads</button></div>
<div class="legend" role="group" aria-label="Highlight a version">{legend}</div>
</div>
{figures.get("release", "")}
<h3>Side by side in time</h3>
<p class="hint">The same shares by date: where each version stood at any moment of the last
three years.</p>
{figures.get("calendar", "")}
<script type="application/json" id="chart-data">{data_json}</script>
</section>"""


def _layer(series: list[dict], kind: str, mode: str, x0: int, x1: int) -> tuple[str, float]:
    """One way of counting as SVG: grid, axis, a line per version and its end label."""
    w, h = _CHART["width"], _CHART["height"]
    left, right, top, bottom = (_CHART[k] for k in ("left", "right", "top", "bottom"))
    plot_w, plot_h = w - left - right, h - top - bottom
    shares = [share for s in series for _, share in s[kind][mode]]
    highest = max(shares, default=0)
    ceiling = max(0.1, min(1.0, -(-highest * 10 // 1) / 10))  # up to the next 10 %
    span = (x1 - x0) or 1

    def x(value: float) -> float:
        return left + (value - x0) / span * plot_w

    def y(share: float) -> float:
        return top + plot_h - share / ceiling * plot_h

    parts = []
    for i in range(round(ceiling * 10) + 1):
        share = i / 10
        parts.append(
            f'<line class="grid" x1="{left}" x2="{left + plot_w}" y1="{y(share):.1f}" '
            f'y2="{y(share):.1f}"/><text class="tick" x="{left - 8}" y="{y(share) + 4:.1f}" '
            f'text-anchor="end">{i * 10}%</text>'
        )
    for value, label in _ticks(kind, x0, x1):
        parts.append(
            f'<text class="tick" x="{x(value):.1f}" y="{top + plot_h + 22}" '
            f'text-anchor="middle">{escape(label)}</text>'
        )
    ends = []
    for s in reversed(series):  # the newest drawn last, on top
        points = s[kind][mode]
        if len(points) < 2:
            continue
        line = " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in points)
        last_x, last_v = points[-1]
        ends.append([y(last_v), x(last_x), y(last_v), s, last_v])
        parts.append(
            f'<g class="series" data-v="{escape(s["v"])}" style="--c:{s["c"]}">'
            f'<polyline points="{line}"/><circle class="end" cx="{x(last_x):.1f}" '
            f'cy="{y(last_v):.1f}" r="4"/></g>'
        )
    # End labels that would touch move apart, with a hairline back to their line's end.
    ends.sort(key=lambda e: e[0])
    for i in range(1, len(ends)):
        ends[i][0] = max(ends[i][0], ends[i - 1][0] + 15)
    labels = []
    for label_y, end_x, end_y, s, share in ends:
        lx = end_x + 10
        leader = (
            f'<line class="leader" x1="{end_x + 5:.1f}" y1="{end_y:.1f}" x2="{lx - 2:.1f}" '
            f'y2="{label_y - 4:.1f}"/>'
            if abs(label_y - end_y) > 1
            else ""
        )
        labels.append(
            f'<g class="series" data-v="{escape(s["v"])}">{leader}'
            f'<text class="end-label" x="{lx:.1f}" y="{label_y:.1f}">{escape(s["name"])} '
            f'<tspan class="value">{share * 100:.0f}%</tspan></text></g>'
        )
    return f'<g class="layer {mode}">{"".join(parts)}{"".join(labels)}</g>', ceiling


_MOVES = (("ready", "now ready"), ("blocked", "now blocked"), ("dropped", "no longer ready"))


def _change_lines(changed: dict) -> list[tuple[str, str]]:
    """``("Django 6.1", "now ready: django-filter, django-ninja")`` per version and move."""
    lines = []
    for version, moved in sorted(changed["versions"].items(), key=lambda kv: Version(kv[0])):
        for key, label in _MOVES:
            if moved.get(key):
                lines.append((f"Django {version}", f"{label}: {', '.join(moved[key])}"))
    return lines


def _changes(data: dict) -> str:
    """What changed since last week, the reason to come back."""
    changed = data.get("changes")
    if changed is None:
        return (
            '<h2>This week</h2><p class="hint">Changes since the week before show up here '
            "from the next run on.</p>"
        )
    lines = _change_lines(changed)
    since = escape(changed["since"])
    if not lines:
        return f'<h2>This week</h2><p class="hint">Nothing changed since {since}.</p>'
    items = "".join(f"<li><b>{escape(version)}</b> {escape(text)}</li>" for version, text in lines)
    return (
        f'<h2>This week</h2><p class="hint">Since {since}. Also as a '
        f'<a href="feed.xml">feed</a>.</p><ul class="changes-list">{items}</ul>'
    )


def feed(snapshots: list[dict]) -> str:
    """An Atom feed with one entry per week that changed something, newest first."""
    entries = []
    for snap in sorted(snapshots, key=lambda d: d["generated"], reverse=True):
        changed = snap.get("changes")
        if not changed or not changed["versions"]:
            continue
        lines = _change_lines(changed)
        ready = sum(len(m.get("ready", [])) for m in changed["versions"].values())
        title = (
            f"Week of {snap['generated']}: {ready} newly ready"
            if ready
            else (f"Week of {snap['generated']}: {len(lines)} changes")
        )
        body = "".join(f"<li><b>{escape(v)}</b> {escape(t)}</li>" for v, t in lines)
        entries.append(
            f"<entry><title>{escape(title)}</title>"
            f'<link href="{ECOSYSTEM_URL}"/>'
            f"<id>{ECOSYSTEM_URL}#week-{snap['generated']}</id>"
            f"<updated>{snap['generated']}T06:00:00Z</updated>"
            f'<content type="html">{escape(f"<ul>{body}</ul>")}</content></entry>'
        )
        if len(entries) == 12:
            break
    updated = max((d["generated"] for d in snapshots), default="1970-01-01")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>How ready is the Django ecosystem?</title>"
        f'<link href="{ECOSYSTEM_URL}"/><link rel="self" href="{ECOSYSTEM_URL}feed.xml"/>'
        f"<id>{ECOSYSTEM_URL}</id><updated>{updated}T06:00:00Z</updated>"
        f"<author><name>{escape(AUTHOR)}</name></author>{''.join(entries)}</feed>\n"
    )


def _title(package: dict, version: str) -> str:
    title = package["status"].get(version, "not checked")
    signs = package.get("signs", {}).get(version)
    if signs:
        title = f"{title}: {'; '.join(signs)}"
    since = package["declared_since"].get(version)
    return f"{title}, first declared {since}" if since else title


def _curve_text(curve: list[list]) -> str:
    """Where the curve stands now, for the table: ``56% after 2 years``."""
    if not curve:
        return ""
    days, share = curve[-1]
    return f"{share * 100:.0f}% after {_days(days)}"


def _days(days: int) -> str:
    if days and days % 365 == 0:
        return f"{days // 365} year{'s' if days > 365 else ''}"
    if days > 365:
        return f"{round(days / 30.4)} months"
    return f"{days} days" if days else "the release"


# --- badges for maintainers ----------------------------------------------------------------

_BADGE = {
    "ready": ("declared", "brightgreen"),
    "check": ("not declared", "lightgrey"),
    "blocked": ("excluded", "red"),
}


def badges(data: dict) -> dict[str, dict]:
    """Shields.io endpoint badges: ``name/6.1.json`` per package and version, and
    ``name.json`` for the newest release, as ``{"schemaVersion": 1, "label": ...}``."""
    released = [v["version"] for v in data["versions"] if v["released"]]
    found = {}
    for p in data["packages"]:
        for version, status in p["status"].items():
            message, color = _BADGE[status]
            found[f"{p['name']}/{version}.json"] = {
                "schemaVersion": 1,
                "label": f"Django {version}",
                "message": message,
                "color": color,
            }
        if released and released[-1] in p["status"]:
            found[f"{p['name']}.json"] = found[f"{p['name']}/{released[-1]}.json"]
    return found


def write_badges(data: dict, out: Path) -> None:
    for path, badge in badges(data).items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(badge))


def badge_image(name: str, version: str | None = None) -> str:
    """The Shields.io URL that draws a package's badge from its endpoint on this page."""
    path = f"{name}/{version}.json" if version else f"{name}.json"
    endpoint = f"{ECOSYSTEM_URL}badges/{path}"
    return f"https://img.shields.io/endpoint?url={urllib.parse.quote(endpoint, safe='')}"


def badge_snippet(name: str) -> str:
    """The Markdown for a package's README: the badge, linked to the package on the page."""
    return f"[![Django support]({badge_image(name)})]({ECOSYSTEM_URL}#{name})"


# --- the command line -----------------------------------------------------------------------


def main(argv: list[str] | None = None, pypi: PyPI | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ecosystem/build.py", description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["select", "build"])
    parser.add_argument("--count", type=int, default=COUNT)
    parser.add_argument("--out", type=Path, default=SITE)
    parser.add_argument(
        "--evidence",
        action="store_true",
        help="look at the test matrix on GitHub for packages to check (uses GITHUB_TOKEN)",
    )
    parser.add_argument(
        "--history",
        type=Path,
        help="directory of the weekly data.json snapshots: compares with the newest before "
        "today, then adds today's",
    )
    args = parser.parse_args(argv)
    pypi = pypi or PyPI(cache_dir=default_cache_dir())
    if args.command == "select":
        updated, top, _ = _top()
        chosen = select(pypi, top, args.count)
        PACKAGES.write_text(
            json.dumps({"source": TOP, "top_updated": updated, "packages": chosen}, indent=1) + "\n"
        )
        print(f"{PACKAGES.name}: {len(chosen)} packages", file=sys.stderr)
        return 0
    packages = json.loads(PACKAGES.read_text())["packages"]
    try:
        downloads = _top()[2]
    except OSError as exc:  # the page without download numbers beats no page
        print(f"downloads left out: {exc}", file=sys.stderr)
        downloads = None
    files = None
    if args.evidence:
        token = os.environ.get("GITHUB_TOKEN")
        files = GitHubFiles(default_cache_dir(), token=token)
    data = build(pypi, packages, downloads=downloads, files=files)
    snapshots = []
    if args.history:
        args.history.mkdir(parents=True, exist_ok=True)
        data["changes"] = changes(previous(args.history, data["generated"]), data)
        (args.history / f"{data['generated']}.json").write_text(json.dumps(data, indent=1) + "\n")
        snapshots = [json.loads(p.read_text()) for p in sorted(args.history.glob("*.json"))]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "feed.xml").write_text(feed(snapshots or [data]))
    (args.out / "data.json").write_text(json.dumps(data, indent=1) + "\n")
    (args.out / "index.html").write_text(page(data))
    shutil.copytree(HERE / "fonts", args.out / "fonts", dirs_exist_ok=True)
    write_badges(data, args.out / "badges")
    if render_card(card(data), args.out / "og.png"):
        print(f"wrote {args.out}/og.png", file=sys.stderr)
    else:
        print("og.png left out: pip install playwright to render it", file=sys.stderr)
    print(f"wrote {args.out}/index.html and data.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
