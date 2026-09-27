"""sources.load() feeding analysis.analyse(), with recorded PyPI data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import BASE, RecordedPyPI

from django_upgrade_report import sources
from django_upgrade_report.analysis import Status, analyse

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script")


class Watched(RecordedPyPI):
    def __init__(self):
        super().__init__()
        self.names: set[str] = set()

    def _fetch(self, url: str):
        self.names.add(url[len(BASE) + 1 :].split("/")[0])
        return super()._fetch(url)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    write(
        tmp_path / "requirements.txt",
        "Django==3.2.25\n"
        "django-allauth==0.44.0\n"
        "-e ./libs/foo\n"
        "django-bar @ git+https://user:token@github.com/x/django-bar.git@main\n",
    )
    write(tmp_path / "libs" / "foo" / "pyproject.toml", '[project]\nname = "django-foo"\n')
    write(tmp_path / ".python-version", "3.7.17\n")
    return tmp_path


def test_external_dependencies_are_listed_and_never_requested(project):
    pypi = Watched()
    report = analyse(sources.load(project), pypi, "4.2")
    assert report.external == [
        ("django-bar", "git https://github.com/x/django-bar.git@main"),
        ("django-foo", "path ./libs/foo"),
    ]
    assert not {"django-bar", "django-foo"} & pypi.names
    assert report.missing == []
    allauth = next(p for p in report.packages if p.name == "django-allauth")
    assert (allauth.status, allauth.target_version) == (Status.UPGRADE, "0.55.0")


def test_project_python_reaches_the_report(project):
    report = analyse(sources.load(project), Watched(), "4.2")
    assert report.project_python == "3.7"
    assert report.target_released
    assert any("your project uses 3.7 (from .python-version)" in w for w in report.warnings)


def test_default_target_is_auto(project):
    report = analyse(sources.load(project), Watched())
    assert report.target == "5.2"  # the newest LTS above 3.2


def test_file_argument(project):
    report = analyse(sources.load(project / "requirements.txt"), Watched(), "4.2")
    assert report.project_python == "3.7"
    assert [name for name, _ in report.external] == ["django-bar", "django-foo"]


def test_unusable_python_constraint_is_dropped(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "x"\nrequires-python = ">=3.x"\ndependencies = ["django==5.2.17"]\n',
    )
    deps = sources.load(tmp_path)
    assert (deps.python, deps.python_source) == (None, "")
    report = analyse(deps, Watched(), "6.1")
    assert report.project_python is None


def test_every_python_source_is_x_y(tmp_path):
    write(tmp_path / ".python-version", "3.12.4\n")
    write(tmp_path / "requirements.txt", "Django==5.2.17\n")
    assert sources.load(tmp_path).python == "3.12"


@posix_only
def test_direct_installs_in_an_environment_are_external(tmp_path):
    site = tmp_path / "site"
    for name, version, direct in [
        ("Django", "5.2.17", None),
        ("mysite", "0.1", {"url": "file:///srv/mysite", "dir_info": {"editable": True}}),
        (
            "django-bar",
            "1.0",
            {"url": "https://t:s@github.com/x/django-bar.git", "vcs_info": {"vcs": "git"}},
        ),
        ("django-baz", "2.0", {"url": "https://example.com/baz-2.0.whl", "archive_info": {}}),
    ]:
        dist = site / f"{name}-{version}.dist-info"
        write(dist / "METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        if direct:
            write(dist / "direct_url.json", json.dumps(direct))
    python = tmp_path / "python"
    python.write_text(f'#!/bin/sh\nPYTHONPATH={site} exec {sys.executable} "$@"\n')
    python.chmod(0o755)

    deps = sources.load(tmp_path, python=str(python))
    assert deps.dependencies["django"].external is None
    assert deps.dependencies["mysite"].external == "path /srv/mysite"
    assert deps.dependencies["django-bar"].external == "git https://github.com/x/django-bar.git"
    assert deps.dependencies["django-baz"].external == "url https://example.com/baz-2.0.whl"

    pypi = Watched()
    report = analyse(deps, pypi, "6.1")
    assert not {"mysite", "django-bar", "django-baz"} & pypi.names
    assert {"mysite", "django-bar", "django-baz"} <= {name for name, _ in report.external}
