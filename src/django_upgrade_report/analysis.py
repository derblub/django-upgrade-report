"""Decide, per dependency, whether it supports the target Django version."""

from __future__ import annotations

import enum
import functools
import re
import sys
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Protocol

from packaging.markers import Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from django_upgrade_report.projects import changelog_url, repository_url
from django_upgrade_report.pypi import Project, PyPI, PyPIError, ReleaseInfo
from django_upgrade_report.sources import Dependency, DependencySet
from django_upgrade_report.successors import Successor, successor

if TYPE_CHECKING:
    from django_upgrade_report.diff import Changes
    from django_upgrade_report.python import PythonPlan

_CLASSIFIER = re.compile(r"^Framework :: Django :: (\d+\.\d+)$")
_MAJOR_CLASSIFIER = re.compile(r"^Framework :: Django :: (\d+)$")
_INACTIVE = "Development Status :: 7 - Inactive"
STALE_AFTER_DAYS = 2 * 365
# Nobody declares a Django version long before its first pre-release, so older releases
# cannot say YES and need not be fetched when looking for one.
_DECLARE_WINDOW = timedelta(days=365)
_UPPER_BOUNDS = ("<", "<=", "~=", "==", "===")
_PINS = ("==", "===")


class Verdict(enum.Enum):
    """What one release says about one Django version."""

    YES = "yes"  # declared: classifier, exact pin, or an upper bound set after the release
    LIKELY = "likely"  # allowed by the requirement, but not declared
    UNKNOWN = "unknown"  # no information at all
    NO = "no"  # excluded by the requirement


class Status(enum.Enum):
    READY = "ready"
    UPGRADE = "upgrade"
    CHECK = "check"
    BLOCKED = "blocked"


SEVERITY = {Status.READY: 0, Status.CHECK: 1, Status.UPGRADE: 2, Status.BLOCKED: 3}
"""How much work a status means: a higher number is worse. Orders reports and ``--fail-on``."""


class Phase(enum.Enum):
    BEFORE = "before"  # the new version still runs on your current Django: upgrade it first
    WITH = "with"  # the new version needs the new Django: upgrade it together with Django


@dataclass(frozen=True)
class Support:
    verdict: Verdict
    reason: str


@dataclass(frozen=True)
class Target:
    """A Django feature version (X.Y), as far as the release history knows it."""

    version: Version
    """Major and minor only, e.g. ``5.2``."""
    patches: tuple[Version, ...] = ()
    """Released versions of the series, e.g. ``5.2``, ``5.2.1``, ..."""
    ga: datetime | None = None
    """Upload time of X.Y.0. ``None`` on a released target means unknown: bounds count."""
    requires_python: str | None = None
    released: bool = True
    python: str | None = None
    """The Python this Django would run on, for environment markers."""
    future_patches: bool = True
    """Whether a patch release that does not exist yet counts, too."""

    @classmethod
    def of(cls, version: Version, python: str | None = None) -> Target:
        return cls(Version(f"{version.major}.{version.minor}"), python=python)

    @property
    def label(self) -> str:
        return f"{self.version.major}.{self.version.minor}"

    def probes(self, spec: SpecifierSet) -> list[Version]:
        probes = list(self.patches)
        if self.future_patches:
            probes += [self.version, Version(f"{self.label}.9999")]
            probes += [v for v in _spec_versions(spec) if _same_minor(v, self.version)]
        return probes


@dataclass(frozen=True)
class PreRelease:
    """A pre-release newer than every stable release that says more about the target."""

    version: str
    reason: str
    uploaded: datetime | None


@dataclass(frozen=True)
class Evidence:
    """A sign of support that metadata does not give: for a package to check, never a status."""

    kind: str
    """``"readme"``, ``"test-matrix"`` or ``"changelog"``."""
    text: str
    """``"README of 2.1 mentions Django 5.2"``."""
    url: str | None = None
    """Where to read it."""


@dataclass(frozen=True)
class UpstreamItem:
    """An issue or pull request about the target in the package's repository (``--evidence``)."""

    kind: str
    """``"issue"`` or ``"pr"``."""
    state: str
    """``"open"``, ``"closed"`` or ``"merged"``."""
    title: str
    """Shortened to 80 characters; from outside, so every format escapes it."""
    url: str
    number: int
    updated: str = ""
    """ISO 8601."""

    @property
    def label(self) -> str:
        """``open PR: Add Django 5.2 support (#912)``."""
        kind = "PR" if self.kind == "pr" else "issue"
        return f"{self.state} {kind}: {self.title} (#{self.number})"


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
    """The release to move to: the smallest that declares support, when an upgrade is needed."""
    phase: Phase | None = None
    last_release: datetime | None = None
    notes: list[str] = field(default_factory=list)
    source: str | None = None
    """Where the package comes from when not from PyPI: judged by its own metadata only."""
    successor: Successor | None = None
    """What Django itself has instead, when Django took over the package's job by the target."""
    prerelease: PreRelease | None = None
    """A pre-release that declares the target, or no longer excludes it for a blocked package."""
    majors_crossed: int | None = None
    """Major versions between the current release and :attr:`target_version` (0.x minors count)."""
    changelog_url: str | None = None
    repository_url: str | None = None
    direct: bool | None = None
    """Whether the project names the package itself; ``None`` when its source does not say."""
    origin: str | None = None
    """The requirement file line that pins or names it, as ``path:line``."""
    evidence: list[Evidence] = field(default_factory=list)
    """Signs of support for a package to check, shown as notes; they never change the status."""
    upstream: list[UpstreamItem] = field(default_factory=list)
    """Issues and pull requests about the target, for a blocked package or one to check."""

    @property
    def stale(self) -> bool:
        if self.last_release is None:
            return False
        return (datetime.now(timezone.utc) - self.last_release).days > STALE_AFTER_DAYS


@dataclass(frozen=True)
class ExplainLine:
    """One line of ``--explain``: a section such as ``"search"``, and what happened."""

    section: str
    text: str
    version: Version | None = None
    """The release the line is about, to order the releases looked at."""


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
    failed: list[str] = field(default_factory=list)
    """Dependencies the index could not answer for (after retries): the report is incomplete."""
    external: list[tuple[str, str]] = field(default_factory=list)
    """Dependencies not resolved from PyPI, as (name, where from). Never looked up."""
    warnings: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    """Good to know, but nothing to worry about, e.g. why this is a health check."""
    project_python: str | None = None
    target_released: bool = True
    generated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    kind: str = "report"
    """What the JSON document holds: one report. Other kinds will wrap several reports."""
    changes: Changes | None = None
    """What changed since the ``--baseline`` report, when one was given."""
    python: PythonPlan | None = None
    """What the dependencies need on a newer Python, when the target Django needs one."""
    explanations: dict[str, list[ExplainLine]] = field(default_factory=dict)
    """For each package asked about with ``--explain``, how its verdict came about."""
    django_origin: str | None = None
    """The requirement file line that pins or names Django, as ``path:line``."""
    unused: list[str] = field(default_factory=list)
    """Direct dependencies, Django-related or not, that the project's code never names."""
    removals: list = field(default_factory=list)
    """What Django removed on the way to the target (``removals.Removal``)."""
    code_read: bool = False
    """The project's code was read: ``unused`` and where removals are used are known."""

    def by_status(self, status: Status) -> list[PackageReport]:
        return [p for p in self.packages if p.status is status]

    @property
    def health_check(self) -> bool:
        """The project already runs the target: nothing to upgrade to, only how things stand."""
        if not self.current_django or not _is_version(self.current_django):
            return False
        current = Version(self.current_django)
        return f"{current.major}.{current.minor}" == self.target

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


def declared_majors(info: ReleaseInfo) -> list[int]:
    """Major-only classifiers such as ``Framework :: Django :: 5``."""
    return sorted(int(m.group(1)) for c in info.classifiers if (m := _MAJOR_CLASSIFIER.match(c)))


def _environment(python: str | None) -> dict[str, str]:
    """Marker values for a Linux server, independent of the machine running this tool."""
    if python is None:
        python = f"{sys.version_info.major}.{sys.version_info.minor}"
    v = Version(python)
    return {
        "extra": "",
        "python_version": f"{v.major}.{v.minor}",
        "python_full_version": f"{v.major}.{v.minor}.{v.micro}",
        "sys_platform": "linux",
        "platform_system": "Linux",
        "os_name": "posix",
        "platform_machine": "x86_64",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
    }


