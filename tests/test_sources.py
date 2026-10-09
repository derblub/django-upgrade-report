from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from django_upgrade_report import sources

DATA = Path(__file__).parent / "data"
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def versions(ds: sources.DependencySet) -> dict[str, str | None]:
    return {name: d.version for name, d in ds.dependencies.items()}


def externals(ds: sources.DependencySet) -> dict[str, str]:
    return {name: d.external for name, d in ds.dependencies.items() if d.external}


def test_requirements_txt(tmp_path):
    write(tmp_path / "requirements" / "base.txt", "Django==4.2.7  # the framework\n")
    write(
        tmp_path / "requirements.txt",
        """
-r requirements/base.txt
--extra-index-url https://example.com/simple
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
        "y": None,
    }
    assert ds.dependencies["django-filter"].spec == ">=23"
    assert externals(ds) == {"y": "git https://github.com/x/y.git"}
    assert "requirements.txt" in ds.source


# --- 1. merging: a pin beats an unpinned entry, whatever the order -----------


def test_constraints_pin_but_do_not_add_packages(tmp_path):
    write(tmp_path / "requirements.txt", "-c constraints.txt\nDjango\ndjango-debug-toolbar\n")
    write(
        tmp_path / "constraints.txt",
        "Django==4.2.7\ndjango-debug-toolbar==4.2.0\ncelery==5.3.4\n",
    )
    assert versions(sources.load(tmp_path)) == {
        "django": "4.2.7",
        "django-debug-toolbar": "4.2.0",
    }


def test_constraints_from_an_included_file_apply_everywhere(tmp_path):
    write(tmp_path / "requirements" / "constraints.txt", "Django==4.2.7\nredis==5.0\n")
    write(tmp_path / "requirements" / "base.txt", "--constraint=constraints.txt\n")
    write(tmp_path / "requirements.txt", "-r requirements/base.txt\nDjango>=4.2\n")
    ds = sources.load(tmp_path)
    assert versions(ds) == {"django": "4.2.7"}
    assert "constraints" not in ds.source


@pytest.mark.parametrize(
    "text", ["-r base.txt\nDjango\n", "Django\n-r base.txt\n", "Django>=4\n-rbase.txt\n"]
)
def test_pin_from_include_wins_in_any_order(tmp_path, text):
    write(tmp_path / "base.txt", "Django==4.2.7\n")
    write(tmp_path / "requirements.txt", text)
    assert versions(sources.load(tmp_path)) == {"django": "4.2.7"}


def test_pin_wins_within_one_file(tmp_path):
    write(tmp_path / "requirements.txt", "django>=4.2\nDjango==4.2.7\ndjango\n")
    assert versions(sources.load(tmp_path)) == {"django": "4.2.7"}


def test_pin_wins_across_pyproject_tables(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        """
[project]
dependencies = ["django (==4.2.7)"]
[project.optional-dependencies]
pg = ["django[psycopg]>=4.2"]
[tool.poetry.dependencies]
django = { source = "private" }
""",
    )
    dep = sources.load(tmp_path).dependencies["django"]
    assert (dep.version, dep.external) == ("4.2.7", None)


# --- 2. encodings and broken files -------------------------------------------


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-be", "utf-32"])
def test_requirements_with_bom(tmp_path, encoding):
    text = "Django==4.2.7\r\ndjango-debug-toolbar==4.2.0\r\n"
    data = text.encode(encoding)
    if encoding == "utf-16-be":  # this codec writes no BOM of its own
        data = b"\xfe\xff" + data
    (tmp_path / "requirements.txt").write_bytes(data)
    assert versions(sources.load(tmp_path)) == {
        "django": "4.2.7",
        "django-debug-toolbar": "4.2.0",
    }


def test_toml_is_read_as_utf8_whatever_the_locale(tmp_path, monkeypatch):
    monkeypatch.setattr("locale.getpreferredencoding", lambda *a: "cp1252")
    text = '\ufeff[project]\nauthors = [{name = "Łukasz"}]\ndependencies = ["Django==4.2.7"]\n'
    (tmp_path / "pyproject.toml").write_bytes(text.encode())
    assert versions(sources.load(tmp_path)) == {"django": "4.2.7"}


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("requirements.txt", b"Django==4.2.7\n# M\xfcller\n", "not valid UTF-8"),
        ("pyproject.toml", b"[project\n", "not valid TOML"),
        ("uv.lock", b"version = \n", "not valid TOML"),
        ("Pipfile.lock", b"{", "not valid JSON"),
        ("Pipfile.lock", b"[]", "not a JSON object"),
    ],
)
def test_broken_files_raise_source_error(tmp_path, name, content, message):
    (tmp_path / name).write_bytes(content)
    with pytest.raises(sources.SourceError, match=message) as exc:
        sources.load(tmp_path)
    assert name in str(exc.value)


# --- 3. the project's Python -------------------------------------------------


def test_python_from_python_version_file(tmp_path):
    write(tmp_path / ".python-version", "# pinned\ncpython@3.11.4\n")
    write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.10"\n')
    write(tmp_path / "requirements.txt", "Django==4.2.7\n")
    ds = sources.load(tmp_path)
    assert (ds.python, ds.python_source) == ("3.11", ".python-version")


def test_python_from_uv_lock(tmp_path):
    write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.12"\n')
    write(
        tmp_path / "uv.lock",
        'version = 1\nrequires-python = ">=3.10, <4"\n'
        '[[package]]\nname = "django"\nversion = "5.2"\n',
    )
    ds = sources.load(tmp_path)
    assert (ds.python, ds.python_source) == ("3.10", "uv.lock requires-python")


@pytest.mark.parametrize(
    ("pyproject", "expected"),
    [
        ('[project]\nrequires-python = "~=3.11.2"', ("3.11", "pyproject.toml requires-python")),
        ('[project]\nrequires-python = ">3.9,!=3.10.*"', ("3.9", "pyproject.toml requires-python")),
        ('[project]\nrequires-python = "<4"', (None, "")),
        ('[tool.poetry.dependencies]\npython = "^3.10"', ("3.10", "pyproject.toml Poetry python")),
        (
            '[tool.poetry.dependencies]\npython = "~3.9 || >=3.11,<4"',
            ("3.9", "pyproject.toml Poetry python"),
        ),
    ],
)
def test_python_from_pyproject(tmp_path, pyproject, expected):
    write(tmp_path / "pyproject.toml", pyproject + "\n")
    write(tmp_path / "requirements.txt", "Django==4.2.7\n")
    ds = sources.load(tmp_path)
    assert (ds.python, ds.python_source) == expected


def test_python_from_pipfile_lock(tmp_path):
    lock = {
        "_meta": {"requires": {"python_version": "3.8"}},
        "default": {"django": {"version": "==3.2.25"}},
    }
    write(tmp_path / "Pipfile.lock", json.dumps(lock))
    ds = sources.load(tmp_path)
    assert (ds.python, ds.python_source) == ("3.8", "Pipfile.lock python_version")


def test_python_unknown(tmp_path):
    write(tmp_path / "requirements.txt", "Django==4.2.7\n")
    ds = sources.load(tmp_path)
    assert (ds.python, ds.python_source) == (None, "")


def test_requirement_markers_use_the_project_python(tmp_path):
    write(tmp_path / ".python-version", "3.8\n")
    write(
        tmp_path / "requirements.txt",
        'Django==3.2.25 ; python_version < "3.10"\nDjango==5.2 ; python_version >= "3.10"\n',
    )
    assert versions(sources.load(tmp_path)) == {"django": "3.2.25"}


# --- 4. several lockfile entries for one package -----------------------------


@pytest.mark.parametrize(
    ("python_version", "django", "asgiref"),
    [
        (None, "4.2.30", "3.11.1"),  # from uv.lock: requires-python = ">=3.9"
        ("3.9", "4.2.30", "3.11.1"),
        ("3.11", "5.2.17", "3.12.1"),
        ("3.13", "6.1.1", "3.12.1"),
    ],
)
def test_uv_forked_resolution(tmp_path, python_version, django, asgiref):
    # Written by uv 0.8.17 for requires-python >=3.9, django>=4.2 and six from git.
    shutil.copy(DATA / "uv-forked.lock", tmp_path / "uv.lock")
    if python_version:
        write(tmp_path / ".python-version", python_version + "\n")
    ds = sources.load(tmp_path)
    assert versions(ds)["django"] == django
    assert versions(ds)["asgiref"] == asgiref
    assert "p" not in ds.dependencies  # the project itself
    assert ds.dependencies["six"].external == "git https://github.com/benjaminp/six"


def test_fork_without_known_python_picks_the_lowest_python(tmp_path):
    write(
        tmp_path / "uv.lock",
        """
version = 1
[[package]]
name = "django"
version = "6.1.1"
resolution-markers = ["python_full_version >= '3.12'"]
[[package]]
name = "django"
version = "4.2.30"
resolution-markers = ["python_full_version < '3.10'"]
""",
    )
    assert versions(sources.load(tmp_path)) == {"django": "4.2.30"}


def test_poetry_lock_duplicates_follow_markers(tmp_path):
    write(tmp_path / "pyproject.toml", '[tool.poetry.dependencies]\npython = "^3.11"\n')
    write(
        tmp_path / "poetry.lock",
        """
[[package]]
name = "django"
version = "4.2.30"
python-versions = ">=3.8"
markers = "python_version < \\"3.10\\""

[[package]]
name = "django"
version = "5.2.17"
python-versions = ">=3.10"
markers = "python_version >= \\"3.10\\""
""",
    )
    assert versions(sources.load(tmp_path)) == {"django": "5.2.17"}


def test_pdm_lock_duplicates_follow_requires_python(tmp_path):
    write(
        tmp_path / "pdm.lock",
        """
[[package]]
name = "django"
version = "6.1.1"
requires_python = ">=3.12"

[[package]]
name = "django"
version = "5.2.17"
requires_python = ">=3.10"
""",
    )
    write(tmp_path / ".python-version", "3.11\n")
    assert versions(sources.load(tmp_path)) == {"django": "5.2.17"}


# --- 5. packages that are not from PyPI --------------------------------------


def test_uv_lock_sources(tmp_path):
    write(
        tmp_path / "uv.lock",
        """
version = 1
[manifest]
members = ["myproject", "shared"]

[[package]]
name = "myproject"
version = "0.1.0"
source = { editable = "." }

[[package]]
name = "shared"
version = "0.1.0"
source = { editable = "packages/shared" }

[[package]]
name = "django"
version = "4.2.7"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "django-git"
version = "1.0"
source = { git = "https://user:token@github.com/x/django-git?rev=main#abc123" }

[[package]]
name = "django-local"
version = "0.3"
source = { path = "../libs/django-local" }

[[package]]
name = "django-dir"
version = "0.3"
source = { directory = "../libs/django-dir" }

[[package]]
name = "django-editable"
version = "0.3"
source = { editable = "../libs/django-editable" }

[[package]]
name = "django-wheel"
version = "2.0"
source = { url = "https://files.example.com/django_wheel-2.0-py3-none-any.whl" }

[[package]]
name = "django-private"
version = "3.0"
source = { registry = "https://pkgs.example.com/simple" }
""",
    )
    ds = sources.load(tmp_path)
    assert versions(ds)["django"] == "4.2.7"
    assert "myproject" not in ds.dependencies
    assert "shared" not in ds.dependencies
    assert externals(ds) == {
        "django-git": "git https://github.com/x/django-git",
        "django-local": "path ../libs/django-local",
        "django-dir": "path ../libs/django-dir",
        "django-editable": "path ../libs/django-editable",
        "django-wheel": "url https://files.example.com/django_wheel-2.0-py3-none-any.whl",
        "django-private": "index https://pkgs.example.com/simple",
    }


def test_poetry_lock_sources(tmp_path):
    entries = [
        ("django", None),
        ("django-git", 'type = "git"\nurl = "https://github.com/x/django-git.git"'),
        ("django-dir", 'type = "directory"\nurl = "../libs/django-dir"'),
        ("django-file", 'type = "file"\nurl = "dist/django_file-1.0.tar.gz"'),
        ("django-url", 'type = "url"\nurl = "https://example.com/django_url-1.0.whl"'),
        ("django-private", 'type = "legacy"\nurl = "https://pkgs.example.com/simple"'),
        ("django-mirror", 'type = "legacy"\nurl = "https://pypi.org/simple/"'),
    ]
    text = ""
    for name, source in entries:
        text += f'[[package]]\nname = "{name}"\nversion = "1.0"\n'
        if source:
            text += f"[package.source]\n{source}\n"
    write(tmp_path / "poetry.lock", text)
    assert externals(sources.load(tmp_path)) == {
        "django-git": "git https://github.com/x/django-git.git",
        "django-dir": "path ../libs/django-dir",
        "django-file": "path dist/django_file-1.0.tar.gz",
        "django-url": "url https://example.com/django_url-1.0.whl",
        "django-private": "index https://pkgs.example.com/simple",
    }


def test_pdm_lock_sources(tmp_path):
    write(
        tmp_path / "pdm.lock",
        """
[[package]]
name = "django"
version = "4.2.7"

[[package]]
name = "django-git"
version = "1.0"
git = "https://github.com/x/django-git.git"
revision = "abc123"

[[package]]
name = "django-local"
version = "0.3"
path = "../libs/django-local"

[[package]]
name = "django-url"
version = "2.0"
url = "https://example.com/django_url-2.0.whl"
""",
    )
    assert externals(sources.load(tmp_path)) == {
        "django-git": "git https://github.com/x/django-git.git",
        "django-local": "path ../libs/django-local",
        "django-url": "url https://example.com/django_url-2.0.whl",
    }


def test_pipfile_lock(tmp_path):
    write(
        tmp_path / "Pipfile.lock",
        json.dumps({"default": {"django": {"version": "==4.2.7"}}, "develop": {"x": {}}}),
    )
    assert versions(sources.load(tmp_path)) == {"django": "4.2.7", "x": None}


def test_pipfile_lock_sources(tmp_path):
    default = {
        "django": {"version": "==4.2.7"},
        "django-git": {"git": "https://github.com/x/django-git.git", "ref": "abc"},
        "django-local": {"path": "../libs/django-local", "editable": True},
        "django-file": {"file": "https://example.com/django_file-1.0.whl"},
        "myproject": {"path": ".", "editable": True},
    }
    write(tmp_path / "Pipfile.lock", json.dumps({"default": default}))
    ds = sources.load(tmp_path)
    assert "myproject" not in ds.dependencies
    assert externals(ds) == {
        "django-git": "git https://github.com/x/django-git.git",
        "django-local": "path ../libs/django-local",
        "django-file": "url https://example.com/django_file-1.0.whl",
    }


def test_requirement_lines_not_from_pypi(tmp_path):
    write(tmp_path / "libs" / "app" / "pyproject.toml", '[project]\nname = "Django-App"\n')
    write(tmp_path / "libs" / "legacy" / "setup.cfg", "[metadata]\nname = django-legacy\n")
    write(
        tmp_path / "requirements.txt",
        """
-e .
-e ./libs/app[extra]
--editable=libs/legacy
-e ./libs/unknown
-e git+https://github.com/x/edit.git@main#egg=django-edit
django-at @ git+https://token@github.com/x/at.git@v1
django-whl @ https://example.com/django_whl-1.0-py3-none-any.whl
git+https://github.com/x/bare.git#egg=django_bare
https://example.com/nameless.tar.gz
Django==4.2.7
""",
    )
    ds = sources.load(tmp_path)
    assert externals(ds) == {
        "django-app": "path ./libs/app",
        "django-legacy": "path libs/legacy",
        "django-edit": "git https://github.com/x/edit.git@main",
        "django-at": "git https://github.com/x/at.git@v1",
        "django-whl": "url https://example.com/django_whl-1.0-py3-none-any.whl",
        "django-bare": "git https://github.com/x/bare.git",
        "unknown": "path ./libs/unknown",
        "nameless": "url https://example.com/nameless.tar.gz",
    }
    assert versions(ds)["django"] == "4.2.7"


@pytest.mark.parametrize(
    ("line", "name", "where"),
    [
        ("https://x.example/pkg.zip", "pkg", "url https://x.example/pkg.zip"),
        (
            "https://x.example/files/django_thing-1.0-py3-none-any.whl",
            "django-thing",
            "url https://x.example/files/django_thing-1.0-py3-none-any.whl",
        ),
        ("git+https://github.com/x/django-repo.git@v1", "django-repo", None),
        ("-e git+https://github.com/x/edit-me.git", "edit-me", None),
    ],
)
def test_requirement_url_without_egg_is_named_after_the_file(tmp_path, line, name, where):
    """Regression: a URL without #egg= was silently left out of the report."""
    write(tmp_path / "requirements.txt", f"Django==4.2.7\n{line}\n")
    found = externals(sources.load(tmp_path))
    assert name in found
    if where:
        assert found[name] == where


def test_pyproject_sources_not_from_pypi(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        """
[project]
dependencies = [
    "Django==4.2.7",
    "django-uv-git",
    "django-uv-index==2.0",
    "member",
    "django-at @ git+https://github.com/x/at",
]
[tool.uv.sources]
django-uv-git = { git = "https://github.com/x/uv-git" }
django-uv-index = { index = "private" }
member = { workspace = true }
[[tool.uv.index]]
name = "private"
url = "https://pkgs.example.com/simple"

[tool.poetry.dependencies]
django-poetry-path = { path = "../libs/p", develop = true }
django-poetry-src = { version = "1.0", source = "corp" }
[[tool.poetry.source]]
name = "corp"
url = "https://corp.example.com/simple/"
""",
    )
    ds = sources.load(tmp_path)
    assert "member" not in ds.dependencies
    assert ds.dependencies["django-uv-index"].version == "2.0"
    assert externals(ds) == {
        "django-uv-git": "git https://github.com/x/uv-git",
        "django-uv-index": "index https://pkgs.example.com/simple",
        "django-at": "git https://github.com/x/at",
        "django-poetry-path": "path ../libs/p",
        "django-poetry-src": "index https://corp.example.com/simple/",
    }


# --- 6. the project argument may be a file -----------------------------------


def test_project_argument_is_a_requirements_file(tmp_path):
    write(tmp_path / "base.txt", "Django==4.2.7\n")
    path = write(tmp_path / "requirements-prod.txt", "-r base.txt\ngunicorn\n")
    ds = sources.load(path)
    assert versions(ds) == {"django": "4.2.7", "gunicorn": None}
    assert ds.source == str(path)


def test_project_argument_is_a_lockfile(tmp_path):
    write(tmp_path / "requirements.txt", "Django==3.2\n")
    path = write(tmp_path / "poetry.lock", '[[package]]\nname = "Django"\nversion = "4.2.7"\n')
    assert versions(sources.load(path)) == {"django": "4.2.7"}


def test_project_argument_is_pyproject(tmp_path):
    write(tmp_path / "requirements.txt", "Django==3.2\n")
    path = write(tmp_path / "pyproject.toml", '[project]\ndependencies = ["Django==4.2.7"]\n')
    assert versions(sources.load(path)) == {"django": "4.2.7"}


def test_project_argument_is_pipfile_lock(tmp_path):
    lock = {"default": {"django": {"version": "==4.2.7"}}}
    path = write(tmp_path / "Pipfile.lock", json.dumps(lock))
    assert versions(sources.load(path)) == {"django": "4.2.7"}


def test_project_argument_is_an_unsupported_file(tmp_path):
    path = write(tmp_path / "setup.py", "")
    with pytest.raises(sources.NoDependenciesFound, match="pdm.lock"):
        sources.load(path)


def test_project_argument_does_not_exist(tmp_path):
    with pytest.raises(sources.NoDependenciesFound, match="does not exist"):
        sources.load(tmp_path / "missing")


# --- 7. empty lockfiles and nothing found ------------------------------------

ROOT_ONLY_UV_LOCK = """
version = 1
[[package]]
name = "p"
version = "0.1"
source = { virtual = "." }
"""


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("uv.lock", ROOT_ONLY_UV_LOCK),
        ("poetry.lock", "[metadata]\n"),
        ("pdm.lock", ""),
        ("Pipfile.lock", "{}"),
    ],
)
def test_empty_lockfile_is_an_error(tmp_path, name, text):
    write(tmp_path / "requirements.txt", "Django==4.2.7\n")
    write(tmp_path / name, text)
    with pytest.raises(sources.NoDependenciesFound, match=f"{name} lists no packages"):
        sources.load(tmp_path)


