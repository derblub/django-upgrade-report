"""``-i``: the report in the terminal, to move through instead of scrolling.

Imported only for ``-i``: it needs the ``tui`` extra (Textual), the core does not. It shows
the finished report through the same helpers as the other formats and decides nothing.
"""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from django_upgrade_report import commands
from django_upgrade_report.analysis import PackageReport, Report
from django_upgrade_report.render import (
    headline,
    row_links,
    row_notes,
    sections,
    summary,
    version_cell,
)

_COLOR = {
    "blocked": "red",
    "before": "yellow",
    "with": "yellow",
    "upgrade": "yellow",
    "check": "cyan",
    "ready": "green",
    "python": "magenta",
}
_MARK = {"blocked": "✗", "before": "↑", "with": "↑", "upgrade": "↑", "check": "?", "ready": "✓"}


def details(report: Report, p: PackageReport, tool: str | None) -> list[tuple[str, str]]:
    """What the right-hand side shows for ``p``, as (label, text); labels may be empty."""
    lines = [("", f"{p.display_name}  {version_cell(p)}"), ("", p.reason)]
    lines += [("", note) for note in row_notes(p)]
    explained = report.explanations.get(p.name, [])
    if explained:
        lines.append(("Why", ""))
        lines += [("", f"{n}. {line.text}") for n, line in enumerate(explained, 1)]
    command = commands.command(report, p, tool) if tool else None
    if command and not command.startswith("#"):
        lines.append(("Command", command))
    links = [] if p.source else [("PyPI", f"https://pypi.org/project/{p.name}/")]
    links += row_links(p)
    if p.repository_url:
        links.append(("repository", p.repository_url))
    lines += [(label, url) for label, url in links]
    return lines


class ReportApp(App):
    """Sections and packages on the left, the chosen package on the right."""

    TITLE = "django-upgrade-report"
    CSS = """
    #head { height: 1; padding: 0 1; background: $panel; }
    #search { display: none; }
    #search.shown { display: block; }
    #packages { width: 42%; min-width: 30; }
    #details { padding: 0 1; }
    """
    BINDINGS = [
        Binding("/", "search", "search"),
        Binding("escape", "clear", "clear", show=False),
        Binding("question_mark", "help", "help"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, report: Report):
        super().__init__()
        self.report = report
        try:
            self.tool: str | None = commands.tool_for(report, "auto")
        except commands.EmitError:
            self.tool = None
        self.query_text = ""
        self.rows: dict[str, PackageReport] = {}

    def compose(self) -> ComposeResult:
        yield Static(Text(f"{headline(self.report)}  ·  {summary(self.report)}"), id="head")
        yield Input(placeholder="Search names, reasons and notes", id="search")
        with Horizontal():
            yield OptionList(id="packages")
            with VerticalScroll():
                yield Static(id="details")
        yield Footer()

    def on_mount(self) -> None:
        self.fill()
        self.query_one("#packages", OptionList).focus()

    # --- the list

    def groups(self) -> list[tuple[str, str, list[PackageReport]]]:
        """(key, title, packages) in the report's order, the Python section first."""
        found = []
        if self.report.python and self.report.python.packages:
            plan = self.report.python
            found.append(("python", f"Python {plan.target} first", plan.packages))
        found += [(s.key, s.title, s.packages) for s in sections(self.report)]
        return found

    def matches(self, p: PackageReport) -> bool:
        words = self.query_text.lower().split()
        text = " ".join([p.name, p.display_name, p.reason, *row_notes(p)]).lower()
        return all(word in text for word in words)

    def fill(self) -> None:
        packages = self.query_one("#packages", OptionList)
        packages.clear_options()
        self.rows = {}
        options: list[Option | None] = []
        for key, title, rows in self.groups():
            shown = [p for p in rows if self.matches(p)]
            if not shown:
                continue
            if options:
                options.append(None)
            heading = Text(f"{title} ({len(shown)})", style=f"bold {_COLOR[key]}")
            options.append(Option(heading, id=f"title:{key}", disabled=True))
            width = max(len(p.display_name) for p in shown)
            for p in shown:
                row_id = f"{key}:{p.name}"
                self.rows[row_id] = p
                line = Text(f"{_MARK.get(p.status.value, '·')} ", style=_COLOR[key])
                line.append(f"{p.display_name.ljust(width)}  {version_cell(p)}")
                options.append(Option(line, id=row_id))
        packages.add_options(options)
        first = next((i for i, o in enumerate(options) if o and not o.disabled), None)
        if first is not None:
            packages.highlighted = first
        else:
            self.query_one("#details", Static).update("No package matches.")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        p = self.rows.get(event.option.id or "")
        if p is not None:
            self.show(p)

    def show(self, p: PackageReport) -> None:
        rows = details(self.report, p, self.tool)
        width = max((len(label) for label, _ in rows), default=0)
        lines = []
        for n, (label, text) in enumerate(rows):
            if n == 0:
                lines.append(f"[b]{escape(text)}[/b]")
                continue
            if label and not rows[n - 1][0]:
                lines.append("")  # the labelled part below the notes
            if label:
                lines.append(f"[dim]{escape(label.ljust(width))}[/dim]  {escape(text)}")
            else:
                lines.append(escape(text))
        self.query_one("#details", Static).update("\n".join(lines))

    # --- search

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.add_class("shown")
        search.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_text = event.value
        self.fill()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#packages", OptionList).focus()

    def action_clear(self) -> None:
        search = self.query_one("#search", Input)
        search.value = ""
        search.remove_class("shown")
        self.query_one("#packages", OptionList).focus()

    def action_help(self) -> None:
        self.notify(
            "↑ ↓ choose a package · / search · Esc clear the search · q quit",
            title="Keys",
        )


def run(report: Report) -> int:
    ReportApp(report).run()
    return 0
