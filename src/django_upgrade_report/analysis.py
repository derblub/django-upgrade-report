"""Decide, per dependency, whether it supports the target Django version."""

from __future__ import annotations

import enum
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from django_upgrade_report.pypi import Project, PyPI, ReleaseInfo
from django_upgrade_report.sources import Dependency, DependencySet

_CLASSIFIER = re.compile(r"^Framework :: Django :: (\d+\.\d+)$")
STALE_AFTER_DAYS = 2 * 365
MAX_RELEASES_SCANNED = 40


class Verdict(enum.Enum):
    """What one release says about one Django version."""

    YES = "yes"  # declared: classifier for this version, or an upper bound above it
    LIKELY = "likely"  # allowed by the requirement, but not declared
    UNKNOWN = "unknown"  # no information at all
    NO = "no"  # excluded by the requirement, or only older Django versions declared


class Status(enum.Enum):
    READY = "ready"
    UPGRADE = "upgrade"
    CHECK = "check"
    BLOCKED = "blocked"


class Phase(enum.Enum):
    BEFORE = "before"  # the new version still runs on your current Django: upgrade it first
    WITH = "with"  # the new version needs the new Django: upgrade it together with Django


@dataclass(frozen=True)
class Support:
    verdict: Verdict
    reason: str


@dataclass
class PackageReport:
    name: str
    display_name: str
    current: str | None
    spec: str
    latest: str
    status: Status
    reason: str
    target_version: str | None = None
    """The smallest release that declares support for the target, when an upgrade is needed."""
    phase: Phase | None = None
    last_release: datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        if self.last_release is None:
            return False
        return (datetime.now(timezone.utc) - self.last_release).days > STALE_AFTER_DAYS


@dataclass
class Report:
    target: str
    current_django: str | None
    django_requires_python: str | None
    source: str
    packages: list[PackageReport]
    skipped: int
    """Dependencies that have nothing to do with Django."""
    missing: list[str]
    """Dependencies not found on the index (private packages, typos)."""
    generated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def by_status(self, status: Status) -> list[PackageReport]:
        return [p for p in self.packages if p.status is status]

    @property
    def counts(self) -> dict[Status, int]:
        return {status: len(self.by_status(status)) for status in Status}


# --- one release, one Django version -----------------------------------------


def declared_versions(info: ReleaseInfo) -> list[Version]:
    found = []
    for classifier in info.classifiers:
        match = _CLASSIFIER.match(classifier)
        if match:
            found.append(Version(match.group(1)))
    return sorted(found)


def django_requirement(info: ReleaseInfo) -> SpecifierSet | None:
    for line in info.requires_dist:
        try:
            req = Requirement(line)
        except InvalidRequirement:
            continue
        if canonicalize_name(req.name) != "django":
            continue
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue  # only needed for an optional extra
        return req.specifier
    return None


def _allows(spec: SpecifierSet, target: Version) -> bool:
    """True when some patch release of the ``target`` minor version satisfies ``spec``."""
    probes = [target, Version(f"{target.major}.{target.minor}.99")]
    return any(spec.contains(probe, prereleases=True) for probe in probes)


def _has_upper_bound_above(spec: SpecifierSet, target: Version) -> bool:
    for s in spec:
        if s.operator in ("<", "<=", "~=", "==") and _allows(SpecifierSet(str(s)), target):
            return True
    return False