def test_legacy_uv_lock_distribution_key(tmp_path):
    write(
        tmp_path / "uv.lock",
        'version = 1\n[[distribution]]\nname = "django"\nversion = "4.2.7"\n'
        'source = { registry = "https://pypi.org/simple" }\n',
    )
    assert versions(sources.load(tmp_path)) == {"django": "4.2.7"}


def test_nothing_found(tmp_path):
    with pytest.raises(sources.NoDependenciesFound) as exc:
        sources.load(tmp_path)
    for name in ("uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock", "requirements", "pyproject"):
        assert name in str(exc.value)


# --- 8. --python -------------------------------------------------------------


def test_environment():
    ds = sources.load(Path("."), python=sys.executable)
    assert "packaging" in ds.dependencies
    assert ds.dependencies["packaging"].version
    assert ds.python == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert ds.python_source == "--python"


def fake_interpreter(tmp_path: Path, script: str) -> str:
    path = tmp_path / "python"
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return str(path)


def dist_info(site: Path, name: str, version: str) -> None:
    write(
        site / f"{name}-{version}.dist-info" / "METADATA",
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
    )


@posix_only
def test_environment_first_distribution_on_sys_path_wins(tmp_path):
    dist_info(tmp_path / "venv_site", "Django", "4.2.7")
    dist_info(tmp_path / "system_site", "django", "3.2.25")
    sites = f"{tmp_path}/venv_site:{tmp_path}/system_site"
    python = fake_interpreter(tmp_path, f'PYTHONPATH={sites} exec {sys.executable} "$@"')
    assert sources.from_environment(python).dependencies["django"].version == "4.2.7"


