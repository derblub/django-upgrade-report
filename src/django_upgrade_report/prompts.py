"""Questions asked in a terminal when the project leaves out something the report needs."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from packaging.version import InvalidVersion, Version

from django_upgrade_report.analysis import (
    _current_django,
    _django_python,
    _min_python,
    _series,
    resolve_target,
    skipped_lts,
    spec_sets,
)
from django_upgrade_report.frameworks import DJANGO, Framework
from django_upgrade_report.pypi import PyPI
from django_upgrade_report.sources import DependencySet

Ask = Callable[[str, list[str]], "int | None"]
"""Shows a question and its options, returns the index chosen or ``None`` for "skip"."""

PYTHONS = ("3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "3.14")
OLDER_PYTHONS = 2
"""Pythons below what the target needs that are offered too: the reason to ask at all."""
SERIES_SHOWN = 5


@dataclass
class Answers:
    current: str | None = None
    """The version of the framework (Django) the project runs, for ``--from``."""
    target: str | None = None
    """The target, for ``--target``."""
    python: str | None = None
    """The project's Python, as X.Y."""
    options: list[str] = field(default_factory=list)
    """The command line options that give the same report next time, without questions."""

    def tip(self) -> str | None:
        """How to skip the questions next time."""
        parts = []
        if self.options:
            parts.append(f"pass {' '.join(self.options)}")
        if self.python:
            parts.append(f"add a .python-version file with {self.python}")
        return f"Tip: next time {' and '.join(parts)}" if parts else None


def ask_missing(
    deps: DependencySet,
    pypi: PyPI,
    target: str,
    current: str | None,
    ask: Ask,
    framework: Framework = DJANGO,
) -> Answers:
    """Ask only what changes the report: the Django in use, a smaller step, the Python."""
    fw = framework
    answers = Answers()
    django = pypi.project(fw.package)
    if django is None:
        return answers
    series = _series(django)
    dep = deps.dependencies.get(fw.package)

    if current is None and dep is not None and not dep.version:
        alternatives = spec_sets(dep.spec) if dep.spec else None
        allowed = []
        for versions in series.values():  # the newest patch of each series the spec allows
            fits = [
                v
                for v in versions
                if alternatives is None or any(a.contains(v) for a in alternatives)
            ]
            if fits:
                allowed.append(fits[-1])
        if len(allowed) > SERIES_SHOWN:  # keep the LTS series, where most projects stay
            newest = allowed[-2:]
            allowed = [v for v in allowed if fw.is_lts(v) and v not in newest] + newest
        if allowed:
            written = "Django" if fw is DJANGO else fw.package
            requirement = f"{written}{dep.spec}" if dep.spec else written
            chosen = ask(
                f"{fw.display} is not pinned ({requirement}). Which version do you run?",
                [str(v) for v in allowed],
            )
            if chosen is not None:
                answers.current = str(allowed[chosen])
                answers.options.append(f"--from {answers.current}")

    # The Django the report will assume: the answer, --from, a pin, or the newest a cap allows.
    assumed, _ = _current_django(django, dep, answers.current or current, fw)
    minor = _minor_of(assumed)
    if target == "auto" and minor is not None:
        goal = resolve_target(django, "auto", minor, fw)
        skipped = skipped_lts(django, minor, goal, fw)
        if skipped:
            step = skipped[0]
            chosen = ask(
                f"{fw.display} {goal} skips the {', '.join(map(str, skipped))} LTS. Which target?",
                [f"{step} (one LTS at a time, easier)", f"{goal}"],
            )
            if chosen is not None:
                answers.target = str(step if chosen == 0 else goal)
                answers.options.append(f"--target {answers.target}")

    if deps.python is None:
        try:
            goal = resolve_target(django, answers.target or target, minor, fw)
        except ValueError:  # an unknown target: the report says so
            return answers
        first = series.get(goal, [None])[0]
        needed = _min_python(_django_python(pypi, str(first), fw)) if first else None
        if needed:
            floor = Version(needed)
            older = [p for p in PYTHONS if Version(p) < floor][-OLDER_PYTHONS:]
            options = older + [p for p in PYTHONS if Version(p) >= floor]
            chosen = ask(
                f"{fw.display} {goal} needs Python {needed} or newer. Which Python does your "
                "project run?",
                options,
            )
            if chosen is not None:
                answers.python = options[chosen]
    return answers


def _minor_of(version: str | None) -> Version | None:
    try:
        v = Version(version or "")
    except InvalidVersion:
        return None
    return Version(f"{v.major}.{v.minor}")


def terminal_ask(question: str, options: list[str]) -> int | None:
    """Ask on stderr, read stdin. Empty input, "skip" and the end of input skip."""
    print(question, file=sys.stderr)
    choices = [*options, "skip"]
    print(
        "   ".join(f"{n}) {option}" for n, option in enumerate(choices, 1)),
        file=sys.stderr,
    )
    while True:
        print("> ", end="", file=sys.stderr, flush=True)
        line = sys.stdin.readline()
        if not line:  # end of input: answer nothing, like before there were questions
            print(file=sys.stderr)
            return None
        text = line.strip().lower()
        if text in ("", "s", "skip"):
            return None
        if text.isascii() and text.isdigit() and 1 <= int(text) <= len(choices):
            n = int(text) - 1
            return None if n == len(options) else n
        print(f"Type a number from 1 to {len(choices)}.", file=sys.stderr)
