"""Whether a release runs on a Python: Requires-Python, classifiers and wheels."""

from __future__ import annotations

import pytest
from packaging.version import Version

from django_upgrade_report.analysis import Verdict
from django_upgrade_report.pypi import ReleaseInfo
from django_upgrade_report.python import PURE, PythonRule, _wheel_fits, python_supports

LINUX = "manylinux_2_17_x86_64"


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