def django_requirement(info: ReleaseInfo, python: str | None = None) -> SpecifierSet | None:
    """All Django requirement lines that apply on ``python``, combined into one."""
    env = _environment(python)
    combined = None
    for req in _requirements(info, "django"):
        if req.marker is not None and not req.marker.evaluate(env):
            continue  # another Python, or only needed for an optional extra
        combined = req.specifier if combined is None else combined & req.specifier
    return combined


def _requirements(info: ReleaseInfo, name: str) -> Iterable[Requirement]:
    return _parsed(info.requires_dist).get(name, ())


@functools.lru_cache(maxsize=4096)
def _parsed(requires_dist: tuple[str, ...]) -> dict[str, tuple[Requirement, ...]]:
    """Requirement lines by canonical package name, parsed once per release."""
    by_name: dict[str, list[Requirement]] = {}
    for line in requires_dist:
        try:
            req = Requirement(line)
        except (InvalidRequirement, TypeError):
            continue
        by_name.setdefault(canonicalize_name(req.name), []).append(req)
    return {name: tuple(reqs) for name, reqs in by_name.items()}


def _needed(req: Requirement) -> bool:
    """False when the requirement is only needed for an optional extra."""
    if req.marker is None or "extra" not in str(req.marker):
        return True
    return _marker_true_somewhere(req.marker)


def _marker_true_somewhere(marker: Marker) -> bool:
    return any(marker.evaluate(_environment(p)) for p in ("3.8", "3.10", "3.12", "3.14"))


def _spec_versions(spec: SpecifierSet) -> list[Version]:
    found = []
    for s in spec:
        try:
            found.append(Version(s.version.removesuffix(".*")))
        except InvalidVersion:
            continue
    return found


def _same_minor(a: Version, b: Version) -> bool:
    return (a.major, a.minor) == (b.major, b.minor)


def _allows(spec: SpecifierSet, target: Target) -> bool:
    """True when a release of the ``target`` series (or a future patch) satisfies ``spec``."""
    return any(spec.contains(probe, prereleases=True) for probe in target.probes(spec))


def _upper_bounds(spec: SpecifierSet, target: Target) -> list[str]:
    """Operators of the specifiers that cap Django at or above ``target``."""
    return [
        s.operator
        for s in spec
        if s.operator in _UPPER_BOUNDS and _allows(SpecifierSet(str(s)), target)
    ]


def excluded_side(info: ReleaseInfo, target: Target) -> int:
    """Where the releases that allow ``target`` are, when ``info`` excludes it.

    -1: older ones (a lower bound is above the target), 1: newer ones (an upper bound is below
    it), 0: cannot tell. Bounds only move up from release to release, so this steers searches.
    """
    spec = django_requirement(info, target.python)
    sides = set()
    for s in spec or ():
        if _allows(SpecifierSet(str(s)), target):
            continue
        if s.operator in (">", ">="):
            sides.add(-1)
        elif s.operator in ("<", "<="):
            sides.add(1)
        elif s.operator in ("==", "===", "~="):
            try:
                bound = Version(s.version.removesuffix(".*"))
            except InvalidVersion:
                return 0
            sides.add(1 if bound < target.version else -1)
        else:
            return 0
    return sides.pop() if len(sides) == 1 else 0


def supports(
    info: ReleaseInfo, target: Target | Version, uploaded: datetime | None = None
) -> Support:
    """What ``info``, uploaded at ``uploaded``, says about Django ``target``."""
    if isinstance(target, Version):
        target = Target.of(target)
    spec = django_requirement(info, target.python)
    declared = declared_versions(info)
    majors = declared_majors(info)
    label = target.label

    if spec is not None and str(spec) and not _allows(spec, target):
        return Support(Verdict.NO, f"requires Django{spec}")
    if target.version in declared:
        return Support(Verdict.YES, f"declares Django {label}")
    bounds = _upper_bounds(spec, target) if spec is not None else []
    if bounds:
        if _bound_counts(bounds, target, uploaded):
            return Support(Verdict.YES, f"allows Django{spec}")
        if not target.released:
            return Support(Verdict.LIKELY, f"allows Django{spec}, {label} is not released yet")
        if uploaded is None:
            return Support(Verdict.LIKELY, f"allows Django{spec}, may predate {label}")
        return Support(Verdict.LIKELY, f"allows Django{spec}, released before {label}")
    # Classifiers often lag behind releases, so a missing one is a question, not a blocker.
    if target.version.major in majors:
        return Support(Verdict.LIKELY, f"declares Django {target.version.major}, not {label}")
    if declared and max(declared) < target.version:
        newest = declared[-1]
        return Support(
            Verdict.LIKELY, f"declares Django up to {newest.major}.{newest.minor}, not {label}"
        )
    if declared and min(declared) > target.version:
        oldest = declared[0]
        return Support(
            Verdict.LIKELY, f"declares Django {oldest.major}.{oldest.minor} and newer, not {label}"
        )
    if declared:
        return Support(Verdict.LIKELY, f"declares Django {_span(declared)}, not {label}")
    if majors:
        listed = ", ".join(str(m) for m in majors)
        return Support(Verdict.LIKELY, f"declares Django {listed}, not {label}")
    if spec is not None and str(spec):
        return Support(Verdict.LIKELY, f"allows Django{spec}, no upper bound")
    return Support(Verdict.UNKNOWN, "declares no Django versions")


@dataclass(frozen=True)
class RuleStep:
    """One check of :func:`supports`, as :func:`explain_support` describes it."""

    check: str
    """``"requirement"``, ``"classifiers"``, ``"upper bound"`` or ``"verdict"``."""
    finding: str


def explain_support(
    info: ReleaseInfo, target: Target | Version, uploaded: datetime | None = None
) -> list[RuleStep]:
    """How :func:`supports` reaches its verdict on ``info``, one check per step.

    The steps describe what the release declares; the last one is the verdict of
    :func:`supports` itself, so the explanation can never disagree with the report.
    """
    if isinstance(target, Version):
        target = Target.of(target)
    label = target.label
    env = _environment(target.python)
    steps = []

    lines = list(_requirements(info, "django"))
    applied = [str(r) for r in lines if r.marker is None or r.marker.evaluate(env)]
    ignored = [str(r) for r in lines if r.marker is not None and not r.marker.evaluate(env)]
    python = env["python_version"]
    if not lines:
        steps.append(RuleStep("requirement", "no Django requirement"))
    else:
        spec = django_requirement(info, target.python)
        if not applied:
            finding = f"no line applies on Python {python}"
        elif spec is not None and str(spec) and not _allows(spec, target):
            finding = f"{'; '.join(applied)}: excludes every Django {label}"
        else:
            finding = f"{'; '.join(applied)}: allows Django {label}"
        if ignored:
            finding += f" (not on Python {python}: {'; '.join(ignored)})"
        steps.append(RuleStep("requirement", finding))

    declared = declared_versions(info)
    majors = declared_majors(info)
    if declared or majors:
        listed = ", ".join([*(f"{v.major}.{v.minor}" for v in declared), *map(str, majors)])
        found = "includes" if target.version in declared else "does not include"
        steps.append(RuleStep("classifiers", f"Django {listed}: {found} {label}"))
    else:
        steps.append(RuleStep("classifiers", "no Framework :: Django :: X.Y classifier"))

    spec = django_requirement(info, target.python)
    bounds = _upper_bounds(spec, target) if spec is not None else []
    if bounds:
        when = f"uploaded {uploaded:%Y-%m-%d}" if uploaded else "upload date unknown"
        if not target.released:
            finding = f"{when}; Django {label} is not released, so it says nothing yet"
        elif target.ga is None:
            finding = f"{when}; counts, the release date of Django {label} is unknown"
        else:
            ga = f"Django {label} came out {target.ga:%Y-%m-%d}"
            counts = _bound_counts(bounds, target, uploaded)
            finding = f"{when}, {ga}: {'counts' if counts else 'set before it, does not count'}"
        steps.append(RuleStep("upper bound", finding))

    support = supports(info, target, uploaded)
    steps.append(RuleStep("verdict", f"{support.verdict.value}: {support.reason}"))
    return steps


