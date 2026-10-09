"""Machine-readable report. The shape is versioned by ``schema_version``.

Adding a field keeps the version; renaming, removing or retyping one bumps it.

Top level (schema_version 1):

- ``schema_version`` (int): 1.
- ``kind`` (str): ``"report"``, one report for one project and target. Read it before the
  rest: later versions may write documents of other kinds.
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
- ``notices`` (list of str): good to know, nothing to worry about, e.g. why the report is a
  health check of the Django the project already runs.
- ``counts`` (object): number of packages per status (``ready``, ``upgrade``, ``check``,
  ``blocked``).
- ``packages`` (list): one object per Django-related package, most urgent first:
  ``name``, ``current`` (pinned version or null), ``spec`` (requirement when not pinned, or
  null), ``latest``, ``status``, ``upgrade_to`` (release to move to, or null), ``phase``
  (``"before"``, ``"with"`` or null), ``reason``, ``notes`` (list of str),
  ``last_release`` (ISO 8601 or null) and ``source`` (where a package not from PyPI comes
  from, e.g. ``"git https://github.com/org/fork"``, or null). A package with a ``source``
  was judged by its own metadata, read locally, and never looked up.
  ``built_into_django`` is null, or, when Django took over the package's job by the
  target, an object with ``since`` (e.g. ``"3.1"``), ``replacement`` (e.g.
  ``"models.JSONField"``) and ``source`` (a URL where that is written down).
  ``prerelease`` is null, or, for a package to check or blocked, the newest pre-release when
  it declares the target, or no longer excludes it for a blocked package: an object with
  ``version`` (e.g. ``"2.0rc1"``), ``reason`` and ``uploaded`` (ISO 8601 or null).
  ``majors_crossed`` (int or null): major versions between ``current`` and ``upgrade_to``,
  counted by the releases in between, each 0.x minor as one; null without both versions or
  for calendar versions. ``changelog_url`` (the link the project labels as its changelog, else
  its GitHub releases page) and ``repository_url`` (its GitHub repository), or null; both
  come from the project's own metadata on the index. ``direct`` (bool or null): true when
  the project names the package itself, false when only another dependency needs it, null
  when the source does not say (a lockfile without its ``pyproject.toml`` or ``Pipfile``).
  ``origin`` (str or null): the requirement file line that pins or names it, as
  ``"requirements/base.txt:12"``, relative to the project; null for other sources.
  ``evidence`` (list): for a package to check, signs of support the metadata does not give,
  each with ``kind`` (``"readme"``, ``"test-matrix"`` or ``"changelog"``), ``text`` (also in
  ``notes``-like form, e.g. ``"README of 2.1 mentions Django 5.2"``) and ``url`` (or null).
  They never change the status. ``upstream`` (list): with ``--evidence``, for a blocked
  package or one to check without a sign, at most two issues or pull requests in its GitHub
  repository whose title names the target: ``kind`` (``"issue"`` or ``"pr"``), ``state``
  (``"open"``, ``"closed"`` or ``"merged"``), ``title`` (at most 80 characters, from outside:
  escape it), ``url``, ``number`` and ``updated`` (ISO 8601 or null).
- ``not_on_index`` (list of str): dependencies the package index does not know.
- ``not_checked`` (list of str): dependencies the index could not answer for, even after
  retries. When not empty, the report is incomplete; ``warnings`` says why.
- ``external`` (list): dependencies not from PyPI, never looked up and not judged (no
  Django-related metadata could be read locally), as objects with
  ``name`` and ``source`` (e.g. ``"git https://github.com/org/repo"``).
- ``skipped_non_django`` (int): dependencies without a Django requirement.
- ``unused`` (list of str): direct dependencies, Django-related or not, that the project's
  code never names (read locally); empty when the code was not read (``--no-scan-code``, a
  file as the project, no Python code, or too big to read).
- ``changes`` (object or null): with ``--baseline``, what changed since that report:
  ``since`` (its ``generated``), ``target`` (its target), ``compared`` (false when that target
  is not this report's: then nothing is compared) and ``items``, most important first,
  each with ``name`` (null for a warning), ``kind`` (``"status"``, ``"upgrade"``, ``"new"``,
  ``"gone"`` or ``"warning"``), ``from`` and ``to`` (e.g. ``"blocked"``, ``"upgrade first"``,
  a version, a warning, or null), ``direction`` (``"better"``, ``"worse"``, ``"same"``,
  ``"new"`` or ``"gone"``) and ``text``.
- ``python`` (object or null): when the target Django needs a newer Python than the project
  uses, or ``--python-target`` names one, what every pinned dependency from PyPI needs on it:
  ``target`` and ``current`` (X.Y), ``packages`` (objects like those under ``packages``, with
  ``status`` ``"upgrade"``, ``"check"`` or ``"blocked"`` and ``upgrade_to`` the first release
  that runs on the target), ``ready`` and ``pure`` (how many run on it already, the second
  pure Python), ``silent`` (names whose release says nothing about Python), ``not_checked``
  (names the index did not answer for) and ``django_note`` (str or null: whether your Django
  patch release declares the target Python, or which one does).
- ``commands`` (object or null): with ``--emit``, the commands that carry out the plan:
  ``tool`` (``"uv"``, ``"poetry"``, ``"pdm"``, ``"pip"`` or ``"pipenv"``), ``steps``, in
  order, each with ``phase`` (``"python"``, ``"before"``, ``"with"`` or ``"upgrade"``) and
  ``commands`` (list of str, shell-quoted; for pip, comments with the lines to change, with
  the file and line when known), and ``left_out`` (str, e.g. ``"django-taggit (blocked)"``:
  packages that need a person).
- ``explain`` (object): for each package given with ``--explain``, by canonical name, how its
  verdict came about: a list of objects with ``section`` (``"inputs"``, ``"release"``,
  ``"search"``, ``"phase"`` or ``"result"``) and ``text``, in the order they happened. Empty
  without ``--explain``.

A path (``--via``) is a document of its own, ``"kind": "path"``: ``schema_version``, ``kind``,
``tool``, ``generated``, ``target``, ``via`` (``"lts"`` or ``"each"``), ``blocked_at`` (the
first step, from 1, with a blocked package, or null) and ``steps``: one report as above per
step, each with ``"kind": "report"``, the first from the Django you run, every later one from
where the step before ends.
"""

