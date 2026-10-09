"""Output formats. Every renderer walks the same sections in the same order."""

from __future__ import annotations

import re
from dataclasses import dataclass

from django_upgrade_report.analysis import PackageReport, PathReport, Phase, Report, Status
from django_upgrade_report.projects import safe_url


@dataclass
class Section:
    key: str
    title: str
    hint: str
    packages: list[PackageReport]


def sections(report: Report) -> list[Section]:
    upgrades = report.by_status(Status.UPGRADE)
    ready = report.by_status(Status.READY)
    unpinned = any(not p.current for p in ready)
    current = _minor_label(report.current_django)
    blocked = report.by_status(Status.BLOCKED)
    result = [
        Section(
            "blocked",
            "Blocked",
            f"Your copy excludes Django {report.target}. Fix its requirement, or go back to a "
            "release on PyPI."
            if blocked and all(p.source for p in blocked)
            else f"No release declares support for Django {report.target}.",
            blocked,
        ),
        Section(
            "before",
            "Upgrade first",
            f"These releases still run on Django {current}. Upgrade them before Django, "
            "one at a time.",
            [p for p in upgrades if p.phase is Phase.BEFORE],
        ),
        Section(
            "with",
            "Upgrade together with Django",
            f"These releases no longer run on Django {current}. Bump them in the same change "
            "as Django.",
            [p for p in upgrades if p.phase is Phase.WITH],
        ),
        Section(
            "upgrade",
            "Upgrade",
            f"A newer release declares support for Django {report.target}.",
            [p for p in upgrades if p.phase is None],
        ),
        Section(
            "check",
            "Check manually",
            f"You already run these on Django {report.target}, but their metadata does not say "
            "so. If your test suite passes, there is nothing to do."
            if report.health_check
            else "The metadata does not say either way. Read the changelog or run the test suite."
            + _signs(report.by_status(Status.CHECK)),
            report.by_status(Status.CHECK),
        ),
        Section(
            "ready",
            "Ready",
            f"Your version declares support for Django {report.target}"
            + (" (unpinned: the newest release your requirement allows)." if unpinned else "."),
            ready,
        ),
    ]
    return [s for s in result if s.packages]


def _signs(packages: list[PackageReport]) -> str:
    signed = sum(1 for p in packages if p.evidence)
    if not signed:
        return ""
    if signed == len(packages) == 1:
        return " It has signs of support, see its notes."
    if signed == 1:
        return " 1 of them has signs of support, see its notes."
    return f" {signed} of them have signs of support, see their notes."


def source_label(where: str) -> str:
    """``git https://github.com/org/fork.git`` as ``git github.com/org/fork``.

    Index URLs stay whole: they are what ``--index-url`` needs.
    """
    kind, _, location = where.partition(" ")
    if kind not in ("git", "hg", "svn", "bzr") or not location:
        return where
    return f"{kind} {re.sub(r'^[a-z+]+://', '', location).removesuffix('.git')}"


def row_notes(package: PackageReport) -> list[str]:
    """The notes to show on a package's row: where it comes from first, if not from PyPI."""
    source = [f"from {source_label(package.source)}"] if package.source else []
    signs = [e.text for e in package.evidence] + [i.label for i in package.upstream]
    return source + package.notes + signs


def row_links(package: PackageReport) -> list[tuple[str, str]]:
    """Links to show on a package's row, as (label, URL) with the URL safe to embed."""
    return [("changelog", safe_url(package.changelog_url))] if package.changelog_url else []


_CHANGE_MARK = {"better": "✓", "worse": "✗", "new": "+", "gone": "−", "same": "~"}


def since_label(report: Report) -> str:
    """The day of the baseline, e.g. ``2026-10-02``, or what stands in for it."""
    since = report.changes.since
    return since[:10] if since[:4].isdigit() else since


def changes_title(report: Report) -> str:
    changes = report.changes
    if not changes.compared:
        return (
            f"The baseline was for Django {changes.target}, this report for {report.target}: "
            "nothing compared"
        )
    if not changes.items:
        return f"No changes since {since_label(report)}"
    return f"Changes since {since_label(report)} ({len(changes.items)})"


def change_rows(report: Report) -> list[tuple[str, str, str, str, str]]:
    """(direction, mark, name, before → after, why) per change; a warning has no name."""
    rows = []
    for c in report.changes.items:
        mark = _CHANGE_MARK[c.direction]
        if c.kind == "warning":
            rows.append((c.direction, mark, "", "", c.text))
            continue
        step = " → ".join(s for s in (c.before, c.after) if s)
        rows.append((c.direction, mark, c.name, step, c.text))
    return rows


