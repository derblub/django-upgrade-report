"""Several projects in one report: a monorepo or a set of services.

Each project gets its own report, made one after the other with one package index client, so
a package several projects use is looked up once. The overview says which package blocks
more than one project and which upgrade several projects share.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from django_upgrade_report.analysis import Report, Status
from django_upgrade_report.sources import LOCKFILES
from django_upgrade_report.usage import SKIP_DIRS


@dataclass
class ProjectResult:
    path: str
    """The project as given or found, e.g. ``services/api``."""
    report: Report | None = None
    error: str | None = None
    """Why there is no report: no dependencies found, the index did not answer, ..."""


@dataclass
class MultiReport:
    projects: list[ProjectResult]
    generated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    kind: str = "multi"

    @property
    def reports(self) -> list[tuple[str, Report]]:
        return [(p.path, p.report) for p in self.projects if p.report is not None]

    @property
    def blocking(self) -> dict[str, list[str]]:
        """Every blocked package with the projects it blocks, the most widespread first."""
        found: dict[str, list[str]] = {}
        for path, report in self.reports:
            for p in report.packages:
                if p.status is Status.BLOCKED:
                    found.setdefault(p.name, []).append(path)
        return dict(sorted(found.items(), key=lambda item: (-len(item[1]), item[0])))

    @property
    def shared_upgrades(self) -> dict[str, tuple[str, list[str]]]:
        """Upgrades to the same version in more than one project: one change for all of them."""
        found: dict[tuple[str, str], list[str]] = {}
        for path, report in self.reports:
            for p in report.packages:
                if p.status is Status.UPGRADE and p.target_version:
                    found.setdefault((p.name, p.target_version), []).append(path)
        return {
            name: (version, paths)
            for (name, version), paths in sorted(found.items())
            if len(paths) > 1
        }


def _is_project(directory: Path, names: set[str]) -> bool:
    if any(name in names for name in LOCKFILES):
        return True
    if any(n.startswith("requirements") and n.endswith(".txt") for n in names):
        return True
    if "requirements" in names and any((directory / "requirements").glob("*.txt")):
        return True
    if "pyproject.toml" in names:
        try:
            text = (directory / "pyproject.toml").read_text("utf-8", errors="replace")
        except OSError:
            return False
        return "dependencies" in text or "[tool.poetry" in text
    return False


def discover(roots: list[Path]) -> list[Path]:
    """The projects under ``roots``, in path order. A directory inside a project is not a
    project of its own unless it has its own lockfile: ``requirements/`` or a package's
    ``pyproject.toml`` belong to the project around them."""
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            found.append(root)  # a file or a missing path: the report says what is wrong
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            here = Path(current)
            names = set(files) | set(dirs)
            inside = any(here.is_relative_to(p) for p in found)
            if inside and not any(name in files for name in LOCKFILES):
                continue
            if _is_project(here, names):
                found.append(here)
    unique = list(dict.fromkeys(found))
    return sorted(unique, key=lambda p: p.as_posix())
