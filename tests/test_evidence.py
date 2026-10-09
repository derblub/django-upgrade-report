"""Signs of support for packages to check: they add notes, never a status."""

from __future__ import annotations

import json

from conftest import release

from django_upgrade_report import cli


def check_project(project, index, *, description):
    """django-lagging and django-silent to check; the newest django-lagging has ``description``."""
    index.packages["django-lagging"].append(
        release("django-lagging", "1.1", ">=3.2", ["4.1", "4.2"], description=description)
    )
    (project / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-lagging==1.0\ndjango-silent==0.2\n"
    )
    return project


def test_readme_that_names_the_target(project, index, capsys):
    check_project(project, index, description="Tested with Django 4.2 and Django 5.2.")
    assert cli.main([str(project), "-f", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    packages = {p["name"]: p for p in data["packages"]}
    assert packages["django-lagging"]["status"] == "check"  # a sign, not a status
    assert packages["django-lagging"]["evidence"] == [
        {
            "kind": "readme",
            "text": "README of 1.1 mentions Django 5.2",
            "url": "https://pypi.org/project/django-lagging/1.1/",
        }
    ]
    assert packages["django-silent"]["evidence"] == []
    # without a sign first: that is where the work is
    assert [p["name"] for p in data["packages"] if p["status"] == "check"] == [
        "django-silent",
        "django-lagging",
    ]


def test_readme_that_does_not(project, index, capsys):
    check_project(project, index, description="Supports Django 4.2 and 5.1.")
    cli.main([str(project), "-f", "json"])
    assert all(p["evidence"] == [] for p in json.loads(capsys.readouterr().out)["packages"])


def test_every_format_shows_the_sign(project, index, capsys):
    check_project(project, index, description="Works with Django 5.2.")
    cli.main([str(project), "--no-input"])
    text = capsys.readouterr().out
    assert "1 of them has signs of support, see its notes." in text
    assert "README of 1.1 mentions Django 5.2" in text
    cli.main([str(project), "-f", "markdown"])
    assert "README of 1.1 mentions Django 5.2" in capsys.readouterr().out
    cli.main([str(project), "-f", "html"])
    assert (
        '<a class="note sign" href="https://pypi.org/project/django-lagging/1.1/">'
        "README of 1.1 mentions Django 5.2</a>"
    ) in capsys.readouterr().out
