from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from django_upgrade_report import __version__, sources
from django_upgrade_report.analysis import Status, analyse
from django_upgrade_report.pypi import PyPI, default_cache_dir
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
        default="lts",
        help="Django version to upgrade to: 'lts' (default), 'latest' or e.g. '5.2'",
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
        help="exit with status 1 when a package is blocked, needs an upgrade or needs a check",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="list ready packages too")
    parser.add_argument(
        "--index-url", default="https://pypi.org/pypi", help="PyPI JSON API base URL"
    )
    parser.add_argument("--no-cache", action="store_true", help="do not cache PyPI responses")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        deps = sources.load(args.project, args.python)
    except sources.NoDependenciesFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    pypi = PyPI(args.index_url, cache_dir=None if args.no_cache else default_cache_dir())
    interactive = sys.stderr.isatty()
    total = len(deps.dependencies)
    done = 0

    def progress(name: str) -> None:
        nonlocal done
        done += 1
        if interactive:
            print(f"\r\033[2KChecking {done}/{total} {name}", end="", file=sys.stderr, flush=True)

    try:
        report = analyse(deps, pypi, args.target, progress=progress)
    except (ValueError, RuntimeError, OSError) as exc:
        if interactive:
            print("\r\033[2K", end="", file=sys.stderr)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if interactive:
        print("\r\033[2K", end="", file=sys.stderr, flush=True)

    if args.format == "text":
        use_color = args.output is None and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        output = text.render(report, color=use_color, verbose=args.verbose) + "\n"
    else:
        output = {"markdown": markdown, "json": json, "html": html}[args.format].render(report)

    if args.output:
        args.output.write_text(output)
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(output)

    if args.fail_on and any(p.status in _FAIL_ON[args.fail_on] for p in report.packages):
        return 1
    return 0
