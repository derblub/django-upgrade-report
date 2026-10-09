"""``--evidence``: signs of support in a package's repository, for packages to check.

Each parser is careful: it would rather find nothing than a version that is not tested. The
files are read from the default branch, so a sign says what the code in development does,
never what a release declares.
"""

from __future__ import annotations

import ast
import configparser
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from packaging.version import InvalidVersion, Version

from django_upgrade_report.analysis import (
    SEVERITY,
    Evidence,
    PackageReport,
    Phase,
    Report,
    Status,
    UpstreamItem,
)
from django_upgrade_report.client import ONLINE, FetchError, JsonClient, UnexpectedAnswer

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

RAW = "https://raw.githubusercontent.com"
API = "https://api.github.com"
MAX_SIZE = 512 * 1024
"""Bytes read of one file: a test matrix is near the top of a small file."""
TOX = ("tox.ini", "pyproject.toml")
NOX = ("noxfile.py",)
WORKFLOWS = ("test.yml", "tests.yml", "ci.yml", "main.yml", "python-package.yml")
"""Workflow files to try when the list of them cannot be read (no ``GITHUB_TOKEN``)."""
_FACTOR = re.compile(r"^dj(?:ango)?[-_]?(\d)(\d+)$", re.IGNORECASE)
_VERSION = re.compile(r"^(?:django\s*(?:[~=><!]=?|==)\s*)?(\d+)\.(\d+)(?:\.[\d*]+)?$", re.I)


# --- tox --------------------------------------------------------------------------


def tox_versions(text: str) -> set[str]:
    """Django versions in a ``tox.ini`` (or the ``legacy_tox_ini`` of ``pyproject.toml``)."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return set()
    found = set()
    for section in ("tox", "testenv"):
        for key in ("envlist", "env_list"):
            if parser.has_option(section, key):
                for env in expand_envlist(parser.get(section, key)):
                    found |= _factors(env)
    if parser.has_section("gh-actions:env"):
        for value in parser["gh-actions:env"].values():
            for line in value.splitlines():
                version, _, factors = line.partition(":")
                if factors and _VERSION.match(version.strip()):
                    found |= _factors(factors.strip())
    return found


def tox_toml_versions(text: str) -> set[str]:
    """The tox configuration in a ``pyproject.toml``."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set()
    tox = data.get("tool", {}).get("tox", {})
    if not isinstance(tox, dict):
        return set()
    found = set()
    if isinstance(tox.get("legacy_tox_ini"), str):
        found |= tox_versions(tox["legacy_tox_ini"])
    env_list = tox.get("env_list")
    if isinstance(env_list, list):
        for env in env_list:
            if isinstance(env, str):
                for name in expand_envlist(env):
                    found |= _factors(name)
    return found


def expand_envlist(value: str) -> list[str]:
    """``py{310,312}-django{42,52}`` as the four environments it names."""
    names = []
    for part in _split_top(value.replace("\n", ",")):
        names += _expand(part.strip())
    return [n for n in names if n]


def _split_top(value: str) -> list[str]:
    """Split on commas outside braces."""
    parts, depth, current = [], 0, ""
    for char in value:
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    return [*parts, current]


def _expand(name: str) -> list[str]:
    match = re.search(r"\{([^{}]*)\}", name)
    if not match:
        return [name]
    head, tail = name[: match.start()], name[match.end() :]
    return [
        expanded
        for choice in match.group(1).split(",")
        for expanded in _expand(f"{head}{choice.strip()}{tail}")
    ]


def _factors(env: str) -> set[str]:
    found = set()
    for factor in re.split(r"[-\s]+", env):
        match = _FACTOR.match(factor)
        if match:
            found.add(f"{match.group(1)}.{int(match.group(2))}")
    return found


# --- GitHub workflows -------------------------------------------------------------


def workflow_versions(text: str) -> set[str]:
    """Django versions in the ``matrix:`` of a GitHub workflow, for keys that name Django.

    Two shapes only, without a YAML parser: ``django: ["4.2", "5.2"]`` and a block list.
    """
    found: set[str] = set()
    matrix_indent = None
    block_indent = None
    exclude_indent = None  # combinations left out are not tested
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if block_indent is not None:
            item = re.match(r"^\s*-\s*(.+)$", line)
            if item and indent >= block_indent:
                found |= _items([item.group(1)])
                continue
            block_indent = None
        if matrix_indent is not None and indent <= matrix_indent:
            matrix_indent = None
        if exclude_indent is not None and indent <= exclude_indent:
            exclude_indent = None
        if re.match(r"^\s*matrix:\s*$", line):
            matrix_indent = indent
            continue
        if matrix_indent is None or exclude_indent is not None:
            continue
        if re.match(r"^\s*exclude:\s*$", line):
            exclude_indent = indent
            continue
        key = re.match(r"^\s*(?:-\s+)?([\w.-]*django[\w.-]*)\s*:\s*(.*)$", line, re.IGNORECASE)
        if not key:
            continue
        value = key.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            found |= _items(value[1:-1].split(","))
        elif value:
            found |= _items([value])
        else:
            block_indent = indent
    return found


