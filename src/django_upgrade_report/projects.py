"""Where a project lives and where its changes are written down, from its own links."""

from __future__ import annotations

import re
import urllib.parse

from django_upgrade_report.pypi import ReleaseInfo

_REPOSITORY_LABELS = ("source", "sourcecode", "repository", "code", "github", "homepage")
_CHANGELOG_LABELS = ("changelog", "changes", "releasenotes", "history", "whatsnew", "news")
_GITHUB = re.compile(
    r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#?].*)?$", re.IGNORECASE
)
# github.com/<these>/... are pages of GitHub itself, not repositories.
_NOT_OWNERS = {
    "apps",
    "collections",
    "enterprise",
    "features",
    "marketplace",
    "orgs",
    "settings",
    "sponsors",
    "topics",
    "users",
}


def _label(text: str) -> str:
    """``"Release Notes"``, ``"release-notes"`` and ``"ReleaseNotes"`` all as ``releasenotes``."""
    return re.sub(r"[^a-z]", "", text.lower())


def repository_url(info: ReleaseInfo) -> str | None:
    """The project's GitHub repository, as ``https://github.com/org/repo``."""
    links = {_label(label): url for label, url in info.project_urls}
    candidates = [links[label] for label in _REPOSITORY_LABELS if label in links]
    if info.home_page:
        candidates.append(info.home_page)
    for url in candidates:
        match = _GITHUB.match(url)
        if match and match.group(1).lower() not in _NOT_OWNERS:
            return f"https://github.com/{match.group(1)}/{match.group(2)}"
    return None


def changelog_url(info: ReleaseInfo) -> str | None:
    """The link the project labels as its changelog, else its GitHub releases page."""
    links = {_label(label): url for label, url in info.project_urls}
    for label in _CHANGELOG_LABELS:
        if label in links:
            return links[label]
    repository = repository_url(info)
    return f"{repository}/releases" if repository else None


def safe_url(url: str) -> str:
    """``url`` with the characters that end a Markdown link or an HTML attribute escaped."""
    return urllib.parse.quote(url, safe=":/?#[]@!$&'*+,;=%~-._")
