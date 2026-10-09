"""Repository and changelog links, from the project's own metadata."""

from __future__ import annotations

import pytest

from django_upgrade_report.projects import changelog_url, repository_url, safe_url
from django_upgrade_report.pypi import ReleaseInfo


def info(urls: dict[str, str] | None = None, home_page: str | None = None) -> ReleaseInfo:
    return ReleaseInfo(
        "pkg", "1.0", (), (), None, project_urls=tuple((urls or {}).items()), home_page=home_page
    )


@pytest.mark.parametrize(
    ("urls", "home_page", "expected"),
    [
        ({"Source": "https://github.com/org/pkg"}, None, "https://github.com/org/pkg"),
        ({"Source Code": "https://github.com/org/pkg.git"}, None, "https://github.com/org/pkg"),
        ({"repository": "https://www.github.com/org/pkg/"}, None, "https://github.com/org/pkg"),
        ({"Code": "https://github.com/org/pkg/tree/main"}, None, "https://github.com/org/pkg"),
        (
            {"GitHub": "http://github.com/org/django.pkg#readme"},
            None,
            "https://github.com/org/django.pkg",
        ),
        ({"Homepage": "https://github.com/org/pkg"}, None, "https://github.com/org/pkg"),
        ({}, "https://github.com/org/pkg", "https://github.com/org/pkg"),
        ({"Funding": "https://github.com/sponsors/someone"}, None, None),
        ({"Source": "https://GitHub.com/Org/Pkg"}, None, "https://github.com/Org/Pkg"),
        ({"Homepage": "https://github.com/marketplace/actions/foo"}, None, None),
        ({"Source": "https://github.com/sponsors/someone"}, None, None),
        ({"Source": "https://gitlab.com/org/pkg"}, "https://pkg.example.com", None),
        ({"Documentation": "https://github.com/org/pkg"}, None, None),
    ],
)
def test_repository_url(urls, home_page, expected):
    assert repository_url(info(urls, home_page)) == expected


@pytest.mark.parametrize(
    ("urls", "expected"),
    [
        ({"Changelog": "https://x.test/changes"}, "https://x.test/changes"),
        ({"Change Log": "https://x.test/a"}, "https://x.test/a"),
        ({"release-notes": "https://x.test/b"}, "https://x.test/b"),
        ({"What's New": "https://x.test/c"}, "https://x.test/c"),
        ({"History": "https://x.test/h", "Changes": "https://x.test/c"}, "https://x.test/c"),
        ({"Source": "https://github.com/org/pkg"}, "https://github.com/org/pkg/releases"),
        ({"Documentation": "https://pkg.readthedocs.io"}, None),
    ],
)
def test_changelog_url(urls, expected):
    assert changelog_url(info(urls)) == expected


def test_safe_url_keeps_links_whole():
    assert safe_url('https://x.test/a b)<c>"d') == "https://x.test/a%20b%29%3Cc%3E%22d"
    assert safe_url("https://x.test/p?q=1&r=%20#s") == "https://x.test/p?q=1&r=%20#s"
