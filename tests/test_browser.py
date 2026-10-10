"""The script of the HTML report, in a real browser.

    uv run --group dev --with playwright pytest -m browser

Skipped without Playwright. ``CHROMIUM`` names a Chromium to use instead of Playwright's own.
"""

from __future__ import annotations

import os

import pytest

from django_upgrade_report import cli

sync_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        yield browser
        browser.close()


@pytest.fixture
def page(project, browser):
    requirements = project / "requirements.txt"
    requirements.write_text(requirements.read_text() + "django-lagging==1.0\ndjango-silent==0.2\n")
    target = project / "report.html"
    assert cli.main([str(project), "-f", "html", "-o", str(target)]) == 0
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(error))
    page.goto(target.as_uri())
    page.url_of = target.as_uri()
    yield page
    context.close()
    assert not errors


def shown(page) -> list[str]:
    return page.eval_on_selector_all(
        "[data-filter]", "rows => rows.filter(r => r.offsetParent).map(r => r.dataset.name)"
    )


def test_search_from_the_keyboard(page):
    assert page.is_visible("#toolbar")
    page.keyboard.press("/")
    page.keyboard.type("blocked")
    assert shown(page) == ["django-blocked"]
    assert page.url.endswith("#q=blocked")
    assert page.text_content("#shown") == "1 of 5 shown"
    page.keyboard.press("Escape")
    assert len(shown(page)) == 5
    assert "#" not in page.url


def test_chip_and_tile_filter_by_status(page):
    page.click('button[data-chip="before"]')
    assert shown(page) == ["django-before"]
    assert page.get_attribute('button[data-chip="before"]', "aria-pressed") == "true"
    assert page.is_hidden("section#blocked")
    page.click(".tile.blocked")
    assert shown(page) == ["django-blocked"]
    assert page.url.endswith("#status=blocked")
    page.click(".tile.blocked")
    assert len(shown(page)) == 5


def test_a_filtered_view_can_be_passed_on(page):
    page.goto(page.url_of + "#status=before,ready&q=django")
    assert shown(page) == ["django-before", "django-ready"]
    assert page.input_value("#q") == "django"
    page.emulate_media(media="print")
    assert len(shown(page)) == 5  # printing shows every row


def test_rows_open_from_the_keyboard(page):
    page.keyboard.press("j")
    summary = page.locator("tr.current summary")
    assert summary.get_attribute("aria-expanded") == "false"
    page.keyboard.press("Enter")
    sync_api.expect(summary).to_have_attribute("aria-expanded", "true")  # set on "toggle"
    assert page.locator("tr.current details.more").get_attribute("open") == ""
    assert "https://pypi.org/project/" in page.inner_html("tr.current details.more")


def test_copy_the_line_to_pin(page):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    row = page.locator('tr[data-name="django-before"]')
    row.locator("summary").click()
    button = row.locator("button.copy")
    button.click()
    sync_api.expect(button).not_to_have_text("copy")
    said = button.text_content()
    if said == "copied":
        assert page.evaluate("navigator.clipboard.readText()") == "django-before==2.0"
    else:  # no clipboard for this page: the line is selected to copy by hand
        assert said == "press Ctrl+C"
        assert page.evaluate("String(window.getSelection())") == "django-before==2.0"


def test_columns_sort(page):
    rows = page.locator("section#check tbody tr")
    heading = "section#check th[data-sort=name]"
    names = "rows => rows.map(r => r.dataset.name)"
    page.click(f"{heading} button")
    assert page.get_attribute(heading, "aria-sort") == "ascending"
    assert rows.evaluate_all(names) == ["django-lagging", "django-silent"]
    page.click(f"{heading} button")
    assert page.get_attribute(heading, "aria-sort") == "descending"
    assert rows.evaluate_all(names) == ["django-silent", "django-lagging"]
    page.click("section#check th[data-sort=released] button")
    assert page.get_attribute(heading, "aria-sort") == "none"


def test_only_direct_dependencies(project, browser):
    from test_cli import lock_with_a_transitive_package

    lock_with_a_transitive_package(project)
    target = project / "report.html"
    assert cli.main([str(project), "-f", "html", "-o", str(target)]) == 0
    page = browser.new_page()
    page.goto(target.as_uri() + "#direct=1")
    assert page.is_checked("#direct")
    assert sorted(shown(page)) == ["django-blocked", "django-ready"]
    page.uncheck("#direct")
    assert len(shown(page)) == 3
    page.close()


def test_ecosystem_page_filters_and_keeps_them_in_the_address(browser, index, tmp_path):
    import importlib.util
    from datetime import date
    from pathlib import Path

    path = Path(__file__).parent.parent / "ecosystem" / "build.py"
    spec = importlib.util.spec_from_file_location("ecosystem_build", path)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    names = ["django-ready", "django-before", "django-with", "django-blocked", "django-lagging"]
    data = build.build(index, names, today=date(2026, 1, 15))
    target = tmp_path / "index.html"
    target.write_text(build.page(data))
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(error))
    page.goto(target.as_uri())
    assert page.is_visible("#toolbar")
    page.fill("#q", "ready")
    sync_api.expect(page.locator("#shown")).to_have_text("1 of 5")
    assert page.evaluate("location.hash") == "#q=ready&v=6.0"
    page.fill("#q", "")
    page.select_option("#v", "5.0")
    page.select_option("#status", "blocked")
    sync_api.expect(page.locator("#shown")).to_have_text("2 of 5")
    page.goto(target.as_uri() + "#status=ready&v=5.2")
    page.reload()
    sync_api.expect(page.locator("#shown")).to_have_text("3 of 5")
    page.goto(target.as_uri() + "#5.0")
    page.reload()
    assert page.evaluate("document.getElementById('5.0').open")
    assert not errors


