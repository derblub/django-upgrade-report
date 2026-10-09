"""Several projects in one report: PROJECT ... and --recursive."""

from __future__ import annotations

import json

import pytest

from django_upgrade_report import cli
from django_upgrade_report.multi import discover


@pytest.fixture
def monorepo(tmp_path, index, monkeypatch):
    """Three services, as uv, Poetry and requirements projects, and one without dependencies."""
    services = tmp_path / "services"
    api, admin, worker, broken = (services / n for n in ("api", "admin", "worker", "broken"))
    for d in (api, admin, worker, broken):
        d.mkdir(parents=True)
    (api / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "django"\nversion = "4.2.7"\n\n'
        '[[package]]\nname = "django-blocked"\nversion = "1.0"\n\n'
        '[[package]]\nname = "django-before"\nversion = "1.0"\n'
    )
    (admin / "poetry.lock").write_text(
        '[[package]]\nname = "Django"\nversion = "4.2.7"\nfiles = []\n\n'
        '[[package]]\nname = "django-before"\nversion = "1.0"\nfiles = []\n\n'
        '[metadata]\nlock-version = "2.0"\n'
    )
    (worker / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-blocked==1.0\nrequests==2.31.0\n"
    )
    (broken / "pyproject.toml").write_text('[project]\nname = "broken"\ndependencies = []\n')
    monkeypatch.setattr(cli, "PyPI", lambda *args, **kwargs: index)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(capsys, *args):
    code = cli.main([*args, "--no-scan-code", "--target", "5.2"])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_overview(monorepo, capsys):
    code, out, err = run(capsys, "services/api", "services/admin", "services/worker")
    assert code == 0, err
    head = out.split("\n\n")[0].splitlines()
    assert head[0] == "3 projects"
    row = " ".join(head[1].split())
    assert row == "services/api Django 4.2.7 → 5.2 0 ready · 1 to upgrade · 0 to check · 1 blocked"
    assert "Blocking more than one project\n  django-blocked  services/api, services/worker" in out
    assert (
        "Upgrades several projects share\n  django-before → 2.0  services/api, services/admin"
        in out
    )
    assert "\nservices/worker\n\nDjango 4.2.7 → 5.2\nfrom requirements.txt" in out


def test_one_index_client_for_all(monorepo, index, capsys):
    run(capsys, "services/api", "services/admin")
    asked = [url for url in index.requests if url.endswith("/django-before/json")]
    assert len(asked) == 1


def test_recursive_finds_the_projects(monorepo, capsys):
    (monorepo / "services" / "api" / "requirements").mkdir()
    (monorepo / "services" / "api" / "requirements" / "dev.txt").write_text("pytest\n")
    (monorepo / "services" / "worker" / "pkg").mkdir()
    (monorepo / "services" / "worker" / "pkg" / "pyproject.toml").write_text(
        '[project]\ndependencies = ["django"]\n'
    )
    (monorepo / "services" / "worker" / "own").mkdir()
    (monorepo / "services" / "worker" / "own" / "uv.lock").write_text("version = 1\n")
    for skipped in (".venv/lib", "node_modules/x", ".git/x"):
        (monorepo / skipped).mkdir(parents=True)
        (monorepo / skipped / "requirements.txt").write_text("Django==1.0\n")
    found = [p.as_posix() for p in discover([monorepo])]
    names = [p.removeprefix(monorepo.as_posix() + "/") for p in found]
    assert names == [
        "services/admin",
        "services/api",
        "services/broken",
        "services/worker",
        "services/worker/own",  # its own lockfile: a project of its own
    ]


def test_a_broken_project_does_not_stop_the_others(monorepo, capsys):
    code, out, err = run(capsys, "-r", "services", "-f", "json")
    assert code == 2
    data = json.loads(out)
    assert data["kind"] == "multi" and data["schema_version"] == 1
    paths = [p["path"] for p in data["projects"]]
    assert paths == ["services/admin", "services/api", "services/broken", "services/worker"]
    broken = data["projects"][2]
    assert "report" not in broken and "No " in broken["error"]
    assert data["projects"][1]["report"]["target"] == "5.2"
    assert data["blocking"] == {"django-blocked": ["services/api", "services/worker"]}
    assert data["shared_upgrades"] == {
        "django-before": {"version": "2.0", "projects": ["services/admin", "services/api"]}
    }
    assert "1 of 4 projects could not be checked: services/broken: No " in err


def test_fail_on_looks_at_every_project(monorepo, capsys):
    code, _, _ = run(capsys, "services/admin", "services/worker", "--fail-on", "blocked")
    assert code == 1
    code, _, _ = run(capsys, "services/admin", "services/api", "--fail-on", "check")
    assert code == 1
    code, _, _ = run(capsys, "services/admin", "services/admin", "--fail-on", "blocked")
    assert code == 0


def test_markdown_folds_each_project(monorepo, capsys):
    code, out, _ = run(capsys, "services/api", "services/worker", "-f", "markdown")
    assert code == 0
    assert out.startswith("## 2 projects\n")
    assert "| services/api | Django 4.2.7 → 5.2 | 0 | 1 | 0 | 1 |" in out
    assert "### Blocking more than one project" in out
    assert "<summary><b>services/worker: Django 4.2.7 → 5.2</b></summary>" in out


@pytest.mark.parametrize(
    "flag", [["--emit", "uv"], ["--via", "lts"], ["-f", "html"], ["--explain", "django-blocked"]]
)
def test_what_does_not_go_with_several_projects(monorepo, capsys, flag):
    code, _, err = run(capsys, "services/api", "services/worker", *flag)
    assert code == 2
    assert "several projects make one report each, that cannot go with" in err


def test_recursive_without_projects(tmp_path, capsys):
    assert cli.main([str(tmp_path), "-r"]) == 2
    assert "--recursive found no project in" in capsys.readouterr().err


def test_recursive_with_one_project_is_the_usual_report(monorepo, capsys):
    code, out, _ = run(capsys, "-r", "services/worker", "-f", "json")
    assert code == 0 and json.loads(out)["kind"] == "report"
