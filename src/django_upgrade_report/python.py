"""Whether a release runs on a given Python: what it requires, declares and ships wheels for."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from django_upgrade_report.analysis import Support, Verdict
from django_upgrade_report.pypi import ReleaseInfo

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
