"""A single, self-contained HTML file you can attach to a ticket or send to a client."""

from __future__ import annotations

from html import escape

from django_upgrade_report import AUTHOR, COMPANY, COMPANY_URL, REPO_URL, __version__
from django_upgrade_report.analysis import PackageReport, Report, Status
from django_upgrade_report.render import (
    headline,
    packages_line,
    private_index_hint,
    python_hint,
    python_line,
    python_summary,
    row_links,
    row_notes,
    sections,
    skipped_line,
    split_noted,
    version_cell,
)

_CSS = """
:root {
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
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 32px 0 40px; }
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
.note { display: inline-block; margin: 4px 6px 0 0; padding: 1px 8px; border-radius: 99px;
  font-size: 12px; background: var(--bg); color: var(--muted); border: 1px solid var(--line); }
a.link { color: var(--muted); font-size: 13px; }
.note.warn { background: var(--blocked-bg); color: var(--blocked); border-color: transparent; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { background: var(--ready-bg); color: var(--ready); border-radius: 99px;
  padding: 3px 12px; font-size: 13px; font-weight: 500; }
.warnings { background: var(--upgrade-bg); color: var(--upgrade); border-radius: 10px;
  padding: 12px 16px; margin: 24px 0 0; }
.warnings p { margin: 0; } .warnings p + p { margin-top: 6px; }
.chips + .table { margin-top: 12px; }
.aside { margin-top: 28px; }
.aside h3 { font-size: 15px; margin: 0 0 6px; }
.aside ul { margin: 0; padding-left: 20px; }
.aside li span { color: var(--muted); }
.meta { color: var(--muted); font-size: 13px; margin-top: 48px; border-top: 1px solid var(--line);
  padding-top: 16px; }
.meta a { color: inherit; }
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


def render(report: Report) -> str:
    counts = report.counts
    title = headline(report)
    start, arrow, end = title.partition(" → ")
    heading = (
        f'{escape(start)} <span class="arrow">→</span> {escape(end)}' if arrow else escape(title)
    )
    kind = "Health check" if report.health_check else "Upgrade report"
    intro = " ".join(f"{escape(n)}." for n in report.notices)
    intro = f"{intro} {kind} for {packages_line(report)}".strip()

    tiles = "".join(
        f'<div class="tile {css}{" zero" if not counts[status] else ""}">'
        f"<b>{counts[status]}</b><span>{label}</span></div>"
        for status, css, label in (
            (Status.READY, "ready", "ready"),
            (Status.UPGRADE, "upgrade", "to upgrade"),
            (Status.CHECK, "check", "to check"),
            (Status.BLOCKED, "blocked", "blocked"),
        )
    )

    body = []
    plan = report.python
    if plan is not None:
        title = f"Python {plan.target} first" if plan.packages else f"Python {plan.target}"
        count = f' <span class="count">{len(plan.packages)}</span>' if plan.packages else ""
        body.append(
            f'<section id="python"><h2><span class="dot python"></span>{escape(title)}{count}</h2>'
        )
        if plan.packages:
            body.append(f'<p class="hint">{escape(python_hint(report))}</p>')
            body.append(_table(plan.packages))
        summary = "".join(f"<p>{escape(line)}.</p>" for line in python_summary(report))
        body.append(f'<div class="hint">{summary}</div></section>')
    for section in sections(report):
        color = _SECTION_COLOR[section.key]
        body.append(
            f'<section id="{section.key}"><h2><span class="dot {color}"></span>'
            f'{escape(section.title)} <span class="count">{len(section.packages)}</span></h2>'
            f'<p class="hint">{escape(section.hint)}</p>'
        )
        packages = section.packages
        if section.key == "ready":
            plain, packages = split_noted(packages)
            if plain:
                chips = "".join(
                    f'<span class="chip" title="{escape(p.reason)}">{escape(p.display_name)}</span>'
                    for p in plain
                )
                body.append(f'<div class="chips">{chips}</div>')
        if packages:
            body.append(_table(packages))
        body.append("</section>")

    if report.missing:
        items = "".join(f"<li>{escape(name)}</li>" for name in report.missing)
        body.append(
            '<section class="aside" id="missing"><h3>Not on the package index</h3>'
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
            '<section class="aside" id="external"><h3>Not from PyPI, not checked</h3>'
            f"<ul>{items}</ul>{hint_html}</section>"
        )

    meta = [f"Dependencies from <code>{escape(report.source)}</code>."]
    if report.skipped:
        meta.append(f"{escape(skipped_line(report))}.")
    python = python_line(report)
    if python:
        meta.append(f"{escape(python)}.")
    meta.append(
        f"Generated {report.generated:%Y-%m-%d %H:%M} UTC by "
        f'<a href="{REPO_URL}">django-upgrade-report</a> {__version__} '
        f'by {AUTHOR}, <a href="{COMPANY_URL}">{COMPANY}</a>, '
        "from the metadata packages publish on PyPI. "
        "A green row means the maintainers declare support, not that your tests pass."
    )

    warnings = ""
    if report.warnings:
        paragraphs = "".join(f"<p>{escape(w)}</p>" for w in report.warnings)
        warnings = f'<div class="warnings" role="note">{paragraphs}</div>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · upgrade report</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<header>
<h1>{heading}</h1>
<p>{intro}</p>
</header>
{warnings}<div class="tiles">{tiles}</div>
{"".join(body)}
<p class="meta">{" ".join(meta)}</p>
</main>
</body>
</html>
"""


def _table(packages: list[PackageReport]) -> str:
    rows = []
    for p in packages:
        notes = "".join(
            f'<span class="note{" warn" if n.startswith("no release") else ""}">{escape(n)}</span>'
            for n in row_notes(p)
        )
        links = "".join(
            f' <a class="link" href="{escape(url)}">{escape(label)}</a>'
            for label, url in row_links(p)
        )
        rows.append(
            f'<tr><td class="name">{escape(p.display_name)}</td>'
            f'<td class="version">{escape(version_cell(p))}</td>'
            f"<td>{escape(p.reason)}{links}{'<br>' + notes if notes else ''}</td></tr>"
        )
    return (
        '<div class="table"><table><thead><tr><th>Package</th><th>Version</th>'
        f"<th>Why</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )
