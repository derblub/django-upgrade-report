"""The frameworks a report can be about: Django, and Wagtail and django CMS on top of it.

Each one has its package on PyPI, its trove classifiers, its long-term support releases and
the way its feature versions count up. Everything else about checking packages against a
version is the same for all three.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from packaging.version import Version


@dataclass(frozen=True)
class Framework:
    key: str
    """``"django"``, ``"wagtail"`` or ``"django-cms"``: what ``--framework`` takes."""
    display: str
    """``"Django"``, ``"Wagtail"``, ``"django CMS"``: how the report names it."""
    package: str
    """The canonical name of its package on PyPI."""
    classifier: str
    """``"Framework :: Django"``: followed by `` :: X.Y`` (or `` :: X``) for a version."""
    lts_versions: frozenset[Version] = frozenset()
    """Long-term support releases, when the framework does not follow a rule for them."""
    major_classifiers: bool = False
    """The classifiers name major versions only (``Framework :: Wagtail :: 6``)."""
    minor_re: re.Pattern = field(init=False, repr=False, compare=False)
    major_re: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        prefix = re.escape(self.classifier)
        object.__setattr__(self, "minor_re", re.compile(rf"^{prefix} :: (\d+\.\d+)$"))
        object.__setattr__(self, "major_re", re.compile(rf"^{prefix} :: (\d+)$"))

    def is_lts(self, version: Version) -> bool:
        if self.key == "django":  # every x.2 since 2.2
            return version.minor == 2 and version.major >= 2
        return Version(f"{version.major}.{version.minor}") in self.lts_versions

    def next_feature(self, version: Version) -> Version:
        """The feature version after ``version``, for a target that is not released yet."""
        if self.key == "django" and version.minor >= 2:  # Django counts X.0, X.1, X.2
            return Version(f"{version.major + 1}.0")
        return Version(f"{version.major}.{version.minor + 1}")

    @property
    def lts_label(self) -> str:
        """How its LTS releases are known: ``"x.2"`` for Django."""
        return "x.2" if self.key == "django" else "LTS"


DJANGO = Framework("django", "Django", "django", "Framework :: Django")
WAGTAIL = Framework(
    "wagtail",
    "Wagtail",
    "wagtail",
    "Framework :: Wagtail",
    # https://github.com/wagtail/wagtail/wiki/Release-schedule
    lts_versions=frozenset(Version(v) for v in ("2.3", "2.7", "2.16", "4.1", "5.2", "6.3", "7.0")),
    major_classifiers=True,
)
DJANGO_CMS = Framework("django-cms", "django CMS", "django-cms", "Framework :: Django CMS")
FRAMEWORKS = {f.key: f for f in (DJANGO, WAGTAIL, DJANGO_CMS)}
