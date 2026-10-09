"""``--via``: one report per step on the way to the target."""

from __future__ import annotations

import json

import pytest
from conftest import release

from django_upgrade_report import cli


@pytest.fixture
def path_project(project, index):
    """django-step needs an upgrade for 5.2, and that release already declares 6.0."""
    index.packages["django-step"] = [
        release("django-step", "1.0", ">=4.2", ["4.2"]),
        release("django-step", "2.0", ">=4.2", ["4.2", "5.2", "6.0"]),
    ]
    requirements = project / "requirements.txt"
    requirements.write_text(requirements.read_text() + "django-step==1.0\n")
    return project


def path(project, capsys, *args):
    assert cli.main([str(project), "-f", "json", "--target", "6.0", *args]) == 0
    return json.loads(capsys.readouterr().out)


def statuses(step):
    return {p["name"]: (p["status"], p["upgrade_to"]) for p in step["packages"]}


def test_lts_path_starts_each_step_where_the_last_ended(path_project, capsys):
    data = path(path_project, capsys, "--via", "lts")
    assert (data["kind"], data["via"], data["target"]) == ("path", "lts", "6.0")
    first, second = data["steps"]
    assert (first["kind"], first["current_django"], first["target"]) == ("report", "4.2.7", "5.2")
    assert (second["current_django"], second["target"]) == ("5.2.3", "6.0")  # newest 5.2
    assert statuses(first)["django-step"] == ("upgrade", "2.0")
    assert statuses(second)["django-step"] == ("ready", None)  # upgraded in step 1
    assert statuses(first)["django-before"] == ("upgrade", "2.0")
    assert statuses(second)["django-before"] == ("upgrade", "2.1")  # from 2.0 on


def test_blocked_package_holds_the_path(path_project, capsys):
    data = path(path_project, capsys, "--via", "lts")
    assert data["blocked_at"] == 1
    (blocked,) = [p for p in data["steps"][1]["packages"] if p["name"] == "django-blocked"]
    assert blocked["status"] == "blocked"
    assert "blocked since step 1" in blocked["notes"]
    assert (
        cli.main([str(path_project), "--target", "6.0", "--via", "lts", "--fail-on", "blocked"])
        == 1
    )


def test_each_feature_version(path_project, capsys):
    data = path(path_project, capsys, "--via", "each", "--target", "5.2")
    assert [s["target"] for s in data["steps"]] == ["5.0", "5.1", "5.2"]


def test_target_that_is_the_next_station_is_one_step(path_project, capsys):
    data = path(path_project, capsys, "--via", "lts", "--target", "5.2")
    assert [s["target"] for s in data["steps"]] == ["5.2"]


def test_text_shows_the_path_and_each_step(path_project, capsys):
    cli.main([str(path_project), "--target", "6.0", "--via", "lts", "--python-target", "none"])
    out = capsys.readouterr().out
    assert out.startswith("Django 4.2.7 → 5.2 → 6.0 (2 steps)\n! The path stops at step 1: ")
    assert "django-blocked is blocked there" in out
    assert "Step 1 of 2\n\nDjango 4.2.7 → 5.2\n" in out
    assert "Step 2 of 2\n\nDjango 5.2.3 → 6.0\n" in out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--target", "4.2"], "--via needs a target above Django 4.2"),
        (["--emit", "uv"], "it cannot go with --emit yet"),
        (["-f", "html"], "it cannot go with --format html yet"),
    ],
)
def test_what_via_cannot_do(path_project, capsys, args, message):
    assert cli.main([str(path_project), "--via", "lts", *args]) == 2
    assert message in capsys.readouterr().err


def test_via_needs_the_django_you_run(project, capsys):
    (project / "requirements.txt").write_text("django-before==1.0\n")
    assert cli.main([str(project), "--via", "lts", "--target", "5.2"]) == 2
    assert "--via needs the Django you run: pin it, or pass --from" in capsys.readouterr().err
    assert cli.main([str(project), "--via", "lts", "--target", "6.0", "--from", "4.2"]) == 0


def test_python_per_step_and_a_file(path_project, index, capsys):
    (path_project / ".python-version").write_text("3.10\n")
    for r in index.packages["django-step"]:
        r["requires_python"] = "<3.12"
    target = path_project / "path.json"
    args = [str(path_project), "--target", "6.0", "--via", "lts", "-f", "json", "-o", str(target)]
    assert cli.main([*args, "--python-target", "3.12", "--fail-on-python", "blocked"]) == 1
    data = json.loads(target.read_text())
    (row,) = data["steps"][0]["python"]["packages"]
    assert (row["name"], row["status"]) == ("django-step", "blocked")
    assert cli.main([*args, "--python-target", "none", "--fail-on-python", "blocked"]) == 0
