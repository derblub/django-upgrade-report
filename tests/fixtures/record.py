"""Record the PyPI metadata the golden tests run against.

    PYTHONPATH=src python3 tests/fixtures/record.py [NAME ...]   # from the repository root

Downloads each project's JSON and the release JSONs from the oldest version a test uses
onwards, keeps only the fields the engine reads, and writes one file per project to
``tests/fixtures/pypi/``. Re-recording changes the facts the tests assert, so check the
golden tests in ``tests/test_analysis.py`` against the new data.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from django_upgrade_report.pypi import USER_AGENT, _slim_files

INDEX = "https://pypi.org/pypi"
OUT = Path(__file__).parent / "pypi"

# Project -> the oldest release the tests need metadata for. All newer releases are recorded,
# because the engine may look at any of them.
CASES = {
    "django-allauth": "0.44.0",
    "wagtail": "5.2.8",
    "wagtail-grapple": "0.31.0",
    "django-prometheus": "2.4.0",
    "django-ses": "4.8.0",
    "mayan-edms": "4.12.2",
    "netbox": "4.7.1",
    "dj-database-url": "3.0.1",
    "djangorestframework": "3.12.0",
    "wagtail-modeladmin": "2.0.0",
    "wagtail-localize": "1.11",
    "django-cms": "4.1.0",
    "djangocms-text": "0.9.2",
}
# Django: the release history from here on, and the release JSON of every X.Y.0 and of the
# versions the tests use as the project's current Django.
DJANGO_SINCE = "3.0"
DJANGO_CURRENT = ["3.2.25", "4.2.7", "4.2.20", "5.2.17", "6.1.1"]

_KEEP_CLASSIFIER = re.compile(r"^(Framework :: (Django|Wagtail)|Development Status :: 7)")
_KEEP_REQUIREMENT = {"django", "wagtail", "django-cms"}


def get(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def slim_info(info: dict, django: bool = False) -> dict:
    """The fields ``pypi._release_info`` reads, without the ones the engine ignores."""
    slim = {
        "name": info["name"],
        "version": info["version"],
        "classifiers": [] if django else [c for c in info.get("classifiers") or () if _keep(c)],
        "requires_dist": [] if django else [r for r in info.get("requires_dist") or () if _dep(r)],
        "requires_python": info.get("requires_python") or None,
    }
    return {key: value for key, value in slim.items() if value}


def _keep(classifier: str) -> bool:
    return bool(_KEEP_CLASSIFIER.match(classifier))


def _dep(line: str) -> bool:
    try:
        return canonicalize_name(Requirement(line).name) in _KEEP_REQUIREMENT
    except InvalidRequirement:
        return False


def _version(raw: str) -> Version | None:
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def record(name: str, since: str, extra: list[str] = (), django: bool = False) -> None:
    data = get(f"{INDEX}/{name}/json")
    floor = Version(since)
    # version -> earliest upload time, or None for a release without files
    uploaded: dict[str, str | None] = {}
    yanked = []
    for raw, files in data["releases"].items():
        v = _version(raw)
        if v is None or (django and v < floor):
            continue
        slim = _slim_files(files)
        uploaded[raw] = slim[0]["upload_time_iso_8601"] if slim else None
        if slim and slim[0]["yanked"]:
            yanked.append(raw)
    wanted = [
        raw
        for raw, time in uploaded.items()
        if time
        and (v := Version(raw)) >= floor
        and not v.is_prerelease
        and (not django or v.micro == 0)
    ]
    # The engine reads the newest pre-release when it is newer than every stable release.
    # Like Project.stable_releases(): installable and not yanked. Raw keys, as PyPI spells them.
    usable = [raw for raw, time in uploaded.items() if time and raw not in yanked]
    stable = [raw for raw in usable if not Version(raw).is_prerelease]
    pre = [raw for raw in usable if Version(raw).is_prerelease]
    newest_pre = max(pre, key=Version, default=None)
    if (
        not django
        and newest_pre
        and (not stable or Version(newest_pre) > max(map(Version, stable)))
    ):
        wanted.append(newest_pre)
    wanted += [raw for raw in extra if raw not in wanted]
    with ThreadPoolExecutor(8) as pool:
        infos = pool.map(lambda raw: get(f"{INDEX}/{name}/{raw}/json")["info"], wanted)
        recorded = {raw: slim_info(info, django) for raw, info in zip(wanted, infos, strict=True)}

    def by_version(items: dict) -> dict:
        return dict(sorted(items.items(), key=lambda item: Version(item[0])))

    lines = [
        "{",
        f' "info": {_compact(slim_info(data["info"], django))},',
        f' "yanked": {_compact(sorted(yanked, key=Version))},',
        f' "uploaded": {_rows(by_version(uploaded))},',
        f' "releases": {_rows(by_version(recorded))}',
        "}",
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{canonicalize_name(name)}.json"
    path.write_text("\n".join(lines) + "\n")
    print(f"{path.name}: {len(uploaded)} releases, {len(recorded)} recorded", file=sys.stderr)


def _compact(value) -> str:
    return json.dumps(value, separators=(",", ":"))


def _rows(items: dict) -> str:
    """One line per entry keeps the files small and their diffs readable."""
    rows = [f"  {_compact(key)}:{_compact(value)}" for key, value in items.items()]
    return "{\n" + ",\n".join(rows) + "\n }"


def main(names: list[str]) -> None:
    """Record every case, or only the projects named on the command line."""
    unknown = set(names) - {"django", *CASES}
    if unknown:
        sys.exit(f"not in CASES: {', '.join(sorted(unknown))}")
    if not names or "django" in names:
        record("django", DJANGO_SINCE, DJANGO_CURRENT, django=True)
    for name, since in CASES.items():
        if not names or name in names:
            record(name, since)


if __name__ == "__main__":
    main(sys.argv[1:])
