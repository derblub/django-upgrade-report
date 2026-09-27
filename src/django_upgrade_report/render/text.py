from __future__ import annotations

from django_upgrade_report.analysis import Report
from django_upgrade_report.render import headline, sections, summary, version_cell

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

    lines = [
        paint(headline(report), "1"),
        paint(f"from {report.source} · {len(report.packages)} Django-related packages", "2"),
        "",
    ]
    for section in sections(report):
        mark, code = _STYLE[section.key]
        lines.append(paint(f"{section.title} ({len(section.packages)})", f"1;{code}"))
        if section.key == "ready" and not verbose:
            names = ", ".join(p.display_name for p in section.packages)
            lines.append(f"  {names}")
            lines.append("")
            continue
        lines.append(paint(f"  {section.hint}", "2"))
        name_width = max(len(p.display_name) for p in section.packages)
        version_width = max(len(version_cell(p)) for p in section.packages)
        for p in section.packages:
            detail = "; ".join([p.reason, *p.notes])
            lines.append(
                f"  {paint(mark, code)} {p.display_name.ljust(name_width)}  "
                f"{version_cell(p).ljust(version_width)}  {paint(detail, '2')}"
            )
        lines.append("")

    if report.missing:
        lines.append(paint(f"Not on the package index: {', '.join(report.missing)}", "2"))
    if report.django_requires_python:
        python = report.django_requires_python
        lines.append(paint(f"Django {report.target} requires Python {python}", "2"))
    lines.append(paint(summary(report), "1"))
    return "\n".join(lines)
