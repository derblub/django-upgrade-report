from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import DJANGO, _info, release
from packaging.version import Version

from django_upgrade_report.analysis import Phase, Status, Target, Verdict, analyse, supports
from django_upgrade_report.pypi import PyPI, PyPIError, ReleaseInfo
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


# --- the verdict of one release -----------------------------------------------

GA_52 = datetime(2025, 4, 2, tzinfo=timezone.utc)
T52_RELEASED = Target(
    Version("5.2"),
    tuple(Version(v) for v in ("5.2", "5.2.3", "5.2.17")),
    ga=GA_52,
    requires_python=">=3.10",
    python="3.12",
)
BEFORE_GA = datetime(2024, 11, 1, tzinfo=timezone.utc)
AFTER_GA = datetime(2025, 4, 24, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("django", "classifiers", "uploaded", "verdict"),
    [
        ("==5.2.17", [], None, Verdict.YES),  # an exact pin is a declaration
        ("===5.2.3", [], None, Verdict.YES),
        (">=5.2.3,<5.2.8", [], AFTER_GA, Verdict.YES),  # a range inside the series
        ("==5.2.17", [], AFTER_GA, Verdict.YES),
        ("==4.2.7", [], AFTER_GA, Verdict.NO),
        (">=4.2,<6.0", ["4.2", "5.0", "5.1"], BEFORE_GA, Verdict.LIKELY),  # wagtail 6.3
        (">=4.2,<6.0", ["4.2", "5.0", "5.1"], AFTER_GA, Verdict.YES),  # wagtail 6.3.4
        (">=4.2,<6.0", [], None, Verdict.LIKELY),  # no date, no pin: not a declaration
        (">=4.2", ["5.2"], BEFORE_GA, Verdict.YES),  # a classifier counts regardless of date
        (">=4.2", ["4.2", "5"], None, Verdict.LIKELY),  # major-only classifier
        (None, ["5"], None, Verdict.LIKELY),
        (None, ["4"], None, Verdict.LIKELY),
    ],
)
def test_supports_released_target(django, classifiers, uploaded, verdict):
    assert supports(info(django, classifiers), T52_RELEASED, uploaded).verdict is verdict


def test_upper_bound_before_ga_names_the_reason():
    support = supports(info(">=4.2,<6.0", ["4.2"]), T52_RELEASED, BEFORE_GA)
    assert support.reason == "allows Django<6.0,>=4.2, released before 5.2"


def test_major_only_classifier_reason():
    support = supports(info(None, ["4.2", "5"]), T52_RELEASED)
    assert (support.verdict, support.reason) == (Verdict.LIKELY, "declares Django 5, not 5.2")


def test_unreleased_target_ignores_upper_bounds():
    t62 = Target(Version("6.2"), released=False, python="3.12")
    assert supports(info(">=4.2,<7.0", []), t62, AFTER_GA).verdict is Verdict.LIKELY
    assert supports(info(">=4.2", ["6.2"]), t62).verdict is Verdict.YES
    assert supports(info("<6.2", []), t62).verdict is Verdict.NO


SES_LINES = (
    'django<6,>=3.2; python_version >= "3.10" and python_version < "3.12"',
    'django<7,>=3.2; python_version == "3.12"',
    'django<7,>=4; python_version >= "3.13"',
)


@pytest.mark.parametrize(
    ("python", "verdict"), [("3.11", Verdict.NO), ("3.12", Verdict.YES), ("3.13", Verdict.YES)]
)
def test_markers_are_evaluated_for_the_python_django_runs_on(python, verdict):
    t60 = Target(Version("6.0"), (Version("6.0"),), ga=GA_52, python=python)
    assert supports(info(extra=SES_LINES), t60, AFTER_GA).verdict is verdict


def test_all_matching_django_lines_count():
    split = info(extra=("Django>=4.2", "Django<5.0"))
    assert supports(split, T52_RELEASED, AFTER_GA).verdict is Verdict.NO


def test_current_django_respects_patch_floors():
    current = Target(Version("4.2"), (Version("4.2.7"),), python="3.12", future_patches=False)
    assert supports(info(">=4.2.16"), current).verdict is Verdict.NO
    assert supports(info(">=4.2.7", ["4.2"]), current).verdict is Verdict.YES


# --- statuses and phases ------------------------------------------------------


def status_index(**packages):
    from conftest import DJANGO, FakePyPI

    return FakePyPI({"django": DJANGO, **{k.replace("_", "-"): v for k, v in packages.items()}})


def check_one(index, dep: Dependency, django="4.2.7", target="5.2", python=None):
    deps = {dep.name: dep}
    if django:
        deps["django"] = Dependency("django", django)
    report = analyse(DependencySet("test", deps, python=python), index, target=target)
    return report, by_name(report, dep.name)


def pinned(name, version):
    return Dependency(name, version)


def test_installed_release_that_allows_the_target_is_not_blocked():
    """The newest release moved past the target, but the installed one still allows it."""
    index = status_index(
        pkg_a=[
            release("pkg-a", "1.0", ">=4.2", ["4.2", "5.0"]),
            release("pkg-a", "3.0", ">=6.0", ["6.0"]),
        ],
        pkg_b=[
            release("pkg-b", "1.0", "<5.0"),
            release("pkg-b", "2.0", ">=4.2", ["4.2", "5.0"]),
            release("pkg-b", "3.0", ">=6.0", ["6.0"]),
        ],
        pkg_c=[release("pkg-c", "1.0", ""), release("pkg-c", "3.0", ">=6.0")],
    )
    _, a = check_one(index, pinned("pkg-a", "1.0"))
    assert (a.status, a.target_version) == (Status.CHECK, None)
    assert "newer releases exclude Django 5.2" in a.notes
    _, b = check_one(index, pinned("pkg-b", "1.0"))
    assert (b.status, b.target_version) == (Status.CHECK, "2.0")
    _, c = check_one(index, pinned("pkg-c", "1.0"))
    assert c.status is Status.CHECK


def test_blocked_only_when_every_release_from_the_installed_one_excludes_the_target():
    index = status_index(
        pkg=[
            release("pkg", "0.5", ">=3.2"),
            release("pkg", "1.0", "<5.0"),
            release("pkg", "2.0", "<5.1"),
        ]
    )
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert p.status is Status.BLOCKED
    assert p.reason == "latest 2.0 requires Django<5.1"