@posix_only
def test_environment_without_importlib_metadata(tmp_path):
    """Python <= 3.7 has no importlib.metadata; the helper falls back to pkg_resources."""
    stubs = tmp_path / "stubs"
    write(stubs / "importlib" / "__init__.py", "")  # hides importlib.metadata
    write(
        stubs / "pkg_resources.py",
        "class D:\n"
        "    def __init__(self, name, version):\n"
        "        self.project_name, self.version = name, version\n"
        "working_set = [D('Django', '2.2.28'), D('django', '1.11')]\n",
    )
    python = fake_interpreter(tmp_path, f'PYTHONPATH={stubs} exec {sys.executable} -S "$@"')
    ds = sources.from_environment(python)
    assert ds.dependencies == {"django": sources.Dependency("django", "2.2.28")}


@posix_only
def test_environment_failure_shows_stderr_not_the_script(tmp_path):
    python = fake_interpreter(
        tmp_path,
        'echo "Traceback (most recent call last):" >&2\n'
        'echo "ModuleNotFoundError: No module named importlib.metadata" >&2\n'
        "exit 1",
    )
    with pytest.raises(sources.NoDependenciesFound) as exc:
        sources.from_environment(python)
    assert "No module named importlib.metadata" in str(exc.value)
    assert "distributions" not in str(exc.value)


