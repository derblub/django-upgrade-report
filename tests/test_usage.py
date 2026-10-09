"""Reading the project's code for dependencies it never uses: locally, conservatively."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from django_upgrade_report import cli, usage

SAMPLE = Path(__file__).parent / "data" / "usage"


@pytest.fixture(scope="module")
def found():
    return usage.scan(SAMPLE)


def test_what_the_code_names(found):
    names = found.names
    for module in ("taggit", "crispy_forms", "django_ready", "whitenoise", "allauth"):
        assert module in names, module
    assert names["rest_framework"] == "shop/views.py:1"
    assert names["django_filters"].startswith("mysite/settings.py:")  # in a dotted string
    assert "django_extensions" in names  # runserver_plus in the Makefile
    assert "django_tables2" in names and "widget_tweaks" in names  # {% load %}
    assert "polymorphic" not in names  # only in a virtualenv
    assert "reversion" not in names  # only in node_modules
    assert found.unreadable == ["shop/broken.py"] and found.complete


@pytest.mark.parametrize(
    ("name", "unused"),
    [
        ("djangorestframework", False),
        ("django-filter", False),
        ("django-taggit", False),
        ("django-allauth", False),
        ("django-crispy-forms", False),
        ("django-extensions", False),
        ("django-tables2", False),
        ("django-ready", False),
        ("django-polymorphic", True),
        ("django-reversion", True),
        ("django-htmx", True),
        ("gunicorn", False),  # run, not imported
        ("pytest-django", False),
    ],
)
def test_unused(found, name, unused):
    assert usage.unused(name, found) is unused


def test_modules():
    assert usage.modules("djangorestframework") == ("rest_framework",)
    assert usage.modules("Django_HTMX") == ("django_htmx", "htmx")
    assert usage.modules("python-slugify") == ("python_slugify", "slugify")


def test_too_big_or_no_code_says_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "MAX_FILES", 2)
    big = usage.scan(SAMPLE)
    assert not big.complete and not usage.unused("django-polymorphic", big)
    (tmp_path / "requirements.txt").write_text("django-polymorphic\n")
    assert not usage.unused("django-polymorphic", usage.scan(tmp_path))  # no Python at all


def sample_project(project: Path) -> Path:
    shutil.copytree(SAMPLE, project, dirs_exist_ok=True)
    return project


def test_note_in_the_report(project, capsys):
    sample_project(project)
    cli.main([str(project), "--no-input", "-v"])
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if usage.NOTE in line or "django-" in line]
    blocked = next(i for i, line in enumerate(lines) if "django-blocked" in line)
    assert usage.NOTE in "\n".join(lines[blocked : blocked + 3])  # not in the code
    ready = next(line for line in out.splitlines() if "django-ready" in line)
    assert usage.NOTE not in ready  # in INSTALLED_APPS


def test_switched_off_or_pointed_elsewhere(project, tmp_path, capsys):
    sample_project(project)
    cli.main([str(project), "--no-input", "--no-scan-code"])
    assert usage.NOTE not in capsys.readouterr().out
    requirements = project / "requirements.txt"
    cli.main([str(requirements), "--no-input"])  # a file: no scan unless asked
    assert usage.NOTE not in capsys.readouterr().out
    cli.main([str(requirements), "--no-input", "--scan-code", str(project)])
    assert usage.NOTE in capsys.readouterr().out
    assert cli.main([str(project), "--scan-code", str(tmp_path / "none")]) == 2
    assert "--scan-code" in capsys.readouterr().err


def test_possibly_unused_in_every_format(project, capsys):
    sample_project(project)
    (project / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-ready==1.0\ndjango-blocked==1.0\nrequests==2.31.0\ngunicorn==22.0\n"
    )
    cli.main([str(project), "--no-input"])
    out = capsys.readouterr().out
    assert "Possibly unused (2): django-blocked, requests" in out  # not Django-related too
    cli.main([str(project), "-f", "markdown"])
    assert "**Possibly unused (2):** `django-blocked`, `requests`: not imported" in (
        capsys.readouterr().out
    )
    cli.main([str(project), "-f", "html"])
    assert '<h3>Possibly unused <span class="count">2</span></h3>' in capsys.readouterr().out
    cli.main([str(project), "-f", "json"])
    assert json.loads(capsys.readouterr().out)["unused"] == ["django-blocked", "requests"]
    cli.main([str(project), "-f", "json", "--no-scan-code"])
    assert json.loads(capsys.readouterr().out)["unused"] == []


def test_explain_says_where_the_code_uses_it(project, capsys):
    sample_project(project)
    cli.main([str(project), "--explain", "django-ready", "--explain", "django-blocked"])
    out = capsys.readouterr().out
    assert "your code uses it: mysite/settings.py:7" in out
    assert "your code never names it: not imported or configured" in out


def test_known_module_names_win():
    found = usage.Scan(names={"bs4": "a.py:1"}, python=1)
    assert usage.unused("beautifulsoup4", found) is False  # from the table
    assert usage.unused("weird-dist", found, {"weird-dist": ("bs4",)}) is False
    assert usage.unused("weird-dist", found) is True
