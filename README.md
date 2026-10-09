<div align="center">

<a href="https://pushingpixels.at">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
    <img src="docs/assets/logo.svg" alt="Pushing Pixels" width="96">
  </picture>
</a>

<h1>django-upgrade-report</h1>

<p><strong>Which of your dependencies block a Django upgrade, and in which order to upgrade them.</strong></p>

<p>
  <a href="https://github.com/derblub/django-upgrade-report/actions/workflows/ci.yml"><img src="https://github.com/derblub/django-upgrade-report/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/derblub/django-upgrade-report/actions/workflows/ci.yml"><img src="https://img.shields.io/badge/coverage-94%25-brightgreen" alt="Coverage 94%"></a>
  <a href="https://pypi.org/project/django-upgrade-report/"><img src="https://img.shields.io/pypi/v/django-upgrade-report?cacheSeconds=3600" alt="PyPI"></a>
  <a href="https://pypi.org/project/django-upgrade-report/"><img src="https://img.shields.io/pypi/pyversions/django-upgrade-report?cacheSeconds=3600" alt="Python versions"></a>
  <a href="https://pypi.org/project/django-upgrade-report/"><img src="https://img.shields.io/pypi/frameworkversions/django/django-upgrade-report?cacheSeconds=3600" alt="Django versions"></a>
  <a href="https://djangopackages.org/packages/p/django-upgrade-report/"><img src="https://img.shields.io/badge/Django%20Packages-django--upgrade--report-8c3c26" alt="Django Packages"></a>
  <a href="https://www.reddit.com/r/django/comments/1wsa9d3/54_days_after_django_61_14_of_the_top_200_django/"><img src="https://img.shields.io/badge/discuss-r%2Fdjango-FF4500?logo=reddit&logoColor=white" alt="Discuss on r/django"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
</p>

<p>
  <a href="#quick-start">Quick start</a> ·
  <a href="#what-the-statuses-mean">Statuses</a> ·
  <a href="#usage">Usage</a> ·
  <a href="#in-ci">CI</a> ·
  <a href="#how-it-decides">How it decides</a> ·
  <a href="#faq">FAQ</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

</div>

---

Before you touch Django, you need to know whether the 20 to 200 packages your project depends on are ready for the new version, which of them you can upgrade today, and which have to move together with Django. Finding that out by hand means reading every changelog.

**django-upgrade-report** reads your lockfile, asks PyPI what every Django-related package declares, and gives you the upgrade plan: blockers first, then the smallest safe step for each package, in the right order.

```console
uvx django-upgrade-report
```

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/report-dark.png">
    <img src="docs/assets/report-light.png" alt="HTML report for an upgrade from Django 4.2.7 to 5.2: 15 packages to upgrade before Django, 3 to check manually" width="820">
  </picture>
</p>

## Highlights

- **Knows the order.** Separates upgrades you can ship today, on your current Django, from the ones that have to land in the same change as the Django bump. It also tells you when a release first needs a newer patch of your Django, or a newer version of another package.
- **Smallest step, not latest.** For every package it names the oldest release that declares support for the target, so each change stays small and reviewable.
- **Reads what you already have.** `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements*.txt`, `pyproject.toml` or an installed environment, transitive dependencies included.
- **Honest about uncertainty.** A missing classifier means "check manually", not "blocked". An upper bound written before the target was released is not taken as a promise. Packages without a release in two years, or marked inactive, are flagged.
- **Knows your Python.** Finds your project's Python version, warns when the target Django needs a newer one, and evaluates environment markers for your project, not for the machine running the tool.
- **Made for pipelines and for people.** Markdown for pull request summaries, versioned JSON for scripts, a self-contained HTML report to attach to a ticket, and `--fail-on` to break the build.
- **Your code stays put.** Only names and versions of packages that come from PyPI are sent to PyPI. Git, path and private-index packages are listed, never looked up. No account, no configuration.

## Quick start

Run it in your project directory. You need Python 3.10 or newer, but not Django and not your project's virtualenv.

```console
uvx django-upgrade-report                      # with uv
pipx run django-upgrade-report                 # with pipx
pip install django-upgrade-report              # or install it
```

By default it picks the next sensible step for your project: the newest LTS above the Django you run, or the newest release when no LTS is above it. A project on Django 4.2 gets a report for 5.2, a project on 5.2 one for 6.1. Pick another target with `--target`:

```console
django-upgrade-report --target 6.1
```