def test_environment_missing_interpreter(tmp_path):
    with pytest.raises(sources.NoDependenciesFound, match="Could not run"):
        sources.from_environment(str(tmp_path / "nope"))


# --- 9. Poetry multiple-constraint dependencies ------------------------------


@pytest.mark.parametrize(
    ("python", "spec"), [("^3.11", ">=5.0,<6"), ("^3.8", ">=4.2,<5"), (None, ">=4.2,<5")]
)
def test_poetry_multiple_constraints(tmp_path, python, spec):
    python_line = f'python = "{python}"\n' if python else ""
    write(
        tmp_path / "pyproject.toml",
        "[tool.poetry.dependencies]\n"
        + python_line
        + 'django = [\n  {version = "^4.2", python = "<3.10"},\n'
        '  {version = "^5.0", python = ">=3.10"},\n]\n',
    )
    dep = sources.load(tmp_path).dependencies["django"]
    assert (dep.version, dep.spec) == (None, spec)


# --- lockfiles, pyproject.toml -----------------------------------------------


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


# --- private indexes never see pypi.org (roadmap 6) ----------------------------


def test_pipfile_lock_packages_from_a_private_index(tmp_path):
    lock = {
        "_meta": {
            "sources": [
                {"name": "pypi", "url": "https://pypi.org/simple", "verify_ssl": True},
                {"name": "acme", "url": "https://tok:s3cret@pkgs.acme.example/simple"},
            ]
        },
        "default": {
            "django": {"index": "pypi", "version": "==4.2.7"},
            "acme-private": {"index": "acme", "version": "==1.0"},
            "django-filter": {"index": "pypi", "version": "==23.3"},
            "no-index-given": {"version": "==2.0"},
        },
    }
    write(tmp_path / "Pipfile.lock", json.dumps(lock))
    assert externals(sources.load(tmp_path)) == {
        "acme-private": "index https://pkgs.acme.example/simple"
    }


