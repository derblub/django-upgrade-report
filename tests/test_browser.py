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
