"""Helpers for CI that talk to GitHub: one pull request comment that follows the report.

    python -m django_upgrade_report.ci comment --report report.json --markdown report.md

Reads ``GITHUB_TOKEN``, ``GITHUB_REPOSITORY``, ``GITHUB_EVENT_NAME`` and ``GITHUB_EVENT_PATH``,
as GitHub Actions sets them. A comment that cannot be written never fails the job: the
report is in the job summary anyway.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from django_upgrade_report.client import JsonClient
from django_upgrade_report.frameworks import DJANGO, FRAMEWORKS
from django_upgrade_report.render.markdown import escape

API = "https://api.github.com"
MAX_COMMENT = 65_000
"""GitHub refuses comments over 65,536 characters; leave room for the marker."""
PULL_REQUEST_EVENTS = ("pull_request", "pull_request_target")


class GitHub(JsonClient):
    """The few calls of the GitHub REST API the comment needs. No cache: comments change."""

    def __init__(self, token: str, api: str = API):
        super().__init__(
            api,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            connections=2,
        )
        self._secrets = (*self._secrets, token)

    def get(self, path: str) -> object | None:
        return self._fetch(f"{self.base_url}{path}")

    def send(self, method: str, path: str, body: dict) -> object:
        """POST or PATCH ``body``; a refusal raises :class:`FetchError` with the status."""
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={**self._headers, "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise self._error(url, f"HTTP {exc.code} {exc.reason}") from None
        except (OSError, ValueError) as exc:
            raise self._error(url, str(exc) or type(exc).__name__) from None


def fingerprint(report: dict) -> str:
    """What a reader of the comment would see change: every package's status and step."""
    if report.get("kind") == "path":  # --via: every step
        steps = "\n".join(fingerprint(step) for step in report.get("steps") or [])
        return hashlib.sha256(steps.encode()).hexdigest()[:16]
    if report.get("kind") == "multi":  # several projects: each, and which could not be checked
        parts = "\n".join(
            f"{path}:{fingerprint(part) if part else 'error'}" for path, part in _projects(report)
        )
        return hashlib.sha256(parts.encode()).hexdigest()[:16]
    python = report.get("python") or {"packages": []}
    rows = sorted(
        f"{p.get('name')}:{p.get('status')}:{p.get('phase')}:{p.get('upgrade_to')}"
        for p in [*report.get("packages", []), *python["packages"]]
    )
    rows.append(f"target:{report.get('target')}")
    rows += [f"python:{python.get(k)}" for k in ("target", "ready", "pure", "django_note")]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]


def marker(key: str) -> str:
    """The start of the hidden line that finds the comment again; ``key`` tells projects apart."""
    return f"<!-- django-upgrade-report:{key} "


def body(key: str, report: dict, markdown: str) -> str:
    text = markdown
    if len(text) > MAX_COMMENT:
        cut = text.rfind("\n", 0, MAX_COMMENT)
        text = text[: cut if cut > 0 else MAX_COMMENT] + (
            "\n\n_The report is longer than a comment can be: the whole of it is in the job "
            "summary._\n"
        )
    return f"{marker(key)}fp={fingerprint(report)} -->\n{text}"


def comment(
    github: GitHub,
    repository: str,
    number: int,
    key: str,
    report: dict,
    markdown: str,
    on_change: bool = False,
) -> str:
    """Create or update the pull request's comment for ``key``; returns what was done."""
    existing = None
    page = 1
    while existing is None:
        comments = github.get(
            f"/repos/{repository}/issues/{number}/comments?per_page=100&page={page}"
        )
        if not isinstance(comments, list) or not comments:
            break
        existing = next((c for c in comments if _ours(c, key)), None)
        if len(comments) < 100:
            break
        page += 1
    text = body(key, report, markdown)
    if existing is None:
        github.send("POST", f"/repos/{repository}/issues/{number}/comments", {"body": text})
        return "created"
    if on_change and str(existing.get("body", "")).split("\n", 1)[0] == text.split("\n", 1)[0]:
        return "unchanged"
    github.send("PATCH", f"/repos/{repository}/issues/comments/{existing['id']}", {"body": text})
    return "updated"


def _ours(comment: object, key: str) -> bool:
    """A comment this tool wrote for ``key``: a bot's, starting with the marker."""
    if not isinstance(comment, dict) or "id" not in comment:
        return False
    user = comment.get("user")
    if isinstance(user, dict) and user.get("type") not in (None, "Bot"):
        return False  # a person who copied the comment, marker and all
    return str(comment.get("body", "")).startswith(marker(key))


