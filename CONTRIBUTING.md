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

The tests use an in-memory package index (`tests/conftest.py`) and never touch the network. Please keep it that way: add the releases you need to the fake index instead of calling PyPI.

## Where things live

| File | What it does |
| --- | --- |
| `src/django_upgrade_report/sources.py` | Reads lockfiles, requirement files, `pyproject.toml` and environments into `Dependency` objects |
| `src/django_upgrade_report/pypi.py` | Cached client for the PyPI JSON API |
| `src/django_upgrade_report/analysis.py` | The verdict rules (`supports()`) and the per-package status |
| `src/django_upgrade_report/render/` | Text, Markdown, JSON and HTML output |
| `src/django_upgrade_report/cli.py` | Command line interface |
| `action.yml` | The GitHub Action |

A change to the verdict rules in `supports()` needs a test case in `tests/test_analysis.py` and an update to "How it decides" in the README.

## Pull requests

- One topic per pull request, with a test that fails without the change.
- Describe the user-visible change in `CHANGELOG.md` under "Unreleased".
- By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