def test_smallest_supporting_release_among_many():
    """No scan window: the oldest supporting release is found even with 100 newer ones."""
    releases = [release("pkg", f"1.{i}", ">=3.2,<5.0", ["4.2"]) for i in range(60)]
    releases += [release("pkg", f"2.{i}", ">=4.2", ["4.2", "5.2"]) for i in range(60)]
    _, p = check_one(status_index(pkg=releases), pinned("pkg", "1.0"))
    assert (p.status, p.target_version, p.phase) == (Status.UPGRADE, "2.0", Phase.BEFORE)


def test_upgrade_that_needs_a_newer_django_patch_still_comes_first():
    """2.0 runs on Django 4.2.16+: a patch update of the current Django, then 2.0, then 5.2."""
    index = status_index(
        pkg=[
            release("pkg", "1.0", "<5.0", ["4.2"]),
            release("pkg", "2.0", ">=4.2.16", ["4.2", "5.2"]),
        ]
    )
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert (p.status, p.phase) == (Status.UPGRADE, Phase.BEFORE)
    assert "requires Django>=4.2.16, you have 4.2.7: update Django 4.2 first" in p.notes


def test_upgrade_first_when_only_likely_on_current_django_says_so():
    index = status_index(
        pkg=[release("pkg", "1.0", "<5.0", ["4.2"]), release("pkg", "2.0", ">=3.2", ["4.1", "5.2"])]
    )
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert (p.status, p.phase) == (Status.UPGRADE, Phase.BEFORE)
    assert "not declared for Django 4.2.7" in p.notes


def test_release_that_declares_only_newer_djangos_goes_with_django():
    """Classifiers from 5.0 on and a stale Django>=3.2: 4.2 support was dropped."""
    index = status_index(
        pkg=[release("pkg", "1.0", "<5.0", ["4.2"]), release("pkg", "2.0", ">=3.2", ["5.0", "5.2"])]
    )
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert (p.status, p.phase) == (Status.UPGRADE, Phase.WITH)
    assert "declares Django 5.0 and newer only" in p.notes


def test_release_that_needs_a_newer_version_of_another_package_goes_with_django():
    """wagtail-modeladmin 2.5 needs Wagtail>=7.0, which itself only comes with the new Django."""
    index = status_index(
        plugin=[
            release("plugin", "1.0", ">=3.2", ["4.2"], extra=["wagtail>=5.0"]),
            release("plugin", "2.0", ">=4.2", ["4.2", "5.2"], extra=["wagtail>=7.0"]),
        ],
        wagtail=[
            release("wagtail", "5.2.8", ">=3.2,<5.1", ["4.2", "5.0"]),
            release("wagtail", "7.0", ">=5.2", ["5.2"]),
        ],
    )
    deps = {
        "django": Dependency("django", "4.2.7"),
        "plugin": pinned("plugin", "1.0"),
        "wagtail": pinned("wagtail", "5.2.8"),
    }
    report = analyse(DependencySet("test", deps), index, target="5.2")
    p = by_name(report, "plugin")
    assert (p.status, p.phase) == (Status.UPGRADE, Phase.WITH)
    assert "needs wagtail>=7.0, you have 5.2.8, and wagtail 7.0 no longer runs on Django 4.2" in (
        p.notes
    )


def conflict_index(**extra):
    return status_index(
        pkg=[
            release("pkg", "1.0", ">=3.2", ["4.2"]),
            release("pkg", "2.0", ">=4.2", ["4.2", "5.2"], extra=["requests>=2.32"]),
        ],
        requests=[release("requests", "2.31.0"), release("requests", "2.32.0")],
        **extra,
    )


def test_needing_a_newer_unrelated_package_still_comes_first():
    """Regression: any conflicting pin used to force "Upgrade together with Django"."""
    deps = {
        "django": Dependency("django", "4.2.7"),
        "pkg": pinned("pkg", "1.0"),
        "requests": pinned("requests", "2.31.0"),
    }
    p = by_name(analyse(DependencySet("test", deps), conflict_index(), target="5.2"), "pkg")
    assert (p.status, p.phase) == (Status.UPGRADE, Phase.BEFORE)
    assert "needs requests>=2.32, you have 2.31.0: upgrade requests first" in p.notes


def test_conflicts_with_private_packages_are_never_looked_up():
    """A pinned package from a private source must not reach the index, not even for conflicts."""
    index = conflict_index()
    deps = {
        "django": Dependency("django", "4.2.7"),
        "pkg": pinned("pkg", "1.0"),
        "requests": Dependency("requests", "2.31.0", external="index https://pkgs.example.com"),
    }
    p = by_name(analyse(DependencySet("test", deps), index, target="5.2"), "pkg")
    assert p.status is Status.UPGRADE
    assert not any("/requests/" in url for url in index.requests)


def test_needing_an_upgrade_that_runs_on_current_django_comes_first():
    """wagtail 6.3 needs DRF>=3.15.1, and DRF 3.15.1 still runs on Django 4.2."""
    index = status_index(
        wagtail=[
            release("wagtail", "5.2.3", ">=3.2,<5.1", ["4.2", "5.0"], extra=["drf>=3.11"]),
            release("wagtail", "6.3", ">=4.2,<6.0", ["4.2", "5.2"], extra=["drf>=3.15.1"]),
        ],
        drf=[
            release("drf", "3.14.0", ">=3.0", ["4.1"]),
            release("drf", "3.15.1", ">=3.0", ["4.2", "5.0"]),
            release("drf", "3.16.0", ">=4.2", ["4.2", "5.2"]),
        ],
    )
    deps = {
        "django": Dependency("django", "4.2.7"),
        "wagtail": pinned("wagtail", "5.2.3"),
        "drf": pinned("drf", "3.14.0"),
    }
    report = analyse(DependencySet("test", deps), index, target="5.2")
    wagtail, drf = by_name(report, "wagtail"), by_name(report, "drf")
    assert (wagtail.status, wagtail.phase) == (Status.UPGRADE, Phase.BEFORE)
    assert "needs drf>=3.15.1, you have 3.14.0: upgrade drf first" in wagtail.notes
    assert (drf.status, drf.phase) == (Status.UPGRADE, Phase.BEFORE)