def supports(info: ReleaseInfo, target: Version) -> Support:
    spec = django_requirement(info)
    declared = declared_versions(info)
    label = f"{target.major}.{target.minor}"

    if spec is not None and str(spec) and not _allows(spec, target):
        return Support(Verdict.NO, f"requires Django{spec}")
    if target in declared:
        return Support(Verdict.YES, f"declares Django {label}")
    if spec is not None and _has_upper_bound_above(spec, target):
        return Support(Verdict.YES, f"allows Django{spec}")
    # Classifiers often lag behind releases, so a missing one is a question, not a blocker.
    if declared and max(declared) < target:
        newest = declared[-1]
        return Support(
            Verdict.LIKELY, f"declares Django up to {newest.major}.{newest.minor}, not {label}"
        )
    if declared and min(declared) > target:
        oldest = declared[0]
        return Support(
            Verdict.LIKELY, f"declares Django {oldest.major}.{oldest.minor} and newer, not {label}"
        )
    if declared:
        return Support(Verdict.LIKELY, f"declares Django {_span(declared)}, not {label}")
    if spec is not None and str(spec):
        return Support(Verdict.LIKELY, f"allows Django{spec}, no upper bound")
    return Support(Verdict.UNKNOWN, "declares no Django versions")


def _span(versions: list[Version]) -> str:
    low, high = versions[0], versions[-1]
    return f"{low.major}.{low.minor}–{high.major}.{high.minor}"


def is_django_related(info: ReleaseInfo) -> bool:
    return django_requirement(info) is not None or any(
        c.startswith("Framework :: Django") for c in info.classifiers
    )


# --- the whole project -------------------------------------------------------


def latest_lts(django: Project) -> Version:
    """Django's LTS releases are the x.2 series."""
    stable = [r.version for r in django.stable_releases()]
    lts = [v for v in stable if v.minor == 2 and v.major >= 2]
    newest = max(lts)
    return Version(f"{newest.major}.{newest.minor}")


def resolve_target(django: Project, target: str) -> Version:
    stable = [r.version for r in django.stable_releases()]
    if target == "lts":
        return latest_lts(django)
    if target == "latest":
        newest = max(stable)
        return Version(f"{newest.major}.{newest.minor}")
    try:
        version = Version(target)
    except InvalidVersion as exc:
        raise ValueError(f"Not a Django version: {target!r}") from exc
    return Version(f"{version.major}.{version.minor}")


def analyse(
    deps: DependencySet,
    pypi: PyPI,
    target: str = "lts",
    workers: int = 16,
    progress: Callable[[str], None] | None = None,
) -> Report:
    django = pypi.project("django")
    if django is None:
        raise RuntimeError("Could not read Django's release history from the package index")
    target_version = resolve_target(django, target)
    current_dep = deps.dependencies.get("django")
    current_django = current_dep.version if current_dep else None
    current_django_minor = _minor(current_django)

    target_release = _first_release_of(django, target_version)
    django_python = None
    if target_release is not None:
        info = pypi.release("django", str(target_release))
        django_python = info.requires_python if info else None

    others = [d for name, d in sorted(deps.dependencies.items()) if name != "django"]

    def check(dep: Dependency) -> PackageReport | str | None:
        if progress:
            progress(dep.name)
        project = pypi.project(dep.name)
        if project is None:
            return dep.name
        if not is_django_related(project.latest):
            current_info = pypi.release(dep.name, dep.version) if dep.version else None
            if current_info is None or not is_django_related(current_info):
                return None
        return _check_package(dep, project, pypi, target_version, current_django_minor)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(check, others))

    packages = [r for r in results if isinstance(r, PackageReport)]
    missing = [r for r in results if isinstance(r, str)]
    skipped = sum(1 for r in results if r is None)

    order = {Status.BLOCKED: 0, Status.UPGRADE: 1, Status.CHECK: 2, Status.READY: 3}
    packages.sort(key=lambda p: (order[p.status], p.phase is Phase.WITH, p.name))

    return Report(
        target=f"{target_version.major}.{target_version.minor}",
        current_django=current_django,
        django_requires_python=django_python,
        source=deps.source,
        packages=packages,
        skipped=skipped,
        missing=missing,
    )


def _minor(version: str | None) -> Version | None:
    if not version:
        return None
    try:
        v = Version(version)
    except InvalidVersion:
        return None
    return Version(f"{v.major}.{v.minor}")


