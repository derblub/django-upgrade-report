"""Markdown for pull request comments and ``$GITHUB_STEP_SUMMARY``."""

from __future__ import annotations

import re

from django_upgrade_report import AUTHOR, COMPANY, COMPANY_URL, REPO_URL
from django_upgrade_report.analysis import PackageReport, PathReport, Report, Status
from django_upgrade_report.multi import MultiReport
from django_upgrade_report.removals import DJANGO_UPGRADE
from django_upgrade_report.render import (
    BLOCKING_TITLE,
    SHARED_TITLE,
    UNUSED_HINT,
    blocking_rows,
    change_rows,
    changes_title,
    headline,
    multi_headline,
    path_blocked,
    path_headline,
    private_index_hint,
    project_cells,
    python_hint,
    python_line,
    python_summary,
    removal_rows,
    removals_title,
    row_links,
    row_notes,
    sections,
    shared_rows,
    skipped_line,
    split_noted,
    step_title,
    summary,
    version_cell,
)

_ICON = {
    "blocked": "⛔",
    "before": "⬆️",
    "with": "⬆️",
    "upgrade": "⬆️",
    "check": "❔",
    "ready": "✅",
}

# Inline markup characters. Block markup (``#``, ``-``, ...) only counts at the start of a
# line, and no escaped text starts a line.
_MARKUP = re.compile(r"([\\`*_\[\]<>|~&])")


def escape(text: str) -> str:
    """``text`` shown literally, e.g. ``!=4.0.*,!=4.1.*`` without turning into emphasis."""
    return _MARKUP.sub(r"\\\1", text)


def _cell(text: str) -> str:
    return "<br>".join(escape(line) for line in text.splitlines() or [""])