class Rule(Protocol):
    """What the release searches ask of a release: Django support, or later a Python's."""

    def judge(self, info: ReleaseInfo, uploaded: datetime | None) -> Support:
        """What ``info``, uploaded at ``uploaded``, says."""

    def side(self, info: ReleaseInfo) -> int:
        """For a release judged NO, where the ones that are not: -1 older, 1 newer, 0 unknown."""


@dataclass(frozen=True)
class DjangoRule:
    """Support for one Django version: :func:`supports` and :func:`excluded_side`."""

    target: Target

    def judge(self, info: ReleaseInfo, uploaded: datetime | None) -> Support:
        return supports(info, self.target, uploaded)

    def side(self, info: ReleaseInfo) -> int:
        return excluded_side(info, self.target)


def _bound_counts(operators: list[str], target: Target, uploaded: datetime | None) -> bool:
    """An upper bound speaks about ``target`` only when it was set after ``target`` came out."""
    if not target.released:
        return False
    if target.ga is None:
        return True
    if uploaded is None:
        return any(op in _PINS for op in operators)
    return uploaded >= target.ga


def _span(versions: list[Version]) -> str:
    low, high = versions[0], versions[-1]
    return f"{low.major}.{low.minor}–{high.major}.{high.minor}"


def is_django_related(info: ReleaseInfo) -> bool:
    return (
        any(_needed(req) for req in _requirements(info, "django"))
        or any(c.startswith("Framework :: Django") for c in info.classifiers)
        or _framework_note(info) is not None
    )


def _framework_note(info: ReleaseInfo) -> str | None:
    """Packages built on a framework on top of Django need a check against that one, too."""
    name = canonicalize_name(info.name)
    if name != "wagtail" and (
        any(c.startswith("Framework :: Wagtail") for c in info.classifiers)
        or any(_needed(req) for req in _requirements(info, "wagtail"))
    ):
        return "Wagtail package: also check it against your Wagtail version"
    if name != "django-cms" and any(_needed(req) for req in _requirements(info, "django-cms")):
        return "django CMS package: also check it against your django CMS version"
    return None


# --- Django's release history ------------------------------------------------


def _series(django: Project) -> dict[Version, list[Version]]:
    """Released Django versions by feature version, oldest first."""
    series: dict[Version, list[Version]] = {}
    for release in django.stable_releases():
        v = release.version
        series.setdefault(Version(f"{v.major}.{v.minor}"), []).append(v)
    return series


def _next_feature(version: Version) -> Version:
    """Django counts X.0, X.1, X.2, then (X+1).0."""
    if version.minor >= 2:
        return Version(f"{version.major + 1}.0")
    return Version(f"{version.major}.{version.minor + 1}")


@dataclass
class PathReport:
    """``--via``: one report per station from the project's Django to the target."""

    via: str
    """``"lts"`` or ``"each"``."""
    steps: list[Report]
    blocked_at: int | None = None
    """The first step (from 1) with a blocked package: the plan stops there."""
    generated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    kind: str = "path"

    @property
    def target(self) -> str:
        return self.steps[-1].target


def stations(django: Project, current: Version, target: Version, via: str) -> list[Version]:
    """The feature versions to go through from ``current`` (X.Y) to ``target``, ``target``
    last: every LTS between them for ``lts``, every feature version for ``each``."""
    between = [
        v for v in sorted(_series(django)) if current < v < target and (via == "each" or _is_lts(v))
    ]
    return [*between, target]


def analyse_path(
    deps: DependencySet,
    pypi: PyPI,
    target: str,
    via: str,
    current: str | None = None,
    after: Callable[[Report, DependencySet], None] | None = None,
    **options,
) -> PathReport:
    """Judge ``deps`` against each station on the way to ``target``, every step starting
    where the one before ends: the upgrades it proposes done, Django on the newest patch of
    the station. ``after`` sees each step's report with the dependencies it was made from."""
    django = pypi.project("django")
    if django is None:
        raise RuntimeError("Could not read Django's release history from the package index")
    start, _ = _current_django(django, deps.dependencies.get("django"), current)
    start_minor = _minor(start)
    if start is None or start_minor is None:
        raise ValueError("--via needs the Django you run: pin it, or pass --from")
    goal = resolve_target(django, target, start_minor)
    if goal <= start_minor:
        raise ValueError(f"--via needs a target above Django {start_minor}, the one you run")
    series = _series(django)
    path = PathReport(via, [])
    blocked: dict[str, int] = {}
    for number, station in enumerate(stations(django, start_minor, goal, via), 1):
        report = analyse(deps, pypi, str(station), current=start, **options)
        for p in report.packages:
            if p.name in blocked:
                p.notes.append(f"blocked since step {blocked[p.name]}")
            elif p.status is Status.BLOCKED:
                blocked[p.name] = number
        if after is not None:
            after(report, deps)
        path.steps.append(report)
        if station not in series:
            break  # not released: nothing to start the next step from
        start = str(series[station][-1])
        upgraded = {
            p.name: p.target_version
            for p in report.packages
            if p.status is Status.UPGRADE and p.target_version
        }
        upgraded["django"] = start
        moved = dict(deps.dependencies)
        for name, version in upgraded.items():
            dep = moved.get(name) or Dependency(name, None)
            moved[name] = replace(dep, version=version, spec="")
        deps = replace(deps, dependencies=moved)
    path.blocked_at = min(blocked.values(), default=None)
    return path


def skipped_lts(django: Project, current: Version, target: Version) -> list[Version]:
    """The LTS series between ``current`` and ``target`` (X.Y), oldest first."""
    return sorted(v for v in _series(django) if _is_lts(v) and current < v < target)


def _is_lts(version: Version) -> bool:
    return version.minor == 2 and version.major >= 2


def latest_lts(django: Project) -> Version:
    """Django's LTS releases are the x.2 series."""
    lts = [v for v in _series(django) if v.minor == 2 and v.major >= 2]
    if not lts:
        raise ValueError(
            "The package index has no Django LTS release (x.2): is --index-url a full PyPI mirror?"
        )
    return max(lts)


def resolve_target(django: Project, target: str, current: Version | None = None) -> Version:
    """The feature version to check against; ``current`` is the project's Django (X.Y).

    ``auto`` is the newest LTS above ``current``, else the newest release above it. A named
    target never goes below ``current``: that shows a health check of ``current`` instead.
    An explicit version below ``current`` is an error, a downgrade report helps nobody.
    """
    released = sorted(_series(django))
    if target in ("auto", "lts", "latest"):
        if target == "auto" and current is not None:
            above = [v for v in released if v > current]
            lts = [v for v in above if v.minor == 2]
            named = max(lts or above, default=current)
        elif target == "latest":
            named = released[-1]
        else:
            named = latest_lts(django)
        return max(named, current) if current is not None else named
    try:
        version = Version(target)
    except InvalidVersion as exc:
        raise ValueError(f"Not a Django version: {target!r}") from exc
    minor = Version(f"{version.major}.{version.minor}")
    upcoming = _next_feature(released[-1])
    if minor not in released and minor != upcoming:
        choices = [str(v) for v in released if v >= Version("4.0")]
        raise ValueError(
            f"There is no Django {target}. Use auto, lts, latest, one of "
            f"{', '.join(choices)}, or {upcoming} (not released yet)"
        )
    if current is not None and minor < current:
        raise ValueError(
            f"Django {minor} is older than your Django {current}: there is nothing to upgrade. "
            f"Use auto, latest or a version from {current} on"
        )
    return minor


def _build_target(
    django: Project, pypi: PyPI, version: Version, project_python: str | None
) -> Target:
    patches = tuple(_series(django).get(version, ()))
    first = str(patches[0]) if patches else None
    if first is None:  # not released: a pre-release may already say which Python it needs
        pre = [r.version for r in django.releases if _same_minor(r.version, version)]
        first = str(pre[-1]) if pre else None
    requires_python = _django_python(pypi, first)
    floor = requires_python
    if floor is None and not patches:  # no pre-release yet: it needs at least what the last did
        floor = _django_python(pypi, str(_series(django)[max(_series(django))][0]))
    return Target(
        version,
        patches,
        ga=_uploaded(django, patches[0]) if patches else None,
        requires_python=requires_python,
        released=bool(patches),
        python=_max_python(project_python, _min_python(floor)),
    )


