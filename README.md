<div align="center">

<a href="https://pushingpixels.at">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo-light.svg" alt="Pushing Pixels" width="88">
  </picture>
</a>

<h1>django-upgrade-report</h1>

<p><strong>Which of your dependencies block a Django upgrade, and in which order to upgrade them.</strong></p>

<p>
  <a href="https://github.com/derblub/django-upgrade-report/actions/workflows/ci.yml"><img src="https://github.com/derblub/django-upgrade-report/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/django-upgrade-report/"><img src="https://img.shields.io/pypi/v/django-upgrade-report" alt="PyPI"></a>
  <a href="https://pypi.org/project/django-upgrade-report/"><img src="https://img.shields.io/pypi/pyversions/django-upgrade-report" alt="Python versions"></a>
  <a href="https://pypi.org/project/django-upgrade-report/"><img src="https://img.shields.io/pypi/frameworkversions/django/django-upgrade-report" alt="Django versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff"></a>
</p>

<p>
  <a href="#quick-start">Quick start</a> ·
  <a href="#what-the-statuses-mean">Statuses</a> ·
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

- **Knows the order.** Separates upgrades you can ship today, on your current Django, from the ones that have to land in the same change as the Django bump.
- **Smallest step, not latest.** For every package it names the oldest release that supports the target, so each change stays small and reviewable.
- **Reads what you already have.** `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements*.txt`, `pyproject.toml` or an installed environment, transitive dependencies included.
- **Honest about uncertainty.** A missing classifier means "check manually", not "blocked". Packages without a release in two years are flagged.
- **Made for pipelines and for people.** Markdown for pull request summaries, JSON for scripts, a self-contained HTML report to attach to a ticket, and `--fail-on` to break the build.
- **Your code stays put.** Only package names and versions are sent to PyPI. No account, no configuration, one runtime dependency.

## Quick start

Run it in your project directory. You need Python 3.10 or newer, but not Django and not your project's virtualenv.

```console
uvx django-upgrade-report                      # with uv
pipx run django-upgrade-report                 # with pipx
pip install django-upgrade-report              # or install it
```

By default it checks against the latest Django LTS. Pick another target with `--target`:

```console
django-upgrade-report --target 6.1
```

<p align="center">
  <img src="docs/assets/terminal.png" alt="Terminal output for an upgrade from Django 5.2.7 to 6.1: django-celery-beat is blocked, five packages can be upgraded first, three need a manual check, one is ready" width="820">
</p>

## What the statuses mean

| Status | Meaning | What to do |
| --- | --- | --- |
| **Blocked** | Even the newest release excludes the target, for example with `Django<6.1`. | Wait for a release, find a fork or replace the package. |
| **Upgrade first** | A newer release supports the target and still runs on your current Django. | Upgrade these one at a time, before you touch Django. |
| **Upgrade together with Django** | The release that supports the target has dropped your current Django. | Bump it in the same change as Django. |
| **Check manually** | Nothing excludes the target, but nothing declares it either. | Read the changelog or run your test suite. Usually a classifier nobody updated. |
| **Ready** | The version you use already declares support. | Nothing. |

## Usage

```console
django-upgrade-report [PROJECT] [options]
```

| Option | Description |
| --- | --- |
| `PROJECT` | Project directory. Defaults to the current directory. |
| `-t`, `--target` | `lts` (default, the newest x.2 release), `latest`, or a version such as `5.2`. |
| `-f`, `--format` | `text` (default), `markdown`, `json` or `html`. |
| `-o`, `--output` | Write the report to a file instead of stdout. |
| `--python PATH` | Read the exact installed versions from this interpreter, e.g. `.venv/bin/python`. |
| `--fail-on` | Exit with status 1 when a package is `blocked`, needs an `upgrade` or a `check`. |
| `-v`, `--verbose` | List ready packages with their reasons. |
| `--index-url` | Use another index that implements PyPI's JSON API. |
| `--no-cache` | Skip the 24 hour cache in `~/.cache/django-upgrade-report`. |

### Where versions come from

The most precise source wins:

| Priority | Source | Versions | Transitive dependencies |
| --- | --- | --- | --- |
| 1 | `--python PATH` | exact, as installed | yes |
| 2 | `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock` | exact | yes |
| 3 | `requirements*.txt`, `requirements/*.txt`, `pyproject.toml` | exact when pinned with `==` | no |

Unpinned requirements are judged by their newest release and marked as such. Use a lockfile for exact results.

## In CI

### GitHub Actions

The action writes the report to the job summary and can fail the job:

```yaml
- uses: actions/checkout@v4
- uses: derblub/django-upgrade-report@v0
  with:
    target: lts        # or "latest", or "6.1"
    fail-on: blocked   # optional: blocked, upgrade or check
```

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

## How it decides

For every release the tool looks at two pieces of metadata that maintainers publish on PyPI: the `Framework :: Django :: X.Y` classifiers and the `Django` requirement. For a target version, in this order:

1. A requirement that excludes the target, such as `Django<5.0`, means **no**. Requirements that only apply to an optional extra are ignored.
2. A `Framework :: Django :: 5.2` classifier means **yes**.
3. An upper bound above the target, such as `Django>=4.2,<6.0`, means **yes**.
4. Classifiers that stop at an older version, or a lower bound without an upper one, mean **not declared**. That is a question, not a blocker: classifiers often lag behind releases.

A package counts as Django-related when it depends on Django or has a `Framework :: Django` classifier. Everything else is skipped.

> [!NOTE]
> The report shows what maintainers declare, not whether your tests pass. Use it to plan the upgrade, then run [django-upgrade](https://github.com/adamchainz/django-upgrade) on your code and your test suite with `python -W error::DeprecationWarning`.

## FAQ

<details>
<summary><strong>Does it send my code anywhere?</strong></summary>

No. It reads your lockfile or requirement files locally and sends only package names and versions to the package index, PyPI by default. It does not import your project and does not need Django installed.
</details>

<details>
<summary><strong>Why are so many packages "check manually"?</strong></summary>

Many maintainers forget to add the classifier for a new Django version, or only add it with the next release. The tool refuses to guess. Packages that are really incompatible almost always say so with an upper bound, and those show up as blocked.
</details>

<details>
<summary><strong>What about private packages?</strong></summary>

Packages that are not on the index are listed as "not on the package index" and otherwise ignored. If your private index implements PyPI's JSON API, point `--index-url` at it.
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
      <img src="docs/assets/pushing-pixels-light.svg" alt="Pushing Pixels" width="420">
    </picture>
  </a>
  <p>Built and maintained by <a href="https://pushingpixels.at">Daniel Kurdoghlian</a> at <a href="https://pushingpixels.at">Pushing Pixels</a> in Vienna.<br>
  Planning a larger upgrade? I do <a href="https://pushingpixels.at/creates/django-upgrades">fixed-price Django upgrade audits</a>.</p>
</div>
