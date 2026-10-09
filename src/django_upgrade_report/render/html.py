"""A single, self-contained HTML file you can attach to a ticket or send to a client."""

from __future__ import annotations

import hashlib
import re
from html import escape
from importlib import resources

from django_upgrade_report import AUTHOR, COMPANY, COMPANY_URL, REPO_URL, __version__, commands
from django_upgrade_report.analysis import PackageReport, PathReport, Report, Status
from django_upgrade_report.multi import MultiReport
from django_upgrade_report.projects import safe_url
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
    packages_line,
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
    version_cell,
)
from django_upgrade_report.render import json as json_report

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f7f7f5; --panel: #ffffff; --text: #1b1d1c; --muted: #6a706d; --line: #e4e4e0;
  --python: #6b46c1; --ready: #1f7a4d; --upgrade: #a15c00; --check: #22639e; --blocked: #b3261e;
  --ready-bg: #e5f3ec; --upgrade-bg: #fbefdc; --check-bg: #e3eef8; --blocked-bg: #fbe4e2;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111312; --panel: #1a1d1c; --text: #e9ebea; --muted: #9aa19d; --line: #2c302e;
    --python: #b794f4; --ready: #5fd49a; --upgrade: #f0b35a; --check: #7db6ec; --blocked: #f28b82;
    --ready-bg: #173325; --upgrade-bg: #3a2a12; --check-bg: #16283a; --blocked-bg: #3d1a18;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 960px; margin: 0 auto; padding: 48px 16px 64px; }
header p { color: var(--muted); margin: 4px 0 0; }
h1 { font-size: 32px; letter-spacing: -0.02em; margin: 0; }
h1 .arrow { color: var(--muted); font-weight: 400; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px;
  margin: 32px 0 40px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px; }
.tile b { display: block; font-size: 30px; line-height: 1.1; font-variant-numeric: tabular-nums; }
.tile span { color: var(--muted); font-size: 13px; }
.tile.ready b { color: var(--ready); } .tile.upgrade b { color: var(--upgrade); }
.tile.check b { color: var(--check); } .tile.blocked b { color: var(--blocked); }
.tile.zero b { color: var(--muted); opacity: 0.5; }
section { margin-top: 36px; }
h2 { font-size: 18px; margin: 0 0 2px; display: flex; align-items: center; gap: 8px; }
h2 .count { color: var(--muted); font-weight: 400; }
.hint { color: var(--muted); margin: 0 0 12px; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dot.ready { background: var(--ready); } .dot.upgrade { background: var(--upgrade); }
.dot.check { background: var(--check); } .dot.blocked { background: var(--blocked); }
.dot.python { background: var(--python); }
.dot.removed { background: var(--muted); }
td.fix { white-space: nowrap; font-size: 13px; color: var(--muted); }
.table + .hint { margin-top: 10px; }
.hint a { color: inherit; }
.table { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 10px 14px; border-top: 1px solid var(--line);
  vertical-align: top; }
thead th { border-top: 0; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--muted); font-weight: 600; }
td.name { font-weight: 600; white-space: nowrap; }
td.version { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
  white-space: nowrap; }
.note { display: inline-block; margin: 4px 6px 0 0; padding: 1px 8px; border-radius: 10px;
  font-size: 12px; background: var(--bg); color: var(--muted); border: 1px solid var(--line); }
a.link { color: var(--muted); font-size: 13px; }
.note.sign { background: var(--check-bg); color: var(--check); border-color: transparent;
  text-decoration: none; }
