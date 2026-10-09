"""Signs of support for packages to check: they add notes, never a status."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import http_error, release, serve

from django_upgrade_report import cli, evidence
from django_upgrade_report.client import FetchError


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


# --- the parsers, on files from real projects -----------------------------------------

DATA = Path(__file__).parent / "data" / "evidence"


def read(name):
    return (DATA / name).read_text(encoding="utf-8")


def test_tox_of_django_taggit():
    assert evidence.tox_versions(read("django-taggit-tox.ini")) == {"5.2", "6.0"}  # not "main"


def test_tox_of_django_filter():
    assert evidence.tox_versions(read("django-filter-tox.ini")) == {"5.2", "6.0", "6.1"}


def test_workflow_of_django_storages():
    assert evidence.workflow_versions(read("django-storages-ci.yml")) == {"4.2", "5.2", "6.0"}


def test_nox_of_django_allauth():
    versions = evidence.nox_versions(read("django-allauth-noxfile.py"))
    assert versions == {"4.2", "5.1", "5.2", "6.0", "6.1"}  # the keys of a dict, through .keys()


@pytest.mark.parametrize(
    ("envlist", "names"),
    [
        (
            "py{310,312}-django{42,52}",
            ["py310-django42", "py310-django52", "py312-django42", "py312-django52"],
        ),
        ("lint, py312-dj52\n docs", ["lint", "py312-dj52", "docs"]),
        ("{py311, py312}-django52,", ["py311-django52", "py312-django52"]),
    ],
)
def test_expand_envlist(envlist, names):
    assert evidence.expand_envlist(envlist) == names


def test_tox_factors_and_gh_actions():
    text = """
[tox]
env_list = py312-dj{51,52}, py313-django-60, djmain, django
[gh-actions:env]
DJANGO =
    6.1: dj61
    main: djmain
"""
    assert evidence.tox_versions(text) == {"5.1", "5.2", "6.1"}


def test_tox_in_pyproject():
    legacy = '[tool.tox]\nlegacy_tox_ini = """\n[tox]\nenvlist = py312-django52\n"""\n'
    assert evidence.tox_toml_versions(legacy) == {"5.2"}
    native = '[tool.tox]\nenv_list = ["py312-dj{52,60}"]\n'
    assert evidence.tox_toml_versions(native) == {"5.2", "6.0"}
    assert evidence.tox_toml_versions("not toml [") == set()


def test_workflow_shapes():
    text = """
jobs:
  test:
    strategy:
      matrix:
        python: ["3.12"]
        django: ["4.2", 'Django~=5.2.0', "main"]  # a comment
        include:
          - django-version: "6.0"
        exclude:
          - django: "6.1"
    steps:
      - run: echo "django: 7.0"