def _items(values: Iterable[str]) -> set[str]:
    found = set()
    for value in values:
        match = _VERSION.match(value.strip().strip("'\""))
        if match:
            found.add(f"{match.group(1)}.{match.group(2)}")
    return found


# --- nox --------------------------------------------------------------------------


def nox_versions(text: str) -> set[str]:
    """``@nox.parametrize("django", [...])``, the list given inline or as a module name."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    names = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                names[target.id] = node.value
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        first = node.args[0]
        if name != "parametrize" or not isinstance(first, ast.Constant):
            continue
        if "django" not in str(first.value).lower():
            continue
        found |= _items(_strings(node.args[1], names))
    return found


def _strings(node: ast.AST, names: dict[str, ast.AST], depth: int = 0) -> list[str]:
    """The strings a list, tuple or dict (its keys) holds, also through a module-level name,
    ``list(...)``, ``sorted(...)`` or ``.keys()``. Anything else holds none."""
    if depth > 4:
        return []
    if isinstance(node, ast.Name) and node.id in names:
        return _strings(names[node.id], names, depth + 1)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        elements = node.elts
    elif isinstance(node, ast.Dict):
        elements = [key for key in node.keys if key is not None]
    elif isinstance(node, ast.Call) and len(node.args) <= 1:
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("list", "tuple", "sorted") and node.args:
            return _strings(node.args[0], names, depth + 1)
        if isinstance(func, ast.Attribute) and func.attr == "keys" and not node.args:
            return _strings(func.value, names, depth + 1)
        return []
    else:
        return []
    return [e.value for e in elements if isinstance(e, ast.Constant) and isinstance(e.value, str)]


# --- reading the repository -------------------------------------------------------


class Files(Protocol):
    def text(self, owner: str, repo: str, path: str) -> str | None: ...

    def workflows(self, owner: str, repo: str) -> list[str] | None:
        """The workflow file names, ``None`` when they cannot be listed."""

    def search(self, owner: str, repo: str, words: str) -> list[dict]:
        """Issues and pull requests whose title has ``words``, as the search API lists them."""

    @property
    def searches(self) -> int:
        """How many searches a run may make: GitHub allows 10 a minute, 30 with a token."""


class _Raw(JsonClient):
    """Files from raw.githubusercontent.com, kept as ``{"text": ...}``: no API rate limit."""

    def __init__(self, cache_dir: Path | None, mode: str):
        super().__init__(RAW, cache_dir=cache_dir, connections=4, mode=mode)

    def _fetch_once(self, request: urllib.request.Request) -> object:
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read(MAX_SIZE)
        return {"text": body.decode("utf-8", errors="replace")}

    def _validate(self, data: object) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise UnexpectedAnswer("unexpected answer")


class _Api(JsonClient):
    def _validate(self, data: object) -> None:
        if not isinstance(data, (list, dict)):
            raise UnexpectedAnswer("unexpected answer")

    def _ttl(self, url: str) -> float | None:
        return 6 * 3600 if "/search/" in url else 24 * 3600


class GitHubFiles:
    """The default branch of public GitHub repositories. With a token, the workflow files are
    listed through the API instead of guessed."""

    def __init__(self, cache_dir: Path | None = None, mode: str = ONLINE, token: str | None = None):
        cache = cache_dir / "github" if cache_dir else None
        self.raw = _Raw(cache, mode)
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.search_api = _Api(API, cache_dir=cache, headers=headers, connections=2, mode=mode)
        self.api = None
        if token:
            self.api = self.search_api
            self.api._secrets = (*self.api._secrets, token)
        self.searches = 30 if token else 10

    def text(self, owner: str, repo: str, path: str) -> str | None:
        data = self.raw._get(f"{RAW}/{owner}/{repo}/HEAD/{path}")
        return data["text"] if isinstance(data, dict) else None

    def search(self, owner: str, repo: str, words: str) -> list[dict]:
        query = urllib.parse.quote(f'repo:{owner}/{repo} "{words}" in:title')
        found = self.search_api._get(
            f"{API}/search/issues?q={query}&sort=updated&order=desc&per_page=5"
        )
        items = found.get("items") if isinstance(found, dict) else None
        return [item for item in items or [] if isinstance(item, dict)]

    def workflows(self, owner: str, repo: str) -> list[str] | None:
        if self.api is None:
            return None
        try:
            listing = self.api._get(f"{API}/repos/{owner}/{repo}/contents/.github/workflows")
        except FetchError:  # a token without access, or the API's rate limit: guess instead
            return None
        if not isinstance(listing, list):
            return []
        return [
            str(entry["name"])
            for entry in listing
            if isinstance(entry, dict) and str(entry.get("name", "")).endswith((".yml", ".yaml"))
        ]


_PARSERS: dict[str, Callable[[str], set[str]]] = {
    "tox.ini": tox_versions,
    "pyproject.toml": tox_toml_versions,
    "noxfile.py": nox_versions,
}


def test_matrix(files: Files, repository: str, target: str) -> Evidence | None:
    """A sign from the first file whose test matrix names ``target``, or ``None``."""
    owner, repo = repository.removeprefix("https://github.com/").split("/", 1)
    workflows = files.workflows(owner, repo)
    names = [f".github/workflows/{name}" for name in (workflows or WORKFLOWS)]
    for path in [*TOX, *NOX, *names]:
        text = files.text(owner, repo, path)
        if text is None:
            continue
        versions = _PARSERS.get(path, workflow_versions)(text)
        if target in versions:
            return Evidence(
                "test-matrix",
                f"main branch tests Django {target} ({path.rsplit('/', 1)[-1]})",
                f"{repository}/blob/HEAD/{path}",
            )
    return None


# --- changelogs --------------------------------------------------------------------

CHANGELOGS = (
    "CHANGELOG.md",
    "CHANGELOG.rst",
    "CHANGES.rst",
    "CHANGES.md",
    "HISTORY.rst",
    "HISTORY.md",
    "docs/changelog.rst",
    "docs/changes.rst",
    "NEWS.rst",
)
_HEADING_VERSION = re.compile(
    r"^\[?(?:version\s+|release\s+|v)?(\d+(?:\.\d+)+(?:(?:a|b|rc)\d+)?)\]?(?=$|[\s(:,-])", re.I
)
_UNRELEASED = re.compile(r"\b(unreleased|upcoming|next release|in development)\b", re.I)
_SUPPORT = re.compile(r"support|compatib|\badd|\btest", re.I)
_DROPPED = re.compile(r"\b(drop|remov|deprecat|no longer|end of|unsupported|not support)", re.I)
_UNDERLINE = re.compile(r"^([=\-~^*+#`'\"])\1{2,}\s*$")


def changelog_sections(text: str) -> list[tuple[str, list[str]]]:
    """(version or ``"unreleased"``, lines) per section, newest first as written.

    A section starts at a Markdown heading or an underlined reStructuredText title that is a
    version number. Lines before the first such heading belong to no section.
    """
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    skip = False
    for i, line in enumerate(lines):
        if skip:
            skip = False
            continue
        title = None
        markdown = re.match(r"^#{1,6}\s+(.*)$", line)
        if markdown:
            title = markdown.group(1).strip()
        elif line.strip() and i + 1 < len(lines) and _UNDERLINE.match(lines[i + 1]):
            title, skip = line.strip(), True
        if title is not None:
            version = _HEADING_VERSION.match(title)
            if version:
                sections.append((version.group(1), []))
                continue
            if _UNRELEASED.search(title):  # "(Unreleased)", "X.YY.Z (UNRELEASED)"
                sections.append(("unreleased", []))
                continue
        if sections:
            sections[-1][1].append(line)
    return sections


def changelog_mention(text: str, installed: str, target: str) -> str | None:
    """The newest section after ``installed`` with a line that names Django ``target`` and
    support, compatibility, adding or testing it; ``None`` without one."""
    try:
        have = Version(installed)
    except InvalidVersion:
        return None
    major, minor = target.split(".")
    # "Django 5.2", "Django>=5.2", also in a list: "Django 5.2 and 6.0", "Django 5.1, 5.2"
    mention = re.compile(
        rf"django\s*(?:[~=<>]=?\s*)?(?:\d+\.\d+\s*(?:,|and|&|/)\s*)*{major}\.{minor}(?![\d.])",
        re.I,
    )
    for version, body in changelog_sections(text):
        if version != "unreleased":
            try:
                if Version(version) <= have:
                    continue
            except InvalidVersion:
                continue
        if any(
            mention.search(line) and _SUPPORT.search(line) and not _DROPPED.search(line)
            for line in body
        ):
            return version
    return None


def changelog(
    files: Files, repository: str, installed: str, target: str, link: str | None = None
) -> Evidence | None:
    """A sign from the repository's changelog: the first file found is read, no other."""
    owner, repo = repository.removeprefix("https://github.com/").split("/", 1)
    paths = list(CHANGELOGS)
    own = re.match(rf"^{re.escape(repository)}/blob/[^/]+/(.+)$", link or "", re.I)
    if own:  # the file the project links as its changelog, on the default branch
        paths.insert(0, own.group(1))
    for path in paths:
        text = files.text(owner, repo, path)
        if text is None:
            continue
        version = changelog_mention(text, installed, target)
        if version is None:
            return None
        where = "the unreleased changes" if version == "unreleased" else version
        return Evidence(
            "changelog",
            f"changelog of {where} mentions Django {target} support",
            f"{repository}/blob/HEAD/{path}",
        )
    return None


