# Architecture

For contributors. The [contributing guide](https://github.com/derblub/django-upgrade-report/blob/main/CONTRIBUTING.md) has the development setup and the rules for pull requests.

## The flow of one run

```text
sources.load()            read lockfiles / requirements / an environment → DependencySet
        │
analysis.analyse()        resolve the target, judge every dependency, fit the upgrades together → Report
        │   uses pypi.PyPI (client.JsonClient: retries, cache, no credentials in errors)
        │
python.plan_python()      optional: every dependency on the newer Python → Report.python
diff.compare()            optional: against --baseline → Report.changes
        │
render.text / markdown / html / json / explain
```

## Modules

| Module | What it does |
| --- | --- |
| `sources.py` | Reads `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, requirement files, `pyproject.toml` or an environment into `Dependency` objects, finds the project's Python, and marks what is not from PyPI. |
| `client.py` | HTTP client for JSON APIs: retries, rate limits, a disk cache with a format version, offline modes, redaction of credentials. |
| `pypi.py` | The PyPI JSON API on top of it: what a valid answer is, what is kept of it (classifiers, requirements, links, Linux wheel tags) and for how long. |
| `analysis.py` | The target (`resolve_target`), the rules for one release (`supports`, `explain_support`), the search for the smallest release (`_Checker` with a `Rule`), the status and phase of each package, and the order of the upgrades. |
| `python.py` | Whether a release runs on a Python (`python_supports`, `PythonRule`) and the Python section of the report. |
| `diff.py` | Comparing with a baseline report. |
| `projects.py` | Repository and changelog links from a project's metadata. |
| `successors.py` | Packages Django took over, each with a source. |
| `prompts.py` | The questions asked at a terminal. |
| `commands.py` | `--emit`: the commands per tool that carry out the plan. |
| `ci.py` | The pull request comment of the GitHub Action. |
| `render/` | Text, Markdown, HTML, JSON and `--explain`. `render/__init__.py` holds what the formats share, so they stay in the same order. |
| `cli.py` | Command-line options and exit codes. |

## The ecosystem page

`ecosystem/build.py` is not part of the package. `select` takes the most downloaded Django-related packages from the top-pypi-packages data set into `ecosystem/packages.json`; `build` runs `analyse()` for every Django version from 4.2 on, plus the next one, on a made-up project that pins the newest release of each, and finds when each package first declared a version with a binary search over its releases. It writes `data.json` and a static `index.html` with the report's styles, which `.github/workflows/ecosystem.yml` publishes to [GitHub Pages](https://derblub.github.io/django-upgrade-report/) every week, with the PyPI cache kept between runs.

## Rules that hold everywhere

- New signals never change a package's status silently; they add notes with a source.
- Only names and versions of packages from PyPI are sent to PyPI; anything else that talks to another host is opt-in.
- The JSON report only grows: `schema_version` stays 1 until a field is renamed, removed or retyped.
- The core depends on `packaging` (and `tomli` on Python 3.10) only.

## Tests

The tests never touch the network. `tests/conftest.py` has an in-memory index (`FakePyPI`, with `release()` to describe releases, wheels and links), real PyPI metadata recorded by `tests/fixtures/record.py` (`RecordedPyPI`, for the golden tests), a fake `urlopen` for the HTTP client and a `FakeGitHub`. Re-record one project with `PYTHONPATH=src python3 tests/fixtures/record.py django-prometheus`.

```console
uv run --group dev pytest --cov=django_upgrade_report
uvx ruff check . && uvx ruff format --check .
```

## This wiki

The pages live in [`docs/wiki/`](https://github.com/derblub/django-upgrade-report/tree/main/docs/wiki) and are published to the GitHub wiki on every push to `main`. The [Command-line reference](Command-Line-Reference), the [JSON report reference](JSON-Report-Reference) and the [GitHub Action reference](GitHub-Action-Reference) are generated from the code by `scripts/wiki.py`; a test fails when they are out of date. Change the wiki with a pull request, not in the GitHub editor: the next publish overwrites edits made there.
