from __future__ import annotations

from django_upgrade_report.analysis import PackageReport, PathReport, Report, Status
from django_upgrade_report.multi import MultiReport
from django_upgrade_report.render import (
    BLOCKING_TITLE,
    SHARED_TITLE,
    UNUSED_HINT,
    blocking_rows,
    change_rows,
    changes_title,
    headline,
    multi_headline,
    packages_line,
    path_blocked,
    path_headline,
    private_index_hint,
    project_cells,
    python_hint,
    python_line,
    python_summary,
    removal_note,
    removal_rows,
    removals_title,
    row_links,
    row_notes,
    sections,
    shared_rows,
    skipped_line,
    source_label,
    step_title,
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


_CHANGE_CODE = {"better": "32", "worse": "31", "new": "33", "gone": "2", "same": "36"}


def render(
    report: Report,
    color: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    only_changes: bool = False,
) -> str:
    """The report for a terminal. ``quiet`` keeps the headline, warnings, blockers and counts."""

    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    def terse(text: str) -> str:
        """The section already says the target is missing: ", not 5.2" adds nothing."""
        return text.removesuffix(f", not {report.target}")

    def rows(
        packages: list[PackageReport], mark: str | None, code: str | None, shared: list[str]
    ) -> list[str]:
        """``mark`` and ``code`` None: each row is styled by its own status."""
        name_width = max(len(p.display_name) for p in packages)
        version_width = max(len(version_cell(p)) for p in packages)
        indent = " " * (8 + name_width + version_width)
        lines = []
        for p in packages:
            details = [terse(n) for n in row_notes(p) if n not in shared]
            # "2.0 declares Django 5.2" on an upgrade to 2.0 repeats the version column.
            if p.reason != f"{p.target_version} declares {report.name} {report.target}":
                details.insert(0, terse(p.reason))
            first, *rest = details or [""]
            if verbose:  # URLs make a row long: only on request, and on their own lines
                rest += [f"{label} {url}" for label, url in row_links(p)]
            row_mark, row_code = (mark, code) if mark else _STYLE[p.status.value]
            lines.append(
                f"  {paint(row_mark, row_code)} {p.display_name.ljust(name_width)}  "
                f"{version_cell(p).ljust(version_width)}  {paint(first, '2')}".rstrip()
            )
            lines += [paint(f"{indent}{note}", "2") for note in rest]
        return lines

    about = [f"from {report.source}", packages_line(report)]
    if report.project_python:
        about.append(f"Python {report.project_python}")
    lines = [paint(headline(report), "1")]
    lines += [paint(notice, "2") for notice in report.notices]
    if not quiet:
        lines.append(paint(" · ".join(about), "2"))
    lines += [paint(f"! {warning}", "1;33") for warning in report.warnings]
    lines.append("")
    if report.changes is not None:
        lines += _changes(report, paint)
        if only_changes:
            return "\n".join(lines).rstrip()
    plan = report.python
    shown = [p for p in plan.packages if p.status is Status.BLOCKED or not quiet] if plan else []
    if plan is not None and (shown or not quiet):
        if shown:
            lines.append(paint(f"Python {plan.target} first ({len(shown)})", "1;35"))
            lines.append(paint(f"  {python_hint(report)}", "2"))
            lines += rows(shown, None, None, [])
        else:
            lines.append(paint(f"Python {plan.target}", "1;35"))
        if not quiet:
            summary = python_summary(report)
            lines += [paint(f"  {line[0].upper()}{line[1:]}.", "2") for line in summary]
        lines.append("")
    for section in sections(report):
        if quiet and section.key != "blocked":
            continue
        mark, code = _STYLE[section.key]
        shared = _shared_notes(section.packages)
        lines.append(paint(f"{section.title} ({len(section.packages)})", f"1;{code}"))
        if section.key == "ready" and not verbose:
            plain = [p for p in section.packages if set(row_notes(p)) <= set(shared)]
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

    if quiet:
        lines.append(_counts(report, paint))
        return "\n".join(lines)
    if report.removals:
        shown, rest, urls = removal_rows(report, verbose)
        lines.append(paint(removals_title(report), "1"))
        width = max((len(r.text) for r in shown), default=0)
        for removal in shown:
            note = removal_note(removal)
            lines.append(f"  {removal.text.ljust(width)}  {paint(note, '2')}".rstrip())
        if rest:
            lines.append(paint(f"  {rest[0].upper()}{rest[1:]}:", "2"))
            lines += [paint(f"    {url}", "2") for url in urls]
        lines.append("")
    if report.missing:
        lines.append(paint(f"Not on the package index: {', '.join(report.missing)}", "2"))
    if report.external:
        external = ", ".join(f"{name} ({source_label(where)})" for name, where in report.external)
        lines.append(paint(f"Not from PyPI, not checked: {external}", "2"))
        hint = private_index_hint(report)
        if hint:
            lines.append(paint(hint, "2"))
    if report.unused:
        lines.append(
            paint(f"Possibly unused ({len(report.unused)}): {', '.join(report.unused)}", "2")
        )
        lines.append(paint(f"  {UNUSED_HINT[0].upper()}{UNUSED_HINT[1:]}", "2"))
    if report.skipped:
        lines.append(paint(skipped_line(report), "2"))
    python = python_line(report)
    if python and not report.project_python:  # a project Python too old is a warning above
        lines.append(paint(python, "2"))
    lines.append(_counts(report, paint))
    return "\n".join(lines)


def _changes(report: Report, paint) -> list[str]:
    rows = change_rows(report)
    if not rows:
        return [paint(changes_title(report), "2"), ""]
    lines = [paint(changes_title(report), "1")]
    named = [r for r in rows if r[2]]
    name_width = max((len(r[2]) for r in named), default=0)
    step_width = max((len(r[3]) for r in named), default=0)
    for direction, mark, name, step, why in rows:
        code = _CHANGE_CODE[direction]
        if not name:
            lines.append(f"  {paint(mark, code)} {paint(why, '2')}")
            continue
        lines.append(
            f"  {paint(mark, code)} {name.ljust(name_width)}  {step.ljust(step_width)}  "
            f"{paint(why, '2')}".rstrip()
        )
    return [*lines, ""]


def _counts(report: Report, paint) -> str:
    counts = report.counts
    return paint(" · ", "2").join(
        paint(f"{counts[status]} {label}", f"1;{code}" if counts[status] else "2")
        for status, label, code in _COUNTS
    )


def _shared_notes(packages: list[PackageReport]) -> list[str]:
    """Notes every package of a section has: said once for the section, not on every row."""
    if len(packages) < 2:
        return []
    first = row_notes(packages[0])
    return [n for n in first if all(n in row_notes(p) for p in packages[1:])]


def render_path(
    path: PathReport, color: bool = False, verbose: bool = False, quiet: bool = False
) -> str:
    """Every step of ``--via`` after a line with the whole path."""
    bold = (lambda text: f"\033[1m{text}\033[0m") if color else (lambda text: text)
    blocks = [bold(path_headline(path))]
    stops = path_blocked(path)
    if stops:
        blocks[0] += f"\n! {stops}."
    for number, step in enumerate(path.steps, 1):
        body = render(step, color=color, verbose=verbose, quiet=quiet)
        blocks.append(f"{bold(step_title(path, number))}\n\n{body}")
    return "\n\n".join(blocks)


def render_multi(
    multi: MultiReport, color: bool = False, verbose: bool = False, quiet: bool = False
) -> str:
    """An overview of the projects, what blocks or is shared across them, then each report."""
    bold = (lambda text: f"\033[1m{text}\033[0m") if color else (lambda text: text)
    width = max(len(p.path) for p in multi.projects)
    cells = [project_cells(p) for p in multi.projects]
    first = max(len(head) for head, _ in cells)
    lines = [bold(multi_headline(multi))]
    for p, (head, rest) in zip(multi.projects, cells, strict=True):
        lines.append(f"  {p.path.ljust(width)}  {head.ljust(first)}  {rest}".rstrip())
    blocks = ["\n".join(lines)]
    if rows := blocking_rows(multi):
        name_width = max(len(name) for name, _ in rows)
        blocks.append(
            "\n".join(
                [bold(BLOCKING_TITLE)]
                + [f"  {name.ljust(name_width)}  {', '.join(paths)}" for name, paths in rows]
            )
        )
    if rows := shared_rows(multi):
        cells = [(f"{name} → {version}", paths) for name, version, paths in rows]
        name_width = max(len(cell) for cell, _ in cells)
        blocks.append(
            "\n".join(
                [bold(SHARED_TITLE)]
                + [f"  {cell.ljust(name_width)}  {', '.join(paths)}" for cell, paths in cells]
            )
        )
    for path, report in multi.reports:
        body = render(report, color=color, verbose=verbose, quiet=quiet)
        blocks.append(f"{bold(path)}\n\n{body}")
    return "\n\n".join(blocks)
