# Contributing

Thanks for taking the time. Bug reports with a real `requirements.txt` or lockfile are the most useful contribution there is: most wrong verdicts come from packaging metadata nobody anticipated.

## Reporting a wrong verdict

Open an [issue](https://github.com/derblub/django-upgrade-report/issues/new/choose) with:

- the command you ran and `django-upgrade-report --version`,
- the package, the version you use and the target Django version,
- what the tool said and what you expected, ideally with a link to the package's changelog or PyPI page.

`--format json` output helps, since it includes the reason for every verdict.

## Development setup

You need [uv](https://docs.astral.sh/uv/).

```console
$ git clone https://github.com/derblub/django-upgrade-report
$ cd django-upgrade-report
$ uv run --group dev pytest
$ uv run python -m django_upgrade_report path/to/a/project
```

Before you open a pull request:

```console
$ uv run --group dev pytest
$ uvx ruff check .
$ uvx ruff format .
```

The tests never touch the network. Please keep it that way. There are two package indexes in `tests/conftest.py`:

- a small in-memory index for the rules: add the releases you need to it instead of calling PyPI,
- real PyPI metadata recorded in `tests/fixtures/pypi/`, for the golden tests in `tests/test_analysis.py`. To add a package, add it to `CASES` in `tests/fixtures/record.py` and run `PYTHONPATH=src python3 tests/fixtures/record.py`. Re-recording changes the facts the golden tests assert, so check them against the new data.

CI also measures coverage: `uv run --group dev pytest --cov=django_upgrade_report`.

## Where things live

| File | What it does |
| --- | --- |
| `src/django_upgrade_report/sources.py` | Reads lockfiles, requirement files, `pyproject.toml` and environments into `Dependency` objects, and finds the project's Python |
| `src/django_upgrade_report/pypi.py` | Cached client for the PyPI JSON API, with retries |
| `src/django_upgrade_report/analysis.py` | The target (`resolve_target()`), the verdict rules (`supports()`) and the per-package status and phase |
| `src/django_upgrade_report/render/` | Text, Markdown, JSON and HTML output. `render/json.py` documents the JSON fields and `schema_version` |
| `src/django_upgrade_report/cli.py` | Command line interface |
| `action.yml` | The GitHub Action |

A change to the verdict rules in `supports()` needs a test case in `tests/test_analysis.py` and an update to "How it decides" in the README.

## Pull requests

- One topic per pull request, with a test that fails without the change.
- Describe the user-visible change in `CHANGELOG.md` under "Unreleased".
- By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
