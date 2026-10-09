"""Several projects in one report: PROJECT ... and --recursive."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

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
    "flag", [["--emit", "uv"], ["--via", "lts"], ["--python", "python3"], ["--explain", "x"]]
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


def test_html_links_the_projects(monorepo, capsys):
    code, out, _ = run(capsys, "services/api", "services/worker", "-f", "html")
    assert code == 0
    assert "<h1>2 projects</h1>" in out
    assert '<a href="#project-1">services/api</a>' in out
    assert '<section class="step" id="project-2"><h2 class="step">services/worker: Django' in out
    assert "<h2>Blocking more than one project</h2>" in out
    data = out.split('<script type="application/json" id="report-data">')[1].split("</script>")[0]
    assert json.loads(data)["kind"] == "multi"


def test_html_escapes_project_paths(monorepo, capsys):
    odd = monorepo / "R&D"  # < and > are not allowed in Windows file names
    odd.mkdir()
    (odd / "requirements.txt").write_text("Django==4.2.7\ndjango-blocked==1.0\n")
    code, out, _ = run(capsys, "services/worker", "R&D", "-f", "html", "--static")
    assert code == 0
    assert '<a href="#project-2">R&amp;D</a>' in out and ">R&D<" not in out


def test_tracking_issue_goes_project_by_project(monorepo, capsys):
    from django_upgrade_report import ci

    code, out, _ = run(capsys, "services/api", "services/worker", "-f", "json")
    data = json.loads(out)
    assert ci.issue_title(data, ".") == "Django 5.2 upgrade plan"
    ids = [task_id for task_id, _ in ci.tasks(data)]
    assert "services-api:django:django-blocked" in ids
    assert "services-worker:django:django-blocked" in ids
    body = ci.issue_body(data, ".")
    assert "from `services/api, services/worker`" in body
    assert "- [ ] services/api: **django-blocked**" in body
    before = ci.fingerprint(data)
    data["projects"][1] = {"path": "services/worker", "error": "gone"}
    assert ci.fingerprint(data) != before


ACTION = Path(__file__).parent.parent / "action.yml"
# Through PATH: on Windows a bare "bash" finds System32's WSL launcher before Git Bash, the
# shell the Action runs in.
BASH = shutil.which("bash") or "bash"


def test_action_outputs_sum_the_projects(monorepo, capsys, tmp_path):
    script = re.search(r"<<'PY'\n(.*?)\n\s*PY\n", ACTION.read_text(), re.S).group(1)
    _, out, _ = run(capsys, "services/api", "services/worker", "-f", "json")
    report = tmp_path / "report.json"
    report.write_text(out)
    outputs = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), str(report)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "blocked=2" in outputs.splitlines()


def test_action_reads_one_path_per_line():
    block = re.search(r"(# One path per line.*?)\n\s*common=", ACTION.read_text(), re.S).group(1)
    script = textwrap.dedent("        " + block) + '\nprintf "<%s>" "${paths[@]}"; echo " key=$key"'
    run_ = subprocess.run(
        [BASH, "-c", script],
        env=_env(DUR_PATH="  services/api\n\nservices/my worker  \n"),
        capture_output=True,
        text=True,
        check=True,
    )
    assert run_.stdout == "<services/api><services/my worker> key=services/api services/my worker\n"
    empty = subprocess.run(
        [BASH, "-c", script], env=_env(DUR_PATH=""), capture_output=True, text=True, check=True
    )
    assert empty.stdout == "<.> key=.\n"


def _env(**values: str) -> dict[str, str]:
    """The environment with ``values``: Windows needs its own variables to start bash."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("DUR_")}
    return env | values