def python_hint(report: Report) -> str:
    plan = report.python
    return f"These dependencies need something before they run on Python {plan.target}."


def python_summary(report: Report) -> list[str]:
    """What the rows of the Python section leave out: the ready ones, the silent ones, Django."""
    plan = report.python
    lines = []
    ready = plan.ready + plan.pure
    if ready:
        if plan.pure == ready:
            pure = ", all pure Python" if ready > 1 else ", pure Python"
        else:
            pure = f", {plan.pure} of them pure Python" if plan.pure else ""
        what = "dependency runs" if ready == 1 else "dependencies run"
        more = "more " if plan.packages else ""
        lines.append(f"{ready} {more}{what} on Python {plan.target}{pure}")
    if plan.silent:
        says = "says" if len(plan.silent) == 1 else "say"
        lines.append(
            f"{len(plan.silent)} {says} nothing about Python versions: {', '.join(plan.silent)}"
        )
    if plan.unknown:
        lines.append(f"Could not check, run again later: {', '.join(plan.unknown)}")
    if plan.django_note:
        lines.append(plan.django_note)
    return lines


def packages_line(report: Report) -> str:
    n = len(report.packages)
    return f"{n} Django-related {'package' if n == 1 else 'packages'}"


def private_index_hint(report: Report) -> str | None:
    """Packages from a private index are not looked up on pypi.org; say how to check them."""
    if not any(where.startswith("index ") for _, where in report.external):
        return None
    return (
        "If that index mirrors PyPI, pass --check-private-on-pypi. "
        "To check packages from a private index, pass its JSON API with --index-url"
    )


def split_noted(packages: list[PackageReport]) -> tuple[list[PackageReport], list[PackageReport]]:
    """Packages without notes, and those whose notes must stay visible (e.g. not pinned)."""
    return [p for p in packages if not p.notes], [p for p in packages if p.notes]


def skipped_line(report: Report) -> str:
    n = report.skipped
    return f"{n} {'dependency' if n == 1 else 'dependencies'} without a Django requirement skipped"


def python_line(report: Report) -> str | None:
    parts = []
    if report.django_requires_python:
        parts.append(f"Django {report.target} requires Python {report.django_requires_python}")
    if report.project_python:
        parts.append(f"your project uses Python {report.project_python}")
    if not parts:
        return None
    line = ", ".join(parts)
    return line[0].upper() + line[1:]


def headline(report: Report) -> str:
    if report.health_check:
        return f"Django {report.target} · health check"
    start = f"Django {report.current_django}" if report.current_django else "Django"
    return f"{start} → {report.target}"


def summary(report: Report) -> str:
    counts = report.counts
    parts = [
        f"{counts[Status.READY]} ready",
        f"{counts[Status.UPGRADE]} to upgrade",
        f"{counts[Status.CHECK]} to check",
        f"{counts[Status.BLOCKED]} blocked",
    ]
    return " · ".join(parts)


def version_cell(package: PackageReport) -> str:
    current = package.current or package.spec or "?"
    if package.target_version:
        return f"{current} → {package.target_version}"
    return current


def _minor_label(version: str | None) -> str:
    if not version:
        return "your current version"
    return ".".join(version.split(".")[:2])


def path_headline(path: PathReport) -> str:
    """``Django 3.2.25 → 4.2 → 5.2 (2 steps)``."""
    first = path.steps[0].current_django
    stops = " → ".join(step.target for step in path.steps)
    count = len(path.steps)
    return f"Django {first} → {stops} ({count} step{'s' if count != 1 else ''})"


def step_title(path: PathReport, number: int) -> str:
    """``Step 2 of 3``, ``number`` from 1: the step's own headline follows it."""
    return f"Step {number} of {len(path.steps)}"


def path_blocked(path: PathReport) -> str | None:
    """Where the plan stops, when a step has a blocked package."""
    if path.blocked_at is None:
        return None
    names = [
        p.display_name
        for p in path.steps[path.blocked_at - 1].packages
        if p.status is Status.BLOCKED
    ]
    return (
        f"The path stops at step {path.blocked_at}: {', '.join(names)} "
        f"{'is' if len(names) == 1 else 'are'} blocked there, and the steps after it assume "
        "they are not"
    )