<p align="center">
  <img src="docs/assets/terminal.png" alt="Terminal output for an upgrade from Django 4.2.7 to 5.2: a git fork of django-taggit is blocked by its own Django&lt;5.0 requirement, four packages can be upgraded first, one goes together with Django, two need a manual check, one is ready, and one git package is listed as not checked" width="820">
</p>

## What the statuses mean

| Status | Meaning | What to do |
| --- | --- | --- |
| **Blocked** | Your release and every newer one exclude the target, for example with `Django<6.1`. | Wait for a release, find a fork or replace the package. |
| **Upgrade first** | A newer release declares the target and still runs on your current Django. | Upgrade these one at a time, before you touch Django. |
| **Upgrade together with Django** | The release that declares the target has dropped your current Django, or needs another package that has. | Bump it in the same change as Django. |
| **Check manually** | Nothing excludes the target, but nothing declares it either. | Read the changelog or run your test suite. Usually a classifier nobody updated. |
| **Ready** | The version you use already declares support. | Nothing. |

The notes on a row tell you more, for example "update Django 4.2 first" when a release needs a newer patch of your current Django, "upgrade django-crispy-forms first" when it needs a newer version of another package you pin, or "newer releases exclude Django 6.1".

An upgrade also says how big the step is, "crosses 2 major versions", counted by the releases in between, where each 0.x minor release counts as a major one. The rows of the Markdown and HTML reports link the package's changelog: the link its maintainers label as such, or its GitHub releases page. The text report shows the links with `-v`.

Some packages did a job Django now does itself, and no metadata says so. For a short list of them the row says what Django has instead, for example "built into Django 1.7: its own migrations, remove South" or "built into Django 3.1: models.JSONField" for django-jsonfield. An entry needs a source, the package's maintainers pointing to Django or Django's release notes; quiet packages, or opinions about a better third-party package, stay out. The list is in [`successors.py`](src/django_upgrade_report/successors.py), and additions with a source are welcome.

## Usage

```console
django-upgrade-report [PROJECT] [options]
```