def host_index(host_new_django):
    return status_index(
        filters=[
            release("filters", "1.0", ">=3.2", ["4.2"]),
            release("filters", "2.0", ">=4.2", ["4.2", "5.2"]),
        ],
        host=[
            release("host", "1.0", ">=4.2,<5.0", ["4.2"], extra=["filters<2,>=1.0"]),
            release("host", "2.0", host_new_django, ["4.2", "5.2"], extra=["filters>=1.0"]),
        ],
    )


def host_deps():
    return DependencySet(
        "test",
        {
            "django": Dependency("django", "4.2.7"),
            "filters": pinned("filters", "1.0"),
            "host": pinned("host", "1.0"),
        },
    )


def test_upgrade_that_an_installed_package_forbids_says_so():
    """Regression: django-filter 25.1 was proposed first although wagtail 5.2 caps it <24."""
    report = analyse(host_deps(), host_index(">=4.2"), target="5.2")
    p = by_name(report, "filters")
    assert (p.target_version, p.phase) == ("2.0", Phase.BEFORE)
    assert "host 1.0 requires filters<2,>=1.0: upgrade host to 2.0 first" in p.notes


def test_upgrade_that_waits_for_a_package_going_with_django_goes_with_it():
    report = analyse(host_deps(), host_index(">=5.2"), target="5.2")
    assert by_name(report, "host").phase is Phase.WITH
    p = by_name(report, "filters")
    assert p.phase is Phase.WITH
    assert "goes with host" in p.notes


def test_upgrade_that_an_installed_package_forbids_without_a_way_out():
    index = host_index(">=4.2")
    index.packages["host"] = index.packages["host"][:1]  # host 1.0 is its latest release
    p = by_name(analyse(host_deps(), index, target="5.2"), "filters")
    assert "host 1.0 requires filters<2,>=1.0, which excludes 2.0" in p.notes


def test_health_check_has_no_phases():
    index = status_index(
        pkg=[
            release("pkg", "1.0", ">=4.0", ["4.1"]),
            release("pkg", "2.0", ">=5.2.3", ["5.2"]),
        ]
    )
    report, p = check_one(index, pinned("pkg", "1.0"), django="5.2", target="5.2")
    assert (p.status, p.target_version, p.phase) == (Status.UPGRADE, "2.0", None)
    assert "requires Django>=5.2.3, you have 5.2: update Django 5.2 first" in p.notes


def test_release_without_files_is_not_a_candidate():
    index = status_index(
        pkg=[
            release("pkg", "1.0", "<5.0", ["4.2"]),
            release("pkg", "1.5", ">=4.2", ["4.2", "5.2"]),
            release("pkg", "2.0", ">=4.2", ["4.2", "5.2"]),
        ]
    )
    index.no_files = {("pkg", "1.5")}
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert p.target_version == "2.0"


def test_installed_version_missing_from_the_index_is_never_ready():
    index = status_index(
        pkg_up=[
            release("pkg-up", "1.0", ">=3.2,<5.0", ["4.2"]),
            release("pkg-up", "2.0", ">=4.2", ["4.2", "5.2", "6.0"]),
        ]
    )
    _, p = check_one(index, pinned("pkg-up", "1.5.dev0"))
    assert p.status is Status.CHECK
    assert "installed version 1.5.dev0 not found on the index" in p.notes


RANGED = [
    release("ranged", "1.0", ">=3.2,<5.0", ["4.2"]),
    release("ranged", "2.0", ">=4.2", ["4.2", "5.2"]),
    release("ranged", "3.0", ">=6.0", ["6.0"]),
]


@pytest.mark.parametrize(
    ("spec", "status", "target_version", "note"),
    [
        ("<3", Status.READY, None, None),
        (">=1.0", Status.CHECK, "2.0", "latest 3.0 requires Django>=6.0"),
        ("<2", Status.UPGRADE, "2.0", "outside your requirement <2"),
        ("", Status.CHECK, "2.0", None),
    ],
)
def test_ranged_dependency_is_judged_by_the_releases_it_allows(spec, status, target_version, note):
    _, p = check_one(status_index(ranged=RANGED), Dependency("ranged", None, spec))
    assert (p.status, p.target_version) == (status, target_version)
    assert "version not pinned, add a lockfile for exact results" in p.notes
    if note:
        assert note in p.notes


def test_ranged_dependency_blocked_when_nothing_allows_the_target():
    index = status_index(
        ranged=[release("ranged", "1.0", "<5.0"), release("ranged", "2.0", "<5.1")]
    )
    _, p = check_one(index, Dependency("ranged", None, ">=1"))
    assert p.status is Status.BLOCKED


def test_wagtail_packages_are_django_related():
    index = status_index(
        wagtail_thing=[release("wagtail-thing", "1.0", extra=["wagtail>=6.3"], classifiers=[])]
    )
    _, p = check_one(index, pinned("wagtail-thing", "1.0"))
    assert p.status is Status.CHECK
    assert "Wagtail package: also check it against your Wagtail version" in p.notes


def test_inactive_packages_are_flagged():
    r = release("old", "1.0", ">=3.2", ["4.2", "5.2"])
    r["classifiers"].append("Development Status :: 7 - Inactive")
    _, p = check_one(status_index(old=[r]), pinned("old", "1.0"))
    assert "marked inactive by its maintainers" in p.notes


def test_external_dependencies_are_listed_and_never_looked_up(index):
    deps = DependencySet(
        "test",
        {
            "django": Dependency("django", "4.2.7"),
            "our-app": Dependency("our-app", "1.0", external="git https://git.example.com/app"),
        },
    )
    report = analyse(deps, index)
    assert report.external == [("our-app", "git https://git.example.com/app")]
    assert not any("our-app" in url for url in index.requests)
    assert report.missing == []


# --- targets ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target", "resolved"),
    [
        ("4.2.7", "auto", "5.2"),  # the newest LTS above the current Django
        ("5.2.3", "auto", "6.0"),  # no LTS above: the newest release
        ("6.0", "auto", "6.0"),  # already on the newest: a health check
        (None, "auto", "5.2"),
        ("6.0", "lts", "6.0"),  # the newest LTS is older: a health check, not a downgrade
        ("6.0", "latest", "6.0"),
        ("3.2.25", "auto", "5.2"),
        ("4.2.7", "latest", "6.0"),
        ("4.2.7", "5.1", "5.1"),
        ("4.2.7", "6.1", "6.1"),  # the next feature version, not released yet
    ],
)
def test_resolve_target(index, current, target, resolved):
    report = analyse(deps(django=current) if current else deps(), index, target=target)
    assert report.target == resolved


