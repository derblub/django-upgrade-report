"""``-i``: the report in the terminal, to move through instead of scrolling.

Imported only for ``-i``: it needs the ``tui`` extra (Textual), the core does not. It shows
the finished report through the same helpers as the other formats and decides nothing.
"""

from __future__ import annotations

import json
import webbrowser
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState

from django_upgrade_report import commands
from django_upgrade_report.analysis import PackageReport, Report
from django_upgrade_report.render import (
    headline,
    markdown,
    row_links,
    row_notes,
    sections,
    summary,
    text,
    version_cell,
)
from django_upgrade_report.render import html as html_report
from django_upgrade_report.render import json as json_report

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


_FILTERS = (None, "blocked", "upgrade", "check", "ready")
"""What ``f`` steps through: everything, then one status at a time."""


class ReportApp(App):
    """Sections and packages on the left, the chosen package on the right."""

    TITLE = "django-upgrade-report"
    CSS = """
    #head { height: 1; padding: 0 1; background: $panel; }
    #search, #ask { display: none; }
    #search.shown, #ask.shown { display: block; }
    #packages { width: 42%; min-width: 30; }
    #details { padding: 0 1; }
    """
    BINDINGS = [
        Binding("/", "search", "search"),
        Binding("f", "filter", "filter"),
        Binding("space", "done", "done"),
        Binding("e", "copy", "command"),
        Binding("o", "open", "open link"),
        Binding("t", "target", "target"),
        Binding("w", "write", "write"),
        Binding("escape", "clear", "clear", show=False),
        Binding("question_mark", "help", "help"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(
        self,
        report: Report,
        recompute: Callable[[str], Report] | None = None,
        state: Path | None = None,
    ):
        super().__init__()
        self.recompute = recompute
        self.state = state
        self.query_text = ""
        self.status: str | None = None
        self.asking: str | None = None
        self.busy = ""
        self.rows: dict[str, PackageReport] = {}
        self.done: dict[tuple[str, str], str] = self._load()
        self.use(report)

    def use(self, report: Report) -> None:
        self.report = report
        try:
            self.tool: str | None = commands.tool_for(report, "auto")
        except commands.EmitError:
            self.tool = None

    def compose(self) -> ComposeResult:
        yield Static(id="head")
        yield Input(placeholder="Search names, reasons and notes", id="search")
        yield Input(id="ask")
        with Horizontal():
            yield OptionList(id="packages")
            with VerticalScroll():
                yield Static(id="details")
        yield Footer()

    def on_mount(self) -> None:
        self.fill()
        self.query_one("#packages", OptionList).focus()

    def heading(self) -> None:
        parts = [headline(self.report), summary(self.report)]
        if self.status:
            parts.append(f"only {self.status}")
        if self.busy:
            parts.append(self.busy)
        self.query_one("#head", Static).update(Text("  ·  ".join(parts)))

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
        if self.status and p.status.value != self.status:
            return False
        words = self.query_text.lower().split()
        text = " ".join([p.name, p.display_name, p.reason, *row_notes(p)]).lower()
        return all(word in text for word in words)

    def fill(self) -> None:
        self.heading()
        packages = self.query_one("#packages", OptionList)
        was = self.current_id()
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
                ticked = self.is_done(p)
                mark = "✔" if ticked else _MARK.get(p.status.value, "·")
                line = Text(f"{mark} ", style="dim" if ticked else _COLOR[key])
                line.append(
                    f"{p.display_name.ljust(width)}  {version_cell(p)}",
                    style="dim strike" if ticked else "",
                )
                options.append(Option(line, id=row_id))
        packages.add_options(options)
        # Indices as the list counts them: separators (None) are not options.
        listed = [packages.get_option_at_index(i) for i in range(packages.option_count)]
        ids = [o.id for o in listed]
        first = next((i for i, o in enumerate(listed) if not o.disabled), None)
        if first is None:
            self.query_one("#details", Static).update("No package matches.")
            return
        packages.highlighted = ids.index(was) if was in ids else first
        # The same index fires no event, though the option there may be another one.
        chosen = self.current()
        if chosen is not None:
            self.show(chosen)

    def current_id(self) -> str | None:
        packages = self.query_one("#packages", OptionList)
        if packages.highlighted is None or packages.option_count == 0:
            return None
        return packages.get_option_at_index(packages.highlighted).id

    def current(self) -> PackageReport | None:
        return self.rows.get(self.current_id() or "")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        p = self.rows.get(event.option.id or "")
        if p is not None:
            self.show(p)

    def show(self, p: PackageReport) -> None:
        rows = details(self.report, p, self.tool)
        if self.is_done(p):
            rows.insert(1, ("Done", self.done[(p.name, self.report.target)][:10]))
        width = max((len(label) for label, _ in rows), default=0)
        lines = []
        for n, (label, value) in enumerate(rows):
            if n == 0:
                lines.append(f"[b]{escape(value)}[/b]")
                continue
            if label and not rows[n - 1][0]:
                lines.append("")  # the labelled part below the notes
            if label:
                lines.append(f"[dim]{escape(label.ljust(width))}[/dim]  {escape(value)}")
            else:
                lines.append(escape(value))
        self.query_one("#details", Static).update("\n".join(lines))

    # --- search and filter

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.add_class("shown")
        search.focus()

    def action_filter(self) -> None:
        present = {p.status.value for _, _, rows in self.groups() for p in rows}
        choices = [None, *(s for s in _FILTERS[1:] if s in present)]
        at = choices.index(self.status) if self.status in choices else 0
        self.status = choices[(at + 1) % len(choices)]
        self.fill()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.query_text = event.value
            self.fill()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "ask":
            asked, self.asking = self.asking, None
            event.input.remove_class("shown")
            answer = event.value.strip()
            event.input.value = ""
            if answer and asked == "target":
                self.switch(answer)
            elif answer and asked == "write":
                self.write(Path(answer))
        self.query_one("#packages", OptionList).focus()

    def action_clear(self) -> None:
        for name in ("#search", "#ask"):
            box = self.query_one(name, Input)
            box.value = ""
            box.remove_class("shown")
        self.asking = None
        self.status = None
        self.fill()
        self.query_one("#packages", OptionList).focus()

    def ask(self, what: str, placeholder: str) -> None:
        box = self.query_one("#ask", Input)
        self.asking = what
        box.placeholder = placeholder
        box.add_class("shown")
        box.focus()

    # --- ticks, kept in the project

    def _load(self) -> dict[tuple[str, str], str]:
        try:
            data = json.loads(self.state.read_text(encoding="utf-8")) if self.state else {}
        except (OSError, ValueError):
            return {}
        done = data.get("done") if isinstance(data, dict) else None
        return {
            (str(d["package"]), str(d["target"])): str(d.get("at", ""))
            for d in done or []
            if isinstance(d, dict) and "package" in d and "target" in d
        }

    def is_done(self, p: PackageReport) -> bool:
        return (p.name, self.report.target) in self.done

    def action_done(self) -> None:
        p = self.current()
        if p is None:
            return
        key = (p.name, self.report.target)
        if key in self.done:
            del self.done[key]
        else:
            self.done[key] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if self.state is not None:
            entries = [
                {"package": name, "target": target, "at": at}
                for (name, target), at in sorted(self.done.items())
            ]
            try:
                self.state.parent.mkdir(parents=True, exist_ok=True)
                self.state.write_text(json.dumps({"done": entries}, indent=2) + "\n")
            except OSError as exc:
                self.notify(f"Could not keep the tick in {self.state}: {exc}", severity="error")
        self.fill()
        self.show(p)

    # --- the command and the links

    def action_copy(self) -> None:
        p = self.current()
        command = commands.command(self.report, p, self.tool) if p and self.tool else None
        if not command or command.startswith("#"):
            self.notify("No command for this package: the tool is not clear from the source.")
            return
        self.copy_to_clipboard(command)  # OSC 52: works over SSH, where the terminal allows it
        self.notify(command, title="Copied")

    def action_open(self) -> None:
        p = self.current()
        if p is None:
            return
        url = p.changelog_url or p.repository_url
        if url is None and not p.source:
            url = f"https://pypi.org/project/{p.name}/"
        if url is None:
            self.notify("No link for this package.")
            return
        webbrowser.open(url)
        self.notify(url, title="Opened")

    # --- another target, a file

    def action_target(self) -> None:
        if self.recompute is None:
            self.notify("The target cannot be changed here.")
            return
        self.ask("target", f"Target instead of {self.report.target}: e.g. 6.0, lts, latest")

    def switch(self, target: str) -> None:
        self.busy = f"checking against {target}…"
        self.heading()
        self.run_worker(
            lambda: self.recompute(target),
            thread=True,
            exclusive=True,
            name="target",
            exit_on_error=False,  # an unknown target is a note, not the end of the app
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "target":
            return
        if event.state is WorkerState.SUCCESS:
            self.busy = ""
            self.use(event.worker.result)
            self.fill()
        elif event.state is WorkerState.ERROR:
            self.busy = ""
            self.heading()
            self.notify(str(event.worker.error), title="Could not check", severity="error")

    def action_write(self) -> None:
        self.ask("write", "Write the report to: report.html, report.md, report.json or report.txt")

    def write(self, path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix in (".html", ".htm"):
            output = html_report.render(self.report)
        elif suffix == ".md":
            output = markdown.render(self.report)
        elif suffix == ".json":
            output = json_report.render(self.report)
        else:
            output = text.render(self.report, verbose=True) + "\n"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output, encoding="utf-8", newline="")
        except OSError as exc:
            self.notify(f"Could not write {path}: {exc}", severity="error")
            return
        self.notify(str(path), title="Written")

    def action_help(self) -> None:
        self.notify(
            "↑ ↓ choose · / search · f filter by status · space done · e copy the command · "
            "o open the changelog · t another target · w write the report · Esc clear · q quit",
            title="Keys",
        )


def run(
    report: Report,
    recompute: Callable[[str], Report] | None = None,
    state: Path | None = None,
) -> int:
    ReportApp(report, recompute, state).run()
    return 0