| Option | Description |
| --- | --- |
| `PROJECT` | Project directory, or a single lockfile, requirements file or `pyproject.toml`. Defaults to the current directory. |
| `-t`, `--target` | `auto` (default), `lts` (the newest x.2 release), `latest`, or a version such as `5.2`. See [Choosing the target](#choosing-the-target). |
| `--from VERSION` | The Django version you run today, e.g. `4.2` or `4.2.16`, when your requirements only give a range. `4.2` means the newest 4.2 release. |
| `--python PATH` | Read the exact installed versions from this interpreter, e.g. `.venv/bin/python`. |
| `-f`, `--format` | `text` (default), `markdown`, `json` or `html`. |
| `-o`, `--output` | Write the report to a file instead of stdout. Missing directories are created. |
| `--fail-on` | Exit with status 1 when a package is `blocked`, needs an `upgrade` (or is blocked), or needs a `check` (or anything worse). |
| `--explain PACKAGE` | Show step by step how the verdict on a package came about instead of the report: the requirement lines that apply, the classifiers, whether an upper bound counts, every release looked at, and whether it goes before or with Django. Can be given more than once. Paste it into an issue when you think a verdict is wrong. |
| `-v`, `--verbose` | Text output only: list every ready package with its reason. The other formats always do. |
| `-q`, `--quiet` | Text output only: just the headline, warnings, blocked packages and the counts. |
| `--index-url` | Base URL of an index that implements PyPI's JSON API. Default: `https://pypi.org/pypi`. |
| `--check-private-on-pypi` | Look up packages your project installs from another index on PyPI, too. For an index that mirrors PyPI (Artifactory, Nexus, devpi). Their names are sent to PyPI. |
| `--no-cache` | Do not cache PyPI responses. |
| `--offline` | Answer from the cache only, however old, and never ask the package index. A package that is not in the cache counts as not checked. |
| `--prefer-cache` | Answer from the cache, however old, and ask the index only for what is missing. |
| `--no-input` | Never ask in the terminal. Without it, a run at a terminal asks for what the project leaves out and that changes the report: the Django you run when it is not pinned, a smaller first step when the target skips an LTS, and your Python when no file names it. Never in CI, never with `-o`, `--explain` or a format other than text. |
| `--errors-as-warnings` | Exit with status 0 instead of 2 when the report cannot be made, for example offline without a cache. For hooks that must not block a commit. |
| `--version` | Show the version and exit. |

Responses are cached in `~/.cache/django-upgrade-report` (or `$XDG_CACHE_HOME/django-upgrade-report`): a project's release list for 24 hours, the metadata of a single release for good, since it never changes. A new version of the tool may keep more of each answer; it then fetches them once more and ignores the old files, so delete the directory now and then to free the space.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The report was written, and no package matched `--fail-on`. |
| `1` | A package matched `--fail-on`. |
| `2` | An error: no dependencies found, an unreadable file, an unknown target, the index could not be reached. Also with `--fail-on` when no dependency could be checked because they all come from another index. With `--errors-as-warnings` these exit with `0` and print a warning instead. |

So CI can tell "packages need attention" from "the tool could not run".

### Choosing the target

| `--target` | Checks against |
| --- | --- |
| `auto` | The newest LTS above your Django, or the newest release when no LTS is above it. When you already run the newest release, a health check of it. When your Django version is unknown, the newest LTS. |
| `lts` | The newest x.2 release. When your Django is newer, a health check of your version instead. |
| `latest` | The newest release. |
| `5.2`, `6.1`, ... | That feature version. The next, unreleased one (6.2 today) is accepted for planning, see [How it decides](#how-it-decides). Unknown versions and versions below yours are an error. |

The report warns when the target skips an LTS (upgrading one LTS at a time is easier), when your own Django requirement excludes the target, and when the target needs a newer Python than your project uses.

### Where versions come from

The most precise source wins:

| Priority | Source | Versions | Transitive dependencies |
| --- | --- | --- | --- |
| 1 | `--python PATH` | exact, as installed | yes |
| 2 | `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock` | exact | yes |
| 3 | `requirements*.txt`, `requirements/*.txt`, `pyproject.toml` | exact when pinned with `==` | no |

Requirement files follow `-r` includes and `-c` constraint files; constraints only pin packages that are listed elsewhere. `pyproject.toml` is read as PEP 621, dependency groups and Poetry. When a lockfile holds several versions of one package for different Pythons, as uv's forked resolutions do, the one for your project's Python is used.

Unpinned requirements are judged by the newest release they allow and marked as such. Use a lockfile for exact results. When Django itself is only given as a range with an upper bound, such as `Django>=4.2,<5.0`, the newest release it allows is assumed and a warning says so. Without an upper bound the report cannot tell what can be upgraded first. In both cases, `--from` sets the version you run.

### Which Python

Environment markers such as `python_version < "3.12"` decide which requirements apply, so the tool needs your project's Python. It takes the first of:

1. the interpreter passed with `--python`,
2. `.python-version`,
3. `requires-python` in `uv.lock`,
4. `requires-python` in `pyproject.toml`,
5. the `python` dependency in Poetry's `pyproject.toml`,
6. `python_version` in `Pipfile.lock`.

A range counts as its lower bound. The report shows the Python it found and warns when the target Django needs a newer one, for example `Django 6.1 needs Python >=3.12, your project uses 3.11 (from .python-version)`.

Markers are evaluated for CPython on Linux, where Django apps are deployed, never for the machine running the tool. Against the target, a package is judged on the newer of your project's Python and the oldest Python the target Django supports. Against your current Django, on your project's Python, or the oldest Python your current Django supports when none was found.

### Packages not from PyPI

Packages from git, a local path, a URL or a private index are listed as "Not from PyPI, not checked", with where they come from, and their names are never sent to PyPI. This covers `git+https://...`, `-e` and local path lines in requirement files, `name @ url` requirements, git, path and URL sources in lockfiles, `--index-url` and `--no-index` in requirement files, a private default index or `no-index` in uv, Poetry, PDM or Pipenv, and the `PIP_INDEX_URL`, `UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `PIP_NO_INDEX` and `UV_NO_INDEX` environment variables (lockfiles keep the index they record). Credentials in those URLs are removed before anything is shown.

A fork or a local package can still be judged by what it declares itself, without anything being fetched: the installed metadata with `--python`, the `pyproject.toml` of a local directory, or the constraints `poetry.lock` and `pdm.lock` record. A fork someone pinned years ago with `Django<4.1` shows up as blocked, with a note saying where it comes from. Such a package has no other releases to move to, so it is only ever ready, blocked or to check. Its own requirements count, too: a fork that pins `django-filter<23` holds back that upgrade. Packages whose metadata cannot be read, or that have nothing to do with Django, stay under "Not from PyPI, not checked". The dependencies of a fork are checked like any other package when a lockfile or `--python` lists them.

To check packages from a private index, point `--index-url` at its PyPI JSON API. If the index only mirrors PyPI, pass `--check-private-on-pypi` instead. Packages the index does not know at all are listed as "Not on the package index".

## In CI

### GitHub Actions

The action writes the Markdown report to the job summary, exposes the counts as outputs, and can fail the job:

```yaml
- uses: actions/checkout@v7
- uses: derblub/django-upgrade-report@v0
  id: django
  with:
    fail-on: blocked   # optional: blocked, upgrade or check
- run: echo "${{ steps.django.outputs.blocked }} blocked, ${{ steps.django.outputs.upgrade }} to upgrade"
```

| Input | Default | Description |
| --- | --- | --- |
| `path` | `.` | Project directory with a lockfile, `requirements*.txt` or `pyproject.toml`. |
| `target` | `auto` | `auto`, `lts`, `latest` or a version such as `6.1`. |
| `from` | | The Django version you run today, when your requirements only give a range. Empty reads it from the project. |
| `fail-on` | | `blocked`, `upgrade` or `check`. Empty never fails the step because of a package. |
| `check-private-on-pypi` | `false` | `true` looks up packages from another index on PyPI, too. |

| Output | Description |
| --- | --- |
| `report` | Path to the JSON report, unique per use of the action. |
| `blocked`, `upgrade`, `check`, `ready` | Number of packages with that status. |

The action brings its own Python, runs on Linux and Windows runners, and caches PyPI responses between runs. The summary and the outputs are written before `fail-on` fails the step.

### pre-commit

```yaml
- repo: https://github.com/derblub/django-upgrade-report
  rev: v0.5.0
  hooks:
    - id: django-upgrade-report
```

The hook runs when a lockfile, a requirements file or `pyproject.toml` changes, and fails the commit when a package blocks the next Django upgrade. It answers from the cache where it can (`--prefer-cache`), shows only what blocks (`--quiet`), and lets the commit through when the report cannot be made (`--errors-as-warnings`). It reads the project at the repository root and runs when one of its files changes. Add `args: [--target, "6.1"]` to check another target.

### GitLab CI

```yaml
django-upgrade-report:
  image: ghcr.io/astral-sh/uv:python3.12-bookworm-slim
  script:
    - uvx django-upgrade-report --format html --output upgrade-report.html
    - uvx django-upgrade-report --fail-on blocked
  artifacts:
    when: always
    paths: [upgrade-report.html]
```

### Anywhere else

```console
django-upgrade-report --format markdown >> "$GITHUB_STEP_SUMMARY"
django-upgrade-report --format json --output upgrade-report.json
```

The JSON report carries a `schema_version`: adding a field keeps it, renaming, removing or retyping one bumps it. The fields are documented in [`render/json.py`](src/django_upgrade_report/render/json.py).

## How it decides

For every release the tool looks at two pieces of metadata that maintainers publish on PyPI: the `Framework :: Django :: X.Y` classifiers and the `Django` requirement. For a target version, in this order:

1. A requirement that excludes every release of the target means **no**. Each patch release counts, so `Django==5.2.17` or `Django>=5.2.3,<5.2.8` allow 5.2. Requirements that only apply to an optional extra are ignored. Lines with environment markers count when they apply to your Python ([see above](#which-python)), and all lines that apply are combined.
2. A `Framework :: Django :: 5.2` classifier means **yes**.
3. An upper bound that allows the target, such as `Django>=4.2,<6.0` or an exact pin, means **yes**, but only when the release was uploaded on or after the day the target came out. A bound written before that is a guess, not a promise: Wagtail 6.3 allows `Django<6.0` but came out before Django 5.2, and only Wagtail 6.3.4 added 5.2 support.
4. Everything else means **not declared**: classifiers that stop at an older version or start at a newer one, a major-only classifier such as `Framework :: Django :: 5`, a lower bound without an upper one, or no information at all. That is a question, not a blocker: classifiers often lag behind releases.

For a target that is not released yet, such as 6.2 today, only classifiers count, and the report says so. An upper bound like `<7.0` says nothing about a version nobody could test.

Pre-releases never decide a status: you cannot pin an `rc` in production. When no stable release declares the target but the newest pre-release does, the row says so, for example "2.6.0.dev22 declares Django 6.1 (pre-release)". For a blocked package it also says when the newest pre-release no longer excludes the target. Only a pre-release newer than every stable release counts, judged by the same rules.

From these verdicts, per package:

- **Ready** when the version you use says yes.
- **Upgrade** when a newer release says yes. The report names the oldest one. It goes **first** when that release still runs on your current Django. A release that needs `Django>=4.2.16` while you run 4.2.7 still goes first, with a note to update Django 4.2 first. It goes **together with Django** when the release excludes your whole Django series, declares only newer Django versions, or needs a newer version of another package you pin that has itself dropped your Django.
- **Check manually** when no release says yes, but yours or a newer one is not excluded.
- **Blocked** when your release and every newer one exclude the target.

A package counts as Django-related when it depends on Django or has a `Framework :: Django` classifier. Packages that only depend on Wagtail or django CMS are included too, with a note to check them against that framework. Everything else is skipped.

> [!NOTE]
> The report shows what maintainers declare, not whether your tests pass. Use it to plan the upgrade, then run [django-upgrade](https://github.com/adamchainz/django-upgrade) on your code and your test suite with `python -W error::DeprecationWarning`.

## FAQ

<details>
<summary><strong>Does it send my code anywhere?</strong></summary>

No. It reads your lockfile or requirement files locally and sends only names and versions of packages that come from PyPI to the package index, PyPI by default. Packages from git, local paths or a private index are never looked up unless you ask for it. It does not import your project and does not need Django installed.
</details>

<details>
<summary><strong>Why does it say that about my package?</strong></summary>

Run it again with `--explain` and the package's name, for example `django-upgrade-report --explain wagtail`. It shows the Django requirement lines that apply on your Python, the classifiers, whether an upper bound counts given when it was set, every release it looked at with its verdict, and why an upgrade goes before or with Django. If the verdict is still wrong, paste that output into a [wrong verdict](https://github.com/derblub/django-upgrade-report/issues/new/choose) issue.
</details>

<details>
<summary><strong>Why are so many packages "check manually"?</strong></summary>

Many maintainers forget to add the classifier for a new Django version, or only add it with the next release. The tool refuses to guess. Packages that are really incompatible almost always say so with an upper bound, and those show up as blocked.
</details>

<details>
<summary><strong>What about private packages?</strong></summary>

Packages your project installs from git, a path or a private index are never looked up. Forks and local packages are judged by their own metadata when it can be read locally (with `--python`, from a local `pyproject.toml`, `poetry.lock` or `pdm.lock`); the rest are listed as "Not from PyPI, not checked". If your private index implements PyPI's JSON API, point `--index-url` at it. If it mirrors PyPI, pass `--check-private-on-pypi`. See [Packages not from PyPI](#packages-not-from-pypi).
</details>

<details>
<summary><strong>How is this different from Dependabot or Renovate?</strong></summary>

They bump versions one package at a time. They do not know which release is the first one to support the Django version you are heading for, or which upgrades have to wait for Django. Use this tool to plan, and let them open the pull requests.
</details>

<details>
<summary><strong>How is this different from django-upgrade?</strong></summary>

[django-upgrade](https://github.com/adamchainz/django-upgrade) rewrites *your* code for a new Django version. django-upgrade-report looks at your *dependencies*. You want both.
</details>

## Related projects

| Project | What it does |
| --- | --- |
| [django-upgrade](https://github.com/adamchainz/django-upgrade) | Rewrites your code for new Django versions. |
| [Django Packages readiness](https://djangopackages.org/readiness/) | Shows compatibility per package on the web. |
| [Django's upgrade guide](https://docs.djangoproject.com/en/stable/howto/upgrade-version/) | The official checklist for an upgrade. |

## Contributing

Bug reports with a real lockfile are the most useful contribution. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, and the [Code of Conduct](CODE_OF_CONDUCT.md). Security issues go through [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © Daniel Kurdoghlian, Pushing Pixels

---

<div align="center">
  <a href="https://pushingpixels.at">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/assets/pushing-pixels-dark.svg">
      <source media="(prefers-color-scheme: light)" srcset="docs/assets/pushing-pixels-light.svg">
      <img src="docs/assets/pushing-pixels.svg" alt="Pushing Pixels" width="320">
    </picture>
  </a>
  <p>Built and maintained by <a href="https://pushingpixels.at">Daniel Kurdoghlian</a> at <a href="https://pushingpixels.at">Pushing Pixels</a> in Vienna.<br>
  Planning a larger upgrade? I do <a href="https://pushingpixels.at/creates/django-upgrades">fixed-price Django upgrade audits</a>.</p>
</div>