@pytest.mark.parametrize("target", ["52", "5.3", "6.2", "banana"])
def test_targets_that_do_not_exist_are_rejected(index, target):
    with pytest.raises(ValueError, match="Django"):
        analyse(deps(django="4.2.7"), index, target=target)


def test_target_warnings(index):
    health = analyse(deps(django="6.0"), index)
    assert health.warnings == []
    assert health.notices == ["Django 6.0 is already the newest release"]
    assert health.health_check
    lts = analyse(deps(django="6.0"), index, target="lts")
    assert lts.notices == ["Django 5.2, the newest LTS, is older than your Django 6.0"]
    assert analyse(deps(django="6.0.1"), index, target="6.0").notices == [
        "You are already on Django 6.0.1"
    ]
    upcoming = analyse(deps(django="6.0"), index, target="6.1")
    assert not upcoming.target_released
    assert (
        "Django 6.1 is not released yet: only classifiers count, upper bounds are ignored"
        in upcoming.warnings
    )


def test_project_python_below_the_target_minimum(index):
    ds = DependencySet(
        "test", {"django": Dependency("django", "4.2.7")}, python="3.9", python_source="--python"
    )
    report = analyse(ds, index)
    assert report.project_python == "3.9"
    assert report.warnings == [
        "Django 5.2 needs Python >=3.10, your project uses 3.9 (from --python)"
    ]


# --- the PyPI cache keeps release dates -----------------------------------------


def test_warm_cache_dates_releases_like_a_cold_one(tmp_path):
    files = [
        {"upload_time_iso_8601": "2025-05-01T00:00:00Z", "yanked": False},
        {"upload_time_iso_8601": "2025-03-01T00:00:00Z", "yanked": False},
    ]
    data = {"info": _info(release("pkg", "1.0")), "releases": {"1.0": files, "0.9": []}}

    class Once(PyPI):
        def _fetch(self, url):
            return data

    cold = Once("https://pypi.test/pypi", cache_dir=tmp_path).project("pkg")
    warm = PyPI("https://pypi.test/pypi", cache_dir=tmp_path).project("pkg")
    assert cold == warm
    assert warm.releases[1].uploaded == datetime(2025, 3, 1, tzinfo=timezone.utc)
    assert [str(r.version) for r in warm.stable_releases()] == ["1.0"]  # 0.9 has no files


# --- golden tests: real PyPI metadata, recorded by fixtures/record.py -----------


def golden(recorded, target, django, *packages, python=None):
    ds = {"django": Dependency("django", django)}
    for p in packages:
        ds[p.name] = p
    return analyse(DependencySet("golden", ds, python=python), recorded, target=target)


def test_golden_allauth_smallest_step_is_found(recorded):
    """0.55.0 is the first allauth to declare 4.2, and it still declares 3.2."""
    report = golden(recorded, "4.2", "3.2.25", pinned("django-allauth", "0.44.0"))
    p = by_name(report, "django-allauth")
    assert (p.status, p.target_version, p.phase) == (Status.UPGRADE, "0.55.0", Phase.BEFORE)


@pytest.mark.parametrize("installed", ["5.2.8", "6.3"])
def test_golden_wagtail_early_upper_bound_is_not_support(recorded, installed):
    """Wagtail 6.0–6.4 all allow Django<6.0; only 6.3.4 (after 5.2's release) added 5.2."""
    report = golden(recorded, "5.2", "4.2.20", pinned("wagtail", installed))
    p = by_name(report, "wagtail")
    assert (p.status, p.target_version, p.phase) == (Status.UPGRADE, "6.3.4", Phase.BEFORE)


def test_golden_prometheus_on_the_newest_django_is_not_blocked(recorded):
    report = golden(recorded, "auto", "6.1.1", pinned("django-prometheus", "2.4.0"))
    assert report.target == "6.1"
    assert report.warnings == []
    assert report.notices == ["Django 6.1 is already the newest release"]
    p = by_name(report, "django-prometheus")
    assert p.status is Status.CHECK
    assert "newer releases exclude Django 6.1" in p.notes
    # 2.5.0 caps Django below 6.0, the development release for 2.6 declares 6.1.
    assert "2.6.0.dev22 declares Django 6.1 (pre-release)" in p.notes
    assert p.prerelease.version == "2.6.0.dev22"


@pytest.mark.parametrize("target", ["6.0", "6.1"])
@pytest.mark.parametrize("python", [None, "3.11", "3.13"])
def test_golden_ses_markers_follow_the_python_of_the_target(recorded, target, python):
    report = golden(recorded, target, "5.2.17", pinned("django-ses", "4.8.0"), python=python)
    assert by_name(report, "django-ses").status is Status.READY


def test_golden_ses_is_likely_for_an_unreleased_target(recorded):
    report = golden(recorded, "6.2", "6.1.1", pinned("django-ses", "4.8.0"))
    p = by_name(report, "django-ses")
    assert (p.status, p.reason) == (Status.CHECK, "allows Django<7,>=3.2, 6.2 is not released yet")


@pytest.mark.parametrize(
    ("package", "version", "django"),
    [("mayan-edms", "4.12.2", "5.2.17"), ("netbox", "4.7.1", "6.1.1")],
)
def test_golden_exact_pins_declare_their_series(recorded, package, version, django):
    report = golden(recorded, django[:3], django, pinned(package, version))
    p = by_name(report, package)
    assert (p.status, p.reason) == (Status.READY, f"allows Django=={django}")


def test_golden_major_only_classifier(recorded):
    report = golden(recorded, "6.1", "5.2.17", pinned("dj-database-url", "3.1.2"))
    p = by_name(report, "dj-database-url")
    assert (p.status, p.reason) == (Status.CHECK, "declares Django 6, not 6.1")


def test_golden_ranged_dependency_stays_inside_its_range(recorded):
    report = golden(recorded, "4.2", "3.2.25", Dependency("djangorestframework", None, ">=3.12"))
    p = by_name(report, "djangorestframework")
    assert (p.status, p.target_version) == (Status.CHECK, "3.17.2")
    assert "latest 3.18.1 requires Django>=5.2" in p.notes


def test_golden_wagtail_only_package(recorded):
    report = golden(recorded, "5.2", "4.2.20", pinned("wagtail-grapple", "0.31.0"))
    p = by_name(report, "wagtail-grapple")
    assert p.status is Status.CHECK
    assert "Wagtail package: also check it against your Wagtail version" in p.notes


