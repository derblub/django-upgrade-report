"""The wiki in docs/wiki/: generated pages up to date, links that lead somewhere."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WIKI = ROOT / "docs" / "wiki"


def _generator():
    spec = importlib.util.spec_from_file_location("wiki", ROOT / "scripts" / "wiki.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", sorted(_generator().PAGES))
def test_generated_pages_are_up_to_date(name):
    page = (WIKI / name).read_text(encoding="utf-8")
    assert page == _generator().PAGES[name](), "run: PYTHONPATH=src python3 scripts/wiki.py"


def test_every_option_and_action_input_is_documented():
    from django_upgrade_report import cli

    reference = (WIKI / "Command-Line-Reference.md").read_text(encoding="utf-8")
    for action in cli.build_parser()._actions:
        for option in action.option_strings:
            if option in ("-h", "--help"):
                continue
            assert f"`{option}`" in reference
    action_page = (WIKI / "GitHub-Action-Reference.md").read_text(encoding="utf-8")
    action = (ROOT / "action.yml").read_text()
    section = action.split("\ninputs:\n", 1)[1].split("\noutputs:\n", 1)[0]
    inputs = re.findall(r"^  ([a-z-]+):$", section, re.MULTILINE)
    assert inputs and all(f"| `{name}` |" in action_page for name in inputs)


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def test_links_between_pages_lead_somewhere():
    pages = {path.stem: path.read_text(encoding="utf-8") for path in WIKI.glob("*.md")}
    anchors = {
        name: {_slug(h) for h in re.findall(r"^#+ (.+)$", text, re.MULTILINE)}
        for name, text in pages.items()
    }
    broken = []
    for name, text in pages.items():
        for target in re.findall(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            page, _, anchor = target.partition("#")
            if page not in pages or (anchor and anchor not in anchors[page]):
                broken.append(f"{name}: {target}")
    assert broken == []


def test_every_image_is_there_and_shown():
    """docs/wiki/images/ is published with the pages; scripts/screenshots.py makes it."""
    shown = set()
    for path in WIKI.glob("*.md"):
        for target in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", path.read_text(encoding="utf-8")):
            assert (WIKI / target).is_file(), f"{path.name}: {target}"
            shown.add(target)
    assert {f"images/{p.name}" for p in (WIKI / "images").iterdir()} == shown


def test_the_sidebar_lists_every_page():
    sidebar = (WIKI / "_Sidebar.md").read_text(encoding="utf-8")
    for path in WIKI.glob("*.md"):
        if not path.stem.startswith("_"):
            assert f"]({path.stem})" in sidebar, path.stem
