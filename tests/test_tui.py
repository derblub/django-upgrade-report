"""``-i``: the report in the terminal, driven by Textual's Pilot.

    uv run --group dev --with textual pytest tests/test_tui.py

Skipped without Textual, which only the ``tui`` extra installs.
"""

from __future__ import annotations

import asyncio

import pytest

from django_upgrade_report import cli

textual = pytest.importorskip("textual")

from django_upgrade_report import tui  # noqa: E402  (needs Textual)

pytestmark = pytest.mark.tui


@pytest.fixture
def report(project, monkeypatch):
    """The report of the project fixture, as -i would get it."""
    got = []
    monkeypatch.setattr(cli, "_at_terminal", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(tui, "run", lambda r: got.append(r) or 0)
    assert cli.main([str(project), "-i", "--no-input"]) == 0
    return got[0]


def drive(app, steps):
    async def go():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            return await steps(app, pilot)

    return asyncio.run(go())


def details(app):
    return str(app.query_one("#details").render())


def test_sections_and_details(report):
    async def steps(app, pilot):
        options = app.query_one("#packages")
        titles = [
            str(options.get_option_at_index(i).prompt)
            for i in range(options.option_count)
            if options.get_option_at_index(i).disabled
        ]
        first = details(app)
        await pilot.press("down")
        return titles, first, details(app)

    titles, first, second = drive(tui.ReportApp(report), steps)
    assert titles == ["Blocked (1)", "Upgrade first (1)", "Ready (1)"]
    assert first.startswith("django-blocked  1.0")
    assert first.endswith("\n\nPyPI  https://pypi.org/project/django-blocked/")
    assert second.startswith("django-before  1.0 → 2.0")
    assert "Command  " not in second  # requirements: pip, no command to run


def test_search(report):
    async def steps(app, pilot):
        await pilot.press("slash")
        await pilot.press(*"ready")
        await pilot.pause()
        found = details(app)
        count = app.query_one("#packages").option_count
        await pilot.press("escape")
        await pilot.pause()
        return found, count, app.query_one("#packages").option_count

    found, filtered, everything = drive(tui.ReportApp(report), steps)
    assert found.startswith("django-ready")
    assert filtered == 2  # the title and the package
    assert everything > filtered


def test_nothing_matches(report):
    async def steps(app, pilot):
        await pilot.press("slash", *"nothing-like-this")
        await pilot.pause()
        return details(app)

    assert drive(tui.ReportApp(report), steps) == "No package matches."


def test_command_for_a_lockfile(report):
    report.source = "uv.lock"

    async def steps(app, pilot):
        await pilot.press("down")
        return details(app)

    shown = drive(tui.ReportApp(report), steps)
    assert "\nCommand  uv add 'django-before>=2.0'\nPyPI     https://pypi.org/" in shown