"""
    assert evidence.workflow_versions(text) == {"4.2", "5.2", "6.0"}


def test_parsers_find_nothing_rather_than_guess():
    assert evidence.tox_versions("[tox\nenvlist = dj52") == set()
    assert evidence.nox_versions("def broken(:") == set()
    assert evidence.nox_versions('nox.parametrize("python", ["5.2"])') == set()
    assert evidence.workflow_versions("django: 5.2\n") == set()  # not in a matrix


# --- --evidence ---------------------------------------------------------------------


class FakeFiles:
    def __init__(self, files, workflows=None, broken=()):
        self.files, self.listed, self.broken = files, workflows, set(broken)
        self.requests = []

    def text(self, owner, repo, path):
        self.requests.append(f"{owner}/{repo}/{path}")
        if repo in self.broken:
            raise FetchError(f"could not fetch {path}: HTTP 500")
        return self.files.get(f"{owner}/{repo}/{path}")

    def workflows(self, owner, repo):
        return self.listed


@pytest.fixture
def repo_project(project, index, monkeypatch):
    for name in ("django-lagging", "django-silent"):
        for r in index.packages[name]:
            r["project_urls"] = {"Source": f"https://github.com/org/{name}"}
    (project / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-lagging==1.0\ndjango-silent==0.2\n"
    )
    files = FakeFiles({"org/django-silent/tox.ini": "[tox]\nenvlist = py312-django{42,52}\n"})
    made = []

    def github(*args):
        made.append(args)
        return files

    monkeypatch.setattr(evidence, "GitHubFiles", github)
    return SimpleNamespace(path=project, files=files, made=made)


def test_nothing_goes_to_github_without_evidence(repo_project, capsys):
    cli.main([str(repo_project.path), "-f", "json"])
    assert repo_project.made == [] and repo_project.files.requests == []


def test_test_matrix_sign(repo_project, capsys):
    cli.main([str(repo_project.path), "-f", "json", "--evidence"])
    packages = {p["name"]: p for p in json.loads(capsys.readouterr().out)["packages"]}
    assert packages["django-silent"]["evidence"] == [
        {
            "kind": "test-matrix",
            "text": "main branch tests Django 5.2 (tox.ini)",
            "url": "https://github.com/org/django-silent/blob/HEAD/tox.ini",
        }
    ]
    assert packages["django-lagging"]["evidence"] == []
    requested = repo_project.files.requests
    lagging = [r for r in requested if "django-lagging" in r]
    assert len(lagging) == 8 + 9  # the fixed lists of test matrices and changelogs, no more
    assert requested.count("org/django-silent/tox.ini") == 1  # found at once, nothing else asked


def test_listed_workflows_replace_the_guesses(repo_project):
    files = FakeFiles(
        {
            "o/r/.github/workflows/tests-django.yaml": (
                'jobs:\n  t:\n    strategy:\n      matrix:\n        django: ["5.2"]\n'
            )
        },
        workflows=["tests-django.yaml"],
    )
    found = evidence.test_matrix(files, "https://github.com/o/r", "5.2")
    assert found.text == "main branch tests Django 5.2 (tests-django.yaml)"
    assert "o/r/.github/workflows/ci.yml" not in files.requests


def test_a_repository_that_cannot_be_read_is_a_notice(repo_project, capsys):
    repo_project.files.broken = {"django-lagging"}
    assert cli.main([str(repo_project.path), "-f", "json", "--evidence"]) == 0
    notices = json.loads(capsys.readouterr().out)["notices"]
    assert any(n.startswith("Could not read the repository of django-lagging") for n in notices)


def test_private_packages_stay_private(repo_project, capsys):
    cli.main(
        [
            str(repo_project.path),
            "-f",
            "json",
            "--evidence",
            "--index-url",
            "https://pkgs.example/pypi",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert "--evidence looks at packages from PyPI only, none were checked" in out["notices"]
    assert repo_project.files.requests == []


def test_github_files_over_http(monkeypatch, tmp_path):
    fake = serve(monkeypatch, b"[tox]\nenvlist = dj52\n", http_error(404))
    files = evidence.GitHubFiles(tmp_path)
    assert files.text("o", "r", "tox.ini").startswith("[tox]")
    assert files.text("o", "r", "noxfile.py") is None  # 404, remembered
    assert files.workflows("o", "r") is None  # no token: guess
    assert [r.full_url for r in fake.requests] == [
        "https://raw.githubusercontent.com/o/r/HEAD/tox.ini",
        "https://raw.githubusercontent.com/o/r/HEAD/noxfile.py",
    ]
    again = evidence.GitHubFiles(tmp_path)  # from the disk cache, no request
    assert again.text("o", "r", "noxfile.py") is None and len(fake.requests) == 2


def test_github_workflows_with_a_token(monkeypatch):
    listing = [{"name": "tests.yml"}, {"name": "README.md"}, {"name": "lint.yaml"}]
    fake = serve(monkeypatch, listing)
    files = evidence.GitHubFiles(None, token="secret")
    assert files.workflows("o", "r") == ["tests.yml", "lint.yaml"]
    assert fake.requests[0].get_header("Authorization") == "Bearer secret"
    refused = evidence.GitHubFiles(None, token="secret")
    serve(monkeypatch, http_error(403))
    assert refused.workflows("o", "x") is None  # refused: guess instead


# --- changelogs ---------------------------------------------------------------------


def test_changelog_of_django_taggit():
    text = read("django-taggit-CHANGELOG.rst")  # "(Unreleased)": "Add Django 5.2 and 6.0 support"
    assert evidence.changelog_mention(text, "4.0.0", "6.0") == "unreleased"
    assert evidence.changelog_mention(text, "4.0.0", "6.1") is None


def test_changelog_of_django_storages():
    text = read("django-storages-CHANGELOG.rst")  # "X.YY.Z (UNRELEASED)", then versions
    sections = [version for version, _ in evidence.changelog_sections(text)]
    assert sections[:3] == ["unreleased", "1.14.6", "1.14.5"]
    assert evidence.changelog_mention(text, "1.14.2", "5.1") == "1.14.5"
    assert evidence.changelog_mention(text, "1.14.5", "5.1") is None  # not after yours


def test_changelog_of_simplejwt_says_nothing_of_5_2():
    assert evidence.changelog_mention(read("simplejwt-CHANGELOG.md"), "5.3.0", "5.2") is None


@pytest.mark.parametrize(
    ("line", "found"),
    [
        ("- Add support for Django 5.2", True),
        ("- Tested against Django>=5.2", True),
        ("- Compatible with Django 5.1, 5.2", True),
        ("- Django 5.2 is out", False),  # no word for support
        ("- Add support for Django 5.20", False),
        ("- Drop support for Django 5.2", False),
        ("- Django 5.2 is no longer supported", False),
        ("- Remove the deprecated tests for Django 5.2", False),
    ],
)
def test_changelog_lines(line, found):
    text = f"# Changelog\n\n## 2.0 (2026-01-01)\n\n{line}\n\n## 1.0\n\n- First\n"
    assert (evidence.changelog_mention(text, "1.0", "5.2") == "2.0") is found


def test_changelog_needs_sections():
    assert evidence.changelog_mention("Add support for Django 5.2\n", "1.0", "5.2") is None
    assert evidence.changelog_mention("## 2.0\n- Add support for Django 5.2\n", "x", "5.2") is None


def test_changelog_reads_the_linked_file_first():
    files = FakeFiles({"o/r/docs/history.md": "## 2.0\n- Add Django 5.2 support\n"})
    link = "https://github.com/o/r/blob/main/docs/history.md"
    found = evidence.changelog(files, "https://github.com/o/r", "1.0", "5.2", link)
    assert found.text == "changelog of 2.0 mentions Django 5.2 support"
    assert files.requests == ["o/r/docs/history.md"]
    files = FakeFiles({"o/r/CHANGELOG.md": "## 2.0\n- Fixes\n", "o/r/CHANGES.rst": "x"})
    assert evidence.changelog(files, "https://github.com/o/r", "1.0", "5.2") is None
    assert files.requests == ["o/r/CHANGELOG.md"]  # the first file found decides


def test_changelog_sign_in_the_report(repo_project, capsys):
    repo_project.files.files["org/django-lagging/CHANGELOG.md"] = (
        "# Changes\n\n## Unreleased\n\n- Test on Django 5.2\n\n## 1.0\n"
    )
    cli.main([str(repo_project.path), "-f", "json", "--evidence"])
    packages = {p["name"]: p for p in json.loads(capsys.readouterr().out)["packages"]}
    assert packages["django-lagging"]["evidence"] == [
        {
            "kind": "changelog",
            "text": "changelog of the unreleased changes mentions Django 5.2 support",
            "url": "https://github.com/org/django-lagging/blob/HEAD/CHANGELOG.md",
        }
    ]