def gather(report: Report, files: Files, workers: int = 4, skip: Iterable[str] = ()) -> list[str]:
    """Add signs from the repositories of the packages to check; returns what failed.

    Only packages from PyPI with a GitHub repository are looked at: never one from git or a
    path (they have ``source`` set), nor the ones in ``skip`` (from a private index).
    """
    skip = set(skip)
    wanted = [
        p
        for p in report.packages
        if p.status is Status.CHECK and not p.source and p.repository_url and p.name not in skip
    ]

    def look(p: PackageReport) -> str | None:
        try:
            found = [test_matrix(files, p.repository_url, report.target)]
            if p.current:
                found.append(
                    changelog(files, p.repository_url, p.current, report.target, p.changelog_url)
                )
        except FetchError as exc:
            return f"Could not read the repository of {p.display_name}: {exc}"
        p.evidence += [sign for sign in found if sign is not None]
        return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        problems = [problem for problem in pool.map(look, wanted) if problem]
    problems += _upstream(report, files, skip)
    # Stable: the order of the upgrades stays, packages to check with a sign move down.
    report.packages.sort(
        key=lambda p: (-SEVERITY[p.status], p.phase is Phase.WITH, bool(p.evidence))
    )
    return problems


# --- issues and pull requests upstream ---------------------------------------------------


