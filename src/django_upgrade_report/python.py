"""Whether a release runs on a given Python: what it requires, declares and ships wheels for."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from django_upgrade_report.analysis import (
    SEVERITY,
    PackageReport,
    Report,
    Status,
    Support,
    Verdict,
    _is_version,
    _min_python,
)
from django_upgrade_report.pypi import FetchError, PyPI, ReleaseInfo
from django_upgrade_report.sources import Dependency, DependencySet

PURE = "pure Python"
"""The reason for a release with only a pure-Python wheel: it says nothing, but rarely breaks."""


def python_supports(
    python: str,
    requires_python: str | None,
    classifiers: tuple[str, ...] = (),
    wheel_tags: tuple[str, ...] = (),
    has_sdist: bool | None = None,
) -> Support:
    """What a release with this metadata says about running on Python ``python`` (X.Y).

    In this order: a ``Requires-Python`` that excludes it, a classifier that declares it, a
    wheel built for it, a wheel for any Python, wheels only for other Pythons, nothing.
    Wheels count when they install on CPython under Linux on x86_64, where Django apps run.
    """
    target = Version(python)
    spec = _spec(requires_python)
    if spec is not None and not spec.contains(target, prereleases=True):
        return Support(Verdict.NO, f"requires Python {requires_python}")
    if f"Programming Language :: Python :: {python}" in classifiers:
        return Support(Verdict.YES, f"declares Python {python}")
    fits = [kind for tag in wheel_tags if (kind := _wheel_fits(tag, target))]
    if "exact" in fits:
        return Support(Verdict.YES, f"has a wheel for Python {python}")
    if "abi3" in fits:
        return Support(Verdict.LIKELY, f"has a wheel for Python {python} and newer (abi3)")
    if "pure" in fits:
        return Support(Verdict.LIKELY, PURE)
    if wheel_tags:  # built wheels, but none for this Python
        if has_sdist:
            return Support(
                Verdict.LIKELY, f"no wheel for Python {python}, pip builds it from source"
            )
        if has_sdist is False:
            return Support(Verdict.NO, f"no wheel for Python {python} and no source to build")
    return Support(Verdict.UNKNOWN, f"declares nothing about Python {python}")


def _spec(requires_python: str | None) -> SpecifierSet | None:
    if not requires_python:
        return None
    try:
        return SpecifierSet(requires_python)
    except InvalidSpecifier:
        return None  # a broken Requires-Python says nothing, like a missing one


def _wheel_fits(tag: str, python: Version) -> str | None:
    """How a wheel tag fits CPython ``python`` on Linux x86_64: exact, abi3, pure or not."""
    try:
        interpreter, abi, platform = tag.split("-")
    except ValueError:
        return None
    if platform != "any" and not ("linux" in platform and platform.endswith("x86_64")):
        return None
    if interpreter.startswith("cp") and interpreter[2:].isdigit():
        built = _python_of(interpreter[2:])
        if built is None:
            return None
        if abi == "abi3":
            return "abi3" if built <= python else None
        return "exact" if built == python else None
    if abi == "none" and interpreter in ("py3", "py2.py3"):
        return "pure"
    if abi == "none" and interpreter.startswith("py3") and interpreter[2:].isdigit():
        built = _python_of(interpreter[2:])  # py311: pure, for Python 3.11 and newer
        return "pure" if built is not None and built <= python else None
    return None  # PyPy, Python 2 only, or something else


def _python_of(digits: str) -> Version | None:
    """``"312"`` as Python 3.12."""
    try:
        return Version(f"{digits[0]}.{digits[1:]}")
    except (InvalidVersion, IndexError):
        return None


@dataclass(frozen=True)
class PythonRule:
    """Support for one Python version, for the release searches (see ``analysis.Rule``)."""

    python: str

    def judge(self, info: ReleaseInfo, uploaded: datetime | None) -> Support:
        return python_supports(
            self.python, info.requires_python, info.classifiers, info.wheel_tags, info.has_sdist
        )

    def side(self, info: ReleaseInfo) -> int:
        """Releases that drop old Pythons come later: an upper bound sends the search on."""
        spec = _spec(info.requires_python)
        if spec is None:
            return 0
        target = Version(self.python)
        sides = set()
        for s in spec:
            if s.contains(target, prereleases=True):
                continue
            if s.operator in (">", ">="):
                sides.add(-1)
            elif s.operator in ("<", "<="):
                sides.add(1)
            else:
                return 0
        return sides.pop() if len(sides) == 1 else 0


# --- the whole project on a newer Python --------------------------------------------


@dataclass
class PythonPlan:
    """Which dependencies need what before the project can switch to :attr:`target`."""

    target: str
    """The Python to switch to, as X.Y."""
    current: str | None
    """The project's Python, as X.Y, when known."""
    packages: list[PackageReport] = field(default_factory=list)
    """Dependencies that need something: an upgrade, a check, or that block. Worst first."""
    ready: int = 0
    """Dependencies whose installed release declares the target or ships a wheel for it."""
    pure: int = 0
    """Dependencies whose installed release is pure Python: they rarely break."""
    silent: list[str] = field(default_factory=list)
    """Dependencies whose installed release says nothing about Python at all."""
    unknown: list[str] = field(default_factory=list)
    """Dependencies the index could not answer for: the plan leaves them out."""
    django_note: str | None = None
    """Whether your Django release itself declares the target Python."""


