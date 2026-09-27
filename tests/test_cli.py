from __future__ import annotations

import json

import pytest

from django_upgrade_report import cli


@pytest.fixture
def project(tmp_path, index, monkeypatch):
    (tmp_path / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-ready==1.0\ndjango-before==1.0\ndjango-blocked==1.0\n"
    )
    monkeypatch.setattr(cli, "PyPI", lambda *args, **kwargs: index)
    return tmp_path


def test_text(project, capsys):
    assert cli.main([str(project)]) == 0
    out = capsys.readouterr().out
    assert "Django 4.2.7 → 5.2" in out
    assert "Upgrade first (1)" in out
    assert "Blocked (1)" in out
    assert "1 ready · 1 to upgrade · 0 to check · 1 blocked" in out
    assert "\033[" not in out  # no colours when not a terminal


def test_fail_on(project):
    assert cli.main([str(project), "--fail-on", "blocked"]) == 1


def test_json(project, capsys):
    cli.main([str(project), "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert data["target"] == "5.2"
    assert data["counts"] == {"ready": 1, "upgrade": 1, "check": 0, "blocked": 1}
    before = next(p for p in data["packages"] if p["name"] == "django-before")
    assert (before["upgrade_to"], before["phase"]) == ("2.0", "before")


def test_markdown(project, capsys):
    cli.main([str(project), "--format", "markdown"])
    out = capsys.readouterr().out
    assert out.startswith("## Django 4.2.7 → 5.2")
    assert "| `django-before` | 1.0 → 2.0 |" in out


def test_html_to_file(project, tmp_path):
    target = tmp_path / "report.html"
    assert cli.main([str(project), "--format", "html", "-o", str(target)]) == 0
    html = target.read_text()
    assert html.startswith("<!doctype html>")
    assert "django-blocked" in html


def test_missing_project(tmp_path, capsys):
    assert cli.main([str(tmp_path / "nope")]) == 2
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize("fmt", ["markdown", "html", "json"])
def test_attribution(project, capsys, fmt):
    cli.main([str(project), "--format", fmt])
    out = capsys.readouterr().out
    assert "Daniel Kurdoghlian" in out
    assert "Pushing Pixels" in out


def test_version_names_the_author(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "Daniel Kurdoghlian, Pushing Pixels" in capsys.readouterr().out