def test_pipfile_lock_whose_first_source_is_private(tmp_path):
    lock = {
        "_meta": {"sources": [{"name": "acme", "url": "https://pkgs.acme.example/simple"}]},
        "default": {"django": {"version": "==4.2.7"}, "acme-private": {"version": "==1.0"}},
    }
    write(tmp_path / "Pipfile.lock", json.dumps(lock))
    assert externals(sources.load(tmp_path)) == {
        "acme-private": "index https://pkgs.acme.example/simple"
    }


@pytest.mark.parametrize(
    "option",
    [
        "--index-url https://tok:s3cret@pkgs.acme.example/simple",
        "-i https://pkgs.acme.example/simple",
        "--index-url=https://pkgs.acme.example/simple",
    ],
)
def test_requirements_index_url_replaces_pypi(tmp_path, option):
    write(tmp_path / "base.txt", "acme-base==2.0\n")
    write(
        tmp_path / "requirements.txt", f"{option}\n-r base.txt\nDjango==5.2.17\nacme-private==1.0\n"
    )
    ds = sources.load(tmp_path)
    where = "index https://pkgs.acme.example/simple"
    assert externals(ds) == {"acme-private": where, "acme-base": where}
    assert versions(ds)["django"] == "5.2.17"


@pytest.mark.parametrize(
    "url",
    [
        "https://pypi.org/simple/",
        "https://pypi.org:443/simple",
        "https://pypi.python.org/simple",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "https://mirrors.aliyun.com/pypi/simple/",
        "http://pypi.douban.com/simple",
    ],
)
def test_requirements_index_url_to_pypi_or_a_public_mirror_changes_nothing(tmp_path, url):
    write(tmp_path / "requirements.txt", f"-i {url}\ndjango-filter==23.3\n")
    assert externals(sources.load(tmp_path)) == {}


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("uv.lock", '[[package]]\nname = "django"\nversion = 5\n'),
        (
            "Pipfile.lock",
            json.dumps({"default": {"django-x": {"version": "==1.0", "index": ["a"]}}}),
        ),
        (
            "pyproject.toml",
            '[tool.poetry.dependencies]\ndjango = {version = "^4.2", source = ["x"]}\n',
        ),
    ],
)
def test_malformed_values_are_a_source_error(tmp_path, name, text):
    """Regression: these crashed with "unexpected TypeError ... Please report this"."""
    write(tmp_path / name, text)
    with pytest.raises(sources.SourceError, match="Regenerate it"):
        sources.load(tmp_path)


def test_uv_default_index_replaces_pypi(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        """
[project]
dependencies = ["Django==5.2.17", "acme-uvdefault-private>=1", "django-git"]
[tool.uv.sources]
django-git = { git = "https://github.com/x/git" }
[[tool.uv.index]]
name = "acme"
url = "https://pkgs.acme.example/simple"
default = true
""",
    )
    assert externals(sources.load(tmp_path)) == {
        "acme-uvdefault-private": "index https://pkgs.acme.example/simple",
        "django-git": "git https://github.com/x/git",
    }


@pytest.mark.parametrize(
    ("source", "private"),
    [
        ('priority = "primary"', True),
        ("", True),  # primary is the default priority
        ("default = true", True),  # Poetry < 1.5
        ('priority = "supplemental"', False),
        ('priority = "explicit"', False),
        ("secondary = true", False),
    ],
)
def test_poetry_primary_source_replaces_pypi(tmp_path, source, private):
    write(
        tmp_path / "pyproject.toml",
        f"""
[tool.poetry.dependencies]
python = "^3.11"
Django = "5.2.17"
acme-poetryprimary-private = "^1.0"
[[tool.poetry.source]]
name = "acme"
url = "https://pkgs.acme.example/simple"
{source}
""",
    )
    expected = {"acme-poetryprimary-private": "index https://pkgs.acme.example/simple"}
    assert externals(sources.load(tmp_path)) == (expected if private else {})


def test_poetry_with_pypi_as_primary_too_keeps_pypi(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        """
[tool.poetry.dependencies]
acme-private = "^1.0"
[[tool.poetry.source]]
name = "acme"
url = "https://pkgs.acme.example/simple"
priority = "primary"
[[tool.poetry.source]]
name = "PyPI"
priority = "primary"
""",
    )
    assert externals(sources.load(tmp_path)) == {}


