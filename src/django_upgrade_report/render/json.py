"""Machine-readable report. The shape is versioned by ``schema_version``.

Adding a field keeps the version; renaming, removing or retyping one bumps it.

Top level (schema_version 1):

- ``schema_version`` (int): 1.
- ``tool``: ``name``, ``version`` and ``author`` of the tool that wrote the report.
- ``generated`` (str): ISO 8601 timestamp, UTC.
- ``target`` (str): the Django version checked against, e.g. ``"5.2"``.
- ``target_released`` (bool): false for a version that is not out yet (only classifiers count).
- ``current_django`` (str | null): the project's Django version: pinned, given with
  ``--from``, or assumed from a range (a warning says so).
- ``django_requires_python`` (str | null): ``Requires-Python`` of the target Django.
- ``project_python`` (str | null): the project's Python as ``X.Y``, when known.
- ``source`` (str): where the dependencies were read from.
- ``warnings`` (list of str): things that make the whole report questionable, show them first.
- ``counts`` (object): number of packages per status (``ready``, ``upgrade``, ``check``,
  ``blocked``).
- ``packages`` (list): one object per Django-related package, most urgent first:
  ``name``, ``current`` (pinned version or null), ``spec`` (requirement when not pinned, or
  null), ``latest``, ``status``, ``upgrade_to`` (release to move to, or null), ``phase``
  (``"before"``, ``"with"`` or null), ``reason``, ``notes`` (list of str) and
  ``last_release`` (ISO 8601 or null).
- ``not_on_index`` (list of str): dependencies the package index does not know.
- ``external`` (list): dependencies not from PyPI and never looked up, as objects with
  ``name`` and ``source`` (e.g. ``"git https://github.com/org/repo"``).
- ``skipped_non_django`` (int): dependencies without a Django requirement.
"""

from __future__ import annotations

import json

from django_upgrade_report import AUTHOR, COMPANY, COMPANY_URL, __version__
from django_upgrade_report.analysis import Report

SCHEMA_VERSION = 1


def as_dict(report: Report) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {
            "name": "django-upgrade-report",
            "version": __version__,
            "author": f"{AUTHOR}, {COMPANY} ({COMPANY_URL})",
        },
        "generated": report.generated.isoformat(),
        "target": report.target,
        "target_released": report.target_released,
        "current_django": report.current_django,
        "django_requires_python": report.django_requires_python,
        "project_python": report.project_python,
        "source": report.source,
        "warnings": report.warnings,
        "counts": {status.value: n for status, n in report.counts.items()},
        "packages": [
            {
                "name": p.display_name,
                "current": p.current,
                "spec": p.spec or None,
                "latest": p.latest,
                "status": p.status.value,
                "upgrade_to": p.target_version,
                "phase": p.phase.value if p.phase else None,
                "reason": p.reason,
                "notes": p.notes,
                "last_release": p.last_release.isoformat() if p.last_release else None,
            }
            for p in report.packages
        ],
        "not_on_index": report.missing,
        "external": [{"name": name, "source": where} for name, where in report.external],
        "skipped_non_django": report.skipped,
    }


def render(report: Report) -> str:
    return json.dumps(as_dict(report), indent=2) + "\n"
