"""``--via``: one report per step on the way to the target."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

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
        (["--emit", "uv"], "it cannot go with --emit"),
        (["--explain", "django-before"], "it cannot go with --explain"),
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


def test_markdown_folds_each_step(path_project, capsys):
    args = [str(path_project), "--target", "6.0", "--via", "lts", "--python-target", "none"]
    assert cli.main([*args, "-f", "markdown"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("## Django 4.2.7 → 5.2 → 6.0 (2 steps)\n\n> [!WARNING]\n")
    assert "| 1 | Django 4.2.7 → 5.2 | 1 | 2 | 0 | 1 |" in out
    assert "<details open>\n<summary><b>Step 1 of 2: Django 4.2.7 → 5.2</b></summary>" in out
    assert "<details>\n<summary><b>Step 2 of 2: Django 5.2.3 → 6.0</b></summary>" in out
    assert out.count("<sub>Generated by") == 1


def test_html_shows_the_steps_on_one_page(path_project, capsys):
    args = [str(path_project), "--target", "6.0", "--via", "lts", "--python-target", "none"]
    assert cli.main([*args, "-f", "html"]) == 0
    page = capsys.readouterr().out
    assert '<a href="#step-2">Step 2 of 2</a></td><td>Django 5.2.3 → 6.0</td>' in page
    assert '<section class="step" id="step-1">' in page
    assert '<section id="1-blocked" data-rows>' in page and 'id="2-blocked"' in page
    assert 'data-todo="1-django:django-before"' in page  # ticks per step
    assert 'data-todo="2-django:django-before"' in page
    assert page.count('id="toolbar"') == 1 and page.count('id="done"') == 1
    data = re.search(r'id="report-data">(.*?)</script>', page, re.S).group(1)
    assert json.loads(data)["kind"] == "path"
    assert cli.main([*args, "-f", "html", "--static"]) == 0
    assert "<script" not in capsys.readouterr().out


def test_tracking_issue_lists_the_tasks_per_step(path_project, capsys):
    from django_upgrade_report import ci

    data = path(path_project, capsys, "--via", "lts")
    ids = [task_id for task_id, _ in ci.tasks(data)]
    assert "step1:django:django-before" in ids and "step2:django:django-before" in ids
    body = ci.issue_body(data, ".")
    assert "from `requirements.txt`" in body
    assert "- [ ] Step 2: **django-before** 2.0 → 2.1" in body
    assert ci.fingerprint(data) != ci.fingerprint(data["steps"][0])


ACTION = Path(__file__).parent.parent / "action.yml"


@pytest.mark.parametrize("via", [False, True])
def test_action_outputs_sum_the_steps(path_project, capsys, tmp_path, via):
    script = re.search(r"<<'PY'\n(.*?)\n\s*PY\n", ACTION.read_text(), re.S).group(1)
    script = textwrap.dedent(script)
    data = path(path_project, capsys, *(["--via", "lts"] if via else []))
    report = tmp_path / "report.json"
    report.write_text(json.dumps(data))
    out = subprocess.run(
        [sys.executable, "-c", script, str(report)], capture_output=True, text=True, check=True
    ).stdout
    outputs = dict(line.split("=", 1) for line in out.splitlines())
    steps = data["steps"] if via else [data]
    for status in ("ready", "upgrade", "check", "blocked"):
        assert outputs[status] == str(sum(step["counts"][status] for step in steps))
    assert outputs["blocked"] == ("2" if via else "1")  # blocked in both steps