def check_python_target(requested: str) -> str:
    """A ``--python-target`` such as ``3.12`` or ``3.12.1`` as X.Y; a ValueError otherwise."""
    try:
        version = Version(requested)
    except InvalidVersion:
        version = None
    if version is None or len(version.release) < 2 or version.major != 3:
        raise ValueError(
            f"--python-target {requested!r} is not a Python version such as 3.12, auto or none"
        )
    return f"{version.major}.{version.minor}"


def python_target(requested: str, report: Report) -> str | None:
    """The Python to check against: ``auto`` when the target Django needs a newer one."""
    if requested == "none":
        return None
    if requested != "auto":
        return check_python_target(requested)
    needed = _min_python(report.django_requires_python)
    if needed is None:
        return None
    if report.project_python is None:  # the report's last line says what Django needs
        return None
    return needed if Version(needed) > Version(report.project_python) else None


def plan_python(
    target: str, report: Report, deps: DependencySet, pypi: PyPI, workers: int = 8
) -> PythonPlan:
    """Judge every pinned dependency from PyPI, Django-related or not, on Python ``target``.

    The installed release is usually cached from the Django check already. The release
    history is fetched only for a dependency that needs an upgrade, and judged from it alone.
    """
    plan = PythonPlan(target, report.project_python)
    candidates = sorted(
        (
            d
            for d in deps.dependencies.values()
            if d.version and d.external is None and d.name != "django"  # Django: django_note
        ),
        key=lambda d: d.name,
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda d: _judge_on(target, d, plan.current, pypi), candidates))
    for dep, result in zip(candidates, results, strict=True):
        if result == "ready":
            plan.ready += 1
        elif result == "pure":
            plan.pure += 1
        elif result == "silent":
            plan.silent.append(dep.name)
        elif isinstance(result, PackageReport):
            plan.packages.append(result)
        else:
            plan.unknown.append(dep.name)
    for row in plan.packages:
        row.direct = deps.dependencies[row.name].direct
    plan.packages.sort(key=lambda p: (-SEVERITY[p.status], p.name))
    plan.django_note = _django_on(target, report.current_django, pypi)
    _mark_django_upgrades(target, report, pypi)
    return plan


