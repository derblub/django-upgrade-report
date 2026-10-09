# Dependency sources

## Where versions come from

The most precise source wins:

| Priority | Source | Versions | Transitive dependencies |
| --- | --- | --- | --- |
| 1 | `--python PATH` | exact, as installed | yes |
| 2 | `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock` | exact | yes |
| 3 | `requirements*.txt`, `requirements/*.txt`, `pyproject.toml` | exact when pinned with `==` | no |

Requirement files follow `-r` includes and `-c` constraint files; constraints only pin packages that are listed elsewhere. `pyproject.toml` is read as PEP 621, dependency groups and Poetry. When a lockfile holds several versions of one package for different Pythons, as uv's forked resolutions do, the one for your project's Python is used.

Unpinned requirements are judged by the newest release they allow and marked as such. When Django itself is only a range with an upper bound, such as `Django>=4.2,<5.0`, the newest release it allows is assumed and a warning says so. `--from` sets the version you run; at a terminal the tool asks for it.

## Which Python

Environment markers such as `python_version < "3.12"` decide which requirements apply, so the tool needs your project's Python. It takes the first of:

1. the interpreter passed with `--python`,
2. `.python-version`,
3. `requires-python` in `uv.lock`,
4. `requires-python` in `pyproject.toml`,
5. the `python` dependency in Poetry's `pyproject.toml`,
6. `python_version` in `Pipfile.lock`,
7. your answer, when it asks at a terminal.

A range counts as its lower bound. Markers are evaluated for CPython on Linux, where Django apps are deployed, never for the machine running the tool. Against the target, a package is judged on the newer of your project's Python and the oldest Python the target Django supports.

## Packages not from PyPI

Packages from git, a local path, a URL or a private index are never looked up on PyPI and their names are never sent there. This covers `git+https://...`, `-e` and path lines in requirement files, `name @ url` requirements, git, path and URL sources in lockfiles, `--index-url` and `--no-index` in requirement files, a private default index or `no-index` in uv, Poetry, PDM or Pipenv, and the `PIP_INDEX_URL`, `UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `PIP_NO_INDEX` and `UV_NO_INDEX` environment variables. Credentials in those URLs are removed before anything is shown.

A fork or local package is still judged by what it declares itself, read locally: the installed metadata with `--python`, the `pyproject.toml` of a local directory, or the constraints `poetry.lock` and `pdm.lock` record. A fork pinned years ago with `Django<4.1` shows up as blocked, with a note saying where it comes from. Its own requirements count too: a fork that pins `django-filter<23` holds back that upgrade.

## Private indexes

| Your index | Pass |
| --- | --- |
| Implements PyPI's JSON API | `--index-url https://pkgs.example.com/pypi` |
| Mirrors PyPI (Artifactory, Nexus, devpi) | `--check-private-on-pypi`, which sends those names to PyPI |
| Neither | Nothing: those packages are listed as not checked |

Credentials in `--index-url` (`https://user:token@host/...`) are sent as basic authentication and never shown in output or error messages.

## The cache

Answers are cached in `~/.cache/django-upgrade-report` (or `$XDG_CACHE_HOME/django-upgrade-report`): a project's release list for 24 hours, the metadata of a single release for good. `--no-cache` turns it off, `--offline` answers from it alone, `--prefer-cache` asks the index only for what is missing. A new version of the tool may keep more of each answer and fetch everything once more; delete the directory now and then to free the space.
