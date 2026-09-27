"""A single, self-contained HTML file you can attach to a ticket or send to a client."""

from __future__ import annotations

from html import escape

from django_upgrade_report import AUTHOR, COMPANY, COMPANY_URL, REPO_URL, __version__
from django_upgrade_report.analysis import Report, Status
from django_upgrade_report.render import headline, sections, version_cell

_CSS = """
:root {
  --bg: #f7f7f5; --panel: #ffffff; --text: #1b1d1c; --muted: #6a706d; --line: #e4e4e0;
  --ready: #1f7a4d; --upgrade: #a15c00; --check: #22639e; --blocked: #b3261e;
  --ready-bg: #e5f3ec; --upgrade-bg: #fbefdc; --check-bg: #e3eef8; --blocked-bg: #fbe4e2;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111312; --panel: #1a1d1c; --text: #e9ebea; --muted: #9aa19d; --line: #2c302e;
    --ready: #5fd49a; --upgrade: #f0b35a; --check: #7db6ec; --blocked: #f28b82;
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
.note.warn { background: var(--blocked-bg); color: var(--blocked); border-color: transparent; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { background: var(--ready-bg); color: var(--ready); border-radius: 99px;
  padding: 3px 12px; font-size: 13px; font-weight: 500; }
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
    start, _, end = title.partition(" → ")

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
    for section in sections(report):
        color = _SECTION_COLOR[section.key]
        body.append(
            f'<section id="{section.key}"><h2><span class="dot {color}"></span>'
            f'{escape(section.title)} <span class="count">{len(section.packages)}</span></h2>'
            f'<p class="hint">{escape(section.hint)}</p>'
        )
        if section.key == "ready":
            chips = "".join(
                f'<span class="chip" title="{escape(p.reason)}">{escape(p.display_name)}</span>'
                for p in section.packages
            )
            body.append(f'<div class="chips">{chips}</div></section>')
            continue
        rows = []
        for p in section.packages:
            notes = "".join(
                f'<span class="note{" warn" if n.startswith("no release") else ""}">'
                f"{escape(n)}</span>"
                for n in p.notes
            )
            rows.append(
                f'<tr><td class="name">{escape(p.display_name)}</td>'
                f'<td class="version">{escape(version_cell(p))}</td>'
                f"<td>{escape(p.reason)}{'<br>' + notes if notes else ''}</td></tr>"
            )
        body.append(
            '<div class="table"><table><thead><tr><th>Package</th><th>Version</th>'
            f"<th>Why</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
        )

    meta = [
        f"Dependencies from <code>{escape(report.source)}</code>.",
        f"{report.skipped} dependencies without a Django requirement were skipped.",
    ]
    if report.missing:
        meta.append(f"Not on the package index: {escape(', '.join(report.missing))}.")
    if report.django_requires_python:
        meta.append(
            f"Django {escape(report.target)} requires Python "
            f"<code>{escape(report.django_requires_python)}</code>."
        )
    meta.append(
        f"Generated {report.generated:%Y-%m-%d %H:%M} UTC by "
        f'<a href="{REPO_URL}">django-upgrade-report</a> {__version__} '
        f'by {AUTHOR}, <a href="{COMPANY_URL}">{COMPANY}</a>, '
        "from the metadata packages publish on PyPI. "
        "A green row means the maintainers declare support, not that your tests pass."
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
<main>
<header>
<h1>{escape(start)} <span class="arrow">→</span> {escape(end)}</h1>
<p>Upgrade report for {len(report.packages)} Django-related packages</p>
</header>
<div class="tiles">{tiles}</div>
{"".join(body)}
<p class="meta">{" ".join(meta)}</p>
</main>
</body>
</html>
"""
