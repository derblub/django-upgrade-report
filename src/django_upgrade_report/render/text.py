from __future__ import annotations

from django_upgrade_report.analysis import PackageReport, Report
from django_upgrade_report.render import (
    headline,
    packages_line,
    private_index_hint,
    python_line,
    sections,
    skipped_line,
    split_noted,
    summary,
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


def render(report: Report, color: bool = False, verbose: bool = False) -> str:
    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    def rows(packages: list[PackageReport], mark: str, code: str) -> list[str]:
        name_width = max(len(p.display_name) for p in packages)
        version_width = max(len(version_cell(p)) for p in packages)
        return [
            f"  {paint(mark, code)} {p.display_name.ljust(name_width)}  "
            f"{version_cell(p).ljust(version_width)}  {paint('; '.join([p.reason, *p.notes]), '2')}"
            for p in packages
        ]

    lines = [
        paint(headline(report), "1"),
        paint(f"from {report.source} · {packages_line(report)}", "2"),
    ]
    lines += [paint(f"! {warning}", "1;33") for warning in report.warnings]
    lines.append("")
    for section in sections(report):
        mark, code = _STYLE[section.key]
        lines.append(paint(f"{section.title} ({len(section.packages)})", f"1;{code}"))
        if section.key == "ready" and not verbose:
            plain, noted = split_noted(section.packages)
            if plain:
                lines.append(f"  {', '.join(p.display_name for p in plain)}")
            if noted:
                lines += rows(noted, mark, code)
            lines.append("")
            continue
        lines.append(paint(f"  {section.hint}", "2"))
        lines += rows(section.packages, mark, code)
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
    lines.append(paint(summary(report), "1"))
    return "\n".join(lines)