def _django_python(pypi: PyPI, version: str | None) -> str | None:
    info = pypi.release("django", version) if version else None
    return info.requires_python if info else None


def _current_target(
    django: Project, pypi: PyPI, current: str, project_python: str | None
) -> Target | None:
    """The exact Django the project runs today, e.g. 4.2.7."""
    try:
        exact = Version(current)
    except InvalidVersion:
        return None
    minor = Version(f"{exact.major}.{exact.minor}")
    python = project_python or _min_python(_django_python(pypi, current))
    patches = _series(django).get(minor)
    return Target(
        minor,
        (exact,),
        ga=_uploaded(django, patches[0]) if patches else None,
        python=python,
        future_patches=False,
    )


def _uploaded(project: Project, version: Version) -> datetime | None:
    return next((r.uploaded for r in project.releases if r.version == version), None)


def _min_python(requires_python: str | None) -> str | None:
    """The lowest Python a ``requires_python`` range accepts, as X.Y."""
    if not requires_python:
        return None
    try:
        spec = SpecifierSet(requires_python)
    except InvalidSpecifier:
        return None
    floors = []
    for s in spec:
        if s.operator in (">=", ">", "~=", "=="):
            try:
                floors.append(Version(s.version.removesuffix(".*")))
            except InvalidVersion:
                continue
    if not floors:
        return None
    low = max(floors)
    return f"{low.major}.{low.minor}"


def _max_python(*pythons: str | None) -> str | None:
    known = [Version(p) for p in pythons if p]
    if not known:
        return None
    high = max(known)
    return f"{high.major}.{high.minor}"


# --- the whole project -------------------------------------------------------


def analyse(
    deps: DependencySet,
    pypi: PyPI,
    target: str = "auto",
    workers: int = 16,
    progress: Callable[[str], None] | None = None,
    current: str | None = None,
    private_index: bool = False,
    explain: Iterable[str] = (),
) -> Report:
    """Judge every Django-related dependency of ``deps`` against ``target``.

    ``current`` overrides the project's Django version (``--from``). With ``private_index``
    the index is the project's own, so packages from a private index are looked up there.
    For each canonical name in ``explain``, the report records how its verdict came about.
    """
    django = pypi.project("django")
    if django is None:
        raise RuntimeError("Could not read Django's release history from the package index")
    current_dep = deps.dependencies.get("django")
    current_django, notes = _current_django(django, current_dep, current)
    _check_index_knows(django, current_django)
    current_minor = _minor(current_django)
    project_python = deps.python

    goal = _build_target(
        django, pypi, resolve_target(django, target, current_minor), project_python
    )
    today = None
    if current_django and current_minor is not None:
        today = _current_target(django, pypi, current_django, project_python)

    def checked(d: Dependency) -> bool:
        return d.external is None or (private_index and d.external.startswith("index "))

    # Django's release history is always read, so Django itself is never "not checked".
    unchecked = [
        d
        for d in deps.dependencies.values()
        if not checked(d) and d.external and d.name != "django"
    ]
    traces: dict[str, list[ExplainLine]] = {canonicalize_name(n): [] for n in explain}
    _explain_inputs(traces, deps, goal, today, current_django)
    # A fork or a local package cannot be looked up, but what it declares itself can be judged.
    for d in unchecked:
        if d.name in traces:
            traces[d.name].append(ExplainLine("inputs", f"not from PyPI: {d.external}"))
            if d.metadata is not None and not is_django_related(d.metadata):
                why = "skipped: its own metadata does not mention Django"
            elif d.metadata is None and from_other_index(d.external):
                why = (
                    "not checked: it comes from another index; pass --check-private-on-pypi "
                    "if that index mirrors PyPI, or its JSON API with --index-url"
                )
            elif d.metadata is None:
                why = "not checked: its metadata could not be read locally"
            else:
                why = None
            if why:
                traces[d.name].append(ExplainLine("result", why))
            else:
                traces[d.name] += [
                    ExplainLine("release", f"{s.check}: {s.finding}")
                    for s in explain_support(d.metadata, goal)
                ]
    local = [
        _judge_local(d, goal, d.metadata, d.external)
        for d in unchecked
        if d.metadata is not None and d.external and is_django_related(d.metadata)
    ]
    judged = {p.name for p in local}
    external = sorted((d.name, d.external) for d in unchecked if d.name not in judged)
    others = [d for name, d in sorted(deps.dependencies.items()) if name != "django" and checked(d)]
    pinned = {
        name: Version(d.version)
        for name, d in deps.dependencies.items()
        if d.version and _is_version(d.version)
    }

    with (
        ThreadPoolExecutor(max_workers=workers) as release_pool,
        ThreadPoolExecutor(max_workers=workers) as package_pool,
    ):
        private = frozenset(d.name for d in deps.dependencies.values() if not checked(d))
        checker = _Checker(pypi, goal, today, release_pool, workers, pinned, private)
        checker.traces = traces
        checker.installed.update((d.name, d.metadata) for d in unchecked if d.metadata is not None)

        def check(dep: Dependency) -> PackageReport | str | _Failed | None:
            try:
                return checker.check(dep)
            except PyPIError as exc:  # one flaky answer must not cost the whole report
                return _Failed(dep.name, str(exc))
            finally:
                if progress:
                    progress(dep.name)

        results = list(package_pool.map(check, others))

    packages = [r for r in results if isinstance(r, PackageReport)] + local
    for p in packages:
        if p.name in deps.dependencies:
            p.direct = deps.dependencies[p.name].direct
            p.origin = deps.dependencies[p.name].origin
        p.successor = successor(p.name, goal.version)
        if p.successor is not None:  # what a newer release declares no longer matters
            p.notes = [p.successor.note(), *(n for n in p.notes if not n.startswith("latest "))]
    failed = [r for r in results if isinstance(r, _Failed)]
    for f in failed:
        notes.append(f"Could not check {f.name}, run again later: {f.problem}")
    if not packages and any(from_other_index(where) for _, where in external):
        notes.append(
            "No Django-related package was checked: they come from another index. If it "
            "mirrors PyPI, pass --check-private-on-pypi"
        )
    _plan_order(packages, checker, (today or goal).python)
    missing = [r for r in results if isinstance(r, str)]
    skipped = sum(1 for r in results if r is None)

    rank = _upgrade_rank(packages, checker.links)
    # Packages to check without any sign of support first: that is where the work is.
    packages.sort(
        key=lambda p: (
            -SEVERITY[p.status],
            p.phase is Phase.WITH,
            bool(p.evidence),
            rank[p.name],
            p.name,
        )
    )
    _explain_results(traces, packages, missing, failed)

    django_dep = deps.dependencies.get("django")
    return Report(
        target=goal.label,
        django_origin=django_dep.origin if django_dep else None,
        current_django=current_django,
        django_requires_python=goal.requires_python,
        source=deps.source,
        packages=packages,
        skipped=skipped,
        missing=missing,
        failed=[f.name for f in failed],
        external=external,
        warnings=notes + _warnings(target, goal, django, current_minor, deps),
        notices=_notices(target, goal, django, current_minor, current_django),
        project_python=project_python,
        target_released=goal.released,
        explanations=traces,
    )


def _explain_inputs(
    traces: dict[str, list[ExplainLine]],
    deps: DependencySet,
    goal: Target,
    today: Target | None,
    current_django: str | None,
) -> None:
    """What every explanation starts from: the dependency, the Django versions, the Python."""
    for name, lines in traces.items():
        dep = deps.dependencies.get(name)
        if dep is None:
            lines.append(ExplainLine("result", f"{name} is not among your dependencies"))
            continue
        have = f"{dep.version} (pinned)" if dep.version else f"{dep.spec or 'any'} (not pinned)"
        lines.append(ExplainLine("inputs", f"{dep.name} {have}, from {deps.source}"))
        lines.append(ExplainLine("inputs", f"your Django {current_django or 'unknown'}"))
        python = f"Python {goal.python}" if goal.python else "the running Python"
        lines.append(ExplainLine("inputs", f"against Django {goal.label}, markers on {python}"))
        if today is not None and today.python and today.python != goal.python:
            lines.append(
                ExplainLine(
                    "inputs", f"against your Django {today.label}, markers on Python {today.python}"
                )
            )


