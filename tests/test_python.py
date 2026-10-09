"""Whether a release runs on a Python: Requires-Python, classifiers and wheels."""

from __future__ import annotations

import pytest
from packaging.version import Version

from django_upgrade_report.analysis import Verdict
from django_upgrade_report.pypi import ReleaseInfo
from django_upgrade_report.python import PURE, PythonRule, _wheel_fits, python_supports

LINUX = "manylinux_2_17_x86_64"
PY = "Programming Language :: Python"


@pytest.mark.parametrize(
    ("kwargs", "verdict", "reason"),
    [
        ({"requires_python": ">=3.8,<3.12"}, Verdict.NO, "requires Python >=3.8,<3.12"),
        (
            {
                "requires_python": ">=3.8",
                "classifiers": ("Programming Language :: Python :: 3.12",),
            },
            Verdict.YES,
            "declares Python 3.12",
        ),
        ({"wheel_tags": (f"cp312-cp312-{LINUX}",)}, Verdict.YES, "has a wheel for Python 3.12"),
        (
            {"wheel_tags": (f"cp38-abi3-{LINUX}",)},
            Verdict.LIKELY,
            "has a wheel for Python 3.12 and newer (abi3)",
        ),
        ({"wheel_tags": ("py3-none-any",)}, Verdict.LIKELY, PURE),
        ({"wheel_tags": ("py2.py3-none-any",)}, Verdict.LIKELY, PURE),
        (
            {"wheel_tags": (f"cp310-cp310-{LINUX}", f"cp311-cp311-{LINUX}"), "has_sdist": True},
            Verdict.LIKELY,
            "no wheel for Python 3.12, pip builds it from source",
        ),
        (
            {"wheel_tags": (f"cp311-cp311-{LINUX}",), "has_sdist": False},
            Verdict.NO,
            "no wheel for Python 3.12 and no source to build",
        ),
        ({"has_sdist": True}, Verdict.UNKNOWN, "declares nothing about Python 3.12"),
        ({"requires_python": "not a spec"}, Verdict.UNKNOWN, "declares nothing about Python 3.12"),
        # The requirement wins over a classifier, as for Django.
        (
            {
                "requires_python": "<3.12",
                "classifiers": ("Programming Language :: Python :: 3.12",),
            },
            Verdict.NO,
            "requires Python <3.12",
        ),
    ],
)
def test_python_supports(kwargs, verdict, reason):
    kwargs.setdefault("requires_python", None)
    support = python_supports("3.12", **kwargs)
    assert (support.verdict, support.reason) == (verdict, reason)


@pytest.mark.parametrize(
    ("tag", "fits"),
    [
        (f"cp312-cp312-{LINUX}", "exact"),
        ("cp312-cp312-musllinux_1_1_x86_64", "exact"),
        ("cp312-cp312-linux_x86_64", "exact"),
        ("cp312-cp312-manylinux_2_17_aarch64", None),
        ("cp312-cp312-win_amd64", None),
        ("cp312-cp312-macosx_11_0_arm64", None),
        (f"cp313-cp313-{LINUX}", None),
        (f"cp313t-cp313t-{LINUX}", None),
        (f"cp36-abi3-{LINUX}", "abi3"),
        (f"cp313-abi3-{LINUX}", None),
        ("py3-none-any", "pure"),
        ("py311-none-any", "pure"),
        ("py313-none-any", None),
        ("py2-none-any", None),
        (f"pp39-pypy39_pp73-{LINUX}", None),
        ("nonsense", None),
    ],
)
def test_wheel_fits(tag, fits):
    assert _wheel_fits(tag, Version("3.12")) == fits


def info(requires_python=None, wheel_tags=(), has_sdist=None):
    return ReleaseInfo(
        "pkg", "1.0", (), (), requires_python, wheel_tags=wheel_tags, has_sdist=has_sdist
    )


def test_python_rule_judges_and_steers_the_search():
    rule = PythonRule("3.12")
    assert rule.judge(info(wheel_tags=(f"cp312-cp312-{LINUX}",)), None).verdict is Verdict.YES
    assert rule.side(info(">=3.8,<3.12")) == 1  # newer releases may allow it
    assert rule.side(info(">=3.13")) == -1  # older ones may
    assert rule.side(info(">=3.8")) == 0
    assert rule.side(info("!=3.12.*")) == 0
    assert rule.side(info()) == 0


# --- the plan for a newer Python ---------------------------------------------------


def wheel(name, version, python):
    return f"{name}-{version}-cp{python}-cp{python}-{LINUX}.whl"


