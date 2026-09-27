# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-09-27

First release on PyPI.

### Added

- `--target auto`, the new default: the newest LTS above your Django, or the newest release when no LTS is above it.
- `--from` sets the Django version you run when your requirements only give a range.
- The project's Python is read from `.python-version`, `uv.lock`, `pyproject.toml` or `Pipfile.lock`. The report warns when the target Django needs a newer Python.
- Packages from git, local paths, URLs or a private index are listed as "Not from PyPI, not checked" and their names are never sent to PyPI. `--check-private-on-pypi` looks them up anyway, for an index that mirrors PyPI. A project-wide index counts too: `--index-url` or `--no-index` in requirement files, `[tool.uv]` and PDM index settings, and the pip and uv index environment variables.
- Warnings when the target skips an LTS, when your Django requirement excludes the target, and when Django is not pinned.
- Notes when a release needs a newer patch of your current Django, or a newer version of another package you pin. Upgrades that depend on one going with Django go with Django, too.
- Packages marked `Development Status :: 7 - Inactive` are flagged. Packages built only on Wagtail or django CMS are included, with a note.
- `schema_version` and documented fields in the JSON report.
- Action inputs `from` and `check-private-on-pypi`, and outputs `blocked`, `upgrade`, `check` and `ready`. The action caches PyPI responses and runs on Windows runners.
- A release workflow that publishes to PyPI with trusted publishing and moves the `v0` tag, so `uvx django-upgrade-report` and `uses: derblub/django-upgrade-report@v0` work.
- Exit status 2 for every error, so it cannot be mistaken for `--fail-on`'s status 1.

### Changed

- The default target was `lts`, which gave a downgrade report for projects on Django 6.0 or 6.1. `lts` now shows a health check of your version when your Django is newer.
- An upper bound such as `Django<6.0` only counts as support when the release came out after the target. For a target that is not released yet, only classifiers count.
- Unknown targets (`5.3`, `52`) and targets below your Django are an error instead of an empty report.
- Unpinned requirements are judged by the newest release they allow, not the newest release overall.
- Environment markers are evaluated for your project's Python on Linux, not for the machine running the tool, and all Django requirement lines that apply are combined.

### Fixed

- Exact pins such as `Django==5.2.17` were read as excluding the version they pin.
- A package was "Blocked" when only its newest release excluded the target, even though yours or an older one allowed it. It is now "Check manually".
- Only the newest 40 releases were searched, so the suggested release was often not the smallest one.
- Major-only classifiers such as `Framework :: Django :: 5` were ignored.
- uv's forked resolutions, and duplicate entries in `poetry.lock` and `pdm.lock`, used the last entry instead of the one for your Python.
- Pins from `-c` constraint files and `-r` includes were overwritten by later unpinned lines, and constraint-only packages were reported as dependencies. Later `pyproject.toml` tables overwrote earlier pins, and Poetry dependencies with several constraints were dropped.
- Requirement files with a UTF-8 BOM lost their first line, and UTF-16 files crashed. Lockfiles and TOML are read as UTF-8 whatever the locale.
- An empty lockfile gave an all-clear report instead of an error.
- `--python` reported a shadowed copy of a package installed twice, and failed on Python 3.7 and older environments.
- Tracebacks for unreadable files, `--python` failures, a bad `--index-url` (which printed its credentials) or `-o` into a missing directory. Missing directories are now created.
- Reports are written as UTF-8, so `-o` and redirected output no longer crash on Windows.
- Timeouts, connection resets, HTTP 429 and truncated answers from the index are retried.
- Ready packages hid their notes, such as "version not pinned", in text and Markdown output.
- An installed version missing from the index was shown as ready, without a note.
- The Markdown report left out packages not on the index, and `*` in version specifiers turned into emphasis.
- The progress counter never reached its total.
- The action failed on Windows runners, and a second use in one job overwrote the first JSON report.
- The action and CI used Node 20 actions, which GitHub runners no longer run.
- `--fail-on` passed when every Django-related package came from another index, as soon as one unrelated package came from PyPI.

## [0.1.0]

Preview, not published on PyPI.

### Added

- Reads dependencies from `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements*.txt`, `requirements/*.txt`, `pyproject.toml` (PEP 621, dependency groups and Poetry) or an installed environment (`--python`).
- Sorts every Django-related package into blocked, upgrade first, upgrade together with Django, check manually and ready, and names the smallest release that declares support for the target.
- Targets: `lts` (default), `latest` or an explicit version such as `5.2`.
- Text, Markdown, JSON and self-contained HTML output.
- `--fail-on blocked|upgrade|check` for CI.
- GitHub Action that writes the report to the job summary.
- 24 hour cache for PyPI responses.

[Unreleased]: https://github.com/derblub/django-upgrade-report/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/derblub/django-upgrade-report/releases/tag/v0.2.0
[0.1.0]: https://github.com/derblub/django-upgrade-report/commit/35e1e97