def _explain_results(
    traces: dict[str, list[ExplainLine]],
    packages: list[PackageReport],
    missing: list[str],
    failed: list[_Failed],
) -> None:
    """How each explained package ends up in the report."""
    by_name = {p.name: p for p in packages}
    problems = {f.name: f.problem for f in failed}
    for name, lines in traces.items():
        if any(line.section == "result" for line in lines):
            continue
        p = by_name.get(name)
        if p is not None:
            verdict = {
                Phase.BEFORE: f"upgrade to {p.target_version} first, before Django",
                Phase.WITH: f"upgrade to {p.target_version} together with Django",
            }.get(p.phase) or (
                f"{p.status.value}, {p.target_version}" if p.target_version else p.status.value
            )
            lines.append(ExplainLine("result", f"{verdict}: {p.reason}"))
            lines += [ExplainLine("result", note) for note in p.notes]
        elif name in missing:
            lines.append(ExplainLine("result", "not on the package index"))
        elif name in problems:
            lines.append(ExplainLine("result", f"could not be checked: {problems[name]}"))
        elif name == "django":
            lines.append(ExplainLine("result", "Django itself: the version you upgrade from"))
        else:
            lines.append(ExplainLine("result", "skipped: not Django-related"))


def _judge_local(dep: Dependency, goal: Target, info: ReleaseInfo, where: str) -> PackageReport:
    """A package not from PyPI, by its own metadata: there are no other releases to move to."""
    support = supports(info, goal)
    status = {Verdict.YES: Status.READY, Verdict.NO: Status.BLOCKED}.get(
        support.verdict, Status.CHECK
    )
    note = _framework_note(info)
    notes = [note] if note else []
    return PackageReport(
        name=dep.name,
        display_name=info.name or dep.name,
        current=dep.version or info.version or None,
        spec=dep.spec,
        latest=dep.version or info.version,
        status=status,
        reason=support.reason,
        notes=notes,
        source=where,
    )


def _check_index_knows(django: Project, current: str | None) -> None:
    """A mirror that stopped syncing knows no Django as new as the project's: say so.

    A missing patch release is fine (mirrors lag a little); a missing series is not.
    """
    if not current or not _is_version(current):
        return
    stable = [r.version for r in django.stable_releases()]
    series = Version(current).release[:2]
    if stable and series > max(stable).release[:2]:
        raise ValueError(
            f"Your project uses Django {current}, but the package index knows no Django newer "
            f"than {max(stable)}. Is --index-url a complete, up-to-date mirror of PyPI?"
        )


def from_other_index(where: str) -> bool:
    """A package that another index, or no index at all, serves instead of PyPI."""
    return where.startswith("index ") or where == "local files (no index)"


def _plan_order(packages: list[PackageReport], checker: _Checker, python: str | None) -> None:
    """Fit the upgrades to each other: the installed releases of other packages may forbid
    a proposed version, and an upgrade that must wait for another one waits for its phase.
    """
    env = _environment(python)
    by_name = {p.name: p for p in packages}
    for p in packages:
        if p.status is not Status.UPGRADE or p.target_version is None:
            continue
        version = Version(p.target_version)
        for other, info in sorted(checker.installed.items()):
            for spec in _specs_for(info, p.name, env):
                if spec.contains(version, prereleases=True):
                    continue
                needed = f"{info.name} {info.version} requires {p.display_name}{spec}"
                plan = by_name.get(other)
                later = plan.target_version if plan is not None else None
                later_info = checker.pypi.release(other, later) if later else None
                if later_info is not None and all(
                    s.contains(version, prereleases=True)
                    for s in _specs_for(later_info, p.name, env)
                ):
                    checker.link(p.name, other)
                    p.notes.append(f"{needed}: upgrade {info.name} to {later} first")
                else:
                    # Nothing planned lifts the conflict: installing it needs a decision.
                    p.status = Status.CHECK
                    p.phase = None
                    p.notes.append(f"{needed}, which excludes {version}")

    changed = True
    while changed:  # an upgrade that needs one going with Django goes with Django, too
        changed = False
        for p in packages:
            if p.phase is not Phase.BEFORE:
                continue
            waits = sorted(
                by_name[n].display_name
                for n in checker.links.get(p.name, ())
                if n in by_name and by_name[n].phase is Phase.WITH
            )
            if waits:
                p.phase = Phase.WITH
                p.notes.append(f"goes with {', '.join(waits)}")
                checker.trace(p.name, "phase", f"goes with {', '.join(waits)}, so with Django")
                changed = True

    _merge_cycles(packages, checker.links)


def _merge_cycles(packages: list[PackageReport], links: dict[str, set[str]]) -> None:
    """Upgrades that each need the other first can only land together: say so."""
    upgrades = {p.name: p for p in packages if p.status is Status.UPGRADE}
    for group in _cycles(upgrades, links):
        for name in group:
            p = upgrades[name]
            others = [upgrades[o].display_name for o in sorted(group) if o != name]
            p.notes = [
                note.removesuffix(" first") + " in the same change"
                if note.endswith(" first") and any(f"upgrade {o} " in note for o in others)
                else note
                for note in p.notes
            ]
            p.notes.append(f"upgrade together with {', '.join(others)}")


def _cycles(nodes: dict[str, PackageReport], links: dict[str, set[str]]) -> list[set[str]]:
    """Groups of upgrades that need each other (strongly connected, more than one)."""
    edges = {n: {m for m in links.get(n, ()) if m in nodes} for n in nodes}

    def reachable(start: str) -> set[str]:
        seen, todo = set(), [start]
        while todo:
            for m in edges[todo.pop()]:
                if m not in seen:
                    seen.add(m)
                    todo.append(m)
        return seen

    reach = {n: reachable(n) for n in nodes}
    groups: list[set[str]] = []
    for n in sorted(nodes):
        if n in reach[n] and not any(n in g for g in groups):
            groups.append({n} | {m for m in reach[n] if n in reach[m]})
    return groups


def _upgrade_rank(packages: list[PackageReport], links: dict[str, set[str]]) -> dict[str, int]:
    """Position of each package so that what an upgrade needs first comes before it.

    Ties keep alphabetical order. Members of a cycle cannot be ordered, so their mutual
    edges are left out and they stay alphabetical next to each other.
    """
    names = sorted(p.name for p in packages)
    known = set(names)
    needs = {
        n: {m for m in links.get(n, ()) if m in known and not _reaches(m, n, links)} for n in names
    }
    rank: dict[str, int] = {}
    while len(rank) < len(names):
        ready = next(n for n in names if n not in rank and needs[n].issubset(rank))
        rank[ready] = len(rank)
    return rank


def _reaches(start: str, goal: str, links: dict[str, set[str]]) -> bool:
    seen, todo = set(), [start]
    while todo:
        node = todo.pop()
        if node == goal:
            return True
        for m in links.get(node, ()):
            if m not in seen:
                seen.add(m)
                todo.append(m)
    return False


def _specs_for(info: ReleaseInfo, name: str, env: dict[str, str]) -> list[SpecifierSet]:
    """What ``info`` requires of ``name``, where the requirement applies."""
    return [
        req.specifier
        for req in _requirements(info, name)
        if req.marker is None or req.marker.evaluate(env)
    ]


def _current_django(
    django: Project, dep: Dependency | None, override: str | None
) -> tuple[str | None, list[str]]:
    """The project's Django version, and warnings about how it was found."""
    if override:
        return _override(django, dep, override)
    if dep is None or dep.version:
        return (dep.version if dep else None), []
    alternatives = spec_sets(dep.spec)
    requirement = f"Django{dep.spec}" if dep.spec else "Django"
    capped = alternatives is not None and any(
        s.operator in _UPPER_BOUNDS or s.version.endswith(".*") for a in alternatives for s in a
    )
    if capped:
        allowed = [
            r.version
            for r in django.stable_releases()
            if any(a.contains(r.version) for a in alternatives)
        ]
        if allowed:
            newest = str(max(allowed))
            return newest, [
                f"Django is not pinned: assuming {newest}, the newest release {requirement} "
                "allows (pass --from to change)"
            ]
    return None, [
        f"Django is not pinned ({requirement}): pass --from with the version you run "
        "to see which upgrades can come first"
    ]


