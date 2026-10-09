"""What changed since an earlier JSON report: the baseline of ``--baseline``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from django_upgrade_report.analysis import SEVERITY, PackageReport, Report, Status

BETTER, WORSE, SAME, NEW, GONE = "better", "worse", "same", "new", "gone"


class BaselineError(ValueError):
    """The baseline file cannot be read as a report of this tool."""


@dataclass(frozen=True)
class Change:
    name: str
    """The package's name, or ``""`` for a change to the whole report (a warning)."""
    kind: str
    """``"status"``, ``"upgrade"``, ``"new"``, ``"gone"`` or ``"warning"``."""
    before: str | None
    after: str | None
    direction: str
    """``"better"``, ``"worse"``, ``"same"``, ``"new"`` or ``"gone"``."""
    text: str


@dataclass(frozen=True)
class Changes:
    since: str
    """When the baseline was generated (ISO 8601)."""
    target: str
    """The Django version the baseline was for."""
    items: list[Change]
    compared: bool = True
    """False when the baseline was for another target: nothing was compared."""

    @property
    def worse(self) -> bool:
        """Something needs more attention than before."""
        return any(
            c.direction == WORSE or (c.direction == NEW and c.after != "ready") for c in self.items
        )


def load_baseline(path: Path) -> dict:
    """An earlier ``--format json`` report, checked enough to compare against."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BaselineError(f"cannot read the baseline {path}: {exc.strerror or exc}") from None
    except ValueError:
        raise BaselineError(f"the baseline {path} is not JSON") from None
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise BaselineError(
            f"the baseline {path} is not a JSON report of django-upgrade-report (schema_version 1)"
        )
    if data.get("kind", "report") != "report":  # reports before 0.5 have no kind
        raise BaselineError(f"the baseline {path} holds a {data['kind']}, not a single report")
    if not isinstance(data.get("packages"), list):
        raise BaselineError(f"the baseline {path} has no list of packages")
    return data


def compare(baseline: dict, report: Report) -> Changes:
    """What is different in ``report`` from ``baseline``, most important first.

    A baseline for another target is not compared: every verdict depends on the target.
    """
    since = str(baseline.get("generated") or "an earlier run")
    target = str(baseline.get("target") or "")
    if target != report.target:
        return Changes(since, target or "an unknown target", [], compared=False)
    # Keyed by the name shown in both reports, the only one the JSON report has.
    before = {}
    for p in baseline["packages"]:
        if isinstance(p, dict) and isinstance(p.get("name"), str):
            before[canonicalize_name(p["name"])] = p
    now = {canonicalize_name(p.display_name): p for p in report.packages}
    unanswered = {canonicalize_name(name) for name in report.failed}
    items = []
    for name in sorted(before.keys() | now.keys()):
        old, new = before.get(name), now.get(name)
        if old is None:
            items.append(Change(new.display_name, "new", None, _label_of(new), NEW, new.reason))
            continue
        if new is None:
            if name not in unanswered:  # not checked this time is not gone
                gone = "no longer a dependency"
                items.append(Change(old["name"], "gone", _label(old), None, GONE, gone))
            continue
        was, is_now = _label(old), _label_of(new)
        if was != is_now:
            direction = _direction(_rank(old), _rank_of(new))
            items.append(Change(new.display_name, "status", was, is_now, direction, new.reason))
        elif old.get("upgrade_to") != new.target_version:
            items.append(_step_change(new, old.get("upgrade_to")))
    old_warnings = {w for w in baseline.get("warnings") or () if _lasting(w)}
    for warning in dict.fromkeys(w for w in report.warnings if _lasting(w)):
        if warning not in old_warnings:
            items.append(Change("", "warning", None, warning, WORSE, f"new warning: {warning}"))
    for warning in sorted(old_warnings - set(report.warnings)):
        items.append(Change("", "warning", warning, None, BETTER, f"warning gone: {warning}"))
    order = {WORSE: 0, NEW: 1, BETTER: 2, SAME: 3, GONE: 4}
    items.sort(key=lambda c: order[c.direction])  # sort() is stable: names stay in order
    return Changes(since, target, items)


def _lasting(warning: object) -> bool:
    """A warning about the project, not about a failed lookup that the next run may not have."""
    return isinstance(warning, str) and not warning.startswith("Could not check ")


def _step_change(new: PackageReport, old: object) -> Change:
    """The release to move to changed, with the same status: a bigger step is worse."""
    before = old if isinstance(old, str) else None
    after = new.target_version
    if before is None or after is None:
        text = f"now {after}" if after else f"no longer {before}"
        return Change(new.display_name, "upgrade", before, after, SAME, text)
    try:
        bigger = Version(after) > Version(before)
    except InvalidVersion:
        bigger = False
    direction = WORSE if bigger else BETTER
    text = f"now {after} instead of {before}"
    return Change(new.display_name, "upgrade", before, after, direction, text)


_LABELS = {
    ("upgrade", "before"): "upgrade first",
    ("upgrade", "with"): "upgrade with Django",
}


def _label(p: dict) -> str:
    status = str(p.get("status"))
    return _LABELS.get((status, p.get("phase")), status)


def _label_of(p: PackageReport) -> str:
    return _label({"status": p.status.value, "phase": p.phase.value if p.phase else None})


def _rank(p: dict) -> tuple[int, bool]:
    try:
        severity = SEVERITY[Status(p.get("status"))]
    except ValueError:
        severity = -1
    return severity, p.get("phase") == "with"


def _rank_of(p: PackageReport) -> tuple[int, bool]:
    return _rank({"status": p.status.value, "phase": p.phase.value if p.phase else None})


def _direction(before: tuple[int, bool], after: tuple[int, bool]) -> str:
    if after < before:
        return BETTER
    return WORSE if after > before else SAME
