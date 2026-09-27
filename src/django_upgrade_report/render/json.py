from __future__ import annotations

import json

from django_upgrade_report import __version__
from django_upgrade_report.analysis import Report


def as_dict(report: Report) -> dict:
    return {
        "tool": {"name": "django-upgrade-report", "version": __version__},
        "generated": report.generated.isoformat(),
        "target": report.target,
        "current_django": report.current_django,
        "django_requires_python": report.django_requires_python,
        "source": report.source,
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
        "skipped_non_django": report.skipped,
    }


def render(report: Report) -> str:
    return json.dumps(as_dict(report), indent=2) + "\n"
