from __future__ import annotations

import pytest
from conftest import release
from packaging.version import Version

from django_upgrade_report.analysis import Phase, Status, Verdict, analyse, supports
from django_upgrade_report.pypi import ReleaseInfo
from django_upgrade_report.sources import Dependency, DependencySet

T52 = Version("5.2")


def info(django=None, classifiers=(), extra=()) -> ReleaseInfo:
    r = release("pkg", "1.0", django, classifiers, extra)
    return ReleaseInfo(
        r["name"], r["version"], tuple(r["classifiers"]), tuple(r["requires_dist"]), None
    )


@pytest.mark.parametrize(
    ("django", "classifiers", "verdict"),
    [
        (">=4.2", ["4.2", "5.2"], Verdict.YES),
        (None, ["5.2"], Verdict.YES),
        (">=4.2,<6.0", [], Verdict.YES),  # an explicit upper bound above the target
        (">=4.2,<6.0", ["4.2"], Verdict.YES),
        ("<5.0", ["4.2", "5.2"], Verdict.NO),  # the requirement wins over classifiers
        ("<5.2", [], Verdict.NO),
        (">=6.0", ["6.0"], Verdict.NO),
        ("<=5.2", [], Verdict.YES),
        (">=5.2.4", [], Verdict.LIKELY),  # a later patch release of 5.2 is fine
        (">=4.2", ["4.1", "4.2"], Verdict.LIKELY),  # lagging classifiers are not a blocker
        (None, ["6.0"], Verdict.LIKELY),
        (">=3.2", [], Verdict.LIKELY),
        (None, [], Verdict.UNKNOWN),
    ],
)
def test_supports(django, classifiers, verdict):
    assert supports(info(django, classifiers), T52).verdict is verdict


def test_django_only_needed_for_an_extra_is_ignored():
    extra = ['Django<4 ; extra == "admin"']
    assert supports(info(None, ["5.2"], extra), T52).verdict is Verdict.YES


def deps(**versions: str | None) -> DependencySet:
    return DependencySet(
        "test",
        {
            name.replace("_", "-"): Dependency(name.replace("_", "-"), version)
            for name, version in versions.items()
        },
    )


@pytest.fixture
def report(index):
    return analyse(
        deps(
            django="4.2.7",
            django_ready="1.0",
            django_before="1.0",
            django_with="2.0",
            django_blocked="1.0",
            django_lagging="1.0",
            django_silent="0.2",
            requests="2.31.0",
            private_thing="1.0",
        ),
        index,
        target="lts",
    )


def by_name(report, name):
    return next(p for p in report.packages if p.name == name)


def test_target_lts_resolves_to_the_newest_x2(report):
    assert report.target == "5.2"
    assert report.current_django == "4.2.7"
    assert report.django_requires_python == ">=3.10"


def test_ready(report):
    assert by_name(report, "django-ready").status is Status.READY


def test_smallest_upgrade_that_still_runs_on_current_django(report):
    p = by_name(report, "django-before")
    assert p.status is Status.UPGRADE
    assert p.target_version == "2.0"
    assert p.phase is Phase.BEFORE


def test_upgrade_that_needs_the_new_django(report):
    p = by_name(report, "django-with")
    assert p.status is Status.UPGRADE
    assert p.target_version == "3.0"
    assert p.phase is Phase.WITH


def test_blocked_and_stale(report):
    p = by_name(report, "django-blocked")
    assert p.status is Status.BLOCKED
    assert "requires Django<5.0" in p.reason
    assert p.stale
    assert any("no release" in note for note in p.notes)


def test_lagging_classifiers_need_a_check_not_a_block(report):
    assert by_name(report, "django-lagging").status is Status.CHECK
    assert by_name(report, "django-silent").status is Status.CHECK


def test_unrelated_and_missing_packages(report):
    names = {p.name for p in report.packages}
    assert "requests" not in names
    assert report.skipped == 1
    assert report.missing == ["private-thing"]


def test_order_puts_blockers_first(report):
    assert [p.status for p in report.packages][0] is Status.BLOCKED
    assert [p.status for p in report.packages][-1] is Status.READY


def test_explicit_target(index):
    report = analyse(deps(django="4.2.7", django_before="2.0"), index, target="6.0")
    assert report.target == "6.0"
    p = by_name(report, "django-before")
    assert (p.status, p.target_version) == (Status.UPGRADE, "2.1")


def test_unpinned_dependency_is_judged_by_latest(index):
    report = analyse(
        DependencySet("test", {"django-ready": Dependency("django-ready", None, ">=1.0")}),
        index,
    )
    p = by_name(report, "django-ready")
    assert p.status is Status.READY
    assert any("not pinned" in n for n in p.notes)
    assert report.current_django is None


def test_invalid_target(index):
    with pytest.raises(ValueError):
        analyse(deps(django="4.2"), index, target="banana")
