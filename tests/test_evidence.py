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
    def __init__(self, files, workflows=None, broken=(), issues=None, searches=10):
        self.files, self.listed, self.broken = files, workflows, set(broken)
        self.issues, self.searches = issues or {}, searches
        self.requests = []
        self.searched = []

    def search(self, owner, repo, words):
        self.searched.append((repo, words))
        found = self.issues.get(repo, [])
        if isinstance(found, Exception):
            raise found
        return found

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


# --- issues and pull requests upstream ----------------------------------------------


def item(number, title, state="open", pull=None, updated="2026-01-01T00:00:00Z"):
    entry = {
        "number": number,
        "title": title,
        "state": state,
        "html_url": f"https://github.com/org/x/issues/{number}",
        "updated_at": updated,
    }
    if pull is not None:
        entry["pull_request"] = {"merged_at": pull or None}
    return entry


def test_upstream_items_pick_the_most_telling_two():
    found = [
        item(1, "Django 5.2 support?", updated="2026-03-01T00:00:00Z"),
        item(2, "Support Django 5.2", state="closed", pull="2026-02-01T00:00:00Z"),
        item(3, "Add Django 5.2 to CI", pull=False, updated="2026-01-01T00:00:00Z"),
        item(4, "Add Django 5.2 and Python 3.13", pull=False, updated="2026-02-01T00:00:00Z"),
        item(5, "Django 5.20 typo"),  # not the target
        item(6, "Support django 4.2"),
    ]
    items = evidence.upstream_items(found, "5.2")
    assert [(i.number, i.kind, i.state) for i in items] == [(4, "pr", "open"), (3, "pr", "open")]
    assert items[0].label == "open PR: Add Django 5.2 and Python 3.13 (#4)"
    merged = evidence.upstream_items([found[1], found[0]], "5.2")
    assert [i.label for i in merged] == [
        "open issue: Django 5.2 support? (#1)",
        "merged PR: Support Django 5.2 (#2)",
    ]


def test_upstream_titles_are_shortened():
    (long,) = evidence.upstream_items([item(7, "Django 5.2: " + "x" * 200)], "5.2")
    assert len(long.title) == 80 and long.title.endswith("…")


@pytest.fixture
def blocked_project(repo_project, index):
    index.packages["django-blocked"][0]["project_urls"] = {
        "Source": "https://github.com/org/django-blocked"
    }
    (repo_project.path / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-lagging==1.0\ndjango-silent==0.2\ndjango-blocked==1.0\n"
    )
    return repo_project


def test_upstream_in_every_format(blocked_project, capsys):
    blocked_project.files.issues["django-blocked"] = [
        item(912, "Add Django 5.2 support <script>alert(1)</script> | x", pull=False)
    ]
    args = [str(blocked_project.path), "--evidence", "--no-input"]
    cli.main([*args, "-f", "json"])
    packages = {p["name"]: p for p in json.loads(capsys.readouterr().out)["packages"]}
    (upstream,) = packages["django-blocked"]["upstream"]
    assert (upstream["kind"], upstream["state"], upstream["number"]) == ("pr", "open", 912)
    assert packages["django-silent"]["upstream"] == []  # it has a sign already
    cli.main(args)
    assert "open PR: Add Django 5.2 support <script>alert(1)</script> | x (#912)" in (
        capsys.readouterr().out
    )
    cli.main([*args, "-f", "markdown"])
    assert "open PR: Add Django 5.2 support \\<script\\>alert(1)\\</script\\> \\| x (#912)" in (
        capsys.readouterr().out
    )
    cli.main([*args, "-f", "html"])
    page = capsys.readouterr().out
    assert "&lt;script&gt;alert(1)&lt;/script&gt; | x (#912)</a>" in page
    assert "<script>alert(1)" not in page


def test_searches_blocked_first_and_within_the_limit(blocked_project, capsys):
    blocked_project.files.searches = 1
    cli.main([str(blocked_project.path), "--evidence", "-f", "json"])
    notices = json.loads(capsys.readouterr().out)["notices"]
    assert blocked_project.files.searched == [("django-blocked", "Django 5.2")]
    assert "Searched upstream issues for 1 of 2 packages: set GITHUB_TOKEN for more" in notices


def test_a_refused_search_stops_searching(blocked_project, capsys):
    blocked_project.files.issues["django-blocked"] = FetchError("HTTP 403 rate limit exceeded")
    cli.main([str(blocked_project.path), "--evidence", "-f", "json"])
    notices = json.loads(capsys.readouterr().out)["notices"]
    assert any(n.startswith("Could not search the issues of django-blocked") for n in notices)
    assert len(blocked_project.files.searched) == 1


def test_github_search_over_http(monkeypatch):
    fake = serve(monkeypatch, {"total_count": 1, "items": [item(3, "Django 5.2")]})
    files = evidence.GitHubFiles(None)
    assert files.searches == 10 and evidence.GitHubFiles(None, token="t").searches == 30
    assert files.search("org", "x", "Django 5.2")[0]["number"] == 3
    assert fake.requests[0].full_url == (
        "https://api.github.com/search/issues?q=repo%3Aorg/x%20%22Django%205.2%22%20in%3Atitle"
        "&sort=updated&order=desc&per_page=5"
    )
