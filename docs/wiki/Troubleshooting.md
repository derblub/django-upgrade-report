# Troubleshooting

## A verdict looks wrong

Run `django-upgrade-report --explain PACKAGE` the way you ran the report. It shows the requirement lines that apply on your Python, the classifiers, whether an upper bound counts, and every release it looked at. Most surprises are one of these:

- **"Check manually" although it works:** the maintainers did not add the classifier for the new Django. The tool refuses to guess; your test suite decides.
- **An upper bound that does not count:** a bound such as `<6.0` set before Django 5.2 came out is not a promise of 5.2 support (see [How it decides](How-It-Decides)).
- **The wrong Python:** markers are evaluated on your project's Python. Check the Python in the report's second line; set it with `.python-version` or `--python`.

If it is still wrong, open a [wrong verdict issue](https://github.com/derblub/django-upgrade-report/issues/new/choose) with the `--explain` output.

## "Django is not pinned"

Your requirements give Django as a range. Pass `--from 4.2.16` with the version you run, use a lockfile, or answer the question at a terminal.

## Many packages "not checked"

They come from git, a path or a private index and are never looked up. See [Dependency sources](Dependency-Sources#packages-not-from-pypi): `--python` judges forks by their installed metadata, `--index-url` or `--check-private-on-pypi` covers private indexes.

## "Could not check …, run again later"

PyPI did not answer for that package after four attempts. The rest of the report is complete; run it again. With `--fail-on`, an incomplete report exits with 2 unless a package already matched.

## "rate limit exceeded"

An index that rate-limits (or GitHub, for the pull request comment) asked to wait longer than 30 seconds. Run again later; the cache keeps what was already fetched.

## Offline, on a plane or in a locked-down CI

`--offline` answers from the cache only and says how old the oldest answer is; `--prefer-cache` asks the index only for what is missing. Run once online to fill the cache. A pre-commit hook with `--errors-as-warnings` never blocks a commit because of the network.

## The cache takes space

`~/.cache/django-upgrade-report`, or `$XDG_CACHE_HOME/django-upgrade-report`. Delete it whenever you like; a new version of the tool may ignore older files anyway.

## The pull request comment does not appear

The job needs `permissions: pull-requests: write`. A pull request from a fork gets a read-only token, so there is no comment; the log says so and the job goes on. The comment only runs for `pull_request` and `pull_request_target` events.

## Questions in the terminal

At a terminal the tool asks for what the project leaves out. `--no-input` turns that off; in CI (when `CI` is set), with `-o`, `--explain` or a format other than text it never asks.
