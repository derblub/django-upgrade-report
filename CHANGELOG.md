# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- The report lists what Django removed on the way to the target, from its release notes, and where your code still uses it ("The model's Meta.index_together option is removed  shop/models.py:11 · django-upgrade fixes this"); the others as a link to the release notes, all of them with `-v`. Markdown shows them as a table, the HTML report as its own section, and the JSON report has them under `removals`.
- A direct dependency your code never names gets the note "not imported or configured in your code: remove it instead?". The project's code is read locally, never imported or sent: imports, dotted paths in settings strings, app lists, `{% load %}` in templates and `manage.py` commands in scripts. Anything unsure counts as used. The end of every report lists all direct dependencies the code never names, Django-related or not, under "Possibly unused" (JSON: `unused`), and `--explain` says where the code uses a package. With `--python`, module names come from the environment. `--no-scan-code` turns it off, `--scan-code DIR` reads another directory.
- `-i` opens the report in the terminal: sections and packages on the left, the chosen package on the right with its notes, signs of support, command and links, and keys to search (`/`), show one status (`f`), tick packages off (`space`, kept in `.django-upgrade-report/state.json` in the project), copy the command (`e`), open the changelog (`o`), check another target (`t`) and write the report to a file (`w`). It needs the new `tui` extra (`pip install 'django-upgrade-report[tui]'`, or `uvx --with textual django-upgrade-report -i`); the tool itself still depends on `packaging` only.
- Packages to check show signs of support that their metadata does not give: when the README on PyPI of the release the report names, or of the newest one, names the target ("README of 2.1 mentions Django 5.2"). Signs are notes linked to their source and never change a status; packages to check without any sign come first. With `--evidence` (Action: `evidence: true`), also when the default branch of its GitHub repository tests the target: tox, nox and GitHub workflow matrices are read ("main branch tests Django 6.0 (tox.ini)"), and when a changelog section after your version adds or tests it ("changelog of 1.14.5 mentions Django 5.1 support"). That sends the names of those public repositories to GitHub, never of a package from git, a path or a private index. For blocked packages and ones to check without a sign, `--evidence` also lists up to two issues or pull requests in the repository whose title names the target ("open PR: Add Django 5.2 support (#912)", "merged PR: …"), searched through GitHub's API (10 searches a run, 30 with `GITHUB_TOKEN`). The JSON report has the signs under `evidence` and the issues under `upstream`.
- `--via lts` plans an upgrade across several LTS releases in steps: one report per LTS on the way to the target (`--via each`: per feature version), each starting where the one before ends, with its upgrades done and Django on the newest patch. A package blocked in one step says so in the later ones, `--fail-on` looks at every step, and the JSON report of a path has `"kind": "path"` with one report per step. Markdown folds each step, the HTML report shows them on one page under an overview with one checklist, and the GitHub Action takes `via` (the counts are summed over the steps; the tracking issue lists the tasks per step). The warning about a skipped LTS mentions it.
- `--emit renovate` and `--emit dependabot` print a configuration that makes the bots follow the plan: Django held on its series until everything that goes first is upgraded, Django in one pull request with the packages that need it, and upgrades that need each other grouped. Each rule says when to remove it.
- `--emit uv` (or `poetry`, `pdm`, `pipenv`, `pip`, `auto`) prints the commands that carry out the plan instead of the report: what to upgrade first, one command each in the report's order, upgrades that need each other in one command, then Django with what needs it; dependencies that only come with others are upgraded in the lock. For pip it names the requirement lines to change, with file and line. When the target Django needs a newer Python, the Python upgrades come first. With `--format json` the commands are in the new `commands` field, and the HTML report shows each package's command, with a copy button.
- The JSON report says on which requirement file line a package is pinned or named: `origin`, such as `"requirements/base.txt:12"`.
- The report knows which dependencies your project names itself and which only come in through others: from `pyproject.toml` or the `Pipfile` beside a lockfile, the project entries of `uv.lock` (every member of a workspace), and the requirement files themselves. The JSON report has `direct` per package (null when the source does not say), and the HTML report a switch "only direct dependencies".
- The HTML report can be searched and filtered: a search field (`/`) over names, reasons and notes, a chip per status, "only with notes", and tiles that filter by their status. The filters are kept in the address (`#status=blocked&q=allauth`), so a filtered view can be passed on; `Esc` resets them, and printing shows every row. A click on a column heading sorts by name, by the major versions a step crosses or by the last release, and every row opens to its links (PyPI, changelog, repository), its newest and last release, and the line to pin with a copy button; `j`/`k` go from row to row and `Enter` opens one. The page carries the JSON report as data. Without JavaScript it shows everything, and `--static` leaves the scripts out.
- The GitHub Action can keep one open issue per target with the plan as a task list (`issue: true`): each run keeps the ticks people set, ticks off and dates what needs nothing any more, and says once when everything is ready. `examples/weekly.yml` uses it.
- The HTML report is a checklist: every row with something to do has a box to tick, a tile counts what is done, and the ticks are remembered in the browser for that plan (a new plan starts unticked). It prints cleanly.
- The GitHub Action can put the report on the pull request as one comment that it updates on every push (`comment: true`), or only when a status or step changed (`comment: on-change`). Several projects in one repository get one comment each. A comment that cannot be written, as from a fork, never fails the job.
- `--baseline REPORT.json` compares the report with an earlier JSON report and starts with what changed: statuses, smaller or bigger steps, packages added or removed, warnings. `--only-changes` shows nothing else, and nothing when nothing changed, for a weekly job that only speaks up when there is news; `--fail-on-change any` or `worse` fails it. The JSON report has it under `changes`, and the Markdown and HTML reports show it first. The GitHub Action takes `baseline` and `fail-on-change` and reports `changes`; `examples/weekly.yml` runs it every week against last week's report.
- When the target Django needs a newer Python than your project uses, the report starts with what your dependencies need on it, all of them: the ones whose release excludes it or has no wheel for it, with the first newer release that runs on it, the ones no release fixes, how many run on it already, and whether your Django patch release declares that Python ("Django 4.2.7 does not declare Python 3.12, 4.2.8 does"). `--python-target` names another Python or turns it off, `--fail-on-python` fails the run on it, and the JSON report has it under `python`. The GitHub Action has the inputs `python-target` and `fail-on-python` and the output `python-blocked`.
- At a terminal, the report asks for what the project leaves out and that changes it: the Django you run when it is not pinned, a smaller first step when `auto` would skip an LTS, and your Python when no file names it. It then says which option gives the same report next time. It never asks in CI, with `-o`, `--explain` or a format other than text, or with `--no-input`.
- `--explain PACKAGE` shows step by step how the verdict on a package came about: the requirement lines that apply on your Python, the classifiers, whether an upper bound counts, every release looked at with its verdict, and whether the upgrade goes before or with Django. It also explains packages the report leaves out, such as ones skipped as not Django-related.
- A pre-commit hook, `django-upgrade-report`: it fails a commit that changes your dependencies when a package blocks the next Django upgrade, and lets the commit through when the report cannot be made.
- `--offline` answers from the cache only, however old, and never asks the package index; the report says how old its oldest answer is. `--prefer-cache` asks the index only for what is not in the cache.
- `--errors-as-warnings` exits with status 0 instead of 2 when the report cannot be made, for hooks that must not block a commit.
- `-q`, `--quiet` shows only the headline, warnings, blocked packages and the counts.
- An upgrade says how big the step is: "crosses 2 major versions", counted by the releases in between, each 0.x minor release as one. Calendar versions (2024.1) say "read the changelog" instead.
- The rows of the Markdown and HTML reports link the package's changelog, the link its maintainers label as such or else its GitHub releases page; the text report shows it with `-v`. The JSON report has `majors_crossed`, `changelog_url` and `repository_url`.
- Packages to check and blocked packages say when their newest pre-release declares the target, or no longer excludes it: "2.6.0.dev22 declares Django 6.1 (pre-release)". The status stays the same. The JSON report has it under `prerelease`.
- The JSON report says what kind of document it is: `"kind": "report"`. Scripts can check it before reading the rest, so later kinds that hold several reports do not break them.

