# Reading the report

Every format (text, Markdown, HTML, JSON) has the same parts in the same order.

## The head

```text
Django 4.2.7 → 5.2
from uv.lock · 12 Django-related packages · Python 3.11
! Django 5.2 needs Python >=3.10 ...
```

- **The headline** names the Django you run and the target. When you already run the target, it says `Django 5.2 · health check`.
- **Notices** (dim) are good to know, such as why the report is a health check, or how old the cached answers are with `--offline`.
- **Warnings** (`!`) make the whole report questionable and come first: Django not pinned, a target that skips an LTS, a requirement that excludes the target, a Python the target Django does not support, packages the index did not answer for.

## The sections

| Section | Meaning | What to do |
| --- | --- | --- |
| **Changes since …** | With `--baseline`: what changed since an earlier report. | Look at what got worse. |
| **Python 3.12 first** | When the target Django needs a newer Python: what your dependencies need on it. See [Upgrading Python](Upgrading-Python). | Upgrade these before you switch Python. |
| **Blocked** | Your release and every newer one exclude the target. | Wait for a release, find a fork or replace the package. |
| **Upgrade first** | A newer release declares the target and still runs on your current Django. | Upgrade these one at a time, before you touch Django. |
| **Upgrade together with Django** | The release that declares the target has dropped your current Django, or needs another package that has. | Bump it in the same change as Django. |
| **Upgrade** | A newer release declares the target; your Django is unknown, so no order can be given. | Pass `--from`. |
| **Check manually** | Nothing excludes the target, but nothing declares it either. | Read the changelog or run your tests. Usually a classifier nobody updated. |
| **Ready** | The version you use already declares support. | Nothing. |

Within "Upgrade first", the order is the order to do it in: an upgrade that needs another one first comes after it.

At the end: dependencies not on the package index, dependencies not from PyPI (never looked up), how many had nothing to do with Django, the Python the target Django needs, and the counts.

## A row

```text
  ↑ django-allauth   0.54.0 → 65.7.0  crosses 11 major versions
                                      requires Django>=4.2.16, you have 4.2.7: update Django 4.2 first
```

The version column shows what you have and the release to move to: the **oldest** release that declares the target, so each change stays small. After it come the reason, then the notes. The Markdown and HTML rows link the package's changelog; the text report shows it with `-v`.

## The notes

| Note | Meaning |
| --- | --- |
| `crosses 2 major versions` | How big the step is, counted by the releases in between; each 0.x minor release counts as a major one. |
| `calendar versions, read the changelog` | The package uses years as versions (2024.1), so no count is possible. |
| `new version numbering, read the changelog` | The version epoch changed (`1!1.0`), versions before and after do not compare. |
| `requires Django>=4.2.16, you have 4.2.7: update Django 4.2 first` | The release needs a newer patch of the Django series you run. Update Django within the series first. |
| `needs django-filter>=23.3, you have 23.1: upgrade django-filter first` | The release needs a newer version of another package you pin. |
| `… upgrade X in the same change` / `upgrade together with X` | Two upgrades need each other: do them in one change. |
| `goes with X` | It needs a package that goes together with Django, so it goes with Django too. |
| `…, which excludes 2.0` | Another installed package forbids the proposed release: a decision only you can make. The package moves to "Check manually". |
| `not declared for Django 4.2.7` | The proposed release does not say it runs on your current Django either. |
| `declares Django 5.2 and newer only` | The proposed release dropped your current Django: it goes together with Django. |
| `newer releases exclude Django 6.1` | Yours may work, newer ones say they do not. Do not upgrade it blindly. |
| `latest 6.1.0 declares Django up to 5.0` | What the newest release says, for packages to check or blocked. |
| `2.0rc1 declares Django 5.2 (pre-release)` | A pre-release newer than every stable release does what no stable release does yet. The status stays. |
| `2.0b1 no longer excludes Django 5.2 (pre-release)` | For a blocked package: a fix is on the way. |
| `could not check 2.0rc1, run again later` | The index did not answer for the pre-release; the verdict stands. |
| `no release in 3 years` | The package looks unmaintained. |
| `marked inactive by its maintainers` | Its classifiers say `Development Status :: 7 - Inactive`. |
| `built into Django 3.1: models.JSONField` | Django took over the package's job: remove it instead of upgrading. The list is in `successors.py`, each entry with a source. |
| `Wagtail package: also check it against your Wagtail version` | It depends on Wagtail (or django CMS), which has its own versions to check. |
| `version not pinned, add a lockfile for exact results` | Judged by the newest release your requirement allows. |
| `outside your requirement >=1,<2` | The release to move to is outside the range you declared: widen it. |
| `installed version 1.2.3 not found on the index` | Your pinned version is not on the index (yanked, private build). |
| `from git github.com/org/fork` | A fork or local package, judged by its own metadata and never looked up. |
| `2.0 requires Python <3.12` | The release the Django plan proposes does not run on the Python the target Django needs. |