def upstream_items(found: list[dict], target: str) -> list[UpstreamItem]:
    """At most two of the search results: open before closed, pull requests before issues,
    the newest first. A title has to name the target, the search matches words loosely."""
    items = []
    mention = re.compile(rf"django\D{{0,3}}{re.escape(target)}(?![\d])", re.I)
    for entry in found:
        title, url, number = entry.get("title"), entry.get("html_url"), entry.get("number")
        if not isinstance(title, str) or not isinstance(url, str) or not isinstance(number, int):
            continue
        if not mention.search(title) or not url.startswith("https://github.com/"):
            continue
        pull = entry.get("pull_request")
        state = "open" if entry.get("state") == "open" else "closed"
        if isinstance(pull, dict) and pull.get("merged_at"):
            state = "merged"
        short = " ".join(title.split())
        short = short if len(short) <= 80 else short[:79].rstrip() + "…"
        items.append(
            UpstreamItem(
                "pr" if isinstance(pull, dict) else "issue",
                state,
                short,
                url,
                number,
                str(entry.get("updated_at") or ""),
            )
        )
    newest = sorted(items, key=lambda i: i.updated, reverse=True)
    newest.sort(key=lambda i: (i.state != "open", i.kind != "pr"))  # stable: newest stays first
    return newest[:2]


def _upstream(report: Report, files: Files, skip: set[str]) -> list[str]:
    """Search for blocked packages first, then for packages to check without a sign, as many
    as the search API allows; say so when some were left out."""
    wanted = [
        p
        for p in report.packages
        if p.status in (Status.BLOCKED, Status.CHECK)
        and not p.evidence
        and not p.source
        and p.repository_url
        and p.name not in skip
    ]
    wanted.sort(key=lambda p: p.status is not Status.BLOCKED)
    allowed = files.searches
    problems = []
    for p in wanted[:allowed]:
        owner, repo = p.repository_url.removeprefix("https://github.com/").split("/", 1)
        try:
            p.upstream = upstream_items(
                files.search(owner, repo, f"Django {report.target}"), report.target
            )
        except FetchError as exc:
            why = str(exc)
            if "HTTP 403" in why or "HTTP 429" in why:
                why += " (GitHub allows 10 searches a minute, 30 with GITHUB_TOKEN)"
            problems.append(f"Could not search the issues of {p.display_name}: {why}")
            break  # most likely the rate limit: the next search would fail too
    if len(wanted) > allowed:
        problems.append(
            f"Searched upstream issues for {allowed} of {len(wanted)} packages: "
            "set GITHUB_TOKEN for more"
        )
    return problems
