# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

First release.

### Added

- Reads dependencies from `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements*.txt`, `requirements/*.txt`, `pyproject.toml` (PEP 621, dependency groups and Poetry) or an installed environment (`--python`).
- Sorts every Django-related package into blocked, upgrade first, upgrade together with Django, check manually and ready, and names the smallest release that declares support for the target.
- Targets: `lts` (default), `latest` or an explicit version such as `5.2`.
- Text, Markdown, JSON and self-contained HTML output.
- `--fail-on blocked|upgrade|check` for CI.
- GitHub Action that writes the report to the job summary.
- 24 hour cache for PyPI responses.

[Unreleased]: https://github.com/derblub/django-upgrade-report/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/derblub/django-upgrade-report/releases/tag/v0.1.0
