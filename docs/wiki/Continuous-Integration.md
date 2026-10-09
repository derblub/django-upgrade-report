# Continuous integration

Every input and output of the action is in the [GitHub Action reference](GitHub-Action-Reference); this page shows them in whole workflows.

## GitHub Actions: on every pull request

The Markdown report goes to the job summary; with `comment` it also goes on the pull request as one comment that is updated on every push.

```yaml
name: Django upgrade report
on: pull_request

permissions:
  contents: read
  pull-requests: write    # only for comment

jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: derblub/django-upgrade-report@v1
        with:
          comment: on-change     # update the comment only when a status or step changed
          fail-on: blocked
```

A pull request from a fork gets a read-only token: then the comment is left out and the job goes on. With several projects in one repository, give each its own `path`; in a matrix over one path, set `comment-key` so each run has its own comment.

## GitHub Actions: every week, with what changed

[`examples/weekly.yml`](https://github.com/derblub/django-upgrade-report/blob/main/examples/weekly.yml) runs the report every Monday and keeps it in the Actions cache. Each summary starts with what changed since last week, such as a blocked package that has a release for the target now, and the step fails only when something needs more attention than before (`fail-on-change: worse`). The first run has nothing to compare with and just writes the report.

## GitHub Actions: an issue with the plan

With `issue: true` the action keeps one open issue per target, "Django 5.2 upgrade plan", labelled `django-upgrade-report`, with every row that needs something as a task:

```markdown
- [ ] **django-filter** 23.1 → 25.1: upgrade first, 25.1 declares Django 5.2
- [x] **django-allauth** 0.54.0 → 65.7.0: upgrade first, 65.7.0 declares Django 5.2
- [x] ~~**django-taggit**: blocked, latest 5.0 requires Django\<5.2~~ (nothing to do since 2026-11-02)
```

Each run updates it: the ticks people set stay, rows that need nothing any more are ticked off and dated, and when nothing is left the issue says "Everything is ready for Django 5.2" once. It stays open: closing it is for people. The job needs `permissions: issues: write`; [`examples/weekly.yml`](https://github.com/derblub/django-upgrade-report/blob/main/examples/weekly.yml) has it. Several projects in one repository get an issue each, named after `comment-key` or the path.

## GitHub Actions: using the outputs

```yaml
      - uses: derblub/django-upgrade-report@v1
        id: django
      - run: echo "${{ steps.django.outputs.blocked }} blocked, ${{ steps.django.outputs.upgrade }} to upgrade"
      - run: jq '.packages[] | select(.status == "blocked") | .name' "${{ steps.django.outputs.report }}"
```

## GitLab CI

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

## pre-commit

```yaml
- repo: https://github.com/derblub/django-upgrade-report
  rev: v1.1.0
  hooks:
    - id: django-upgrade-report
```

The hook runs when a lockfile, a requirements file or `pyproject.toml` at the repository root changes, and fails the commit when a package blocks the next Django upgrade. It answers from the cache where it can (`--prefer-cache`), shows only what blocks (`--quiet`), and lets the commit through when the report cannot be made (`--errors-as-warnings`). Add `args: [--target, "6.1"]` to check another target. For a project in a subdirectory, override `files` and pass its path in `args`.

## Anywhere else

```console
django-upgrade-report --format markdown >> "$GITHUB_STEP_SUMMARY"
django-upgrade-report --format json --output upgrade-report.json
django-upgrade-report --baseline last-week.json --only-changes     # nothing in a quiet week
```

## Failing the build

| Option | Fails (exit 1) when |
| --- | --- |
| `--fail-on blocked` / `upgrade` / `check` | a Django-related package has that status or a worse one |
| `--fail-on-python blocked` / `upgrade` / `check` | a dependency has it on the newer Python |
| `--fail-on-change any` / `worse` | something changed since `--baseline`, or something got worse |

Exit status 2 always means the tool could not run (no dependencies found, the index unreachable), so a pipeline can tell "packages need attention" from "the check failed". With `--fail-on`, a report the index left incomplete also exits with 2, unless a package already matched.