# --- Poetry version syntax (bug #6) ---------------------------------------------


@pytest.mark.parametrize(
    ("constraint", "spec"),
    [
        ("^2.4", ">=2.4,<3"),
        ("~2.4", ">=2.4,<2.5"),
        ("4.2.*", "==4.2.*"),
        (">=2.4,<3.0", ">=2.4,<3.0"),
        ("^1.0 || ^3.0", ">=1.0,<2 || >=3.0,<4"),
        ("*", ""),
    ],
)
def test_poetry_constraints_become_pep440(tmp_path, constraint, spec):
    write(
        tmp_path / "pyproject.toml",
        f'[tool.poetry.dependencies]\npython = "^3.9"\ndjango-filter = "{constraint}"\n',
    )
    dep = sources.load(tmp_path).dependencies["django-filter"]
    assert (dep.version, dep.spec) == (None, spec)


# --- local paths are listed, never dropped ---------------------------------------


def test_local_paths_in_requirements(tmp_path):
    write(tmp_path / "libs" / "named" / "pyproject.toml", '[project]\nname = "django-named"\n')
    write(
        tmp_path / "requirements.txt",
        "-e ./localpkg\n./libs/named\n../elsewhere/dist/django_arch-1.0.tar.gz\n.\nDjango==4.2.7\n",
    )
    assert externals(sources.load(tmp_path)) == {
        "localpkg": "path ./localpkg",
        "django-named": "path ./libs/named",
        "django-arch": "path ../elsewhere/dist/django_arch-1.0.tar.gz",
    }


# --- --python with wrappers that talk -------------------------------------------


@posix_only
def test_environment_behind_a_chatty_wrapper(tmp_path):
    dist_info(tmp_path / "site", "Django", "4.2.7")
    python = fake_interpreter(
        tmp_path,
        f'echo "Activating venv..."\nPYTHONPATH={tmp_path}/site exec {sys.executable} "$@"',
    )
    assert sources.from_environment(python).dependencies["django"].version == "4.2.7"


@posix_only
def test_environment_unexpected_output_is_short(tmp_path):
    python = fake_interpreter(tmp_path, "printf '%0500d' 0")
    with pytest.raises(sources.NoDependenciesFound, match="unexpected output") as exc:
        sources.from_environment(python)
    assert len(str(exc.value)) < 400


@posix_only
def test_environment_with_a_distribution_without_name(tmp_path):
    write(tmp_path / "site" / "broken-1.0.dist-info" / "METADATA", "Metadata-Version: 2.1\n")
    dist_info(tmp_path / "site", "Django", "4.2.7")
    python = fake_interpreter(
        tmp_path, f'PYTHONPATH={tmp_path}/site exec {sys.executable} -W error "$@"'
    )
    assert sources.from_environment(python).dependencies["django"].version == "4.2.7"


# --- project-wide indexes -----------------------------------------------------

PRIVATE = "index https://pkgs.example.com/simple"


def origins(ds: sources.DependencySet) -> dict[str, str | None]:
    return {name: d.external for name, d in ds.dependencies.items()}


def test_requirements_no_index_keeps_every_name_off_pypi(tmp_path):
    write(
        tmp_path / "requirements.txt",
        "--no-index\n--find-links ./wheels\nDjango==4.2.7\nacme==1.0\n",
    )
    ds = sources.load(tmp_path)
    assert origins(ds) == {"django": None, "acme": "local files (no index)"}


def test_pip_index_url_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://pkgs.example.com/simple")
    write(tmp_path / "requirements.txt", "Django==4.2.7\nacme==1.0\n")
    assert origins(sources.load(tmp_path))["acme"] == PRIVATE


