from __future__ import annotations

import argparse
import codecs
import contextlib
import difflib
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from packaging.utils import canonicalize_name

from django_upgrade_report import (
    AUTHOR,
    COMPANY,
    COMPANY_URL,
    REPO_URL,
    __version__,
    commands,
    sources,
)
from django_upgrade_report.analysis import SEVERITY, Status, analyse, from_other_index
from django_upgrade_report.diff import BaselineError, compare, load_baseline
from django_upgrade_report.prompts import ask_missing, terminal_ask
from django_upgrade_report.pypi import (
    OFFLINE,
    ONLINE,
    PREFER_CACHE,
    NotCached,
    PyPI,
    PyPIError,
    default_cache_dir,
)
from django_upgrade_report.python import check_python_target, plan_python, python_target
from django_upgrade_report.render import explain, html, json, markdown, text

_FAIL_ON = {"blocked": Status.BLOCKED, "upgrade": Status.UPGRADE, "check": Status.CHECK}
"""``--fail-on`` value -> the least severe status that fails."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="django-upgrade-report",
        description=(
            "Find out which of your dependencies block a Django upgrade, "
            "and in which order to upgrade them."
        ),
        epilog=f"Made by {AUTHOR}, {COMPANY} ({COMPANY_URL}).",
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        type=Path,
        help="project directory with a lockfile, requirements*.txt or pyproject.toml "
        "(default: current directory)",
    )
    parser.add_argument(
        "-t",
        "--target",
        default="auto",
        help="Django version to upgrade to: 'auto' (default: the newest LTS above your Django, "
        "or the newest release when no LTS is above it), 'lts', 'latest' or e.g. '5.2'",
    )
    parser.add_argument(
        "--from",
        dest="current",
        metavar="VERSION",
        help="the Django version you run today, e.g. 4.2 or 4.2.16, when your requirements "
        "only give a range",
    )
    parser.add_argument(
        "--python",
        metavar="PATH",
        help="read exact versions from the packages installed for this interpreter, "
        "e.g. .venv/bin/python",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["text", "markdown", "json", "html"],
        default="text",
        help="output format (default: text)",
    )
    parser.add_argument("-o", "--output", type=Path, help="write the report to a file")
    parser.add_argument(
        "--emit",
        choices=["auto", *commands.TOOLS, *commands.BOTS],
        help="print the commands that carry out the plan instead of the report, for this tool; "
        "'auto' picks it by the lockfile. With --format json: the commands field. 'renovate' "
        "and 'dependabot' print a configuration that makes the bot follow the plan",
    )
    parser.add_argument(
        "--static",
        action="store_true",
        help="with --format html: a page without scripts, no search, filters or checklist "
        "counter, for places that block scripts in attachments",
    )
    parser.add_argument(
        "--fail-on",
        choices=sorted(_FAIL_ON),
        help="exit with status 1 when a package is blocked, needs an upgrade or needs a check "
        "(errors exit with status 2)",
    )
    parser.add_argument(
        "--fail-on-python",
        choices=sorted(_FAIL_ON),
        help="like --fail-on, for the dependencies on the newer Python (see --python-target)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="list ready packages too")
    parser.add_argument(
        "--baseline",
        type=Path,
        metavar="REPORT.json",
        help="an earlier --format json report: start with what changed since",
    )
    parser.add_argument(
        "--only-changes",
        action="store_true",
        help="with --baseline: show only what changed, and nothing when nothing did",
    )
    parser.add_argument(
        "--fail-on-change",
        choices=["any", "worse"],
        help="with --baseline: exit with status 1 when anything changed, or when something "
        "needs more attention than before",
    )
    parser.add_argument(
        "--python-target",
        default="auto",
        metavar="VERSION",
        help="check every dependency on this Python too, e.g. 3.12: 'auto' (default) when the "
        "target Django needs a newer Python than your project uses, 'none' never",
    )
    parser.add_argument(
        "--explain",
        action="append",
        default=[],
        metavar="PACKAGE",
        help="show step by step how the verdict on PACKAGE came about, instead of the report; "
        "can be given more than once",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="text output only: the headline, warnings, blocked packages and the counts",
    )
    parser.add_argument(
        "--index-url",
        help=f"PyPI JSON API base URL (default: {PYPI_JSON}). Packages your project installs "
        "from another index are looked up only when you pass this",
    )
    parser.add_argument(
        "--check-private-on-pypi",
        action="store_true",
        help="look up packages your project installs from another index on PyPI, too: for an "
        "index that mirrors PyPI (Artifactory, Nexus, devpi). Their names are sent to PyPI",
    )
    parser.add_argument("--no-cache", action="store_true", help="do not cache PyPI responses")
    cache = parser.add_mutually_exclusive_group()
    cache.add_argument(
        "--offline",
        action="store_true",
        help="answer from the cache only, however old, and never ask the package index",
    )
    cache.add_argument(
        "--prefer-cache",
        action="store_true",
        help="answer from the cache, however old, and ask the index only for what is missing",
    )
    parser.add_argument(
        "--no-input",
        action="store_true",
        help="never ask in the terminal what the project leaves out, such as your Django "
        "version when it is not pinned",
    )
    parser.add_argument(
        "--errors-as-warnings",
        action="store_true",
        help="exit with status 0 instead of 2 when the report cannot be made, e.g. offline: "
        "for hooks that must not block a commit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__} by {AUTHOR}, {COMPANY}",
    )
    return parser


PYPI_JSON = "https://pypi.org/pypi"


class Error(Exception):
    """An expected failure: printed as ``error: ...``, exit status 2."""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _utf8_streams()
    # A hook must not stop a commit because the report could not be made.
    failure, label = (0, "warning") if args.errors_as_warnings else (2, "error")
    try:
        return _run(args)
    except Error as exc:
        print(f"{label}: {exc}", file=sys.stderr)
        return failure
    except Exception as exc:  # a bug: exit 2 (or 0 for a hook), so CI does not read it as "blocked"
        message = f"unexpected {type(exc).__name__}"
        # When the index URL itself is broken, leave out the details: they may hold it.
        with contextlib.suppress(Exception):
            message = PyPI(args.index_url or PYPI_JSON).redact(f"{message}: {exc}")
        print(f"{label}: {message}\nPlease report this at {REPO_URL}/issues", file=sys.stderr)
        return failure


def _run(args: argparse.Namespace) -> int:
    if args.output is not None and args.output.is_dir():
        raise Error(f"{args.output} is a directory, pass a file name to -o")
    mode = OFFLINE if args.offline else PREFER_CACHE if args.prefer_cache else ONLINE
    if args.no_cache and mode != ONLINE:
        raise Error(f"--{mode} reads the cache, it cannot go with --no-cache")
    try:
        deps = sources.load(args.project, args.python)
    except (sources.NoDependenciesFound, sources.SourceError, OSError, ValueError) as exc:
        raise Error(exc) from None

    baseline = None
    if args.baseline is not None:
        try:
            baseline = load_baseline(args.baseline)
        except BaselineError as exc:
            raise Error(exc) from None
    elif args.only_changes or args.fail_on_change:
        flag = "--only-changes" if args.only_changes else "--fail-on-change"
        raise Error(f"{flag} needs --baseline, an earlier --format json report")
    if args.emit and (args.format in ("markdown", "html") or args.explain or args.quiet):
        flag = (
            f"--format {args.format}"
            if args.format in ("markdown", "html")
            else ("--explain" if args.explain else "--quiet")
        )
        raise Error(f"--emit prints commands, or with --format json the commands field: not {flag}")
    if args.emit in commands.BOTS and args.format == "json":
        raise Error(f"--emit {args.emit} prints a configuration, it cannot go with --format json")
    if args.emit and args.only_changes:
        raise Error("--emit prints the whole plan, it cannot go with --only-changes")
    if args.static and args.format != "html":
        raise Error("--static goes with --format html")
    if args.only_changes and (args.format == "html" or args.explain):
        raise Error("--only-changes works with the text, Markdown and JSON reports")
    if args.python_target not in ("auto", "none"):
        try:
            check_python_target(args.python_target)
        except ValueError as exc:
            raise Error(exc) from None
    explained = [canonicalize_name(name) for name in args.explain]
    if explained and args.format in ("markdown", "html"):
        raise Error("--explain prints text, or with --format json the explain field")
    if explained and (args.fail_on or args.quiet):
        flag = "--fail-on" if args.fail_on else "--quiet"
        raise Error(f"--explain shows one package, it cannot go with {flag}")
    for name in explained:
        if name not in deps.dependencies:
            close = difflib.get_close_matches(name, deps.dependencies, n=3)
            hint = f", did you mean {' or '.join(close)}?" if close else ""
            raise Error(f"--explain: {name} is not among your dependencies{hint}")

    index_url = args.index_url or PYPI_JSON
    pypi = PyPI(index_url, cache_dir=None if args.no_cache else default_cache_dir(), mode=mode)
    if _interactive(args):
        try:
            answers = ask_missing(deps, pypi, args.target, args.current, terminal_ask)
        except (PyPIError, ValueError):
            answers = None  # questions are a help: the report says what is wrong itself
        if answers is not None:
            args.current = answers.current or args.current
            args.target = answers.target or args.target
            if answers.python:  # markers in the requirements depend on it: read them again
                deps = sources.load(args.project, args.python, python_version=answers.python)
            if tip := answers.tip():
                print(tip, file=sys.stderr)
    # An index URL you pass, even pypi.org's, is where your packages are meant to be looked up.
    private_index = args.index_url is not None or args.check_private_on_pypi

    def checked(d: sources.Dependency) -> bool:
        return not d.external or (private_index and d.external.startswith("index "))

    progress = _Progress(
        total=sum(1 for name, d in deps.dependencies.items() if name != "django" and checked(d))
    )
    try:
        report = analyse(
            deps,
            pypi,
            args.target,
            progress=progress,
            current=args.current,
            private_index=private_index,
            explain=explained,
        )
        python = python_target(args.python_target, report)
        if python is not None:
            report.python = plan_python(python, report, deps, pypi)
    except NotCached as exc:
        raise Error(f"{pypi.redact(str(exc))}, run once without --offline") from None
    except (PyPIError, ValueError, RuntimeError, OSError) as exc:
        raise Error(pypi.redact(str(exc))) from None
    finally:
        progress.clear()
    oldest = pypi.oldest_cached
    if mode != ONLINE and oldest is not None and time.time() - oldest > pypi.cache_ttl:
        day = datetime.fromtimestamp(oldest, timezone.utc).date()
        report.notices.append(f"Answers from the cache, the oldest from {day}")

    if baseline is not None:
        report.changes = compare(baseline, report)
    if args.only_changes and report.changes.compared and not report.changes.items:
        return 0  # nothing to say, not even an empty file
    elif explained and args.format == "text":
        output = explain.render(report)
    elif args.emit in commands.BOTS:
        output = getattr(commands, args.emit)(report)
    elif args.emit:
        try:
            emitted = commands.plan(report, commands.tool_for(report, args.emit))
        except commands.EmitError as exc:
            raise Error(exc) from None
        output = (
            json.render(report, emitted)
            if args.format == "json"
            else commands.render(report, emitted)
        )
    elif args.format == "text":
        use_color = args.output is None and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        output = text.render(
            report,
            color=use_color,
            verbose=args.verbose,
            quiet=args.quiet,
            only_changes=args.only_changes,
        )
        output += "\n"
    else:
        output = (
            markdown.render(report, only_changes=args.only_changes)
            if args.format == "markdown"
            else html.render(report, static=args.static)
            if args.format == "html"
            else json.render(report)
        )

    if args.output:
        _write(args.output, output)
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(output)
        sys.stdout.flush()

    threshold = SEVERITY[_FAIL_ON[args.fail_on]] if args.fail_on else None
    if threshold is not None and any(SEVERITY[p.status] >= threshold for p in report.packages):
        return 1  # a package that needs attention is a result, even if others are unknown
    if args.fail_on_python and report.python is not None:
        python = SEVERITY[_FAIL_ON[args.fail_on_python]]
        if any(SEVERITY[p.status] >= python for p in report.python.packages):
            return 1
    if args.fail_on and report.failed:
        why = (
            "they are not in the cache. Run once without --offline"
            if mode == OFFLINE
            else "the package index did not answer. Run again later"
        )
        raise Error(
            f"--fail-on: {len(report.failed)} dependencies could not be checked "
            f"({', '.join(report.failed)}): {why}."
        )
    if args.fail_on and not report.packages and _from_other_index(report):
        raise Error(
            "--fail-on: no Django-related package was checked, they come from another index. "
            "If it mirrors PyPI, pass --check-private-on-pypi; else pass its JSON API with "
            "--index-url"
        )
    changed = report.changes is not None and bool(report.changes.items)
    if changed and (args.fail_on_change == "any" or (args.fail_on_change and report.changes.worse)):
        return 1
    return 0


def _interactive(args: argparse.Namespace) -> bool:
    """Questions only for a person at a terminal reading the text report, never in CI."""
    return (
        not args.no_input
        and args.format == "text"
        and args.output is None
        and not args.explain
        and not args.emit
        and "CI" not in os.environ
        and _at_terminal()
    )


def _at_terminal() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def _from_other_index(report) -> bool:
    return any(from_other_index(where) for _, where in report.external)


def _write(path: Path, output: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" keeps "\n" on Windows too, like the other formats' files.
        path.write_text(output, encoding="utf-8", newline="")
    except OSError as exc:
        raise Error(f"cannot write {path}: {exc.strerror or exc}") from None


def _utf8_streams() -> None:
    """Arrows and emoji must not crash a redirect on Windows (cp1252) or in a C locale."""
    for stream, errors in ((sys.stdout, "strict"), (sys.stderr, "backslashreplace")):
        encoding = getattr(stream, "encoding", None) or "ascii"
        try:
            utf8 = codecs.lookup(encoding).name == "utf-8"
        except LookupError:
            utf8 = False
        if not utf8 and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors=errors)


class _Progress:
    """``Checking 3/40 name`` on an interactive stderr; safe to call from worker threads."""

    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self.interactive = sys.stderr.isatty()
        self._lock = threading.Lock()

    def __call__(self, name: str) -> None:
        with self._lock:
            self.done = min(self.done + 1, self.total)
            if self.interactive:
                line = f"\r\033[2KChecking {self.done}/{self.total} {name}"
                print(line, end="", file=sys.stderr, flush=True)

    def clear(self) -> None:
        if self.interactive:
            print("\r\033[2K", end="", file=sys.stderr, flush=True)