# --- the tracking issue -------------------------------------------------------------

LABEL = "django-upgrade-report"
_TASK = re.compile(r"^- \[(?P<tick>[ xX])\] (?P<text>.*?) <!-- (?P<id>[\w.:-]+) -->$")
_LABELS = {("upgrade", "before"): "upgrade first", ("upgrade", "with"): "upgrade with {name}"}


_ID = re.compile(r"[^\w.:-]")
"""What a task id cannot hold: ``services/api`` becomes ``services-api``."""


def _projects(report: dict) -> list[tuple[str, dict | None]]:
    """The projects of a ``"kind": "multi"`` report, with their reports (None: not checked)."""
    return [
        (str(p.get("path")), p.get("report") if isinstance(p.get("report"), dict) else None)
        for p in report.get("projects") or []
        if isinstance(p, dict)
    ]


def _target(report: dict) -> str:
    """The target, or the targets of several projects: ``5.2 and 6.0``."""
    if report.get("kind") != "multi":
        return str(report.get("target"))
    targets = sorted({str(part.get("target")) for _, part in _projects(report) if part})
    return " and ".join(targets) or "?"


def _name(report: dict) -> str:
    """``Django``, or ``Wagtail`` or ``django CMS`` for a report made with ``--framework``."""
    steps = report.get("steps") or [part for _, part in _projects(report) if part] or [{}]
    key = report.get("framework") or steps[0].get("framework") or "django"
    return FRAMEWORKS.get(key, DJANGO).display


def issue_title(report: dict, key: str) -> str:
    where = "" if key in ("", ".") else f" ({key})"
    return f"{_name(report)} {_target(report)} upgrade plan{where}"


def tasks(report: dict) -> list[tuple[str, str]]:
    """(id, text) for every row with something to do, in the report's order; for a path
    (``--via``), step by step; for several projects, project by project."""
    if report.get("kind") == "multi":
        return [
            (f"{_ID.sub('-', path)}:{task_id}", f"{escape(path)}: {text}")
            for path, part in _projects(report)
            if part
            for task_id, text in tasks(part)
        ]
    if report.get("kind") == "path":
        return [
            (f"step{n}:{task_id}", f"Step {n}: {text}")
            for n, step in enumerate(report.get("steps") or [], 1)
            for task_id, text in tasks(step)
        ]
    found = []
    python = report.get("python") or {}
    rows = [("python", p) for p in python.get("packages") or [] if p.get("status") != "ready"]
    rows += [("django", p) for p in report.get("packages") or [] if p.get("status") != "ready"]
    for section, p in rows:
        status = str(p.get("status"))
        what = _LABELS.get((status, p.get("phase")), status).format(name=_name(report))
        if section == "python":
            what = f"{what} on Python {python.get('target')}"
        step = f" {p.get('current')} → {p.get('upgrade_to')}" if p.get("upgrade_to") else ""
        # Metadata from the index: no markup of its own, nothing that ends the hidden id.
        reason = escape(str(p.get("reason"))).replace("\n", " ")
        text = f"**{escape(str(p.get('name')))}**{escape(step)}: {what}, {reason}"
        found.append((f"{section}:{p.get('name')}", text))
    return found


def issue_body(report: dict, key: str, old: str = "", today: str = "") -> str:
    """The task list, keeping the ticks of ``old`` and ticking off what needs nothing now."""
    before = {}
    for line in old.splitlines():
        if match := _TASK.match(line.strip()):
            before[match["id"]] = (match["tick"] != " ", match["text"])
    lines = [
        f"<!-- django-upgrade-report-issue:{key} -->",
        f"What your dependencies need for {_name(report)} {_target(report)}, from "
        f"`{_source(report)}`, kept up to date by django-upgrade-report. Tick what is "
        "done; the ticks stay when the list is updated.",
        "",
    ]
    current = tasks(report)
    for task_id, text in current:
        ticked, was = before.get(task_id, (False, ""))
        ticked = ticked and "(nothing to do since " not in was  # back after it was done
        lines.append(f"- [{'x' if ticked else ' '}] {text} <!-- {task_id} -->")
    ids = {task_id for task_id, _ in current}
    for task_id, (_, text) in before.items():
        if task_id in ids:
            continue
        if "(nothing to do since " not in text:
            text = f"~~{text}~~ (nothing to do since {today})"
        lines.append(f"- [x] {text} <!-- {task_id} -->")
    if not current:
        lines += ["", f"Everything is ready for {_name(report)} {_target(report)}."]
    return "\n".join(lines) + "\n"


