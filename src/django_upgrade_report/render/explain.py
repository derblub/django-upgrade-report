"""``--explain``: how the verdict on a package came about, step by step."""

from __future__ import annotations

from packaging.version import Version

from django_upgrade_report import __version__
from django_upgrade_report.analysis import ExplainLine, Report

_NONE = Version("0")

_SECTIONS = (
    ("inputs", "Inputs"),
    ("release", "Your release"),
    ("search", "Releases looked at"),
    ("phase", "Before or with Django"),
    ("result", "Result"),
)


def render(report: Report) -> str:
    """Plain text to read, or to paste into an issue: it names the tool version and target."""
    blocks = []
    about = [*report.notices, *(f"! {warning}" for warning in report.warnings)]
    if about:
        blocks.append("\n".join(about))
    for name, lines in report.explanations.items():
        out = [f"{name} against Django {report.target} · django-upgrade-report {__version__}"]
        for key, title in _SECTIONS:
            found = [line for line in lines if line.section == key]
            if not found:
                continue
            if key == "search":  # looked at in parallel: show them newest first
                found = sorted(found, key=lambda line: line.version or _NONE, reverse=True)
            found = _unique(found)
            if key == "search":
                title = f"{title} ({len(found)})"
            out.append(title)
            out += [f"  {line.text}" for line in found]
        blocks.append("\n".join(out))
    return "\n\n".join(blocks) + "\n"


def _unique(lines: list[ExplainLine]) -> list[ExplainLine]:
    """A release judged twice (a bisection and a scan) is shown once."""
    seen, kept = set(), []
    for line in lines:
        if line.text not in seen:
            seen.add(line.text)
            kept.append(line)
    return kept
