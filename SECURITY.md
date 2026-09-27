# Security policy

## Supported versions

Security fixes go into the latest release. Please upgrade before reporting.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through [GitHub's vulnerability reporting](https://github.com/derblub/django-upgrade-report/security/advisories/new) or by email to [daniel@pushingpixels.at](mailto:daniel@pushingpixels.at).

You will get an answer within five working days. Once a fix is released, the report is credited in the changelog unless you prefer otherwise.

## What the tool does with your data

django-upgrade-report sends only package names and versions to the package index you point it at (PyPI by default). It does not read your source code, and it sends nothing anywhere else. Responses are cached in `~/.cache/django-upgrade-report`.