def test_lts_on_a_newer_django_reports_no_false_blockers(index):
    """Bug #4: -t lts on Django 6.x used to check a downgrade and fail --fail-on blocked."""
    index.packages["django-new"] = [
        release("django-new", "2.0", ">=4.2", ["5.2"]),
        release("django-new", "3.0", ">=6.0", ["6.0"]),
    ]
    report = analyse(deps(django="6.0", django_new="3.0"), index, target="lts")
    assert report.target == "6.0"
    assert by_name(report, "django-new").status is Status.READY
    assert report.counts[Status.BLOCKED] == 0


@pytest.mark.parametrize("target", ["5.2", "4.2"])
def test_explicit_downgrade_is_an_error(index, target):
    with pytest.raises(ValueError, match=f"Django {target} is older than your Django 6.0"):
        analyse(deps(django="6.0.1"), index, target=target)


def test_skipped_lts_is_pointed_out(index):
    index.packages["django"] = [release("Django", "3.2", uploaded="2021-04-06"), *DJANGO]
    report = analyse(deps(django="3.2"), index)
    assert report.target == "5.2"
    assert (
        "This skips Django 4.2 LTS. Upgrading one LTS at a time is easier: "
        "run with -t 4.2 for a smaller first step"
    ) in report.warnings
    assert not any("skips" in w for w in analyse(deps(django="3.2"), index, "4.2").warnings)


# --- a Django requirement that is a range (roadmap 5: --from) -----------------


def ranged(spec, **others):
    ds = {"django": Dependency("django", None, spec)}
    ds |= {
        name.replace("_", "-"): Dependency(name.replace("_", "-"), v) for name, v in others.items()
    }
    return DependencySet("test", ds)


def test_ranged_django_assumes_the_newest_release_it_allows(index):
    report = analyse(ranged(">=4.2,<5.0", django_before="1.0"), index)
    assert report.current_django == "4.2.7"
    assert report.target == "5.2"
    assert report.warnings[0] == (
        "Django is not pinned: assuming 4.2.7, the newest release Django>=4.2,<5.0 allows "
        "(pass --from to change)"
    )
    assert "Your requirement Django>=4.2,<5.0 excludes Django 5.2: widen it when you upgrade" in (
        report.warnings
    )
    assert by_name(report, "django-before").phase is Phase.BEFORE


def test_open_django_range_asks_for_from(index):
    report = analyse(ranged(">=4.2", django_before="1.0"), index)
    assert report.current_django is None
    assert report.warnings[0].startswith("Django is not pinned (Django>=4.2): pass --from")
    assert by_name(report, "django-before").phase is None


@pytest.mark.parametrize(("given", "used"), [("4.2", "4.2.7"), ("4.2.3", "4.2.3"), ("5.1", "5.1")])
def test_from_overrides_the_current_django(index, given, used):
    report = analyse(ranged(">=4.2", django_before="1.0"), index, current=given)
    assert report.current_django == used
    assert not any("not pinned" in w for w in report.warnings)
    assert by_name(report, "django-before").phase is Phase.BEFORE


@pytest.mark.parametrize("given", ["banana", "3.1"])
def test_from_must_be_a_django_version(index, given):
    with pytest.raises(ValueError, match="--from"):
        analyse(deps(django="4.2.7"), index, current=given)


def test_poetry_alternatives_limit_an_unpinned_dependency(index):
    """``^1.0 || ^3.0`` from Poetry: 2.x is outside, so 1.5 is the newest allowed."""
    ds = DependencySet(
        "test",
        {
            "django": Dependency("django", "4.2.7"),
            "django-before": Dependency("django-before", None, ">=1.0,<1.9 || >=3.0,<4"),
        },
    )
    p = by_name(analyse(ds, index, "5.2"), "django-before")
    assert (p.status, p.target_version) == (Status.UPGRADE, "2.0")
    assert "outside your requirement >=1.0,<1.9 || >=3.0,<4" in p.notes


# --- fewer requests for an unreleased target ----------------------------------


def test_unreleased_target_does_not_scan_for_declarations_nobody_made(index):
    index.packages["django-many"] = [
        release("django-many", f"1.{i}", ">=4.2", ["4.2", "5.0"], uploaded="2025-12-01")
        for i in range(30)
    ]
    report = analyse(deps(django="6.0", django_many="1.0"), index, target="6.1")
    assert by_name(report, "django-many").status is Status.CHECK
    per_release = [u for u in index.requests if "/django-many/" in u and u.count("/") > 5]
    assert len(per_release) <= 2  # the installed release, not every newer one


def test_skipped_lts_hint_names_the_lowest_skipped_lts(index):
    """Regression: -t latest from 3.2 skipped 4.2 and 5.2 and suggested -t 5.2."""
    index.packages["django"] = [release("Django", "3.2", uploaded="2021-04-06"), *DJANGO]
    report = analyse(deps(django="3.2"), index, "latest")
    assert (
        "This skips Django 4.2, 5.2 LTS. Upgrading one LTS at a time is easier: "
        "run with -t 4.2 for a smaller first step"
    ) in report.warnings


# --- --from against what the project says --------------------------------------


def test_from_that_contradicts_the_pin_is_pointed_out(index):
    report = analyse(deps(django="4.2.7"), index, current="5.0")
    assert report.current_django == "5.0"
    assert "--from 5.0: using Django 5.0, but your project pins 4.2.7" in report.warnings


def test_from_in_the_pinned_series_keeps_the_exact_pin(index):
    """--from 4.2 on a 4.2.3 pin must not hide the "update Django 4.2 first" note."""
    index.packages["django-floor"] = [
        release("django-floor", "1.0", "<5.0", ["4.2"]),
        release("django-floor", "2.0", ">=4.2.7", ["4.2", "5.2"]),
    ]
    report = analyse(deps(django="4.2.3", django_floor="1.0"), index, "5.2", current="4.2")
    assert report.current_django == "4.2.3"
    assert not any("--from" in w for w in report.warnings)
    p = by_name(report, "django-floor")
    assert "requires Django>=4.2.7, you have 4.2.3: update Django 4.2 first" in p.notes


def test_from_outside_the_django_range_is_pointed_out(index):
    report = analyse(ranged("<5.0", django_before="1.0"), index, "latest", current="6.0")
    assert "--from 6.0: Django 6.0 is outside your requirement Django<5.0" in report.warnings


