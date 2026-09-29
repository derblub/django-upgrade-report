"""Packages whose job Django itself took over, with where that is written down.

The metadata of a package cannot say that Django made it unnecessary, so these few are
listed by hand. An entry needs a source: the package's own maintainers pointing to Django,
or Django's release notes adding the same feature. Packages that are merely quiet, or that
someone considers replaced by a different third-party package, do not belong here.
"""

from __future__ import annotations

from dataclasses import dataclass

from packaging.version import Version


@dataclass(frozen=True)
class Successor:
    replacement: str
    """What Django has instead, e.g. ``models.JSONField``."""
    since: Version
    """The first Django feature version that has it."""
    source: str
    """Where this is written down."""

    def note(self) -> str:
        return f"built into Django {self.since}: {self.replacement}"


def _entry(replacement: str, since: str, source: str) -> Successor:
    return Successor(replacement, Version(since), source)


_JSONFIELD = _entry(
    "models.JSONField",
    "3.1",
    "https://docs.djangoproject.com/en/stable/releases/3.1/#jsonfield-for-all-supported-database-backends",
)

SUCCESSORS: dict[str, Successor] = {
    "south": _entry(
        "its own migrations, remove South",
        "1.7",
        "https://docs.djangoproject.com/en/stable/releases/1.7/#schema-migrations",
    ),
    "django-discover-runner": _entry(
        "DiscoverRunner, the default test runner",
        "1.6",
        "https://pypi.org/project/django-discover-runner/",
    ),
    "django-secure": _entry(
        "SecurityMiddleware and the security checks",
        "1.8",
        "https://pypi.org/project/django-secure/",
    ),
    "django-uuidfield": _entry(
        "models.UUIDField",
        "1.8",
        "https://docs.djangoproject.com/en/stable/releases/1.8/#new-data-types",
    ),
    "django-durationfield": _entry(
        "models.DurationField",
        "1.8",
        "https://docs.djangoproject.com/en/stable/releases/1.8/#new-data-types",
    ),
    "django-transaction-hooks": _entry(
        "transaction.on_commit()",
        "1.9",
        "https://pypi.org/project/django-transaction-hooks/",
    ),
    "jsonfield": _JSONFIELD,
    "django-jsonfield": _JSONFIELD,
    "django-jsonfield-backport": _JSONFIELD,
    "django-template-partials": _entry(
        "template partials ({% partialdef %})",
        "6.0",
        "https://pypi.org/project/django-template-partials/",
    ),
}


def successor(name: str, target: Version) -> Successor | None:
    """What replaces ``name`` on Django ``target``, if Django has taken it over by then."""
    found = SUCCESSORS.get(name)
    return found if found is not None and found.since <= target else None