def _first_release_of(project: Project, minor: Version) -> Version | None:
    for release in project.stable_releases():
        if (release.version.major, release.version.minor) == (minor.major, minor.minor):
            return release.version
    return None


def _check_package(
    dep: Dependency,
    project: Project,
    pypi: PyPI,
    target: Version,
    current_django: Version | None,
) -> PackageReport:
    stable = project.stable_releases()
    last_release = max((r.uploaded for r in project.releases if r.uploaded), default=None)
    report = PackageReport(
        name=dep.name,
        display_name=project.name,
        current=dep.version,
        spec=dep.spec,
        latest=project.latest.version,
        status=Status.CHECK,
        reason="",
        last_release=last_release,
    )
    if report.stale:
        years = (datetime.now(timezone.utc) - last_release).days // 365
        report.notes.append(f"no release in {years} years")

    latest_support = supports(project.latest, target)

    current_info = pypi.release(dep.name, dep.version) if dep.version else None
    if current_info is None:
        # We only know a range. Judge by the newest release.
        report.reason = f"latest {project.latest.version} {latest_support.reason}"
        report.status = {
            Verdict.YES: Status.READY,
            Verdict.LIKELY: Status.CHECK,
            Verdict.UNKNOWN: Status.CHECK,
            Verdict.NO: Status.BLOCKED,
        }[latest_support.verdict]
        if not dep.version:
            report.notes.append("version not pinned, add a lockfile for exact results")
        return report

    current_support = supports(current_info, target)
    if current_support.verdict is Verdict.YES:
        report.status = Status.READY
        report.reason = current_support.reason
        return report

    current_version = Version(dep.version)
    newer = [r.version for r in stable if r.version > current_version]
    candidate = _first_supporting(dep.name, newer, pypi, target)
    if candidate is not None:
        version, info, support = candidate
        report.status = Status.UPGRADE
        report.target_version = str(version)
        report.reason = f"{version} {support.reason}"
        if current_django is not None:
            runs_on_current = supports(info, current_django).verdict is not Verdict.NO
            report.phase = Phase.BEFORE if runs_on_current else Phase.WITH
        return report

    if latest_support.verdict is Verdict.NO:
        report.status = Status.BLOCKED
        report.reason = f"latest {project.latest.version} {latest_support.reason}"
        return report

    if current_support.verdict is Verdict.NO:
        # Nothing declares support, but a newer release at least stops excluding it.
        candidate = _first_supporting(dep.name, newer, pypi, target, accept=Verdict.LIKELY)
        if candidate is not None:
            version, _, support = candidate
            report.status = Status.CHECK
            report.target_version = str(version)
            report.reason = f"{version} {support.reason}"
            return report

    report.status = Status.CHECK
    if current_support.verdict is Verdict.LIKELY:
        report.reason = current_support.reason
        if project.latest.version != dep.version:
            report.notes.append(f"latest {project.latest.version} {latest_support.reason}")
    else:
        report.reason = f"latest {project.latest.version} {latest_support.reason}"
    return report


def _first_supporting(
    name: str,
    versions: Iterable[Version],
    pypi: PyPI,
    target: Version,
    accept: Verdict = Verdict.YES,
) -> tuple[Version, ReleaseInfo, Support] | None:
    """The oldest of ``versions`` whose verdict for ``target`` is ``accept``.

    Newest first would be cheaper, but the smallest step is the useful answer.
    Releases are checked in parallel batches to keep this quick.
    """
    candidates = list(versions)[-MAX_RELEASES_SCANNED:]
    batch = 8
    with ThreadPoolExecutor(max_workers=batch) as pool:
        for start in range(0, len(candidates), batch):
            chunk = candidates[start : start + batch]
            infos = list(pool.map(lambda v: pypi.release(name, str(v)), chunk))
            for version, info in zip(chunk, infos, strict=True):
                if info is None:
                    continue
                support = supports(info, target)
                if support.verdict is accept:
                    return version, info, support
    return None
