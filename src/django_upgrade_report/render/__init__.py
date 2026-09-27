"""Output formats. Every renderer walks the same sections in the same order."""

from __future__ import annotations

from dataclasses import dataclass

from django_upgrade_report.analysis import PackageReport, Phase, Report, Status


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
    result = [
        Section(
            "blocked",
            "Blocked",
            f"No release declares support for Django {report.target}.",
            report.by_status(Status.BLOCKED),
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
            "The metadata does not say either way. Read the changelog or run the test suite.",
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