.note.warn { background: var(--blocked-bg); color: var(--blocked); border-color: transparent; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { background: var(--ready-bg); color: var(--ready); border-radius: 99px;
  padding: 3px 12px; font-size: 13px; font-weight: 500; }
.warnings { background: var(--upgrade-bg); color: var(--upgrade); border-radius: 10px;
  padding: 12px 16px; margin: 24px 0 0; }
.warnings p { margin: 0; } .warnings p + p { margin-top: 6px; }
.chips + .table { margin-top: 12px; }
.changes { list-style: none; padding: 0; margin: 0; }
.changes li { padding: 4px 0; } .changes li span { color: var(--muted); }
.changes .mark { display: inline-block; width: 1.2em; font-weight: 700; }
.changes .better .mark { color: var(--ready); } .changes .worse .mark { color: var(--blocked); }
.changes .new .mark { color: var(--upgrade); } .changes .same .mark { color: var(--check); }
.aside { margin-top: 28px; }
.aside h3 { font-size: 15px; margin: 0 0 6px; }
.aside ul { margin: 0; padding-left: 20px; }
.aside li span { color: var(--muted); }
.meta { color: var(--muted); font-size: 13px; margin-top: 48px; border-top: 1px solid var(--line);
  padding-top: 16px; }
.meta a { color: inherit; }
td.tick, th.tick { width: 28px; padding-right: 0; }
td.tick input { width: 16px; height: 16px; margin: 3px 0 0; accent-color: var(--ready); }
tr.done td:not(.tick) { opacity: 0.45; text-decoration: line-through; }
.tile.progress b { color: var(--text); }
.tile.progress b span { color: inherit; font-size: inherit; }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px;
  margin: -16px 0 8px; }
.toolbar input[type=search] { flex: 1 1 220px; font: inherit; color: inherit;
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; }
.toolbar label { color: var(--muted); font-size: 13px; white-space: nowrap; }
.toolbar button { font: inherit; font-size: 13px; color: var(--muted); background: var(--panel);
  border: 1px solid var(--line); border-radius: 99px; padding: 3px 12px; cursor: pointer; }
.toolbar button[aria-pressed=true] { color: var(--text); border-color: var(--text); }
.toolbar #reset { border-radius: 8px; }
#shown { color: var(--muted); font-size: 13px; }
.tile[data-tile] { cursor: pointer; }
.tile[aria-pressed=true] { border-color: var(--text); }
:focus-visible { outline: 2px solid var(--check); outline-offset: 2px; }
details.more { margin-top: 4px; font-size: 13px; color: var(--muted); }
details.more summary { cursor: pointer; width: max-content; }
details.more dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px;
  margin: 6px 0 2px; }
details.more dt { color: var(--muted); } details.more dd { margin: 0; color: var(--text); }
details.more a { color: inherit; }
details.more code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
button.copy { font: inherit; font-size: 12px; color: var(--muted); background: var(--panel);
  border: 1px solid var(--line); border-radius: 6px; padding: 0 8px; margin-left: 6px;
  cursor: pointer; }
th button.sort { font: inherit; color: inherit; text-transform: inherit; letter-spacing: inherit;
  background: none; border: 0; padding: 0; cursor: pointer; }
