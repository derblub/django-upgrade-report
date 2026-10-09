# How it decides

The tool reads what maintainers publish on PyPI, never your code. For every release it looks at the `Framework :: Django :: X.Y` classifiers and the `Django` requirement.

## One release, one Django version

In this order:

1. **A requirement that excludes every release of the target means no.** Each patch release counts, so `Django==5.2.17` or `Django>=5.2.3,<5.2.8` allow 5.2. Requirements that only apply to an optional extra are ignored. Lines with environment markers count when they apply to your Python (see [Dependency sources](Dependency-Sources#which-python)), and all lines that apply are combined.
2. **A `Framework :: Django :: 5.2` classifier means yes.**
3. **An upper bound that allows the target means yes, if it was set after the target came out.** `Django>=4.2,<6.0` or an exact pin counts only when the release was uploaded on or after the day the target was released. A bound written before that is a guess, not a promise: Wagtail 6.3 allows `Django<6.0` but came out before Django 5.2, and only Wagtail 6.3.4 added 5.2 support.
4. **Everything else means not declared:** classifiers that stop at an older version or start at a newer one, a major-only classifier such as `Framework :: Django :: 5`, a lower bound without an upper one, or nothing at all. That is a question, not a blocker: classifiers often lag behind releases.

For a target that is not released yet, only classifiers count, and the report says so.

## One package

- **Ready** when the version you use says yes.
- **Upgrade** when a newer release says yes. The report names the oldest one, found by bisecting the release history, so a long history costs few requests.
  - It goes **first** when that release still runs on your current Django. A release that needs a newer patch of your Django still goes first, with a note to update Django within its series.
  - It goes **together with Django** when the release excludes your whole Django series, declares only newer Django versions, or needs a newer version of another package you pin that has itself dropped your Django.
- **Check manually** when no release says yes, but yours or a newer one is not excluded.
- **Blocked** when your release and every newer one exclude the target.

Then the upgrades are fitted to each other: when another installed package forbids a proposed release, the package moves to "Check manually"; when an upgrade needs another first, it is ordered after it; upgrades that need each other are marked to go in one change.

A package counts as Django-related when it depends on Django or has a `Framework :: Django` classifier. Packages that only depend on Wagtail or django CMS are included too. Everything else is skipped, except for the [Python check](Upgrading-Python).

## Pre-releases

Pre-releases never decide a status: you cannot pin an `rc` in production. For a package to check or a blocked one, the newest pre-release is looked at when it is newer than every stable release and than yours. When it declares the target, the row says so; for a blocked package also when it no longer excludes the target. That is one more request at most per such package.

## Signs of support

A package to check may still show that it works on the target, in places metadata does not cover. The report shows these signs as notes, each linked to its source, and they never change a status or what `--fail-on` does. Within "Check manually", packages without a sign come first, and the section says how many have one.

- **README on PyPI:** the description of the release the report names, or else of the newest release, names the target: "README of 2.1 mentions Django 5.2". It is part of the answer the report already reads, so it costs nothing. Only a version written right after "Django" counts ("Django 5.2", "Django>=5.2"), not one further down a list.
- **Test matrix, with `--evidence`:** the repository named in the package's links on PyPI (GitHub only) runs its tests against the target on its default branch: "main branch tests Django 6.0 (tox.ini)". Read are `tox.ini` (`envlist`, factors such as `dj52` or `django52`, `[gh-actions:env]`), the tox settings in `pyproject.toml`, `@nox.parametrize("django", ...)` in `noxfile.py` (a list, a dict's keys, or a name for one) and the `matrix:` of GitHub workflows (keys that name Django, as a list or one per line; `exclude:` is skipped). The workflow files are guessed (`test.yml`, `tests.yml`, `ci.yml`, `main.yml`, `python-package.yml`), or listed through the API when `GITHUB_TOKEN` is set: at most eight files per package, from `raw.githubusercontent.com`, cached for a day. "main branch" matters: it is about code not yet released. `main` or `latest` is never read as a version.

## Packages Django took over

Some packages did a job Django now does itself, and no metadata says so: South, django-jsonfield, django-secure and a few more. Their rows say what Django has instead. Every entry in [`successors.py`](https://github.com/derblub/django-upgrade-report/blob/main/src/django_upgrade_report/successors.py) needs a source: the package's maintainers pointing to Django, or Django's release notes.

## Seeing it for one package

```console
django-upgrade-report --explain wagtail
```

```text
wagtail against Django 5.2 · django-upgrade-report 0.x
Inputs
  wagtail 6.3 (pinned), from requirements.txt
  your Django 4.2.7
  against Django 5.2, markers on Python 3.10
Your release
  requirement: Django<6.0,>=4.2: allows Django 5.2
  classifiers: Django 4.2, 5.0, 5.1: does not include 5.2
  upper bound: uploaded 2024-11-01, Django 5.2 came out 2025-04-02: set before it, does not count
  verdict: likely: allows Django<6.0,>=4.2, released before 5.2
Releases looked at (10)
  7.0 (2025-05-06): yes, declares Django 5.2
  6.3.4 (2025-04-24): yes, allows Django<6.0,>=4.2
  6.3.3 (2025-02-03): likely, allows Django<6.0,>=4.2, released before 5.2
  ...
Before or with Django
  6.3.4 on your Django 4.2.7: yes, declares Django 4.2 → before Django
Result
  upgrade to 6.3.4 first, before Django: 6.3.4 allows Django<6.0,>=4.2
```

It also explains packages the report leaves out, such as one skipped as not Django-related. The last step of "Your release" is the verdict of the rules above itself, so an explanation can never disagree with the report. Paste it into a [wrong verdict issue](https://github.com/derblub/django-upgrade-report/issues/new/choose) when you still think it is wrong.