def _judge_on(
    target: str, dep: Dependency, current: str | None, pypi: PyPI
) -> PackageReport | str | None:
    """``"ready"``, ``"pure"``, ``"silent"``, a row to show, or ``None`` when unknown."""
    try:
        info = pypi.release(dep.name, dep.version)
    except FetchError:
        return None
    if info is None:
        return None
    support = PythonRule(target).judge(info, None)
    if support.verdict is Verdict.YES or (
        support.verdict is Verdict.LIKELY and "abi3" in support.reason
    ):
        return "ready"
    if support.reason == PURE:
        return "pure"
    if support.verdict is Verdict.UNKNOWN:
        return "silent"
    try:
        project = pypi.project(dep.name)
    except FetchError:
        project = None
    row = PackageReport(
        name=dep.name,
        display_name=project.name if project else info.name,
        current=dep.version,
        spec=dep.spec,
        latest=project.latest.version if project else info.version,
        status=Status.CHECK,
        reason=f"{dep.version} {support.reason}",
    )
    if project is None:
        return row
    if not _is_version(dep.version):
        return row  # no way to tell which releases are newer
    installed = Version(dep.version)
    for release in project.stable_releases():
        if release.version <= installed:
            continue
        later = python_supports(
            target, release.requires_python, (), release.wheel_tags, release.has_sdist
        )
        builds = later.verdict is Verdict.LIKELY and "from source" in later.reason
        # A release that only stops excluding the Python is a step for one that excludes it.
        better = support.verdict is Verdict.NO
        if later.verdict is Verdict.NO or (
            (builds or later.verdict is Verdict.UNKNOWN) and not better
        ):
            continue
        row.status, row.target_version = Status.UPGRADE, str(release.version)
        if builds:
            row.notes.append(f"{release.version} {later.reason}")
        if (
            current
            and python_supports(
                current, release.requires_python, (), release.wheel_tags, release.has_sdist
            ).verdict
            is Verdict.NO
        ):
            row.notes.append(f"goes together with the switch to Python {target}")
        return row
    if support.verdict is Verdict.NO:
        row.status = Status.BLOCKED
        row.notes.append(f"no newer release runs on Python {target} either")
    return row


def _django_on(target: str, current: str | None, pypi: PyPI) -> str | None:
    """Whether the Django the project runs declares ``target``, else the first patch that does."""
    if not current or not _is_version(current):
        return None
    try:
        return _django_patch(target, current, pypi)
    except FetchError:  # a note is not worth the report
        return None


def _django_patch(target: str, current: str, pypi: PyPI) -> str | None:
    django = pypi.project("django")
    info = pypi.release("django", current)
    if django is None or info is None:
        return None
    classifier = f"Programming Language :: Python :: {target}"
    if classifier in info.classifiers:
        return None
    mine = Version(current)
    series = [
        r.version
        for r in django.stable_releases()
        if r.version.release[:2] == mine.release[:2] and r.version > mine
    ]
    # Once a patch release declares a Python, the later ones of the series keep it.
    lo, hi, first = 0, len(series), None
    while lo < hi:
        mid = (lo + hi) // 2
        later = pypi.release("django", str(series[mid]))
        if later is not None and classifier in later.classifiers:
            first, hi = series[mid], mid
        else:
            lo = mid + 1
    label = f"{mine.major}.{mine.minor}"
    if first is None:
        return (
            f"No Django {label} release declares Python {target}: "
            "switch Python together with Django"
        )
    return (
        f"Django {current} does not declare Python {target}, {first} does: "
        f"update Django {label} first"
    )


def _mark_django_upgrades(target: str, report: Report, pypi: PyPI) -> None:
    """An upgrade the Django plan proposes that excludes the target Python says so."""
    for p in report.packages:
        if not p.target_version:
            continue
        try:
            info = pypi.release(p.name, p.target_version)
        except FetchError:
            continue
        if info is None:
            continue
        support = python_supports(
            target, info.requires_python, info.classifiers, info.wheel_tags, info.has_sdist
        )
        if support.verdict is Verdict.NO:
            p.notes.append(f"{p.target_version} {support.reason}")