th[aria-sort=ascending] button.sort::after { content: " ↑"; }
th[aria-sort=descending] button.sort::after { content: " ↓"; }
tr.current { box-shadow: inset 3px 0 0 var(--check); }
.overview { margin: 0 0 40px; }
.overview td a { color: inherit; }
.tiles.single { grid-template-columns: minmax(140px, 220px); }
h2.step { font-size: 22px; margin: 56px 0 0; }
section.step > .tiles { margin: 16px 0 8px; }
@media screen { .filtered { display: none !important; } }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
@media print {
  body { background: #fff; color: #000; }
  .tile, .table, .chip, .note, .warnings { background: none !important; border-color: #999; }
  tr { break-inside: avoid; }
  a.link, .toolbar, details.more { display: none !important; }
}
@media (max-width: 640px) {
  .tiles { grid-template-columns: repeat(2, 1fr); }
  h1 { font-size: 26px; }
}
"""

_SECTION_COLOR = {
    "blocked": "blocked",
    "before": "upgrade",
    "with": "upgrade",
    "upgrade": "upgrade",
    "check": "check",
    "ready": "ready",
}


_FILTERS = {
    "blocked": "blocked",
    "before": "upgrade first",
    "with": "upgrade with Django",
    "upgrade": "upgrade",
    "check": "to check",
    "ready": "ready",
}
"""Filter chips, by the key :func:`_filter` gives a row, in the order of the sections."""


def render(report: Report, static: bool = False) -> str:
    """``static`` leaves out every script, for places that block scripts in attachments."""
    title = headline(report)
    start, arrow, end = title.partition(" → ")
    heading = (
        f'{escape(start)} <span class="arrow">→</span> {escape(end)}' if arrow else escape(title)
    )
    kind = "Health check" if report.health_check else "Upgrade report"
    intro = " ".join(f"{escape(n)}." for n in report.notices)
    intro = f"{intro} {kind} for {packages_line(report)}".strip()
    todos = _todos(report)
    progress = _progress(todos) if todos and not static else ""
    inner = (
        f"<header>\n<h1>{heading}</h1>\n<p>{intro}</p>\n</header>\n"
        f'{_warnings(report)}<div class="tiles">{_tiles(report)}{progress}</div>\n'
        f"{'' if static else _toolbar([report])}{''.join(_body(report, static))}\n"
        f'<p class="meta">{" ".join(_meta(report))}</p>'
    )
    return _page(title, checklist_key(report), inner, json_report.render(report), static)


def render_path(path: PathReport, static: bool = False) -> str:
    """``--via``: an overview of the steps, then each step as the single report shows it."""
    title = path_headline(path)
    todos = sum(_todos(step) for step in path.steps)
    rows = "".join(
        f'<tr><td class="name"><a href="#step-{n}">{escape(step_title(path, n))}</a></td>'
        f"<td>{escape(headline(step))}</td>"
        + "".join(f"<td>{step.counts[status]}</td>" for status in _COUNTED)
        + "</tr>"
        for n, step in enumerate(path.steps, 1)
    )
    heads = "".join(f"<th>{label}</th>" for label in ("ready", "to upgrade", "to check", "blocked"))
    stops = path_blocked(path)
    inner = [
        f"<header>\n<h1>{escape(title)}</h1>\n<p>An upgrade in {len(path.steps)} steps, each "
        "starting where the one before ends.</p>\n</header>\n",
        f'<div class="warnings" role="note"><p>{escape(stops)}.</p></div>' if stops else "",
        f'<div class="tiles single">{_progress(todos)}</div>\n' if todos and not static else "",
        f'<div class="table overview"><table><thead><tr><th>Step</th><th>From → to</th>{heads}</tr>'
        f"</thead><tbody>{rows}</tbody></table></div>\n",
        "" if static else _toolbar(path.steps),
    ]
    for n, step in enumerate(path.steps, 1):
        inner.append(
            f'<section class="step" id="step-{n}"><h2 class="step">{escape(step_title(path, n))}: '
            f"{escape(headline(step))}</h2>{_warnings(step)}"
            f'<div class="tiles">{_tiles(step, filters=False)}</div>'
            f"{''.join(_body(step, static, prefix=f'{n}-'))}</section>\n"
        )
    inner.append(f'<p class="meta">{" ".join(_meta(path.steps[0], path=True))}</p>')
    key = (
        "django-upgrade-report:"
        + hashlib.sha256(" ".join(checklist_key(step) for step in path.steps).encode()).hexdigest()[
            :16
        ]
    )
    return _page(title, key, "".join(inner), json_report.render_path(path), static)


def render_multi(multi: MultiReport, static: bool = False) -> str:
    """Several projects: an overview with a link to each, what blocks or is shared across
    them, then each project as the single report shows it."""
    title = multi_headline(multi)
    reports = [report for _, report in multi.reports]
    todos = sum(_todos(report) for report in reports)
    anchors = {p.path: f"project-{n}" for n, p in enumerate(multi.projects, 1)}
    rows = []
    for p in multi.projects:
        if p.report is None:
            rows.append(
                f'<tr><td class="name">{escape(p.path)}</td>'
                f'<td colspan="5">{escape(project_cells(p)[1])}</td></tr>'
            )
            continue
        rows.append(
            f'<tr><td class="name"><a href="#{anchors[p.path]}">{escape(p.path)}</a></td>'
            f"<td>{escape(headline(p.report))}</td>"
            + "".join(f"<td>{p.report.counts[status]}</td>" for status in _COUNTED)
            + "</tr>"
        )
    heads = "".join(f"<th>{label}</th>" for label in ("ready", "to upgrade", "to check", "blocked"))

    def links(paths: list[str]) -> str:
        return ", ".join(f'<a href="#{anchors[path]}">{escape(path)}</a>' for path in paths)

    inner = [
        f"<header>\n<h1>{escape(title)}</h1>\n<p>One report per project, after what they have "
        "in common.</p>\n</header>\n",
        f'<div class="tiles single">{_progress(todos)}</div>\n' if todos and not static else "",
        f'<div class="table overview"><table><thead><tr><th>Project</th><th>From → to</th>'
        f"{heads}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>\n",
    ]
    if blocking := blocking_rows(multi):
        cells = "".join(
            f'<tr><td class="name">{escape(name)}</td><td>{links(paths)}</td></tr>'
            for name, paths in blocking
        )
        inner.append(
            f"<h2>{BLOCKING_TITLE}</h2>"
            '<div class="table overview"><table><thead><tr><th>Package</th><th>Projects</th>'
            f"</tr></thead><tbody>{cells}</tbody></table></div>\n"
        )
    if shared := shared_rows(multi):
        cells = "".join(
            f'<tr><td class="name">{escape(name)}</td><td>{escape(version)}</td>'
            f"<td>{links(paths)}</td></tr>"
            for name, version, paths in shared
        )
        inner.append(
            f"<h2>{SHARED_TITLE}</h2>"
            '<div class="table overview"><table><thead><tr><th>Package</th><th>To</th>'
            f"<th>Projects</th></tr></thead><tbody>{cells}</tbody></table></div>\n"
        )
    if reports and not static:
        inner.append(_toolbar(reports))
    for n, p in enumerate(multi.projects, 1):
        if p.report is None:
            continue
        report = p.report
        inner.append(
            f'<section class="step" id="{anchors[p.path]}"><h2 class="step">{escape(p.path)}: '
            f"{escape(headline(report))}</h2>{_warnings(report)}"
            f'<div class="tiles">{_tiles(report, filters=False)}</div>'
            f"{''.join(_body(report, static, prefix=f'{n}-'))}"
            f'<p class="meta">{" ".join(_meta(report, path=True)[:-1])}</p></section>\n'
        )
    if reports:
        inner.append(f'<p class="meta">{_meta(reports[0])[-1]}</p>')
    plan = " ".join(f"{path}:{checklist_key(report)}" for path, report in multi.reports)
    key = "django-upgrade-report:" + hashlib.sha256(plan.encode()).hexdigest()[:16]
    return _page(title, key, "".join(inner), json_report.render_multi(multi), static)


_COUNTED = (Status.READY, Status.UPGRADE, Status.CHECK, Status.BLOCKED)


def _todos(report: Report) -> int:
    todos = sum(1 for p in report.packages if p.status is not Status.READY)
    return todos + (len(report.python.packages) if report.python else 0)


def _progress(todos: int) -> str:
    return (
        f'<div class="tile progress"><b><span id="done">0</span> / {todos}</b>'
        "<span>done</span></div>"
    )


def _tiles(report: Report, filters: bool = True) -> str:
    """The counts; ``filters`` makes them filter by their status (the script does that)."""
    counted = [_filter(p) for p in report.packages] if filters else []
    counts = report.counts
    return "".join(
        f'<div class="tile {css}{" zero" if not counts[status] else ""}"{_tile(css, counted)}>'
        f"<b>{counts[status]}</b><span>{label}</span></div>"
        for status, css, label in (
            (Status.READY, "ready", "ready"),
            (Status.UPGRADE, "upgrade", "to upgrade"),
            (Status.CHECK, "check", "to check"),
            (Status.BLOCKED, "blocked", "blocked"),
        )
    )


def _warnings(report: Report) -> str:
    if not report.warnings:
        return ""
    paragraphs = "".join(f"<p>{escape(w)}</p>" for w in report.warnings)
    return f'<div class="warnings" role="note">{paragraphs}</div>'


def _body(report: Report, static: bool, prefix: str = "") -> list[str]:
    """The sections of one report. ``prefix`` keeps ids and ticks of several reports apart."""
    try:  # each row shows its command when the tool is clear from the source
        tool = commands.tool_for(report, "auto")
    except commands.EmitError:
        tool = None
    command = {
        p.name: line
        for p in report.packages
        if tool and (line := commands.command(report, p, tool)) and not line.startswith("#")
    }
    body = []
    if report.changes is not None:
        rows = change_rows(report)
        items = "".join(
            f'<li class="{direction}"><span class="mark">{escape(mark)}</span> '
            f"<b>{escape(name)}</b> {escape(step)} <span>{escape(why)}</span></li>"
            for direction, mark, name, step, why in rows
        )
        listing = f'<ul class="changes">{items}</ul>' if rows else ""
        body.append(
            f'<section id="{prefix}changes"><h2>{escape(changes_title(report))}</h2>'
            f"{listing}</section>"
        )
    plan = report.python
    if plan is not None:
        python_title = f"Python {plan.target} first" if plan.packages else f"Python {plan.target}"
        count = f' <span class="count">{len(plan.packages)}</span>' if plan.packages else ""
        body.append(
            f'<section id="{prefix}python"{" data-rows" if plan.packages else ""}><h2>'
            f'<span class="dot python"></span>{escape(python_title)}{count}</h2>'
        )
        if plan.packages:
            body.append(f'<p class="hint">{escape(python_hint(report))}</p>')
            body.append(_table(plan.packages, f"{prefix}python", static))
        summary = "".join(f"<p>{escape(line)}.</p>" for line in python_summary(report))
        body.append(f'<div class="hint">{summary}</div></section>')
    for section in sections(report):
        color = _SECTION_COLOR[section.key]
        body.append(
            f'<section id="{prefix}{section.key}" data-rows><h2>'
            f'<span class="dot {color}"></span>{escape(section.title)} '
            f'<span class="count">{len(section.packages)}</span></h2>'
            f'<p class="hint">{escape(section.hint)}</p>'
        )
        packages = section.packages
        if section.key == "ready":
            plain, packages = split_noted(packages)
            if plain:
                chips = "".join(
                    f'<span class="chip" title="{escape(p.reason)}"{_data(p)}>'
                    f"{escape(p.display_name)}</span>"
                    for p in plain
                )
                body.append(f'<div class="chips">{chips}</div>')
        if packages:
            todo = None if section.key == "ready" else f"{prefix}django"
            body.append(_table(packages, todo, static, command))
        body.append("</section>")

    if report.removals:
        shown, rest, urls = removal_rows(report)
        body.append(
            f'<section id="{prefix}removals"><h2><span class="dot removed"></span>'
            f"{escape(removals_title(report))}</h2>"
        )
        if shown:
            rows = "".join(
                f"<tr><td>{escape(r.text)}</td>"
                f'<td class="version">{"<br>".join(escape(w) for w in r.used_in)}</td>'
                f'<td class="fix">{_fixer(r)}</td></tr>'
                for r in shown
            )
            body.append(
                '<div class="table"><table><thead><tr><th>Removed</th><th>Where</th><th></th>'
                f"</tr></thead><tbody>{rows}</tbody></table></div>"
            )
        if rest:
            links = ", ".join(
                f'<a href="{escape(safe_url(url))}">{escape(_release(url))}</a>' for url in urls
            )
            body.append(f'<p class="hint">{escape(rest[0].upper() + rest[1:])}: {links}.</p>')
        body.append("</section>")

    if report.missing:
        items = "".join(f"<li>{escape(name)}</li>" for name in report.missing)
        body.append(
            f'<section class="aside" id="{prefix}missing"><h3>Not on the package index</h3>'
            f"<ul>{items}</ul></section>"
        )
    if report.external:
        items = "".join(
            f"<li>{escape(name)} <span>{escape(where)}</span></li>"
            for name, where in report.external
        )
        hint = private_index_hint(report)
        hint_html = f'<p class="hint">{escape(hint)}.</p>' if hint else ""
        body.append(
            f'<section class="aside" id="{prefix}external"><h3>Not from PyPI, not checked</h3>'
            f"<ul>{items}</ul>{hint_html}</section>"
        )
    if report.unused:
        items = "".join(f"<li>{escape(name)}</li>" for name in report.unused)
        body.append(
            f'<section class="aside" id="{prefix}unused"><h3>Possibly unused '
            f'<span class="count">{len(report.unused)}</span></h3><ul>{items}</ul>'
            f'<p class="hint">{escape(UNUSED_HINT[0].upper() + UNUSED_HINT[1:])}.</p></section>'
        )
    return body


def _meta(report: Report, path: bool = False) -> list[str]:
    meta = [f"Dependencies from <code>{escape(report.source)}</code>."]
    if report.skipped:
        meta.append(f"{escape(skipped_line(report))}.")
    python = python_line(report)
    if python and not path:
        meta.append(f"{escape(python)}.")
    meta.append(
        f"Generated {report.generated:%Y-%m-%d %H:%M} UTC by "
        f'<a href="{REPO_URL}">django-upgrade-report</a> {__version__} '
        f'by {AUTHOR}, <a href="{COMPANY_URL}">{COMPANY}</a>, '
        "from the metadata packages publish on PyPI. "
        "A green row means the maintainers declare support, not that your tests pass."
    )
    return meta


def _toolbar(reports: list[Report]) -> str:
    rows = [p for r in reports for p in [*r.packages, *(r.python.packages if r.python else ())]]
    present = [k for k in _FILTERS if k in {_filter(p) for p in rows}]
    chips = "".join(
        f'<button type="button" data-chip="{key}" aria-pressed="false">'
        f"{_FILTERS[key].replace('Django', reports[0].name)}</button>"
        for key in present
    )
    direct = (
        '<label><input type="checkbox" id="direct"> only direct dependencies</label>'
        if any(p.direct is False for p in rows)
        else ""
    )
    return (
        '<div class="toolbar" id="toolbar" role="search" hidden>'
        '<input type="search" id="q" placeholder="Search packages, reasons, notes  ( / )" '
        'aria-label="Search packages, reasons and notes">'
        f"{chips if len(present) > 1 else ''}"
        '<label><input type="checkbox" id="notes"> only with notes</label>'
        f"{direct}"
        '<button type="button" id="reset">Reset (Esc)</button>'
        '<span id="shown" aria-live="polite"></span></div>\n'
    )


def _page(title: str, key: str, inner: str, data: str, static: bool) -> str:
    script = ""
    if not static:
        # "<" escaped, so nothing in the data can end the script element.
        safe = data.replace("<", "\\u003c")
        script = (
            f'<script type="application/json" id="report-data">{safe}</script>\n'
            f"<script>{script_source()}</script>\n"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · upgrade report</title>
<style>{_CSS}</style>
</head>
<body>
<main data-checklist="{key}">
{inner}
</main>
{script}</body>
</html>
"""


def checklist_key(report: Report) -> str:
    """Where the ticks of this plan are kept in the browser: a new plan starts unticked."""
    rows = sorted(f"{p.name}={p.current}>{p.target_version}" for p in report.packages)
    if report.python:
        rows += sorted(
            f"py:{p.name}={p.current}>{p.target_version}" for p in report.python.packages
        )
    plan = "\n".join([report.target, report.source, *rows])
    return "django-upgrade-report:" + hashlib.sha256(plan.encode()).hexdigest()[:16]


def script_source() -> str:
    """The page's script, shipped in the package next to this module, without the comments
    on lines of their own and without indentation."""
    source = resources.files(__package__).joinpath("html_report.js").read_text(encoding="utf-8")
    lines = (line.strip() for line in source.splitlines())
    return "\n".join(line for line in lines if line and not line.startswith("//"))


def _fixer(removal) -> str:
    if not removal.fixer:
        return ""
    return f'<a class="link" href="{DJANGO_UPGRADE}">django-upgrade</a> fixes this'


def _release(url: str) -> str:
    match = re.search(r"/releases/([\d.]+)/", url)
    return f"Django {match.group(1)}" if match else url


def _filter(p: PackageReport) -> str:
    """The filter a row belongs to: its status, or its phase for an upgrade."""
    if p.status is Status.UPGRADE and p.phase is not None:
        return p.phase.value
    return p.status.value


def _tile(css: str, counted: list[str]) -> str:
    """A tile that filters by its status, when it counts anything to filter."""
    mine = ("before", "with", "upgrade") if css == "upgrade" else (css,)
    values = [k for k in mine if k in counted]
    return f' data-tile="{",".join(values)}" aria-pressed="false"' if values else ""


def _data(p: PackageReport) -> str:
    """What the script filters, searches and sorts a row by."""
    words = " ".join([p.name, p.display_name, p.reason, *row_notes(p)]).lower()
    noted = " data-notes" if row_notes(p) else ""
    noted += " data-direct" if p.direct else ""
    majors = -1 if p.majors_crossed is None else p.majors_crossed
    released = f"{p.last_release:%Y-%m-%d}" if p.last_release else ""
    return (
        f' data-filter="{_filter(p)}" data-search="{escape(words)}"{noted}'
        f' data-name="{escape(p.name)}" data-majors="{majors}" data-released="{released}"'
    )


def _more(p: PackageReport, static: bool, command: str | None = None) -> str:
    """What a row shows when it is opened: links, the newest release, the line to pin."""
    facts = []
    links = []
    if not p.source:
        links.append(("PyPI", f"https://pypi.org/project/{p.name}/"))
    links += [(label, url) for label, url in row_links(p)]
    if p.repository_url:
        links.append(("repository", safe_url(p.repository_url)))
    if links:
        anchors = " · ".join(f'<a href="{escape(url)}">{escape(label)}</a>' for label, url in links)
        facts.append(("Links", anchors))
    if p.latest:
        facts.append(("Newest", escape(p.latest)))
    if p.last_release:
        facts.append(("Last release", f"{p.last_release:%Y-%m-%d}"))
    if p.target_version and not p.source:
        pin = f"<code>{escape(p.display_name)}=={escape(p.target_version)}</code>"
        if not static:
            pin += '<button type="button" class="copy" hidden>copy</button>'
        facts.append(("Pin", pin))
    if command:
        line = f"<code>{escape(command)}</code>"
        if not static:
            line += '<button type="button" class="copy" hidden>copy</button>'
        facts.append(("Command", line))
    if not facts:
        return ""
    rows = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in facts)
    return f'<details class="more"><summary>details</summary><dl>{rows}</dl></details>'


def _table(
    packages: list[PackageReport],
    todo: str | None = None,
    static: bool = False,
    command: dict[str, str] | None = None,
) -> str:
    """``todo`` adds a checkbox per row, named ``todo:name`` to keep the tick."""
    rows = []
    for p in packages:
        tick = (
            f'<td class="tick"><input type="checkbox" data-todo="{escape(todo)}:{escape(p.name)}" '
            f'aria-label="{escape(p.display_name)} done"></td>'
            if todo
            else ""
        )
        signs = {e.text for e in p.evidence} | {i.label for i in p.upstream}
        notes = "".join(
            f'<span class="note{" warn" if n.startswith("no release") else ""}">{escape(n)}</span>'
            for n in row_notes(p)
            if n not in signs
        )
        notes += "".join(
            f'<a class="note sign" href="{escape(safe_url(e.url))}">{escape(e.text)}</a>'
            if e.url
            else f'<span class="note sign">{escape(e.text)}</span>'
            for e in p.evidence
        )
        notes += "".join(
            f'<a class="note" href="{escape(safe_url(i.url))}">{escape(i.label)}</a>'
            for i in p.upstream
        )
        links = "".join(
            f' <a class="link" href="{escape(url)}">{escape(label)}</a>'
            for label, url in row_links(p)
        )
        more = _more(p, static, (command or {}).get(p.name))
        rows.append(
            f'<tr{_data(p)}>{tick}<td class="name">{escape(p.display_name)}</td>'
            f'<td class="version">{escape(version_cell(p))}</td>'
            f"<td>{escape(p.reason)}{links}{'<br>' + notes if notes else ''}{more}"
            "</td></tr>"
        )
    done = '<th class="tick"><span class="sr">Done</span></th>' if todo else ""
    return (
        f'<div class="table"><table><thead><tr>{done}<th data-sort="name">Package</th>'
        '<th data-sort="majors" title="sorted by major versions crossed">Version</th>'
        '<th data-sort="released" title="sorted by last release">Why</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