def _override(django: Project, dep: Dependency | None, override: str) -> tuple[str, list[str]]:
    """``--from``, checked against what the project itself says."""
    used = _from_version(django, override)
    if dep is None:
        return used, []
    if dep.version and _is_version(dep.version):
        pin = Version(dep.version)
        if len(Version(override).release) <= 2 and _same_minor(pin, Version(used)):
            return dep.version, []  # --from 4.2 on a 4.2.7 pin: the pin is more precise
        if pin != Version(used):
            return used, [f"--from {override}: using Django {used}, but your project pins {pin}"]
        return used, []
    alternatives = spec_sets(dep.spec) if dep.spec else None
    if alternatives and not any(a.contains(Version(used), prereleases=True) for a in alternatives):
        return used, [
            f"--from {override}: Django {used} is outside your requirement Django{dep.spec}"
        ]
    return used, []


def _from_version(django: Project, text: str) -> str:
    """``--from 4.2`` means the newest 4.2.x; ``--from 4.2.7`` means exactly that."""
    try:
        version = Version(text)
    except InvalidVersion:
        raise ValueError(f"--from {text!r} is not a Django version") from None
    if len(version.release) > 2:
        return str(version)
    series = _series(django).get(Version(f"{version.major}.{version.minor}"))
    if not series:
        raise ValueError(f"--from {text}: there is no released Django {text}")
    return str(series[-1])


def spec_sets(spec: str) -> list[SpecifierSet] | None:
    """A requirement as alternatives (``||`` from Poetry); ``None`` when it cannot be read."""
    try:
        return [SpecifierSet(part.strip()) for part in spec.split("||")]
    except InvalidSpecifier:
        return None


def _is_version(text: str) -> bool:
    try:
        Version(text)
    except InvalidVersion:
        return False
    return True


def _notices(
    requested: str,
    goal: Target,
    django: Project,
    current_minor: Version | None,
    current_django: str | None,
) -> list[str]:
    if current_minor is None or goal.version != current_minor:
        return []
    if requested == "auto":
        return [f"Django {goal.label} is already the newest release"]
    if requested == "lts" and (lts := latest_lts(django)) < current_minor:
        return [f"Django {lts}, the newest LTS, is older than your Django {current_django}"]
    return [f"You are already on Django {current_django}"]


def _warnings(
    requested: str,
    goal: Target,
    django: Project,
    current_minor: Version | None,
    deps: DependencySet,
) -> list[str]:
    warnings = []
    label = goal.label
    if not goal.released:
        warnings.append(
            f"Django {label} is not released yet: only classifiers count, upper bounds are ignored"
        )
    if current_minor is not None and requested in ("auto", "lts", "latest"):
        skipped = skipped_lts(django, current_minor, goal.version)
        if skipped:
            step = min(skipped)
            warnings.append(
                f"This skips Django {', '.join(map(str, sorted(skipped)))} LTS. Upgrading one "
                f"LTS at a time is easier: run with -t {step} for a smaller first step, or "
                "with --via lts for a plan per step"
            )
    dep = deps.dependencies.get("django")
    alternatives = spec_sets(dep.spec) if dep and dep.spec and not dep.version else None
    if alternatives and not any(_allows(a, goal) for a in alternatives):
        warnings.append(
            f"Your requirement Django{dep.spec} excludes Django {label}: widen it when you upgrade"
        )
    needed = _min_python(goal.requires_python)
    if deps.python and needed and Version(deps.python) < Version(needed):
        where = f" (from {deps.python_source})" if deps.python_source else ""
        warnings.append(
            f"Django {label} needs Python {goal.requires_python}, "
            f"your project uses {deps.python}{where}"
        )
    return warnings


def _major(version: Version) -> tuple[int, int]:
    """The part of a version that changes on a breaking release: 0.x counts each minor."""
    return (version.major, 0) if version.major else (0, version.minor)


def _minor(version: str | None) -> Version | None:
    if not version:
        return None
    try:
        v = Version(version)
    except InvalidVersion:
        return None
    return Version(f"{v.major}.{v.minor}")


_Found = tuple[Version, ReleaseInfo, Support]


def _drops(info: ReleaseInfo, uploaded: datetime | None, current: Target) -> bool:
    """True when ``info`` no longer runs on the current Django series, even its newest patch."""
    series = Target(current.version, ga=current.ga, python=current.python)
    support = supports(info, series, uploaded)
    if support.verdict is Verdict.NO:
        return True
    declared = declared_versions(info)
    return support.verdict is not Verdict.YES and bool(declared) and min(declared) > current.version


def _is_yes(verdict: Verdict) -> bool:
    return verdict is Verdict.YES


def _not_no(verdict: Verdict) -> bool:
    return verdict is not Verdict.NO


@dataclass(frozen=True)
class _Failed:
    name: str
    problem: str


class _Checker:
    """Judges packages against one target, with one shared pool for all release lookups."""

    def __init__(
        self,
        pypi: PyPI,
        target: Target,
        current: Target | None,
        pool: Executor,
        batch: int,
        pinned: dict[str, Version] | None = None,
        private: frozenset[str] = frozenset(),
    ):
        self.pypi = pypi
        self.target = target
        self.rule: Rule = DjangoRule(target)
        """What the searches look for unless they are given another rule."""
        self.current = current
        self.pool = pool
        self.batch = batch
        self.pinned = pinned or {}
        self.private = private
        """Packages that must never be looked up on the index (git, paths, private indexes)."""
        """Exact versions of the project's other packages, to see what a release conflicts with."""
        self.traces: dict[str, list[ExplainLine]] = {}
        """The packages to explain, with what was found so far."""
        self.installed: dict[str, ReleaseInfo] = {}
        """Metadata of the installed release of every package looked up."""
        self.links: dict[str, set[str]] = {}
        """Package -> the packages whose installed release forbids its upgrade."""
        self._lock = threading.Lock()

    def trace(self, name: str, section: str, text: str, version: Version | None = None) -> None:
        """Note what happened, when ``name`` is to be explained; else nothing."""
        lines = self.traces.get(name)
        if lines is not None:
            with self._lock:
                lines.append(ExplainLine(section, text, version))

    def _judge(
        self, name: str, rule: Rule, version: Version, info: ReleaseInfo, uploaded
    ) -> Support:
        """``rule.judge``, traced for ``--explain``."""
        support = rule.judge(info, uploaded)
        if name in self.traces:
            when = f" ({uploaded:%Y-%m-%d})" if uploaded else ""
            self.trace(
                name,
                "search",
                f"{version}{when}: {support.verdict.value}, {support.reason}",
                version,
            )
        return support

    def link(self, name: str, needs: str) -> None:
        with self._lock:
            self.links.setdefault(name, set()).add(needs)

    def check(self, dep: Dependency) -> PackageReport | str | None:
        # The installed release's own metadata is a few kilobytes; a project's whole release
        # history can be megabytes (botocore). Most dependencies are not Django-related, and
        # their installed release is enough to tell.
        current_info = self.pypi.release(dep.name, dep.version) if dep.version else None
        # Django took over the job of some packages: show them even if they declare nothing.
        replaced = successor(dep.name, self.target.version) is not None
        if current_info is not None:
            with self._lock:
                self.installed[dep.name] = current_info
            if not replaced and not is_django_related(current_info):
                self.trace(
                    dep.name,
                    "result",
                    f"skipped: {dep.version} has no Django requirement and no "
                    "Framework :: Django classifier",
                )
                return None
        project = self.pypi.project(dep.name)
        if project is None:
            return dep.name
        if (
            not replaced
            and not is_django_related(project.latest)
            and (current_info is None or not is_django_related(current_info))
        ):
            return None
        return _Package(self, dep, project, current_info).judge()

    def find(
        self,
        name: str,
        versions: Iterable[Version],
        dates: dict[Version, datetime | None],
        accept: Callable[[Verdict], bool],
        newest_first: bool = False,
        rule: Rule | None = None,
    ) -> _Found | None:
        """The first of ``versions``, in the given order, whose verdict is accepted.

        The scan stops at a release that excludes the target on the side already passed:
        after it, bounds only move further away.
        """
        rule = rule or self.rule
        candidates = list(versions)
        passed = 1 if newest_first else -1
        for start in range(0, len(candidates), self.batch):
            chunk = candidates[start : start + self.batch]
            infos = list(self.pool.map(lambda v: self.pypi.release(name, str(v)), chunk))
            for version, info in zip(chunk, infos, strict=True):
                if info is None:
                    continue
                support = self._judge(name, rule, version, info, dates.get(version))
                if accept(support.verdict):
                    return version, info, support
                if support.verdict is Verdict.NO and rule.side(info) == passed:
                    return None
        return None

    def lowest_yes(
        self,
        name: str,
        versions: list[Version],
        dates: dict[Version, datetime | None],
        rule: Rule | None = None,
    ) -> _Found | None:
        """The oldest of ``versions`` that declares the target.

        Once a package declares a Django version it keeps declaring it, so a bisection finds
        the first declaring release in a few requests. The batch just before it is checked
        too, for the rare release that dropped a classifier and added it back.
        """

        rule = rule or self.rule

        def declares(version: Version) -> _Found | None:
            info = self.pypi.release(name, str(version))
            if info is None:
                return None
            support = self._judge(name, rule, version, info, dates.get(version))
            return (version, info, support) if support.verdict is Verdict.YES else None

        if len(versions) <= self.batch:
            return self.find(name, versions, dates, _is_yes, rule=rule)
        lo, hi, found = 0, len(versions), None
        while lo < hi:
            mid = (lo + hi) // 2
            hit = declares(versions[mid])
            if hit is not None:
                found, hi = hit, mid
            else:
                lo = mid + 1
        if found is None:
            return None
        start = versions.index(found[0])
        earlier = self.find(
            name, versions[max(0, start - self.batch) : start], dates, _is_yes, rule=rule
        )
        return earlier or found

    def search(
        self,
        name: str,
        versions: list[Version],
        dates: dict[Version, datetime | None],
        highest: bool = False,
        rule: Rule | None = None,
    ) -> _Found | None:
        """The lowest (or ``highest``) of ``versions``, oldest first, that allows the target.

        A bisection that follows the Django bounds, so a long release history costs a few
        requests. Where the bounds do not say which way to go, it falls back to a scan.
        """
        rule = rule or self.rule
        lo, hi = 0, len(versions)
        found = None
        while lo < hi:
            mid = (lo + hi) // 2
            version = versions[mid]
            info = self.pypi.release(name, str(version))
            side = 0
            if info is not None:
                support = self._judge(name, rule, version, info, dates.get(version))
                if support.verdict is not Verdict.NO:
                    found = version, info, support
                    lo, hi = (mid + 1, hi) if highest else (lo, mid)
                    continue
                side = rule.side(info)
            if side == 1:
                lo = mid + 1
            elif side == -1:
                hi = mid
            else:
                rest = versions[lo:hi]
                scanned = self.find(
                    name,
                    reversed(rest) if highest else rest,
                    dates,
                    _not_no,
                    newest_first=highest,
                    rule=rule,
                )
                return scanned or found
        return found