### Changed

- `--fail-on` exits with status 1 when a package matches it, even if other packages could not be checked; it used to exit with 2. A blocker that is known is a result. Without a match, an incomplete report still exits with 2.
- The cache keeps a little more of each answer for the features to come: the project's links, the Django versions its description names, and per release the Python it requires and the tags of its Linux wheels. Answers cached by 0.4 are fetched once more; the old files are ignored, delete `~/.cache/django-upgrade-report` to free the space.
- An index that answers HTTP 403 because of a rate limit (`X-RateLimit-Remaining: 0` or `Retry-After`) is asked again when the limit lets it within 30 seconds, like an HTTP 429. A longer wait stops the run at once with "rate limit exceeded" instead of retrying in vain, and any other 403 still stops it.

## [0.4.0] - 2026-09-29

### Added

- `--via lts` plans an upgrade across several LTS releases in steps: one report per LTS on the way to the target (`--via each`: per feature version), each starting where the one before ends, with its upgrades done and Django on the newest patch. A package blocked in one step says so in the later ones, `--fail-on` looks at every step, and the JSON report of a path has `"kind": "path"` with one report per step. The warning about a skipped LTS mentions it.
- Packages whose job Django took over say what Django has instead, for example "built into Django 1.7: its own migrations, remove South" or "built into Django 3.1: models.JSONField" for jsonfield and django-jsonfield. They are shown even when their metadata does not mention Django. The list is short and every entry has a source (the maintainers or Django's release notes): South, django-discover-runner, django-secure, django-uuidfield, django-durationfield, django-transaction-hooks, jsonfield, django-jsonfield, django-jsonfield-backport and django-template-partials. The JSON report has it under `built_into_django`.

### Changed

- A tidier text report. An upgrade row no longer repeats "2.0 declares Django 5.2" next to "1.0 → 2.0", a manual check no longer ends every line with ", not 5.2", the project's Python moves into the line under the headline, and git URLs are shortened to `git github.com/org/fork`.
- A fork or local package says where it comes from as `from git github.com/org/fork` in every format, instead of a longer note; the advice for a blocked fork moved into the section's hint.

## [0.3.0] - 2026-09-29

### Added

- `--via lts` plans an upgrade across several LTS releases in steps: one report per LTS on the way to the target (`--via each`: per feature version), each starting where the one before ends, with its upgrades done and Django on the newest patch. A package blocked in one step says so in the later ones, `--fail-on` looks at every step, and the JSON report of a path has `"kind": "path"` with one report per step. The warning about a skipped LTS mentions it.
- Forks and local packages are judged by what they declare themselves, read locally and never sent anywhere: the installed metadata with `--python`, a local directory's `pyproject.toml`, or the constraints in `poetry.lock` and `pdm.lock`. A fork pinned years ago with `Django<4.1` now shows up as blocked instead of only "not checked", and its requirements on other packages count against their upgrades. The JSON report says where such a package comes from in the new `source` field. When only such packages are blocked, the section says that your copy excludes the target, not that no release supports it.

### Changed

- An upper bound whose upload date is unknown reads "may predate 5.2" instead of "released before 5.2".

## [0.2.4] - 2026-09-28

### Changed

- A health check (you already run the target Django) says so in the headline, "Django 6.1 · health check" instead of "Django 6.1 → 6.1", and its "Check manually" hint says that these packages already run on your Django and only their metadata lags behind.
- Why it is a health check is no longer a warning: the JSON report lists it under the new `notices`, and the text report shows it as a quiet line under the headline.
- Text report: notes go on their own line, aligned under the reason, instead of making the row longer. A note that every package in a section shares is said once for the section. The counts in the summary line are colored by status, and zeros are dimmed.

## [0.2.3] - 2026-09-28

### Changed

- Much less load on PyPI: for a pinned dependency, the small metadata of the installed release is fetched first, and the whole release history only for Django-related packages. On Saleor's lockfile that is 18 instead of 224 full fetches, 6.5 instead of 122 MB, with the same results.
- At most 8 requests in flight at once instead of 16, and four attempts instead of three, with growing pauses.

### Fixed

- One package the index could not answer for, such as an HTTP 503 from PyPI, stopped the whole run. It is now reported as "Could not check" and the rest of the report is shown; `--fail-on` exits with 2 because the report is incomplete. The JSON report lists them under `not_checked`.

## [0.2.2] - 2026-09-28

### Fixed

- The logo and the Pushing Pixels signature were nearly invisible on the PyPI page in dark mode.

## [0.2.1] - 2026-09-28

### Changed

- "Upgrade first" lists packages in the order their notes require: an upgrade that needs another one first comes after it.
- The search for the first release that declares the target bisects instead of scanning, so long release histories cost far fewer requests.

### Fixed

- Upgrades that each need the other first told you to upgrade either one first. They are now marked as going together, in the same change.
- An upgrade that another installed package forbids stayed under "Upgrade first". It is now "Check manually", with the conflict in the notes.
- Planning the upgrades re-read every requirement of every installed package for each upgrade, which made large projects slow.
- Index metadata with values that are not text crashed the run.
- A package index that knows no Django as new as your project's (a mirror that stopped syncing) gave contradictory warnings. It is now an error that says so.
- A download link that does not name its package was listed under a guessed name. It is now listed under its URL.

## [0.2.0] - 2026-09-27

First release on PyPI.

### Added

- `--via lts` plans an upgrade across several LTS releases in steps: one report per LTS on the way to the target (`--via each`: per feature version), each starting where the one before ends, with its upgrades done and Django on the newest patch. A package blocked in one step says so in the later ones, `--fail-on` looks at every step, and the JSON report of a path has `"kind": "path"` with one report per step. The warning about a skipped LTS mentions it.
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

- `--via lts` plans an upgrade across several LTS releases in steps: one report per LTS on the way to the target (`--via each`: per feature version), each starting where the one before ends, with its upgrades done and Django on the newest patch. A package blocked in one step says so in the later ones, `--fail-on` looks at every step, and the JSON report of a path has `"kind": "path"` with one report per step. The warning about a skipped LTS mentions it.
- Reads dependencies from `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements*.txt`, `requirements/*.txt`, `pyproject.toml` (PEP 621, dependency groups and Poetry) or an installed environment (`--python`).
- Sorts every Django-related package into blocked, upgrade first, upgrade together with Django, check manually and ready, and names the smallest release that declares support for the target.
- Targets: `lts` (default), `latest` or an explicit version such as `5.2`.
- Text, Markdown, JSON and self-contained HTML output.
- `--fail-on blocked|upgrade|check` for CI.
- GitHub Action that writes the report to the job summary.
- 24 hour cache for PyPI responses.

[Unreleased]: https://github.com/derblub/django-upgrade-report/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/derblub/django-upgrade-report/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/derblub/django-upgrade-report/compare/v0.2.4...v0.3.0
[0.2.4]: https://github.com/derblub/django-upgrade-report/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/derblub/django-upgrade-report/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/derblub/django-upgrade-report/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/derblub/django-upgrade-report/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/derblub/django-upgrade-report/releases/tag/v0.2.0
[0.1.0]: https://github.com/derblub/django-upgrade-report/commit/35e1e97
