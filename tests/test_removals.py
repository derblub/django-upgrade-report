"""What Django removed on the way, and which of it the project's code still uses."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from django_upgrade_report import cli, removals, usage

ROOT = Path(__file__).parent.parent
SAMPLE = Path(__file__).parent / "data" / "usage"


def script():
    spec = importlib.util.spec_from_file_location(
        "django_removals", ROOT / "scripts" / "django_removals.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NOTES = """
Features deprecated in 9.1
==========================

* The ``OLD_THING`` setting is deprecated.

Features removed in 9.1
=======================

These features have reached the end of their deprecation cycle.

* The ``FANCY_SETTING`` setting is removed.

* ``django.utils.text.old_helper()`` and ``other_helper()`` are removed.

* The ``is_dst`` argument is removed from:

  * ``QuerySet.datetimes()``
  * ``django.utils.timezone.make_aware()``

See :ref:`deprecated-features-9.0` for details.

* The :meth:`.Model.old_method` method is removed.
"""


def test_the_generator_reads_the_removed_section():
    found = script().removals(NOTES, "9.1")
    assert [e["text"] for e in found] == [
        "The FANCY_SETTING setting is removed",
        "django.utils.text.old_helper() and other_helper() are removed",
        "The is_dst argument is removed from:",
        "The Model.old_method method is removed",
    ]
    assert found[1]["symbols"] == [
        "django.utils.text.old_helper",
        "django.utils.text.other_helper",  # a sibling, in the same module
    ]
    assert found[0]["url"] == (
        "https://docs.djangoproject.com/en/9.1/releases/9.1/#features-removed-in-9-1"
    )
    assert script().removals("No such section", "9.1") == []


def test_the_data_ships_in_the_package():
    entries = removals._data()
    assert len(entries) > 100
    assert {e["version"] for e in entries} >= {"4.0", "4.1", "5.0", "5.1", "6.0"}


def test_between():
    assert {r.version for r in removals.between("4.2.7", "5.2")} == {"5.0", "5.1"}
    assert removals.between("5.2", "5.2") == []
    assert removals.between(None, "5.2") == []


@pytest.fixture(scope="module")
def found():
    return usage.scan(SAMPLE)


def test_what_the_sample_still_uses(found):
    used = {r.text: r.used_in for r in removals.check("3.2", "5.2", found) if r.used_in}
    assert used == {
        "The HttpRequest.is_ajax() method is removed": ["shop/views.py:11"],
        "The NullBooleanField model field is removed, except for support in historical "
        "migrations": ["shop/models.py:7"],  # not the migration
        "The USE_L10N setting is removed": ["mysite/settings.py:19"],
        "The django.utils.timezone.utc alias to datetime.timezone.utc is removed": [
            "shop/models.py:8"
        ],
        "The model's Meta.index_together option is removed": ["shop/models.py:11"],
        "The length_is template filter is removed": ["shop/templates/shop/list.html:5"],
        "The DEFAULT_FILE_STORAGE and STATICFILES_STORAGE settings is removed": [
            "mysite/settings.py:20"
        ],
    }


def test_partial_removals_are_not_matched(found):
    order_by = removals.Removal(
        "4.0",
        "Support for passing raw column aliases to QuerySet.order_by() is removed",
        ["QuerySet.order_by"],
        "",
        False,
    )
    assert removals.match(order_by, found) == []


def test_in_the_report(project, capsys):
    cli.main([str(project), "--no-input"])  # no code: listed, not matched
    out = capsys.readouterr().out
    assert "Removed in Django 5.0 and 5.1 (" in out
    assert "removals to look for in your code, in the release notes:\n    https://docs." in out
    cli.main([str(project), "-f", "json"])
    data = json.loads(capsys.readouterr().out)
    assert data["removals"] and all(r["used_in"] is None for r in data["removals"])


def test_used_removals_in_the_report(project, capsys):
    import shutil

    shutil.copytree(SAMPLE, project, dirs_exist_ok=True)
    cli.main([str(project), "--no-input", "-f", "json"])
    data = json.loads(capsys.readouterr().out)
    used = [r for r in data["removals"] if r["used_in"]]
    # USE_L10N and timezone.utc went in 5.0, index_together, length_is and the storage
    # settings in 5.1; is_ajax() and NullBooleanField in 4.0, before this project's 4.2.
    assert [r["version"] for r in used] == ["5.0", "5.0", "5.1", "5.1", "5.1"]
    cli.main([str(project), "--no-input"])
    out = capsys.readouterr().out
    assert "Removed in Django 5.0 and 5.1 (5 used in your code)" in out
    assert "The model's Meta.index_together option is removed" in out
    assert "more removals you do not use" in out
    cli.main([str(project), "--no-input", "-v"])
    assert "more removals" not in capsys.readouterr().out  # -v shows every one


def test_a_health_check_lists_nothing(project, capsys):
    (project / "requirements.txt").write_text("Django==5.2.3\ndjango-ready==1.0\n")
    cli.main([str(project), "-f", "json", "--target", "5.2"])
    assert json.loads(capsys.readouterr().out)["removals"] == []