def _code(text: str) -> str:
    """An inline code span that survives backticks inside ``text``."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _table(packages: list[PackageReport]) -> list[str]:
    lines = ["| Package | Version | Why |", "| --- | --- | --- |"]
    for p in packages:
        why = _cell("; ".join([p.reason, *row_notes(p)]))
        why += "".join(f" · [{label}]({url})" for label, url in row_links(p))
        lines.append(f"| {_code(p.display_name)} | {_cell(version_cell(p))} | {why} |")
    return lines


def _version(url: str) -> str:
    """``Django 5.0`` for the release notes of 5.0."""
    match = re.search(r"/releases/([\d.]+)/", url)
    return f"Django {match.group(1)}" if match else url


def _changes(report: Report) -> list[str]:
    rows = change_rows(report)
    if not rows:
        return [f"_{escape(changes_title(report))}._", ""]
    lines = [f"### {escape(changes_title(report))}", "", "| | Package | Change | Why |"]
    lines.append("| --- | --- | --- | --- |")
    for _, mark, name, step, why in rows:
        package = _code(name) if name else ""
        lines.append(f"| {mark} | {package} | {_cell(step)} | {_cell(why)} |")
    return [*lines, ""]


def render(report: Report, only_changes: bool = False, footer: bool = True) -> str:
    lines = [
        f"## {escape(headline(report))}",
        "",
        f"**{summary(report)}** · from {_code(report.source)}",
        "",
    ]
    if report.notices:
        lines += [" ".join(f"{escape(n)}." for n in report.notices), ""]
    if report.warnings:
        lines.append("> [!WARNING]")
        lines += [f"> {escape(w)}  " for w in report.warnings]
        lines.append("")
    if report.changes is not None:
        lines += _changes(report)
        if only_changes:
            return "\n".join(lines).rstrip() + "\n"
    plan = report.python
    if plan is not None:
        if plan.packages:
            lines += [f"### 🐍 Python {plan.target} first ({len(plan.packages)})", ""]
            lines += [escape(python_hint(report)), "", *_table(plan.packages), ""]
        else:
            lines += [f"### 🐍 Python {plan.target}", ""]
        lines += [f"{escape(line)}.  " for line in python_summary(report)]
        lines.append("")
    for section in sections(report):
        lines.append(f"### {_ICON[section.key]} {section.title} ({len(section.packages)})")
        lines.append("")
        if section.key == "ready":
            plain, noted = split_noted(section.packages)
            if plain:
                lines += [", ".join(_code(p.display_name) for p in plain), ""]
            if noted:
                lines += [*_table(noted), ""]
            continue
        lines += [escape(section.hint), ""]
        lines += [*_table(section.packages), ""]

    if report.removals:
        shown, rest, urls = removal_rows(report)
        lines += [f"### 🗑️ {escape(removals_title(report))}", ""]
        if shown:
            lines += ["| Removed | Where | |", "| --- | --- | --- |"]
            for removal in shown:
                where = ", ".join(_code(w) for w in removal.used_in)
                fixer = f"[django-upgrade]({DJANGO_UPGRADE}) fixes this" if removal.fixer else ""
                lines.append(f"| {_cell(removal.text)} | {where} | {fixer} |")
            lines.append("")
        if rest:
            notes = ", ".join(f"[{_version(url)}]({url})" for url in urls)
            lines += [f"{escape(rest[0].upper() + rest[1:])}: {notes}.", ""]

    if report.missing:
        missing = ", ".join(_code(m) for m in report.missing)
        lines += [f"**Not on the package index:** {missing}", ""]
    if report.external:
        lines.append("**Not from PyPI, not checked:**")
        lines.append("")
        lines += [f"- {_code(name)}: {_code(where)}" for name, where in report.external]
        lines.append("")
        hint = private_index_hint(report)
        if hint:
            lines += [f"{escape(hint)}.", ""]
    if report.unused:
        unused = ", ".join(_code(name) for name in report.unused)
        lines += [f"**Possibly unused ({len(report.unused)}):** {unused}: {UNUSED_HINT}", ""]
    if report.skipped:
        lines += [f"{skipped_line(report)}.", ""]
    python = python_line(report)
    if python:
        lines += [f"{escape(python)}.", ""]
    if footer:
        lines.append(_FOOTER)
    return "\n".join(lines).rstrip() + "\n"


_FOOTER = (
    f"<sub>Generated by [django-upgrade-report]({REPO_URL}) "
    f"by {AUTHOR}, [{COMPANY}]({COMPANY_URL})</sub>"
)


def render_path(path: PathReport) -> str:
    """``--via``: the steps in a table, then each step folded, the first one open."""
    lines = [f"## {escape(path_headline(path))}", ""]
    stops = path_blocked(path)
    if stops:
        lines += ["> [!WARNING]", f"> {escape(stops)}.", ""]
    lines += [
        "| Step | From → to | Ready | To upgrade | To check | Blocked |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for n, step in enumerate(path.steps, 1):
        counts = " | ".join(
            str(step.counts[s])
            for s in (Status.READY, Status.UPGRADE, Status.CHECK, Status.BLOCKED)
        )
        lines.append(f"| {n} | {escape(headline(step))} | {counts} |")
    lines.append("")
    for n, step in enumerate(path.steps, 1):
        lines += [
            "<details open>" if n == 1 else "<details>",
            f"<summary><b>{escape(step_title(path, n))}: {escape(headline(step))}</b></summary>",
            "",
            render(step, footer=False).rstrip(),
            "",
            "</details>",
            "",
        ]
    lines.append(_FOOTER)
    return "\n".join(lines) + "\n"


def render_multi(multi: MultiReport) -> str:
    """The projects in a table, what blocks or is shared across them, each report folded."""
    lines = [f"## {escape(multi_headline(multi))}", ""]
    lines += ["| Project | From → to | Ready | To upgrade | To check | Blocked |"]
    lines += ["| --- | --- | --- | --- | --- | --- |"]
    for p in multi.projects:
        if p.report is None:
            lines.append(f"| {escape(p.path)} | {escape(project_cells(p)[1])} | | | | |")
            continue
        counts = " | ".join(
            str(p.report.counts[s])
            for s in (Status.READY, Status.UPGRADE, Status.CHECK, Status.BLOCKED)
        )
        lines.append(f"| {escape(p.path)} | {escape(headline(p.report))} | {counts} |")
    lines.append("")
    if rows := blocking_rows(multi):
        lines += [f"### {BLOCKING_TITLE}", "", "| Package | Projects |", "| --- | --- |"]
        lines += [f"| {escape(name)} | {escape(', '.join(paths))} |" for name, paths in rows]
        lines.append("")
    if rows := shared_rows(multi):
        lines += [f"### {SHARED_TITLE}", "", "| Package | To | Projects |", "| --- | --- | --- |"]
        lines += [
            f"| {escape(name)} | {escape(version)} | {escape(', '.join(paths))} |"
            for name, version, paths in rows
        ]
        lines.append("")
    for path, report in multi.reports:
        lines += [
            "<details>",
            f"<summary><b>{escape(path)}: {escape(headline(report))}</b></summary>",
            "",
            render(report, footer=False).rstrip(),
            "",
            "</details>",
            "",
        ]
    lines.append(_FOOTER)
    return "\n".join(lines) + "\n"