def py_index():
    """Django 4.2.7 does not declare Python 3.12, 4.2.8 does."""
    from conftest import FakePyPI, release

    django = [
        release("Django", "4.2.7", uploaded="2023-11-01"),
        release("Django", "4.2.8", uploaded="2023-12-04"),
        release("Django", "4.2.9", uploaded="2024-01-02"),
        release("Django", "5.2", uploaded="2025-04-02"),
    ]
    for r in django[1:]:
        r["classifiers"] = [*r["classifiers"], f"{PY} :: 3.12"]
    return FakePyPI(
        {
            "django": django,
            # Only wheels up to 3.11 in 1.0, a 3.12 wheel from 2.0 on.
            "numpylike": [
                release(
                    "numpylike",
                    "1.0",
                    requires_python=">=3.8",
                    files=[wheel("numpylike", "1.0", 311), "numpylike-1.0.tar.gz"],
                ),
                release(
                    "numpylike",
                    "2.0",
                    requires_python=">=3.9",
                    files=[wheel("numpylike", "2.0", 312)],
                ),
            ],
            # Every release caps Python below 3.12.
            "oldlib": [
                release("oldlib", "0.4", requires_python="<3.11", files=["oldlib-0.4.tar.gz"]),
            ],
            # 3.0 runs on 3.12 but no longer on 3.10, the project's Python.
            "jumpy": [
                release("jumpy", "1.0", requires_python=">=3.8,<3.12", files=["jumpy-1.0.tar.gz"]),
                release(
                    "jumpy", "3.0", requires_python=">=3.11", files=["jumpy-3.0-py3-none-any.whl"]
                ),
            ],
            "purelib": [
                release("purelib", "1.0", requires_python=">=3.8", files=["p-1.0-py3-none-any.whl"])
            ],
            "silentlib": [release("silentlib", "1.0", requires_python=None, files=["s.tar.gz"])],
        }
    )


def py_plan(index, python="3.10", target="3.12", **pins):
    from django_upgrade_report.analysis import Report
    from django_upgrade_report.python import plan_python
    from django_upgrade_report.sources import Dependency, DependencySet

    deps = DependencySet(
        "test",
        {"django": Dependency("django", "4.2.7")}
        | {name: Dependency(name, v) for name, v in pins.items()},
        python=python,
    )
    report = Report("5.2", "4.2.7", ">=3.12", "test", [], 0, [], project_python=python)
    return plan_python(target, report, deps, index)


def test_plan_upgrades_blocks_and_counts():
    from django_upgrade_report.analysis import Status

    index = py_index()
    plan = py_plan(
        index, numpylike="1.0", oldlib="0.4", jumpy="1.0", purelib="1.0", silentlib="1.0"
    )
    rows = {p.name: p for p in plan.packages}
    assert [p.name for p in plan.packages] == ["oldlib", "jumpy", "numpylike"]  # worst first
    assert (rows["oldlib"].status, rows["oldlib"].reason) == (
        Status.BLOCKED,
        "0.4 requires Python <3.11",
    )
    assert (rows["numpylike"].status, rows["numpylike"].target_version) == (Status.UPGRADE, "2.0")
    assert rows["numpylike"].reason == "1.0 no wheel for Python 3.12, pip builds it from source"
    assert rows["jumpy"].notes == ["goes together with the switch to Python 3.12"]
    assert (plan.ready, plan.pure, plan.silent) == (0, 1, ["silentlib"])
    assert plan.django_note == (
        "Django 4.2.7 does not declare Python 3.12, 4.2.8 does: update Django 4.2 first"
    )


def test_plan_reads_release_histories_only_where_needed():
    index = py_index()
    py_plan(index, purelib="1.0", silentlib="1.0")
    histories = [u for u in index.requests if u.count("/") == 5 and "/django/" not in u]
    assert histories == []  # no project JSON for packages that need nothing


@pytest.mark.parametrize(
    ("requested", "project", "needs", "expected"),
    [
        ("auto", "3.10", ">=3.12", "3.12"),
        ("auto", "3.12", ">=3.12", None),
        ("auto", None, ">=3.12", None),
        ("none", "3.10", ">=3.12", None),
        ("3.13", "3.12", ">=3.10", "3.13"),
        ("3.13.1", "3.12", ">=3.10", "3.13"),
    ],
)
def test_python_target(requested, project, needs, expected):
    from django_upgrade_report.analysis import Report
    from django_upgrade_report.python import python_target

    report = Report("6.1", "5.2.7", needs, "test", [], 0, [], project_python=project)
    assert python_target(requested, report) == expected


def test_python_target_that_is_not_a_version():
    from django_upgrade_report.analysis import Report
    from django_upgrade_report.python import python_target

    with pytest.raises(ValueError, match="'three' is not a Python version"):
        python_target("three", Report("6.1", None, None, "test", [], 0, []))


def test_a_step_past_an_exclusion_may_build_from_source():
    from conftest import FakePyPI, release

    from django_upgrade_report.analysis import Status

    index = FakePyPI(
        {
            "django": py_index().packages["django"],
            "lib": [
                release("lib", "1.0", requires_python="<3.12", files=["lib-1.0.tar.gz"]),
                release("lib", "2.0", files=[wheel("lib", "2.0", 311), "lib-2.0.tar.gz"]),
            ],
        }
    )
    (row,) = py_plan(index, lib="1.0").packages
    assert (row.status, row.target_version) == (Status.UPGRADE, "2.0")
    assert row.notes == ["2.0 no wheel for Python 3.12, pip builds it from source"]


def test_an_unanswered_lookup_is_said_and_django_note_survives_it():
    index = py_index()
    fetch = index._fetch

    def flaky(url):
        if "/purelib/" in url or "/django/4.2.8/" in url:
            from django_upgrade_report.pypi import PyPIError

            raise PyPIError("HTTP 503")
        return fetch(url)

    index._fetch = flaky
    plan = py_plan(index, purelib="1.0")
    assert plan.unknown == ["purelib"]
    assert plan.django_note is None or "4.2" in plan.django_note
