"""``-i``: the report in the terminal, driven by Textual's Pilot.

    uv run --group dev --with textual pytest tests/test_tui.py

Skipped without Textual, which only the ``tui`` extra installs.
"""

from __future__ import annotations

import asyncio
import json

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
    monkeypatch.setattr(tui, "run", lambda *args: got.append(args) or 0)
    assert cli.main([str(project), "-i", "--no-input"]) == 0
    report, recompute, state = got[0]
    report.recompute, report.state = recompute, state  # for the tests below
    return report


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


def app_for(report):
    return tui.ReportApp(report, report.recompute, report.state)


def test_filter_by_status(report):
    async def steps(app, pilot):
        await pilot.press("f")
        await pilot.pause()
        first = (app.status, sorted(p.name for p in app.rows.values()))
        await pilot.press("f", "f", "f")
        await pilot.pause()
        return first, app.status

    first, back = drive(app_for(report), steps)
    assert first == ("blocked", ["django-blocked"])
    assert back is None  # blocked, upgrade, ready, then everything again


def test_ticks_are_kept_in_the_project(report):
    async def steps(app, pilot):
        await pilot.press("space")
        await pilot.pause()
        return details(app)

    shown = drive(app_for(report), steps)
    assert "\nDone  " in shown
    kept = json.loads(report.state.read_text())
    assert [(d["package"], d["target"]) for d in kept["done"]] == [("django-blocked", "5.2")]
    assert report.state.parent.name == ".django-upgrade-report"

    async def again(app, pilot):
        await pilot.press("space")  # untick
        await pilot.pause()
        return app.done

    assert drive(app_for(report), again) == {}
    assert json.loads(report.state.read_text()) == {"done": []}


def test_copy_and_open(report, monkeypatch):
    report.source = "uv.lock"
    opened = []
    monkeypatch.setattr(tui.webbrowser, "open", opened.append)

    async def steps(app, pilot):
        copied = []
        app.copy_to_clipboard = copied.append
        await pilot.press("e")  # blocked: no command
        await pilot.press("down", "e", "o")
        await pilot.pause()
        return copied

    assert drive(app_for(report), steps) == ["uv add 'django-before>=2.0'"]
    assert opened == ["https://pypi.org/project/django-before/"]


def test_another_target(report):
    async def steps(app, pilot):
        await pilot.press("t", *"6.0", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        return app.report.target, str(app.query_one("#head").render())

    target, head = drive(app_for(report), steps)
    assert target == "6.0"
    assert head.startswith("Django 4.2.7 → 6.0")


def test_a_target_that_cannot_be_checked(report):
    async def steps(app, pilot):
        await pilot.press("t", *"9.9", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        return app.report.target, app.busy

    assert drive(app_for(report), steps) == ("5.2", "")  # the report stays, a note says why


@pytest.mark.parametrize("name", ["report.html", "report.md", "report.json", "report.txt"])
def test_write_the_report(report, tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)

    async def steps(app, pilot):
        await pilot.press("w", *name, "enter")
        await pilot.pause()

    drive(app_for(report), steps)
    written = (tmp_path / name).read_text()
    assert "django-blocked" in written
