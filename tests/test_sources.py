from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from django_upgrade_report import sources


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def versions(ds: sources.DependencySet) -> dict[str, str | None]:
    return {name: d.version for name, d in ds.dependencies.items()}


def test_requirements_txt(tmp_path):
    write(tmp_path / "requirements" / "base.txt", "Django==4.2.7  # the framework\n")
    write(
        tmp_path / "requirements.txt",
        """
-r requirements/base.txt
--index-url https://example.com/simple
django_debug_toolbar==4.2.0 \\
    --hash=sha256:abc
django-filter>=23
pywin32==306 ; sys_platform == "never"
-e git+https://github.com/x/y.git#egg=y
not a requirement !!
""",
    )
    ds = sources.load(tmp_path)
    assert versions(ds) == {
        "django": "4.2.7",
        "django-debug-toolbar": "4.2.0",
        "django-filter": None,
    }
    assert ds.dependencies["django-filter"].spec == ">=23"
    assert "requirements.txt" in ds.source


def test_uv_lock_wins_and_skips_the_project_itself(tmp_path):
    write(tmp_path / "requirements.txt", "Django==3.2\n")
    write(
        tmp_path / "uv.lock",
        """
version = 1
[[package]]
name = "myproject"
version = "0.1.0"
source = { editable = "." }

[[package]]
name = "django"
version = "4.2.7"
source = { registry = "https://pypi.org/simple" }
""",
    )
    ds = sources.load(tmp_path)
    assert ds.source == "uv.lock"
    assert versions(ds) == {"django": "4.2.7"}


def test_poetry_lock(tmp_path):
    write(
        tmp_path / "poetry.lock",
        '[[package]]\nname = "Django"\nversion = "4.2.7"\n\n'
        '[[package]]\nname = "django-environ"\nversion = "0.11.2"\n',
    )
    assert versions(sources.load(tmp_path)) == {"django": "4.2.7", "django-environ": "0.11.2"}


def test_pipfile_lock(tmp_path):
    write(
        tmp_path / "Pipfile.lock",
        json.dumps({"default": {"django": {"version": "==4.2.7"}}, "develop": {"x": {}}}),
    )
    assert versions(sources.load(tmp_path)) == {"django": "4.2.7", "x": None}


def test_pyproject_pep621_and_poetry(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        """
[project]
dependencies = ["Django==4.2.7", "django-extensions>=3"]
[project.optional-dependencies]
s3 = ["django-storages==1.14.2"]
[dependency-groups]
dev = ["django-debug-toolbar==4.2.0", {include-group = "x"}]
[tool.poetry.dependencies]
python = "^3.11"
django-filter = "23.3"
django-cors-headers = "^4.3"
celery = { version = "5.3.4", extras = ["redis"] }
""",
    )
    assert versions(sources.load(tmp_path)) == {
        "django": "4.2.7",
        "django-extensions": None,
        "django-storages": "1.14.2",
        "django-debug-toolbar": "4.2.0",
        "django-filter": "23.3",
        "django-cors-headers": None,
        "celery": "5.3.4",
    }


def test_environment():
    ds = sources.load(Path("."), python=sys.executable)
    assert "packaging" in ds.dependencies
    assert ds.dependencies["packaging"].version


def test_nothing_found(tmp_path):
    with pytest.raises(sources.NoDependenciesFound):
        sources.load(tmp_path)
