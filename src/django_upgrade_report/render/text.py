from __future__ import annotations

from django_upgrade_report.analysis import PackageReport, Report, Status
from django_upgrade_report.render import (
    headline,
    packages_line,
    private_index_hint,
    python_line,
    sections,
    skipped_line,
    version_cell,
)

_STYLE = {
    "blocked": ("✗", "31"),
    "before": ("↑", "33"),
    "with": ("↑", "33"),
    "upgrade": ("↑", "33"),
    "check": ("?", "36"),
    "ready": ("✓", "32"),
}

_COUNTS = (
    (Status.READY, "ready", "32"),
    (Status.UPGRADE, "to upgrade", "33"),
    (Status.CHECK, "to check", "36"),
    (Status.BLOCKED, "blocked", "31"),
)


def render(report: Report, color: bool = False, verbose: bool = False) -> str:
    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    def rows(packages: list[PackageReport], mark: str, code: str, shared: list[str]) -> list[str]:
        name_width = max(len(p.display_name) for p in packages)
        version_width = max(len(version_cell(p)) for p in packages)
        indent = " " * (8 + name_width + version_width)
        lines = []
        for p in packages:
            lines.append(
                f"  {paint(mark, code)} {p.display_name.ljust(name_width)}  "
                f"{version_cell(p).ljust(version_width)}  {paint(p.reason, '2')}"
            )
            lines += [paint(f"{indent}{note}", "2") for note in p.notes if note not in shared]
        return lines

    lines = [paint(headline(report), "1")]
    lines += [paint(notice, "2") for notice in report.notices]
    lines.append(paint(f"from {report.source} · {packages_line(report)}", "2"))
    lines += [paint(f"! {warning}", "1;33") for warning in report.warnings]
    lines.append("")
    for section in sections(report):
        mark, code = _STYLE[section.key]
        shared = _shared_notes(section.packages)
        lines.append(paint(f"{section.title} ({len(section.packages)})", f"1;{code}"))
        if section.key == "ready" and not verbose:
            plain = [p for p in section.packages if set(p.notes) <= set(shared)]
            noted = [p for p in section.packages if p not in plain]
            lines += [paint(f"  {note[0].upper()}{note[1:]}.", "2") for note in shared]
            if plain:
                lines.append(f"  {', '.join(p.display_name for p in plain)}")
            if noted:
                lines += rows(noted, mark, code, shared)
            lines.append("")
            continue
        lines.append(paint(f"  {section.hint}", "2"))
        lines += [paint(f"  {note[0].upper()}{note[1:]}.", "2") for note in shared]
        lines += rows(section.packages, mark, code, shared)
        lines.append("")

    if report.missing:
        lines.append(paint(f"Not on the package index: {', '.join(report.missing)}", "2"))
    if report.external:
        external = ", ".join(f"{name} ({where})" for name, where in report.external)
        lines.append(paint(f"Not from PyPI, not checked: {external}", "2"))
        hint = private_index_hint(report)
        if hint:
            lines.append(paint(hint, "2"))
    if report.skipped:
        lines.append(paint(skipped_line(report), "2"))
    python = python_line(report)
    if python:
        lines.append(paint(python, "2"))
    counts = report.counts
    lines.append(
        paint(" · ", "2").join(
            paint(f"{counts[status]} {label}", f"1;{code}" if counts[status] else "2")
            for status, label, code in _COUNTS
        )
    )
    return "\n".join(lines)


def _shared_notes(packages: list[PackageReport]) -> list[str]:
    """Notes every package of a section has: said once for the section, not on every row."""
    if len(packages) < 2:
        return []
    return [n for n in packages[0].notes if all(n in p.notes for p in packages[1:])]