def _source(report: dict) -> object:
    if report.get("kind") == "multi":
        return ", ".join(path for path, _ in _projects(report))
    steps = report.get("steps") or [{}]
    return report.get("source") or steps[0].get("source")


def track(github: GitHub, repository: str, key: str, report: dict, today: str) -> str:
    """Create or update the open issue for ``key`` and the report's target."""
    title = issue_title(report, key)
    existing = None
    page = 1
    while existing is None:
        issues = github.get(
            f"/repos/{repository}/issues?state=open&labels={LABEL}&per_page=100&page={page}"
        )
        if not isinstance(issues, list) or not issues:
            break
        existing = next(
            (
                i
                for i in issues
                if isinstance(i, dict) and i.get("title") == title and "pull_request" not in i
            ),
            None,
        )
        if len(issues) < 100:
            break
        page += 1
    if existing is None:
        text = issue_body(report, key, today=today)
        github.send(
            "POST", f"/repos/{repository}/issues", {"title": title, "body": text, "labels": [LABEL]}
        )
        return "created"
    old = str(existing.get("body") or "")
    text = issue_body(report, key, old, today)
    if text == old:
        return "unchanged"
    number = existing["number"]
    github.send("PATCH", f"/repos/{repository}/issues/{number}", {"body": text})
    message = f"Everything is ready for {_name(report)} {_target(report)}."
    if message in text and message not in old:  # said once, when the report gets there
        github.send("POST", f"/repos/{repository}/issues/{number}/comments", {"body": message})
    return "updated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m django_upgrade_report.ci")
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("comment", help="put the report on the pull request")
    sub.add_argument("--report", type=Path, required=True, help="the --format json report")
    sub.add_argument("--markdown", type=Path, required=True, help="the --format markdown report")
    sub.add_argument("--key", default=".", help="tells projects in one repository apart")
    sub.add_argument("--on-change", action="store_true", help="update only when it changed")
    sub = commands.add_parser("issue", help="keep an issue with the plan as a task list")
    sub.add_argument("--report", type=Path, required=True, help="the --format json report")
    sub.add_argument("--key", default=".", help="tells projects in one repository apart")
    args = parser.parse_args(argv)
    if args.command == "issue":
        return _issue(args)

    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event not in PULL_REQUEST_EVENTS:
        print(f"django-upgrade-report: no comment, {event or 'this'} is not a pull request event")
        return 0
    token, repository = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repository:
        print("django-upgrade-report: no comment, GITHUB_TOKEN or GITHUB_REPOSITORY is not set")
        return 0
    try:
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        number = int(payload["pull_request"]["number"])
        report = json.loads(args.report.read_text(encoding="utf-8"))
        markdown = args.markdown.read_text(encoding="utf-8")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(f"django-upgrade-report: no comment, cannot read its inputs: {exc}")
        return 0
    try:
        github = GitHub(token)
        done = comment(github, repository, number, args.key, report, markdown, args.on_change)
    except Exception as exc:  # the report is in the job summary: never fail the job here
        why = str(exc) or type(exc).__name__
        if "HTTP 403" in why or "HTTP 401" in why:
            why += (
                " (a pull request from a fork gets a read-only token, the job may lack "
                "pull-requests: write, or GitHub asks to slow down)"
            )
        print(f"django-upgrade-report: no comment: {why.replace(token, '***')}", file=sys.stderr)
        return 0
    print(f"django-upgrade-report: comment {done} on #{number}")
    return 0


def _issue(args: argparse.Namespace) -> int:
    token, repository = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repository:
        print("django-upgrade-report: no issue, GITHUB_TOKEN or GITHUB_REPOSITORY is not set")
        return 0
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        today = str(report.get("generated") or "")[:10]
        done = track(GitHub(token), repository, args.key, report, today)
    except Exception as exc:  # the report is in the job summary: never fail the job here
        why = str(exc) or type(exc).__name__
        if "HTTP 403" in why or "HTTP 401" in why:
            why += " (the job needs permissions: issues: write)"
        print(f"django-upgrade-report: no issue: {why.replace(token, '***')}", file=sys.stderr)
        return 0
    print(f"django-upgrade-report: issue {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