# --- an index that is not a full PyPI mirror --------------------------------------


@pytest.mark.parametrize("target", ["5.2", "lts"])
def test_index_without_any_lts_is_a_clear_error(target):
    index = status_index()
    index.packages["django"] = [release("Django", "1.0", uploaded="2008-09-03")]
    with pytest.raises(ValueError, match="no Django LTS release|There is no Django 5.2") as info:
        analyse(deps(django="1.0"), index, target)
    assert "max()" not in str(info.value)
    assert analyse(deps(django="1.0"), index, "auto").target == "1.0"  # a health check


# --- the search for a release that does not exclude the target -------------------


def per_release_requests(index, name):
    return [u for u in index.requests if f"/{name}/" in u and u.count("/") > 5]


def test_search_for_a_release_that_allows_the_target_follows_the_bounds():
    """60 releases capped below 5.2, then 40 that allow it: a bisection, not 100 requests."""
    releases = [release("pkg", f"1.{i}", "<5.0", uploaded="2020-01-01") for i in range(60)]
    releases += [release("pkg", f"2.{i}", ">=4.2", uploaded="2020-01-01") for i in range(40)]
    index = status_index(pkg=releases)
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert (p.status, p.target_version) == (Status.CHECK, "2.0")
    assert len(per_release_requests(index, "pkg")) <= 10


def test_search_finds_a_window_between_older_and_newer_bounds():
    releases = [release("pkg", f"1.{i}", "<5.0", uploaded="2020-01-01") for i in range(30)]
    releases += [release("pkg", "2.0", ">=4.0", uploaded="2020-01-01")]
    releases += [release("pkg", f"3.{i}", ">=6.0", uploaded="2020-01-01") for i in range(30)]
    index = status_index(pkg=releases)
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert (p.status, p.target_version) == (Status.CHECK, "2.0")
    assert len(per_release_requests(index, "pkg")) <= 10


def test_blocked_package_with_a_long_history_costs_few_requests(index):
    """Regression: wagtail on -t 6.2 fetched all 50 newer releases, each capped below it."""
    releases = [release("pkg", f"1.{i}", "<5.0", uploaded="2020-01-01") for i in range(50)]
    index = status_index(pkg=releases)
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert p.status is Status.BLOCKED
    assert len(per_release_requests(index, "pkg")) <= 10


def test_unpinned_search_stays_as_high_as_possible():
    """Newest allowed first: 3.x exclude 5.2 by a lower bound, 2.x allow it."""
    releases = [release("pkg", f"2.{i}", ">=4.0", uploaded="2020-01-01") for i in range(20)]
    releases += [release("pkg", f"3.{i}", ">=6.0", uploaded="2020-01-01") for i in range(20)]
    _, p = check_one(status_index(pkg=releases), Dependency("pkg", None, ">=2.0"))
    assert (p.status, p.target_version) == (Status.CHECK, "2.19")


def test_search_for_the_first_declaring_release_bisects():
    """100 releases within a year of 5.2, the classifier arrives at 1.60: no linear scan."""
    releases = [release("pkg", f"1.{i}", ">=4.2", ["4.2"], "2025-01-01") for i in range(60)]
    releases += [
        release("pkg", f"1.{i}", ">=4.2", ["4.2", "5.2"], "2025-05-01") for i in range(60, 100)
    ]
    index = status_index(pkg=releases)
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert (p.status, p.target_version, p.phase) == (Status.UPGRADE, "1.60", Phase.BEFORE)
    assert len(per_release_requests(index, "pkg")) <= 20


def test_first_declaring_release_survives_a_dropped_classifier():
    """A release that lost the classifier and got it back must not hide the earlier one."""
    releases = [release("pkg", f"1.{i}", ">=4.2", ["4.2"], "2025-01-01") for i in range(20)]
    releases += [release("pkg", "1.20", ">=4.2", ["4.2", "5.2"], "2025-05-01")]
    releases += [release("pkg", "1.21", ">=4.2", ["4.2"], "2025-05-02")]  # classifier dropped
    releases += [
        release("pkg", f"1.{i}", ">=4.2", ["4.2", "5.2"], "2025-06-01") for i in range(22, 40)
    ]
    _, p = check_one(status_index(pkg=releases), pinned("pkg", "1.0"))
    assert p.target_version == "1.20"


# --- fitting the upgrades to each other -----------------------------------------


def plan_index(**extra):
    return status_index(
        pkg_a=[
            release("pkg-a", "1.0", ">=3.2", ["4.2"]),
            release("pkg-a", "2.0", ">=4.2", ["4.2", "5.2"], extra=extra.get("a", [])),
        ],
        pkg_b=[
            release("pkg-b", "1.0", ">=3.2", ["4.2"]),
            release("pkg-b", "2.0", ">=4.2", ["4.2", "5.2"], extra=extra.get("b", [])),
        ],
    )


def plan(index, **pins):
    deps_ = {"django": Dependency("django", "4.2.7")}
    deps_.update({n.replace("_", "-"): pinned(n.replace("_", "-"), v) for n, v in pins.items()})
    return analyse(DependencySet("test", deps_), index, target="5.2")


def test_upgrade_first_is_in_the_order_the_notes_require():
    report = plan(plan_index(a=["pkg-b>=2.0"]), pkg_a="1.0", pkg_b="1.0")
    assert [p.name for p in report.by_status(Status.UPGRADE)] == ["pkg-b", "pkg-a"]


def test_upgrades_that_need_each_other_go_together():
    report = plan(plan_index(a=["pkg-b>=2.0"], b=["pkg-a>=2.0"]), pkg_a="1.0", pkg_b="1.0")
    a, b = by_name(report, "pkg-a"), by_name(report, "pkg-b")
    assert "upgrade together with pkg-b" in a.notes
    assert "upgrade together with pkg-a" in b.notes
    assert not any(n.endswith(" first") for n in a.notes + b.notes)


def test_upgrade_that_an_installed_package_forbids_needs_a_check():
    index = plan_index()
    index.packages["keeper"] = [release("keeper", "1.0", ">=3.2", ["4.2", "5.2"], ["pkg-a<2"])]
    p = by_name(plan(index, pkg_a="1.0", keeper="1.0"), "pkg-a")
    assert (p.status, p.target_version, p.phase) == (Status.CHECK, "2.0", None)
    assert "keeper 1.0 requires pkg-a<2, which excludes 2.0" in p.notes


