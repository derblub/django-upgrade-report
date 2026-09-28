from __future__ import annotations

import argparse
import codecs
import contextlib
import os
import sys
import threading
from pathlib import Path

from django_upgrade_report import AUTHOR, COMPANY, COMPANY_URL, REPO_URL, __version__, sources
from django_upgrade_report.analysis import Status, analyse, from_other_index
from django_upgrade_report.pypi import PyPI, PyPIError, default_cache_dir
from django_upgrade_report.render import html, json, markdown, text

_FAIL_ON = {
    "blocked": {Status.BLOCKED},
    "upgrade": {Status.BLOCKED, Status.UPGRADE},
    "check": {Status.BLOCKED, Status.UPGRADE, Status.CHECK},
}


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
        "--fail-on",
        choices=sorted(_FAIL_ON),
        help="exit with status 1 when a package is blocked, needs an upgrade or needs a check "
        "(errors exit with status 2)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="list ready packages too")
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
    try:
        return _run(args)
    except Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # a bug: still exit 2, so CI does not read it as "blocked"
        message = f"unexpected {type(exc).__name__}"
        # When the index URL itself is broken, leave out the details: they may hold it.
        with contextlib.suppress(Exception):
            message = PyPI(args.index_url or PYPI_JSON).redact(f"{message}: {exc}")
        print(f"error: {message}\nPlease report this at {REPO_URL}/issues", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    if args.output is not None and args.output.is_dir():
        raise Error(f"{args.output} is a directory, pass a file name to -o")
    try:
        deps = sources.load(args.project, args.python)
    except (sources.NoDependenciesFound, sources.SourceError, OSError, ValueError) as exc:
        raise Error(exc) from None

    index_url = args.index_url or PYPI_JSON
    pypi = PyPI(index_url, cache_dir=None if args.no_cache else default_cache_dir())
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
        )
    except (PyPIError, ValueError, RuntimeError, OSError) as exc:
        raise Error(pypi.redact(str(exc))) from None
    finally:
        progress.clear()

    if args.format == "text":
        use_color = args.output is None and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        output = text.render(report, color=use_color, verbose=args.verbose) + "\n"
    else:
        output = {"markdown": markdown, "json": json, "html": html}[args.format].render(report)

    if args.output:
        _write(args.output, output)
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(output)
        sys.stdout.flush()

    if args.fail_on and report.failed:
        raise Error(
            f"--fail-on: {len(report.failed)} dependencies could not be checked "
            f"({', '.join(report.failed)}): the package index did not answer. Run again later."
        )
    if args.fail_on and not report.packages and _from_other_index(report):
        raise Error(
            "--fail-on: no Django-related package was checked, they come from another index. "
            "If it mirrors PyPI, pass --check-private-on-pypi; else pass its JSON API with "
            "--index-url"
        )
    if args.fail_on and any(p.status in _FAIL_ON[args.fail_on] for p in report.packages):
        return 1
    return 0


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