def test_ecosystem_charts_answer_pointer_and_keys(browser, index, tmp_path):
    import importlib.util
    from datetime import date
    from pathlib import Path

    path = Path(__file__).parent.parent / "ecosystem" / "build.py"
    spec = importlib.util.spec_from_file_location("ecosystem_build", path)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    names = ["django-ready", "django-before", "django-with", "django-blocked", "django-lagging"]
    downloads = {"django-ready": 900, "django-before": 5_000_000, "django-blocked": 1_200}
    data = build.build(index, names, today=date(2026, 1, 15), downloads=downloads)
    target = tmp_path / "index.html"
    target.write_text(build.page(data))
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(error))
    page.goto(target.as_uri())
    charts = page.locator(".charts")
    assert page.is_visible(".toggle")

    figure = page.locator('figure[data-chart="release"]')
    figure.focus()  # the keyboard reaches the newest point first
    tip = figure.locator(".tooltip")
    sync_api.expect(tip).to_be_visible()
    first = tip.inner_text()
    page.keyboard.press("ArrowLeft")
    assert tip.inner_text() != first and "days after release" in tip.inner_text()
    page.keyboard.press("Escape")
    sync_api.expect(tip).to_be_hidden()

    box = figure.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.1, box["y"] + box["height"] * 0.5)
    sync_api.expect(tip).to_be_visible()
    assert "Django 5.2 LTS" in tip.inner_text()

    page.click('.legend button[data-v="5.2"]')
    sync_api.expect(charts).to_have_attribute("data-focus", "5.2")
    assert figure.locator('.series.focus[data-v="5.2"]').count() >= 1
    page.click('.legend button[data-v="5.2"]')
    assert charts.get_attribute("data-focus") is None

    page.click('.toggle button[data-mode="downloads"]')
    sync_api.expect(charts).to_have_attribute("data-mode", "downloads")
    assert figure.locator(".layer.downloads").is_visible()
    assert not figure.locator(".layer.packages").is_visible()
    assert not errors


def test_ecosystem_command_field_copies_like_its_button(browser, index, tmp_path):
    import importlib.util
    from datetime import date
    from pathlib import Path

    path = Path(__file__).parent.parent / "ecosystem" / "build.py"
    spec = importlib.util.spec_from_file_location("ecosystem_build", path)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    names = ["django-ready", "django-before", "django-with", "django-blocked", "django-lagging"]
    target = tmp_path / "index.html"
    target.write_text(build.page(build.build(index, names, today=date(2026, 1, 15))))
    context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    page = context.new_page()
    page.goto(target.as_uri())
    field = page.locator(".try .command")
    field.locator("code").click()
    sync_api.expect(field.locator("button.copy")).to_have_text("Copied")
    assert page.evaluate("navigator.clipboard.readText()") == "uvx django-upgrade-report"
    badge = page.locator("#badges ~ .command")
    badge.locator("code").click()
    assert page.evaluate("navigator.clipboard.readText()").startswith("[![Django support]")
    assert page.locator(".badge-preview img").count() == 4  # the badge and its three messages
    context.close()


def test_ecosystem_package_list_fits_a_desktop(browser, index, tmp_path):
    import importlib.util
    from datetime import date
    from pathlib import Path

    path = Path(__file__).parent.parent / "ecosystem" / "build.py"
    spec = importlib.util.spec_from_file_location("ecosystem_build", path)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    names = ["django-ready", "django-before", "django-with", "django-blocked", "django-lagging"]
    target = tmp_path / "index.html"
    target.write_text(build.page(build.build(index, names, today=date(2026, 1, 15))))
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    page.goto(target.as_uri())
    width, room = page.evaluate(
        "(() => { const m = document.querySelector('.matrix'); "
        "return [m.scrollWidth, m.clientWidth]; })()"
    )
    assert width <= room  # down, never sideways


def test_ecosystem_package_row_marks_on_click(browser, index, tmp_path):
    import importlib.util
    from datetime import date
    from pathlib import Path

    path = Path(__file__).parent.parent / "ecosystem" / "build.py"
    spec = importlib.util.spec_from_file_location("ecosystem_build", path)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    names = ["django-ready", "django-before", "django-blocked"]
    target = tmp_path / "index.html"
    target.write_text(build.page(build.build(index, names, today=date(2026, 1, 15))))
    page = browser.new_page()
    page.goto(target.as_uri())
    ready, blocked = (
        page.locator("#django-ready td").first,
        page.locator("#django-blocked td").first,
    )
    ready.click()
    assert page.locator("#rows tr.active").count() == 1
    assert "active" in page.locator("#django-ready").get_attribute("class")
    blocked.click()  # one row at a time
    assert page.locator("#rows tr.active").evaluate_all("rs => rs.map(r => r.id)") == [
        "django-blocked"
    ]
    blocked.click()  # a second click clears it
    assert page.locator("#rows tr.active").count() == 0