def test_uv_default_index_from_the_environment_wins_over_pip(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://pypi.org/simple")
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://pkgs.example.com/simple")
    write(tmp_path / "requirements.txt", "acme==1.0\n")
    assert origins(sources.load(tmp_path))["acme"] == PRIVATE


def test_no_index_flag_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("UV_NO_INDEX", "1")
    write(tmp_path / "requirements.txt", "acme==1.0\n")
    assert origins(sources.load(tmp_path))["acme"] == "local files (no index)"


def test_environment_index_does_not_override_a_lockfile(tmp_path, monkeypatch):
    """uv.lock records where each package comes from; the environment does not change that."""
    monkeypatch.setenv("PIP_INDEX_URL", "https://pkgs.example.com/simple")
    write(
        tmp_path / "uv.lock",
        'version = 1\n[[package]]\nname = "acme"\nversion = "1.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n',
    )
    assert origins(sources.load(tmp_path)) == {"acme": None}


def test_legacy_uv_index_url_in_pyproject(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        '[project]\ndependencies = ["acme==1.0"]\n'
        '[tool.uv]\nindex-url = "https://pkgs.example.com/simple"\n',
    )
    assert origins(sources.load(tmp_path))["acme"] == PRIVATE


def test_uv_no_index_in_pyproject(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        '[project]\ndependencies = ["acme==1.0"]\n[tool.uv]\nno-index = true\n',
    )
    assert origins(sources.load(tmp_path))["acme"] == "local files (no index)"


def test_pdm_source_named_pypi_replaces_pypi_for_the_lockfile(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        '[project]\ndependencies = ["acme"]\n'
        '[[tool.pdm.source]]\nname = "pypi"\nurl = "https://pkgs.example.com/simple"\n',
    )
    write(
        tmp_path / "pdm.lock",
        '[[package]]\nname = "django"\nversion = "4.2.7"\n\n'
        '[[package]]\nname = "acme"\nversion = "1.0"\n',
    )
    assert origins(sources.load(tmp_path)) == {"django": None, "acme": PRIVATE}


def test_a_public_mirror_in_the_environment_still_counts_as_pypi(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://pypi.org/simple")
    write(tmp_path / "requirements.txt", "acme==1.0\n")
    assert origins(sources.load(tmp_path)) == {"acme": None}


def test_download_link_without_a_package_name_is_listed_under_its_url(tmp_path):
    write(
        tmp_path / "requirements.txt",
        "https://x.example/download?id=3\nhttps://x.example/pkg-1.0.zip\n",
    )
    deps = sources.load(tmp_path).dependencies
    assert deps["pkg"].external == "url https://x.example/pkg-1.0.zip"
    assert deps["https://x.example/download"].external == "url https://x.example/download"
    assert "download" not in deps


# --- what packages not from PyPI declare themselves ---------------------------


def test_local_directory_metadata_comes_along(tmp_path):
    write(
        tmp_path / "vendor" / "taggit" / "pyproject.toml",
        '[project]\nname = "django-taggit"\nversion = "3.0+fork"\n'
        'dependencies = ["Django>=3.2,<4.1"]\n'
        'classifiers = ["Framework :: Django :: 4.0"]\nrequires-python = ">=3.8"\n',
    )
    write(tmp_path / "requirements.txt", "Django==4.2.7\n-e ./vendor/taggit\n")
    meta = sources.load(tmp_path).dependencies["django-taggit"].metadata
    assert meta is not None
    assert (meta.name, meta.version, meta.requires_python) == ("django-taggit", "3.0+fork", ">=3.8")
    assert meta.requires_dist == ("Django>=3.2,<4.1",)
    assert meta.classifiers == ("Framework :: Django :: 4.0",)


def test_dynamic_metadata_is_not_guessed(tmp_path):
    write(
        tmp_path / "app" / "pyproject.toml",
        '[project]\nname = "app"\ndynamic = ["dependencies", "classifiers"]\n',
    )
    write(tmp_path / "requirements.txt", "./app\n")
    assert sources.load(tmp_path).dependencies["app"].metadata is None


def test_uv_lock_directory_source_reads_its_pyproject(tmp_path):
    write(
        tmp_path / "libs" / "fork" / "pyproject.toml",
        '[project]\nname = "django-fork"\nversion = "1.0"\ndependencies = ["django<5"]\n',
    )
    write(
        tmp_path / "uv.lock",
        'version = 1\n[[package]]\nname = "django-fork"\nversion = "1.0"\n'
        'source = { editable = "libs/fork" }\n',
    )
    dep = sources.load(tmp_path).dependencies["django-fork"]
    assert dep.external == "path libs/fork"
    assert dep.metadata is not None and dep.metadata.requires_dist == ("django<5",)


def test_poetry_lock_git_package_keeps_its_constraints(tmp_path):
    write(
        tmp_path / "poetry.lock",
        """
[[package]]
name = "django-taggit"
version = "3.0.0"
python-versions = ">=3.8"

[package.dependencies]
Django = "^3.2"
pillow = {version = ">=9", optional = true}

[package.source]
type = "git"
url = "https://github.com/someone/django-taggit.git"
reference = "HEAD"
resolved_reference = "3f2a1c9"
""",
    )
    dep = sources.load(tmp_path).dependencies["django-taggit"]
    assert dep.external == "git https://github.com/someone/django-taggit.git"
    assert dep.metadata is not None
    assert dep.metadata.requires_dist == ("Django (>=3.2,<4)",)


def test_pdm_lock_git_package_keeps_its_dependencies(tmp_path):
    write(
        tmp_path / "pdm.lock",
        """
[[package]]
name = "django-taggit"
version = "3.0.0"
requires_python = ">=3.8"
git = "https://github.com/someone/django-taggit.git"
revision = "3f2a1c9"
dependencies = ["Django<4.1,>=3.2"]
""",
    )
    meta = sources.load(tmp_path).dependencies["django-taggit"].metadata
    assert meta is not None and meta.requires_dist == ("Django<4.1,>=3.2",)


@posix_only
def test_environment_reads_the_metadata_of_direct_installs_only(tmp_path):
    site = tmp_path / "site"
    write(
        site / "django_taggit-3.0.0.dist-info" / "METADATA",
        "Metadata-Version: 2.1\nName: django-taggit\nVersion: 3.0.0\n"
        "Requires-Python: >=3.8\nRequires-Dist: Django<4.1,>=3.2\n"
        "Classifier: Framework :: Django :: 4.0\n",
    )
    write(
        site / "django_taggit-3.0.0.dist-info" / "direct_url.json",
        '{"url": "https://github.com/someone/django-taggit", '
        '"vcs_info": {"vcs": "git", "commit_id": "3f2a1c9"}}',
    )
    write(
        site / "django_ready-1.0.dist-info" / "METADATA",
        "Metadata-Version: 2.1\nName: django-ready\nVersion: 1.0\nRequires-Dist: Django>=4.2\n",
    )
    python = fake_interpreter(tmp_path, f'PYTHONPATH={site} exec {sys.executable} "$@"')
    deps = sources.from_environment(python).dependencies
    fork = deps["django-taggit"]
    assert fork.external == "git https://github.com/someone/django-taggit"
    assert fork.metadata is not None
    assert fork.metadata.requires_dist == ("Django<4.1,>=3.2",)
    assert fork.metadata.classifiers == ("Framework :: Django :: 4.0",)
    assert deps["django-ready"].metadata is None  # from an index: looked up there instead


# --- direct and transitive dependencies -----------------------------------------------


def direct(ds: sources.DependencySet) -> dict[str, bool | None]:
    return {name: d.direct for name, d in ds.dependencies.items()}


UV_LOCK = """
version = 1
requires-python = ">=3.10"

[[package]]
name = "app"
version = "0.1.0"
source = { virtual = "." }
dependencies = [{ name = "django" }]

[package.optional-dependencies]
api = [{ name = "djangorestframework" }]

[package.dev-dependencies]
dev = [{ name = "django-debug-toolbar" }]

[[package]]
name = "django"
version = "5.2"
source = { registry = "https://pypi.org/simple" }
dependencies = [{ name = "asgiref" }]

[[package]]
name = "asgiref"
version = "3.8.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "djangorestframework"
version = "3.16.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "django-debug-toolbar"
version = "5.1.0"
source = { registry = "https://pypi.org/simple" }
"""


def test_uv_lock_direct_dependencies(tmp_path):
    write(tmp_path / "uv.lock", UV_LOCK)
    assert direct(sources.load(tmp_path)) == {
        "django": True,
        "asgiref": False,
        "djangorestframework": True,
        "django-debug-toolbar": True,
    }


def test_uv_workspace_members_are_roots(tmp_path):
    write(
        tmp_path / "uv.lock",
        """
version = 1

[manifest]
members = ["app", "lib"]

[[package]]
name = "app"
version = "0.1.0"
source = { editable = "." }
dependencies = [{ name = "lib" }, { name = "django" }]

[[package]]
name = "lib"
version = "0.1.0"
source = { editable = "packages/lib" }
dependencies = [{ name = "django-filter" }]

[[package]]
name = "django"
version = "5.2"
source = { registry = "https://pypi.org/simple" }
dependencies = [{ name = "sqlparse" }]

[[package]]
name = "django-filter"
version = "25.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "sqlparse"
version = "0.5.0"
source = { registry = "https://pypi.org/simple" }
""",
    )
    assert direct(sources.load(tmp_path)) == {
        "django": True,
        "django-filter": True,
        "sqlparse": False,
    }


POETRY_LOCK = """
[[package]]
name = "django"
version = "5.2"

[[package]]
name = "sqlparse"
version = "0.5.0"

[[package]]
name = "pytest-django"
version = "4.9.0"
"""


def test_poetry_lock_direct_dependencies_from_pyproject(tmp_path):
    write(tmp_path / "poetry.lock", POETRY_LOCK)
    assert set(direct(sources.load(tmp_path)).values()) == {None}  # nothing says
    write(
        tmp_path / "pyproject.toml",
        """
[tool.poetry.dependencies]
python = "^3.12"
django = "^5.2"

[tool.poetry.group.test.dependencies]
pytest-django = "*"
""",
    )
    assert direct(sources.load(tmp_path)) == {
        "django": True,
        "sqlparse": False,
        "pytest-django": True,
    }


def test_pdm_lock_direct_dependencies_from_pyproject(tmp_path):
    write(tmp_path / "pdm.lock", POETRY_LOCK)
    write(
        tmp_path / "pyproject.toml",
        """
[project]
dependencies = ["Django>=5.2", "sqlparse; sys_platform == 'never'"]

[tool.pdm.dev-dependencies]
test = ["pytest-django"]
""",
    )
    assert direct(sources.load(tmp_path)) == {
        "django": True,
        "sqlparse": True,  # named for another platform, still the project's own
        "pytest-django": True,
    }


def test_pipfile_lock_direct_dependencies_from_pipfile(tmp_path):
    write(
        tmp_path / "Pipfile.lock",
        json.dumps(
            {
                "default": {"django": {"version": "==5.2"}, "sqlparse": {"version": "==0.5.0"}},
                "develop": {"pytest-django": {"version": "==4.9.0"}},
            }
        ),
    )
    assert set(direct(sources.load(tmp_path)).values()) == {None}
    write(tmp_path / "Pipfile", '[packages]\nDjango = "*"\n\n[dev-packages]\npytest-django = "*"\n')
    assert direct(sources.load(tmp_path)) == {
        "django": True,
        "sqlparse": False,
        "pytest-django": True,
    }


def test_requirements_and_pyproject_are_all_direct(tmp_path):
    write(tmp_path / "constraints.txt", "sqlparse==0.5.0\nDjango==5.2\n")
    write(tmp_path / "requirements.txt", "-c constraints.txt\nDjango\n")
    write(tmp_path / "pyproject.toml", '[project]\ndependencies = ["django-filter>=25"]\n')
    ds = sources.load(tmp_path)
    assert direct(ds) == {"django": True, "django-filter": True}
    assert versions(ds)["django"] == "5.2"  # a constraint pins, it adds nothing


def test_direct_wins_when_sources_disagree():
    a = sources.Dependency("django", "5.2", direct=False)
    assert sources._combine(a, sources.Dependency("django", None, ">=5", direct=True)).direct
    assert sources._combine(a, sources.Dependency("django", None)).direct is False
    assert sources._combine(sources.Dependency("x", None), a).direct is False


@posix_only
def test_environment_takes_direct_dependencies_from_the_project(tmp_path):
    site = tmp_path / "site"
    dist_info(site, "Django", "5.2")
    dist_info(site, "sqlparse", "0.5.0")
    python = fake_interpreter(tmp_path, f'PYTHONPATH={site} exec {sys.executable} -S "$@"')
    project = tmp_path / "project"
    project.mkdir()
    assert set(direct(sources.load(project, python=python)).values()) == {None}
    write(project / "pyproject.toml", '[project]\ndependencies = ["django"]\n')
    found = direct(sources.load(project, python=python))
    assert (found["django"], found["sqlparse"]) == (True, False)
    write(project / "uv.lock", UV_LOCK)  # wins over pyproject.toml, like for the versions
    found = direct(sources.load(project, python=python))
    assert (found["django"], found["sqlparse"]) == (True, False)


def test_requirement_lines_remember_where_they_are(tmp_path):
    write(tmp_path / "requirements" / "base.txt", "# the framework\nDjango==4.2.7\n")
    write(
        tmp_path / "requirements.txt",
        "-r requirements/base.txt\n-c constraints.txt\ndjango-filter \\\n  >=23\ndjango-allauth\n",
    )
    write(tmp_path / "constraints.txt", "django-allauth==0.57.0\n")
    origins = {name: d.origin for name, d in sources.load(tmp_path).dependencies.items()}
    assert origins == {
        "django": "requirements/base.txt:2",
        "django-filter": "requirements.txt:3",  # where a continued line starts
        "django-allauth": "constraints.txt:1",  # where the pin is
    }
    single = sources.load(tmp_path / "requirements" / "base.txt").dependencies["django"]
    assert single.origin == "base.txt:2"