# --- broken or stale indexes ------------------------------------------------------


def test_metadata_with_non_strings_is_ignored_not_a_crash():
    weird = release("weird", "1.0", ">=3.2", ["4.2", "5.2"])
    weird["classifiers"].append(5)
    weird["requires_dist"].insert(0, None)
    _, p = check_one(status_index(weird=[weird]), pinned("weird", "1.0"))
    assert p.status is Status.READY


def test_index_that_does_not_know_the_projects_django_series_is_an_error():
    index = status_index()
    index.packages["django"] = [release("Django", "4.1", uploaded="2022-08-03")]
    with pytest.raises(ValueError, match="knows no Django newer than 4.1"):
        analyse(deps(django="4.2.7"), index)


# --- a busy or flaky index ------------------------------------------------------------


def test_unrelated_pinned_package_costs_one_small_request():
    """Only the installed release's metadata, never the whole release history (botocore)."""
    index = status_index(
        s3transfer=[release("s3transfer", "0.10.0"), release("s3transfer", "0.11.0")]
    )
    report = analyse(deps(django="4.2.7", s3transfer="0.10.0"), index, target="5.2")
    assert report.skipped == 1
    asked = [u for u in index.requests if "/s3transfer/" in u]
    assert asked == ["https://pypi.test/pypi/s3transfer/0.10.0/json"]


class Flaky:
    """An index that fails for one package, like PyPI's "503 Backend is unhealthy"."""

    def __init__(self, index, broken):
        self.index, self.broken = index, broken

    def __getattr__(self, name):
        return getattr(self.index, name)

    def project(self, name):
        if name == self.broken:
            raise PyPIError(
                f"could not fetch {name}: HTTP 503 Backend is unhealthy (tried 4 times)"
            )
        return self.index.project(name)

    def release(self, name, version):
        if name == self.broken:
            raise PyPIError(
                f"could not fetch {name}: HTTP 503 Backend is unhealthy (tried 4 times)"
            )
        return self.index.release(name, version)


def test_one_package_the_index_cannot_answer_for_does_not_stop_the_report(index):
    report = analyse(
        deps(django="4.2.7", django_ready="1.0", django_before="1.0"),
        Flaky(index, "django-before"),
        target="5.2",
    )
    assert report.failed == ["django-before"]
    assert [p.name for p in report.packages] == ["django-ready"]
    assert any(
        w.startswith("Could not check django-before, run again later") for w in report.warnings
    )


# --- packages not from PyPI, judged by what they declare themselves -----------


def fork(name, django=None, classifiers=(), version="1.0", extra=()):
    meta = ReleaseInfo(
        name,
        version,
        tuple(f"Framework :: Django :: {c}" for c in classifiers),
        (*([f"Django{django}"] if django else []), *extra),
        None,
    )
    where = f"git https://git.example.com/{name}"
    return Dependency(name, version, external=where, metadata=meta)


def test_forks_are_judged_by_their_own_metadata_and_never_looked_up(index):
    ds = DependencySet(
        "test",
        {
            "django": Dependency("django", "4.2.7"),
            "old-fork": fork("old-fork", ">=3.2,<4.1", ["4.0"]),
            "good-fork": fork("good-fork", ">=4.2", ["4.2", "5.2"]),
            "vague-fork": fork("vague-fork", ">=3.2", ["4.2"]),
            "tool-fork": fork("tool-fork", extra=("requests>=2",)),
        },
    )
    report = analyse(ds, index)
    assert (by_name(report, "old-fork").status, by_name(report, "old-fork").reason) == (
        Status.BLOCKED,
        "requires Django<4.1,>=3.2",
    )
    assert by_name(report, "good-fork").status is Status.READY
    assert by_name(report, "vague-fork").status is Status.CHECK
    old = by_name(report, "old-fork")
    assert old.source == "git https://git.example.com/old-fork"
    assert old.notes == []  # where it comes from is in `source`
    assert old.target_version is None and old.phase is None
    # Not Django-related: still listed as not checked, like any package not from PyPI.
    assert report.external == [("tool-fork", "git https://git.example.com/tool-fork")]
    assert not any("fork" in url for url in index.requests)


def test_blocked_hint_for_forks_only(index):
    from django_upgrade_report.render import sections

    ds = DependencySet(
        "test",
        {"django": Dependency("django", "4.2.7"), "old-fork": fork("old-fork", "<4.1")},
    )
    blocked = sections(analyse(ds, index))[0]
    assert blocked.hint == (
        "Your copy excludes Django 5.2. Fix its requirement, or go back to a release on PyPI."
    )


def test_a_forks_version_comes_from_its_metadata_when_not_pinned(index):
    dep = fork("old-fork", "<4.1", version="3.0+ours")
    ds = DependencySet(
        "test",
        {
            "django": Dependency("django", "4.2.7"),
            "old-fork": Dependency("old-fork", None, external=dep.external, metadata=dep.metadata),
        },
    )
    assert by_name(analyse(ds, index), "old-fork").current == "3.0+ours"


def test_a_forks_requirements_count_against_planned_upgrades(index):
    ds = DependencySet(
        "test",
        {
            "django": Dependency("django", "4.2.7"),
            "django-before": Dependency("django-before", "1.0"),
            "our-fork": fork("our-fork", ">=4.2", ["4.2", "5.2"], extra=("django-before<2",)),
        },
    )
    p = by_name(analyse(ds, index), "django-before")
    assert p.status is Status.CHECK
    assert "our-fork 1.0 requires django-before<2, which excludes 2.0" in p.notes


def test_an_upper_bound_of_unknown_age_is_not_dated():
    support = supports(info(">=4.2,<6.0"), T52_RELEASED, None)
    assert (support.verdict, support.reason) == (
        Verdict.LIKELY,
        "allows Django<6.0,>=4.2, may predate 5.2",
    )


# --- packages Django took over ---------------------------------------------------


def test_a_package_django_took_over_says_so_even_without_django_metadata(index):
    index.packages["south"] = [
        release("South", "0.8.4", classifiers=[]),
        release("South", "1.0.2", classifiers=[]),
    ]
    index.packages["django-jsonfield"] = [release("django-jsonfield", "1.4.1", ">=1.8", ["2.2"])]
    report = analyse(deps(django="4.2.7", south="0.8.4", django_jsonfield="1.4.1"), index)
    south = by_name(report, "south")
    assert south.notes == ["built into Django 1.7: its own migrations, remove South"]
    assert south.successor is not None and str(south.successor.since) == "1.7"
    assert by_name(report, "django-jsonfield").notes[0] == (
        "built into Django 3.1: models.JSONField"
    )