from __future__ import annotations

import json

from django_upgrade_report import AUTHOR, COMPANY, COMPANY_URL, __version__, commands
from django_upgrade_report.analysis import PackageReport, PathReport, Report
from django_upgrade_report.commands import Commands

SCHEMA_VERSION = 1


def as_dict(report: Report) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": report.kind,
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
        "notices": report.notices,
        "counts": {status.value: n for status, n in report.counts.items()},
        "packages": [_package(p) for p in report.packages],
        "not_on_index": report.missing,
        "not_checked": report.failed,
        "external": [{"name": name, "source": where} for name, where in report.external],
        "skipped_non_django": report.skipped,
        "unused": report.unused,
        "changes": _changes(report),
        "python": _python(report),
        "explain": {
            name: [{"section": line.section, "text": line.text} for line in lines]
            for name, lines in report.explanations.items()
        },
    }


def _package(p: PackageReport) -> dict:
    return {
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
        "source": p.source,
        "built_into_django": {
            "since": str(p.successor.since),
            "replacement": p.successor.replacement,
            "source": p.successor.source,
        }
        if p.successor
        else None,
        "prerelease": {
            "version": p.prerelease.version,
            "reason": p.prerelease.reason,
            "uploaded": p.prerelease.uploaded.isoformat() if p.prerelease.uploaded else None,
        }
        if p.prerelease
        else None,
        "majors_crossed": p.majors_crossed,
        "changelog_url": p.changelog_url,
        "repository_url": p.repository_url,
        "direct": p.direct,
        "origin": p.origin,
        "evidence": [{"kind": e.kind, "text": e.text, "url": e.url} for e in p.evidence],
        "upstream": [
            {
                "kind": i.kind,
                "state": i.state,
                "title": i.title,
                "url": i.url,
                "number": i.number,
                "updated": i.updated or None,
            }
            for i in p.upstream
        ],
    }


def _changes(report: Report) -> dict | None:
    changes = report.changes
    if changes is None:
        return None
    return {
        "since": changes.since,
        "target": changes.target,
        "compared": changes.compared,
        "items": [
            {
                "name": c.name or None,
                "kind": c.kind,
                "from": c.before,
                "to": c.after,
                "direction": c.direction,
                "text": c.text,
            }
            for c in changes.items
        ],
    }


def _python(report: Report) -> dict | None:
    plan = report.python
    if plan is None:
        return None
    return {
        "target": plan.target,
        "current": plan.current,
        "packages": [_package(p) for p in plan.packages],
        "ready": plan.ready,
        "pure": plan.pure,
        "silent": plan.silent,
        "not_checked": plan.unknown,
        "django_note": plan.django_note,
    }


def render(report: Report, emitted: Commands | None = None) -> str:
    data = as_dict(report)
    data["commands"] = commands.as_dict(emitted) if emitted else None
    return json.dumps(data, indent=2) + "\n"


def render_path(path: PathReport) -> str:
    data = {
        "schema_version": SCHEMA_VERSION,
        "kind": path.kind,
        "tool": as_dict(path.steps[0])["tool"],
        "generated": path.generated.isoformat(),
        "target": path.target,
        "via": path.via,
        "blocked_at": path.blocked_at,
        "steps": [as_dict(step) for step in path.steps],
    }
    return json.dumps(data, indent=2) + "\n"
