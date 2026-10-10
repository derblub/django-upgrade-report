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
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import Version

from django_upgrade_report import AUTHOR, ECOSYSTEM_URL, REPO_URL, __version__
from django_upgrade_report.analysis import (
    Status,
    Verdict,
    _build_target,
    _series,
    analyse,
    is_django_related,
    supports,
)
from django_upgrade_report.frameworks import DJANGO
from django_upgrade_report.pypi import USER_AGENT, PyPI, PyPIError, default_cache_dir
from django_upgrade_report.render.html import _CSS, FAVICON, brand
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


def _top() -> tuple[str, list[str]]:
    request = urllib.request.Request(TOP, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)
    return data.get("last_update", ""), [row["project"] for row in data["rows"]]


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


def build(pypi: PyPI, packages: list[str], today: date | None = None) -> dict:
    """The status of the newest release of each package for every Django version."""
    today = today or datetime.now(timezone.utc).date()
    django = pypi.project("django")
    if django is None:
        raise RuntimeError("Could not read Django's release history")
    series = _series(django)
    rows = {
        name: {"name": name, "version": None, "status": {}, "declared_since": {}}
        for name in packages
    }
    columns = []
    for target, before in versions(django):
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
        for name, p in judged.items():
            status = Status.CHECK if p.status is Status.UPGRADE else p.status  # a pre-release
            counts[status.value] += 1
            rows[name]["status"][label] = status.value
            if since.get(name) is not None:
                rows[name]["declared_since"][label] = since[name].date().isoformat()
                declared.append(since[name].date())
        ga = goal.ga.date() if goal.ga else None
        columns.append(
            {
                "version": label,
                "released": goal.released,
                "ga": ga.isoformat() if ga else None,
                "counts": counts,
                "curve": _curve(declared, ga, today, len(judged)),
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


def _curve(since: list[date], ga: date | None, today: date, total: int) -> list[list]:
    """``[days after the release, share that had declared it]``, up to today: how fast the
    ecosystem caught up, counting packages that dropped the version since, too."""
    if ga is None or not total:
        return []
    points = []
    for days in CURVE_DAYS:
        day = ga + timedelta(days=days)
        if day > today:
            break
        points.append([days, round(sum(1 for d in since if d <= day) / total, 3)])
    return points


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
    """The preview image as a page, 1200 by 630: what a shared link shows."""
    h = highlights(data)
    new = h["newest"]
    figures = []
    if h["lts"]:
        figures.append(
            (f"{h['lts']['ready']}", f"of {h['total']} declare Django {h['lts']['version']} LTS")
        )
    figures.append(
        (
            f"{new['ready']}",
            f"declare Django {new['version']}, {new['days']} days after its release",
        )
    )
    figures.append((f"{new['blocked']}", f"packages block Django {new['version']}"))
    tiles = "".join(
        f'<div class="figure"><b>{escape(value)}</b><span>{escape(label)}</span></div>'
        for value, label in figures
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><style>{_CSS}{_CARD_CSS}</style></head>
<body><div class="card">
<p class="kicker">Django ecosystem readiness · {escape(data["generated"])}</p>
<h1>How ready is the Django ecosystem?</h1>
<div class="figures">{tiles}</div>
<p class="foot"><span>derblub.github.io/django-upgrade-report · updated every week</span>
<span>{brand()}</span></p>
</div></body></html>
"""


_CARD_CSS = """
:root { color-scheme: light; }
body { margin: 0; width: 1200px; height: 630px; background: var(--bg); }
.card { box-sizing: border-box; width: 1200px; height: 630px; padding: 64px 72px;
  display: flex; flex-direction: column; }
.kicker { color: var(--muted); font-size: 24px; margin: 0 0 12px; }
.card h1 { font-size: 60px; margin: 0; }
.figures { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; margin-top: 40px; }
.figure { background: var(--panel); border: 1px solid var(--line); border-radius: 16px;
  padding: 28px; }
.figure b { display: block; font-size: 84px; line-height: 1; color: var(--ready);
  font-variant-numeric: tabular-nums; }
.figure:last-child b { color: var(--blocked); }
.figure span { display: block; margin-top: 12px; font-size: 24px; color: var(--text); }
.foot { margin-top: auto; color: var(--muted); font-size: 22px; display: flex;
  justify-content: space-between; }
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
.ecosystem { margin-top: 32px; }
.ecosystem h2 { margin-top: 40px; }
.ecosystem td.name a { color: inherit; }
.curve-label { display: block; color: var(--muted); font-size: 12px; }
.bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; min-width: 160px;
  background: var(--line); }
.bar span { display: block; }
.bar .ready { background: var(--ready); }
.bar .check { background: var(--check); }
.bar .blocked { background: var(--blocked); }
.curve { width: 160px; height: 48px; }
.curve polyline { fill: none; stroke: var(--ready); stroke-width: 2; }
td.s { text-align: center; font-weight: 600; }
td.s.ready { color: var(--ready); } td.s.check { color: var(--check); }
td.s.blocked { color: var(--blocked); }
"""
_ICON = {"ready": "✓", "check": "?", "blocked": "✗"}


def page(data: dict) -> str:
    """A static page with the same look as the HTML report."""
    heads = "".join(f"<th>{escape(v['version'])}</th>" for v in data["versions"])
    summary = []
    for v in data["versions"]:
        counts = v["counts"]
        total = sum(counts.values()) or 1
        bar = "".join(
            f'<span class="{key}" style="width:{counts[key] / total * 100:.1f}%"></span>'
            for key in ("ready", "check", "blocked")
        )
        when = f"released {v['ga']}" if v["released"] else "not released yet"
        summary.append(
            f'<tr><td class="name">Django {escape(v["version"])}</td><td>{escape(when)}</td>'
            f'<td><div class="bar" title="{counts["ready"]} ready, {counts["check"]} to check, '
            f'{counts["blocked"]} blocked">{bar}</div></td>'
            f"<td>{counts['ready']}</td><td>{counts['check']}</td><td>{counts['blocked']}</td>"
            f"<td>{_svg(v['curve'])}</td></tr>"
        )
    rows = []
    for p in data["packages"]:
        cells = "".join(
            f'<td class="s {p["status"].get(v["version"], "")}" '
            f'title="{escape(_title(p, v["version"]))}">'
            f"{_ICON.get(p['status'].get(v['version']), '')}</td>"
            for v in data["versions"]
        )
        name = escape(p["name"])
        rows.append(
            f'<tr><td class="name"><a href="https://pypi.org/project/{name}/">{name}</a></td>'
            f"<td>{escape(str(p['version']))}</td>{cells}</tr>"
        )
    inner = f"""<header>
<h1>How ready is the Django ecosystem?</h1>
<p>The newest release of the {data["packages_count"]} most downloaded Django-related packages on
PyPI, judged against every Django version by what its maintainers declare: the
<code>Framework :: Django</code> classifiers and the Django requirement. Updated every week.</p>
</header>
<div class="ecosystem">
<div class="table overview"><table><thead><tr><th>Version</th><th></th><th>Share</th>
<th>ready</th><th>to check</th><th>blocked</th><th>Declared after release</th></tr></thead>
<tbody>{"".join(summary)}</tbody></table></div>
<p class="hint">Declared after release: the share of packages with a release that declared the
version by then, counting packages whose newest release has dropped it since.</p>
<h2>Every package</h2>
<div class="table"><table><thead><tr><th>Package</th><th>Newest</th>{heads}</tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
</div>
<p class="meta">Generated {escape(data["generated"])} by
<a href="{REPO_URL}">{escape(data["tool"])}</a> with the rules of the report:
<a href="{REPO_URL}#how-it-decides">how it decides</a>. To check is not blocked: the
metadata does not say either way. Your own project:
<code>uvx django-upgrade-report</code>. By {escape(AUTHOR)}, {brand()}.</p>"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Django ecosystem readiness</title>
{_meta_tags(data)}
{FAVICON}
<style>{_CSS}{_PAGE_CSS}</style>
</head>
<body>
<main>
{inner}
</main>
</body>
</html>
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


def _title(package: dict, version: str) -> str:
    title = package["status"].get(version, "not checked")
    since = package["declared_since"].get(version)
    return f"{title}, first declared {since}" if since else title


def _svg(curve: list[list]) -> str:
    """The share ready over the days after the release, as a small line."""
    if len(curve) < 2:
        return ""
    width, height = 160, 48
    most = CURVE_DAYS[-1]
    points = " ".join(
        f"{days / most * width:.1f},{height - share * height:.1f}" for days, share in curve
    )
    last = curve[-1]
    label = f"{last[1] * 100:.0f}% had declared it {last[0]} days after the release"
    return (
        f'<svg class="curve" viewBox="0 0 {width} {height}" role="img" aria-label="{label}">'
        f'<title>{label}</title><polyline points="{points}"/></svg>'
        f'<span class="curve-label">{last[1] * 100:.0f}% after {_days(last[0])}</span>'
    )


def _days(days: int) -> str:
    if days and days % 365 == 0:
        return f"{days // 365} year{'s' if days > 365 else ''}"
    if days > 365:
        return f"{round(days / 30.4)} months"
    return f"{days} days" if days else "the release"


# --- the command line -----------------------------------------------------------------------


def main(argv: list[str] | None = None, pypi: PyPI | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ecosystem/build.py", description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["select", "build"])
    parser.add_argument("--count", type=int, default=COUNT)
    parser.add_argument("--out", type=Path, default=SITE)
    args = parser.parse_args(argv)
    pypi = pypi or PyPI(cache_dir=default_cache_dir())
    if args.command == "select":
        updated, top = _top()
        chosen = select(pypi, top, args.count)
        PACKAGES.write_text(
            json.dumps({"source": TOP, "top_updated": updated, "packages": chosen}, indent=1) + "\n"
        )
        print(f"{PACKAGES.name}: {len(chosen)} packages", file=sys.stderr)
        return 0
    packages = json.loads(PACKAGES.read_text())["packages"]
    data = build(pypi, packages)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "data.json").write_text(json.dumps(data, indent=1) + "\n")
    (args.out / "index.html").write_text(page(data))
    if render_card(card(data), args.out / "og.png"):
        print(f"wrote {args.out}/og.png", file=sys.stderr)
    else:
        print("og.png left out: pip install playwright to render it", file=sys.stderr)
    print(f"wrote {args.out}/index.html and data.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