def test_a_successor_only_counts_from_the_django_that_has_it(index):
    from django_upgrade_report.successors import successor

    assert successor("django-template-partials", Version("5.2")) is None
    assert successor("django-template-partials", Version("6.0")) is not None
    assert successor("django-debug-toolbar", Version("6.0")) is None


def test_severity_orders_the_statuses():
    """Reports list the most work first, and --fail-on fails on a status and everything worse."""
    from django_upgrade_report.analysis import SEVERITY

    worst_first = sorted(Status, key=lambda s: -SEVERITY[s])
    assert worst_first == [Status.BLOCKED, Status.UPGRADE, Status.CHECK, Status.READY]


# --- pre-releases ---------------------------------------------------------------


def test_prerelease_that_declares_the_target_is_noted():
    index = status_index(
        pkg=[
            release("pkg", "1.0", ">=3.2", ["4.1", "4.2"]),
            release("pkg", "2.0rc1", ">=4.2", ["4.2", "5.2"], uploaded="2026-03-02"),
        ]
    )
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert p.status is Status.CHECK  # a pre-release is not a release
    assert p.notes[-1] == "2.0rc1 declares Django 5.2 (pre-release)"
    assert (p.prerelease.version, p.prerelease.reason) == ("2.0rc1", "declares Django 5.2")
    assert p.prerelease.uploaded == datetime(2026, 3, 2, tzinfo=timezone.utc)


def test_prerelease_that_lifts_a_block_is_noted():
    index = status_index(
        pkg=[
            release("pkg", "1.0", ">=3.2,<5.0", ["4.2"], uploaded="2023-01-01"),
            release("pkg", "2.0b1", ">=4.2", ["4.2"]),  # allows 5.2, declares nothing
        ]
    )
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert p.status is Status.BLOCKED
    assert p.notes[-1] == "2.0b1 no longer excludes Django 5.2 (pre-release)"


@pytest.mark.parametrize(
    ("releases", "why"),
    [
        (
            [
                release("pkg", "0.9b1", ">=4.2", ["5.2"]),
                release("pkg", "1.0", ">=3.2", ["4.2"]),
            ],
            "older than the newest stable release",
        ),
        (
            [release("pkg", "1.0", ">=3.2", ["4.2"]), release("pkg", "2.0a1", ">=4.2", ["4.2"])],
            "says no more than the stable one",
        ),
        (
            [
                release("pkg", "1.0", ">=3.2", ["4.2"]),
                release("pkg", "2.0rc1", ">=4.2", ["5.2"], yanked=True),
            ],
            "yanked",
        ),
    ],
)
def test_prereleases_that_say_nothing_new_are_left_out(releases, why):
    _, p = check_one(status_index(pkg=releases), pinned("pkg", "1.0"))
    assert p.prerelease is None, why
    assert not any("pre-release" in n for n in p.notes)


def test_prerelease_without_files_is_left_out():
    index = status_index(
        pkg=[release("pkg", "1.0", ">=3.2", ["4.2"]), release("pkg", "2.0rc1", ">=4.2", ["5.2"])]
    )
    index.no_files.add(("pkg", "2.0rc1"))
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert p.prerelease is None


def test_only_the_newest_prerelease_is_fetched():
    index = status_index(
        pkg=[
            release("pkg", "1.0", ">=3.2", ["4.2"]),
            release("pkg", "2.0a1", ">=4.2", ["5.2"]),
            release("pkg", "2.0b1", ">=4.2", ["5.2"]),
            release("pkg", "2.0rc1", ">=4.2", ["5.2"]),
        ]
    )
    check_one(index, pinned("pkg", "1.0"))
    fetched = [u for u in per_release_requests(index, "pkg") if "rc" in u or "a1" in u or "b1" in u]
    assert fetched == ["https://pypi.test/pypi/pkg/2.0rc1/json"]


def test_ready_and_upgrade_fetch_no_prerelease():
    index = status_index(
        pkg=[
            release("pkg", "1.0", ">=3.2", ["4.2"]),
            release("pkg", "2.0", ">=4.2", ["4.2", "5.2"]),
            release("pkg", "3.0rc1", ">=4.2", ["5.2"]),
        ]
    )
    _, p = check_one(index, pinned("pkg", "1.0"))
    assert p.status is Status.UPGRADE
    assert not any("3.0rc1" in u for u in index.requests)


def test_prerelease_the_index_cannot_answer_for_keeps_the_verdict():
    index = status_index(
        pkg=[release("pkg", "1.0", ">=3.2", ["4.2"]), release("pkg", "2.0rc1", ">=4.2", ["5.2"])]
    )
    fetch = index._fetch

    def flaky(url):
        if "2.0rc1" in url:
            raise PyPIError("could not fetch: HTTP 503")
        return fetch(url)

    index._fetch = flaky
    report, p = check_one(index, pinned("pkg", "1.0"))
    assert (p.status, p.prerelease, report.failed) == (Status.CHECK, None, [])
    assert p.notes[-1] == "could not check 2.0rc1, run again later"


def test_no_prerelease_hint_when_a_stable_release_declares_the_target():
    """An unpinned range whose newest allowed release lags, but an older allowed one declares."""
    index = status_index(
        pkg=[
            release("pkg", "2.5", ">=4.2", ["4.2", "5.2"]),
            release("pkg", "2.9", ">=4.2", ["4.2"]),
            release("pkg", "3.0rc1", ">=4.2", ["5.2"]),
        ]
    )
    _, p = check_one(index, Dependency("pkg", None, spec=">=2,<3"))
    assert (p.status, p.target_version) == (Status.CHECK, "2.5")
    assert p.prerelease is None


def test_project_with_only_prereleases_is_judged_by_them():
    """With no stable release the newest pre-release stands in for one: no extra hint."""
    index = status_index(
        pkg=[release("pkg", "1.0a1", ">=3.2", []), release("pkg", "1.0b2", ">=4.2", ["5.2"])]
    )
    _, p = check_one(index, pinned("pkg", "1.0a1"))
    assert (p.status, p.target_version, p.prerelease) == (Status.UPGRADE, "1.0b2", None)