class _Package:
    """One dependency, judged against the target."""

    def __init__(
        self,
        checker: _Checker,
        dep: Dependency,
        project: Project,
        current_info: ReleaseInfo | None,
    ):
        self.checker = checker
        self.target = checker.target
        self.dep = dep
        self.project = project
        self.current_info = current_info
        self.dates = {r.version: r.uploaded for r in project.releases}
        self.stable = [r.version for r in project.stable_releases()]
        if not self.stable:
            self.stable = [Version(project.latest.version)]
        self.latest_support = self._supports(project.latest)
        self.declared = False
        """Whether a stable release that the report names declares the target."""
        last_release = max((r.uploaded for r in project.releases if r.uploaded), default=None)
        self.report = PackageReport(
            name=dep.name,
            display_name=project.name,
            current=dep.version,
            spec=dep.spec,
            latest=project.latest.version,
            status=Status.CHECK,
            reason="",
            last_release=last_release,
        )

    def judge(self) -> PackageReport:
        report = self.report
        if report.stale:
            years = (datetime.now(timezone.utc) - report.last_release).days // 365
            report.notes.append(f"no release in {years} years")
        if _INACTIVE in self.project.latest.classifiers:
            report.notes.append("marked inactive by its maintainers")
        note = _framework_note(self.current_info or self.project.latest)
        if note:
            report.notes.append(note)

        if self.current_info is not None:
            if self.dep.name in self.checker.traces:
                uploaded = self._uploaded(self.current_info)
                for step in explain_support(self.current_info, self.target, uploaded):
                    self.checker.trace(self.dep.name, "release", f"{step.check}: {step.finding}")
            self._pinned()
        elif self.dep.version:
            self._not_on_index()
        else:
            self._unpinned()
        if report.status in (Status.CHECK, Status.BLOCKED):
            self._prerelease()
        report.changelog_url = changelog_url(self.project.latest)
        report.repository_url = repository_url(self.project.latest)
        if report.status is Status.CHECK:
            self._readme()
        self._size()
        return report

    def _readme(self) -> None:
        """The description on PyPI of the release the report names, or the newest one, names
        the target: a sign, not a declaration."""
        report = self.report
        info = self.project.latest
        if report.target_version and report.target_version != info.version:
            info = self.checker.pypi.release(self.dep.name, report.target_version) or info
        target = f"{self.target.version.major}.{self.target.version.minor}"
        if target in info.django_mentions:
            report.evidence.append(
                Evidence(
                    "readme",
                    f"README of {info.version} mentions Django {target}",
                    f"https://pypi.org/project/{self.dep.name}/{info.version}/",
                )
            )

    # --- the three kinds of dependency

    def _pinned(self) -> None:
        report = self.report
        current_support = self._supports(self.current_info)
        if current_support.verdict is Verdict.YES:
            report.status = Status.READY
            report.reason = current_support.reason
            return

        newer = self._newer()
        found = self._lowest_yes(self._could_declare(newer))
        if found is not None:
            self._upgrade(found)
            return

        report.status = Status.CHECK
        if current_support.verdict is not Verdict.NO:
            report.reason = current_support.reason
            if self.latest_support.verdict is Verdict.NO:
                report.notes.append(f"newer releases exclude Django {self.target.label}")
            elif self.project.latest.version != self.dep.version:
                report.notes.append(self._latest_reason())
            return

        found = self._search(newer)
        if found is not None:
            self._check(found)
            return
        report.status = Status.BLOCKED
        report.reason = self._latest_reason()

    def _not_on_index(self) -> None:
        report = self.report
        report.status = Status.CHECK
        report.reason = self._latest_reason()
        report.notes.append(f"installed version {self.dep.version} not found on the index")
        found = self._lowest_yes(self._could_declare(self._newer()))
        if found is not None:
            self._check(found)

    def _unpinned(self) -> None:
        report = self.report
        report.notes.append("version not pinned, add a lockfile for exact results")
        allowed = self._allowed()
        outside = [v for v in self.stable if not allowed or v > allowed[-1]]
        older_allowed = list(reversed(allowed[:-1]))  # newest first: stay as high as possible

        newest = None
        if allowed:
            info = self.checker.pypi.release(self.dep.name, str(allowed[-1]))
            support = self._supports(info) if info else self.latest_support
            where = (
                "latest" if str(allowed[-1]) == self.project.latest.version else "newest allowed"
            )
            newest = (support, f"{where} {allowed[-1]} {support.reason}")
            if support.verdict is Verdict.YES:
                report.status = Status.READY
                report.reason = newest[1]
                return
            found = self._find(self._could_declare(older_allowed), _is_yes, newest_first=True)
            if found is not None:
                self._check(found, note=newest[1])
                return

        found = self._lowest_yes(self._could_declare(outside))
        if found is not None:
            self._upgrade(found)
            report.notes.append(f"outside your requirement {self.dep.spec}")
            return

        if newest is not None:
            if newest[0].verdict is not Verdict.NO:
                report.status = Status.CHECK
                report.reason = newest[1]
                return
            found = self._search(allowed[:-1], highest=True)
            if found is not None:
                self._check(found, note=newest[1])
                return
        found = self._search(outside)
        if found is not None:
            self._check(found, note=f"outside your requirement {self.dep.spec}")
            return
        report.status = Status.BLOCKED
        report.reason = self._latest_reason()

    def _prerelease(self) -> None:
        """Say so when the newest pre-release does what no stable release does yet.

        Only the newest one is fetched, and only when it is newer than every stable release:
        an older pre-release was overtaken. The status stays, a pre-release is not a release.
        """
        if self.declared or successor(self.dep.name, self.target.version) is not None:
            return  # a stable release declares it, or Django took over the package's job
        floor = [r.version for r in self.project.stable_releases()]
        if self.dep.version and _is_version(self.dep.version):
            floor.append(Version(self.dep.version))
        candidates = [
            r
            for r in self.project.releases
            if r.version.is_prerelease
            and r.has_files
            and not r.yanked
            and (not floor or r.version > max(floor))
        ]
        if not candidates:
            return
        release = max(candidates, key=lambda r: r.version)
        try:
            info = self.checker.pypi.release(self.dep.name, str(release.version))
        except PyPIError:  # a hint is not worth losing the package's verdict over
            self.report.notes.append(f"could not check {release.version}, run again later")
            return
        if info is None:
            return
        support = self._supports(info)
        if support.verdict is Verdict.YES:
            reason = support.reason
        elif self.report.status is Status.BLOCKED and support.verdict is not Verdict.NO:
            reason = f"no longer excludes Django {self.target.label}"
        else:
            return
        self.report.prerelease = PreRelease(str(release.version), reason, release.uploaded)
        self.report.notes.append(f"{release.version} {reason} (pre-release)")

    def _size(self) -> None:
        """How many major versions the proposed step crosses, by the releases in between."""
        report = self.report
        if not report.target_version or not report.current or not _is_version(report.current):
            return
        if successor(self.dep.name, self.target.version) is not None:
            return  # the advice is to remove it, not to take the step
        current, target = Version(report.current), Version(report.target_version)
        if current.epoch != target.epoch:  # a new numbering: the numbers do not compare
            report.notes.insert(0, "new version numbering, read the changelog")
            return
        if current.major >= 1000 or target.major >= 1000:  # 2024.1: calendar versions
            if target.major != current.major:
                report.notes.insert(0, "calendar versions, read the changelog")
            return
        crossed = {
            _major(v) for v in self.stable if current < v <= target and _major(v) != _major(current)
        }
        if _major(target) != _major(current):
            crossed.add(_major(target))
        report.majors_crossed = len(crossed)
        if crossed:
            n = len(crossed)
            # First: it is about the step itself, the notes after it about its conditions.
            report.notes.insert(
                0, "crosses a major version" if n == 1 else f"crosses {n} major versions"
            )

    # --- helpers

    def _newer(self) -> list[Version]:
        try:
            installed = Version(self.dep.version)
        except InvalidVersion:
            return []
        return [v for v in self.stable if v > installed]

    def _uploaded(self, info: ReleaseInfo) -> datetime | None:
        try:
            return self.dates.get(Version(info.version))
        except InvalidVersion:
            return None

    def _supports(self, info: ReleaseInfo, target: Target | None = None) -> Support:
        return supports(info, target or self.target, self._uploaded(info))

    def _find(
        self, versions: Iterable[Version], accept, newest_first: bool = False
    ) -> _Found | None:
        return self.checker.find(self.dep.name, versions, self.dates, accept, newest_first)

    def _lowest_yes(self, versions: list[Version]) -> _Found | None:
        return self.checker.lowest_yes(self.dep.name, versions, self.dates)

    def _search(self, versions: list[Version], highest: bool = False) -> _Found | None:
        return self.checker.search(self.dep.name, versions, self.dates, highest)

    def _could_declare(self, versions: list[Version]) -> list[Version]:
        """Leave out releases uploaded too long before the target to declare it.

        An unreleased target is declared only by a classifier, and once a package adds that it
        keeps it: when the latest release does not declare the target, no older one does.
        """
        if not self.target.released and self.latest_support.verdict is not Verdict.YES:
            return []
        horizon = (self.target.ga or datetime.now(timezone.utc)) - _DECLARE_WINDOW
        return [v for v in versions if (d := self.dates.get(v)) is None or d >= horizon]

    def _latest_reason(self) -> str:
        return f"latest {self.project.latest.version} {self.latest_support.reason}"

    def _check(self, found: _Found, note: str | None = None) -> None:
        version, _, support = found
        self.declared = support.verdict is Verdict.YES
        self.report.status = Status.CHECK
        self.report.target_version = str(version)
        self.report.reason = f"{version} {support.reason}"
        if note:
            self.report.notes.append(note)

    def _upgrade(self, found: _Found) -> None:
        version, info, support = found
        self.report.status = Status.UPGRADE
        self.report.target_version = str(version)
        self.report.reason = f"{version} {support.reason}"
        self._phase(info)

    def _phase(self, info: ReleaseInfo) -> None:
        """Can ``info`` be installed before Django is upgraded?"""
        current = self.checker.current
        if current is None:
            return
        support = self._supports(info, current)
        exact = current.patches[0]
        series = Target(current.version, ga=current.ga, python=current.python)
        phase = Phase.BEFORE
        if support.verdict is Verdict.NO:
            if self._supports(info, series).verdict is Verdict.NO:
                phase = Phase.WITH
            else:  # a newer patch of the current Django is enough
                self.report.notes.append(
                    f"{support.reason}, you have {exact}: update Django {current.label} first"
                )
        elif support.verdict is not Verdict.YES:
            declared = declared_versions(info)
            if declared and min(declared) > current.version:
                phase = Phase.WITH
                oldest = declared[0]
                self.report.notes.append(
                    f"declares Django {oldest.major}.{oldest.minor} and newer only"
                )
            else:
                self.report.notes.append(f"not declared for Django {exact}")
        for name, display, spec, have in self._conflicts(info, current.python):
            needed = f"needs {display}{spec}, you have {have}"
            bound = self._needs_newer_django(name, spec, current)
            if bound is None:
                self.checker.link(self.dep.name, name)
                self.report.notes.append(f"{needed}: upgrade {display} first")
                self._trace_phase(f"{needed}: {display} can be upgraded first")
            else:
                phase = Phase.WITH
                self.report.notes.append(
                    f"{needed}, and {display} {bound} no longer runs on Django {current.label}"
                )
                self._trace_phase(f"{needed}, and {display} {bound} needs a newer Django")
        if current.version != self.target.version:  # no phases in a health check
            self.report.phase = phase
            self.checker.trace(
                self.dep.name,
                "phase",
                f"{info.version} on your Django {exact}: {support.verdict.value}, "
                f"{support.reason} → {'with Django' if phase is Phase.WITH else 'before Django'}",
            )

    def _trace_phase(self, text: str) -> None:
        if self.checker.current and self.checker.current.version != self.target.version:
            self.checker.trace(self.dep.name, "phase", text)

    def _conflicts(
        self, info: ReleaseInfo, python: str | None
    ) -> list[tuple[str, str, SpecifierSet, Version]]:
        """Requirements of ``info`` that the project's other pinned packages do not meet."""
        env = _environment(python)
        found = []
        for line in info.requires_dist:
            try:
                req = Requirement(line)
            except InvalidRequirement:
                continue
            name = canonicalize_name(req.name)
            have = self.checker.pinned.get(name)
            if name in ("django", self.dep.name) or have is None:
                continue
            if req.marker is not None and not req.marker.evaluate(env):
                continue
            if not req.specifier.contains(have, prereleases=True):
                found.append((name, req.name, req.specifier, have))
        return found

    def _needs_newer_django(self, name: str, spec: SpecifierSet, current: Target) -> str | None:
        """The first release of ``name`` that meets ``spec``, if it dropped the current Django.

        ``None`` when that release still runs on it, so the package can be upgraded first.
        """
        if name in self.checker.private:
            return None
        project = self.checker.pypi.project(name)
        if project is None:
            return None
        first = next(
            (r for r in project.stable_releases() if spec.contains(r.version, prereleases=True)),
            None,
        )
        if first is None:
            return None
        info = self.checker.pypi.release(name, str(first.version))
        if info is None or not _drops(info, first.uploaded, current):
            return None
        return str(first.version)

    def _allowed(self) -> list[Version]:
        alternatives = spec_sets(self.dep.spec) if self.dep.spec else None
        if alternatives is None:
            return self.stable
        return [v for v in self.stable if any(a.contains(v) for a in alternatives)]
