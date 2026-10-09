# django-upgrade-report

**Which of your dependencies block a Django upgrade, and in which order to upgrade them.**

django-upgrade-report reads your lockfile, asks PyPI what every Django-related package declares, and gives you the upgrade plan: blockers first, then the smallest safe step for each package, in the right order. When the new Django needs a newer Python, it checks every dependency on that Python too.

```console
uvx django-upgrade-report
```

This wiki is the manual. The [README](https://github.com/derblub/django-upgrade-report#readme) is the short tour; here every option, rule and output is explained in full.

## Start here

| If you want to | Read |
| --- | --- |
| Run it on your project for the first time | [Getting started](Getting-Started) |
| Understand a row of the report | [Reading the report](Reading-the-Report) |
| Know why a package got its verdict | [How it decides](How-It-Decides), then `--explain` |
| Move to a newer Python as well | [Upgrading Python](Upgrading-Python) |
| Use lockfiles, forks or a private index | [Dependency sources](Dependency-Sources) |
| Run it in GitHub Actions, GitLab or pre-commit | [Continuous integration](Continuous-Integration) |
| Look up an option | [Command-line reference](Command-Line-Reference) |
| Process the JSON report | [JSON report reference](JSON-Report-Reference) |
| Look up an input of the GitHub Action | [GitHub Action reference](GitHub-Action-Reference) |
| Fix something that does not work | [Troubleshooting](Troubleshooting) |
| Change the code | [Architecture](Architecture) |

## What it is not

The report shows what maintainers declare, not whether your tests pass. Use it to plan the upgrade, then run [django-upgrade](https://github.com/adamchainz/django-upgrade) on your own code and your test suite with `python -W error::DeprecationWarning`.

## Privacy in one sentence

Only the names and versions of packages that come from PyPI are sent to PyPI; your code, git and path dependencies and packages from a private index stay where they are (see [Dependency sources](Dependency-Sources)).
