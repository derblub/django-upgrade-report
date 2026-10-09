"""The README's table of contents and its links within the page."""

from __future__ import annotations

import re
from pathlib import Path

README = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")


def slug(title: str) -> str:
    """The anchor GitHub gives a heading."""
    return re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")


def headings() -> list[tuple[int, str]]:
    return [
        (len(hashes), title)
        for hashes, title in re.findall(r"^(##|###) (.+)$", README, re.M)
        if title != "Contents"
    ]


def test_contents_list_every_heading_in_order():
    contents = README.split("## Contents\n", 1)[1].split("\n## ", 1)[0]
    listed = re.findall(r"^( *)- \[(.+?)\]\(#(.+?)\)$", contents, re.M)
    assert [(2 if not indent else 3, title, anchor) for indent, title, anchor in listed] == [
        (level, title, slug(title)) for level, title in headings()
    ]


def test_links_within_the_page_find_their_heading():
    anchors = {slug(title) for _, title in headings()} | {"contents"}
    links = re.findall(r"\]\(#([^)]+)\)", README) + re.findall(r'href="#([^"]+)"', README)
    assert links
    assert sorted(set(links) - anchors) == []
