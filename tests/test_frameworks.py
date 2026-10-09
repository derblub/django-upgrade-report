"""Wagtail and django CMS as what the report plans: --framework."""

from __future__ import annotations

import json

import pytest
from conftest import DJANGO, FakePyPI, release

from django_upgrade_report import cli
from django_upgrade_report.analysis import Status, Target, Verdict, supports
from django_upgrade_report.frameworks import DJANGO_CMS, WAGTAIL
from django_upgrade_report.pypi import ReleaseInfo


def wagtail_release(name, version, wagtail=None, majors=(), uploaded="2026-01-01", django=">=4.2"):
    r = release(name, version, django, uploaded=uploaded)
    r["classifiers"] = [f"Framework :: Wagtail :: {m}" for m in majors]
    if wagtail is not None:
        r["requires_dist"] = [*r["requires_dist"], f"wagtail{wagtail}"]
    return r


WAGTAIL_RELEASES = [
    release("wagtail", v, ">=4.2", uploaded=date)
    for v, date in [
        ("5.2", "2023-11-01"),
        ("5.2.3", "2024-01-20"),
        ("6.0", "2024-02-07"),
        ("6.3", "2024-11-01"),
        ("6.3.1", "2024-12-01"),
        ("7.0", "2025-05-06"),
    ]
]


@pytest.fixture
def wagtail_index():
    return FakePyPI(
        {
            "django": DJANGO,
            "wagtail": WAGTAIL_RELEASES,
            # 2.0 still runs on 5.2 and declares 6: upgrade it first.
            "wagtail-first": [
                wagtail_release("wagtail-first", "1.0", ">=5.0,<6.0", [5]),
                wagtail_release("wagtail-first", "2.0", ">=5.2", [5, 6], uploaded="2024-12-15"),
            ],
            # 3.0 needs Wagtail 6.3: together with Wagtail.
            "wagtail-with": [
                wagtail_release("wagtail-with", "2.0", ">=5.2,<6.0", [5]),
                wagtail_release("wagtail-with", "3.0", ">=6.3", [6], uploaded="2024-12-15"),
            ],
            # Declares 6, but from before 6.3 came out: a question, not a yes.
            "wagtail-early": [
                wagtail_release("wagtail-early", "1.0", ">=5.0", [5, 6], uploaded="2024-03-01"),
            ],
            "django-filter": [release("django-filter", "23.3", ">=4.2", ["4.2", "5.0"])],
        }
    )


@pytest.fixture
def wagtail_project(tmp_path, wagtail_index, monkeypatch):
    (tmp_path / "requirements.txt").write_text(
        "Django==4.2.7\nwagtail==5.2.3\nwagtail-first==1.0\nwagtail-with==2.0\n"
        "wagtail-early==1.0\ndjango-filter==23.3\n"
    )
    monkeypatch.setattr(cli, "PyPI", lambda *args, **kwargs: wagtail_index)
    return tmp_path


def report(project, capsys, *args):
    assert cli.main([str(project), "--framework", "wagtail", "-f", "json", *args]) == 0
    return json.loads(capsys.readouterr().out)


def rows(data):
    return {p["name"]: (p["status"], p["upgrade_to"], p["phase"]) for p in data["packages"]}


def test_wagtail_plan(wagtail_project, capsys):
    data = report(wagtail_project, capsys, "--target", "6.3")
    assert (data["framework"], data["target"]) == ("wagtail", "6.3")
    assert (data["current_framework"], data["current_django"]) == ("5.2.3", "4.2.7")
    assert rows(data) == {
        "wagtail-first": ("upgrade", "2.0", "before"),
        "wagtail-with": ("upgrade", "3.0", "with"),
        "wagtail-early": ("check", None, None),
    }
    assert data["skipped_non_django"] == 2  # Django and django-filter
    assert data["python"] is None and data["removals"] == []  # Django's, not Wagtail's


def test_text_names_wagtail(wagtail_project, capsys):
    args = [str(wagtail_project), "--framework", "wagtail", "--target", "6.3"]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert out.startswith("Wagtail 5.2.3 → 6.3\n")
    assert "Upgrade together with Wagtail (1)" in out
    assert "These releases still run on Wagtail 5.2. Upgrade them before Wagtail" in out
    assert "? wagtail-early  1.0  declares Wagtail 6" in out


def test_wagtail_targets(wagtail_project, capsys):
    data = report(wagtail_project, capsys)  # auto: the latest LTS, like Django's
    assert data["target"] == "7.0"
    assert any("skips Wagtail 6.3 LTS" in w for w in data["warnings"])
    assert cli.main([str(wagtail_project), "--framework", "wagtail", "--target", "9.9"]) == 2
    assert "There is no Wagtail 9.9" in capsys.readouterr().err


def test_evidence_is_for_django_only(wagtail_project, capsys):
    data = report(wagtail_project, capsys, "--evidence")
    assert "--evidence reads Django versions only: not for Wagtail" in data["notices"]


def test_wagtail_major_classifier_counts_after_the_release():
    from datetime import datetime, timezone

    ga = datetime(2024, 11, 1, tzinfo=timezone.utc)
    from packaging.version import Version

    target = Target(Version("6.3"), ga=ga, framework=WAGTAIL)
    info = ReleaseInfo(
        name="x",
        version="1.0",
        classifiers=("Framework :: Wagtail :: 6",),
        requires_dist=(),
        requires_python=None,
    )
    after = supports(info, target, datetime(2024, 12, 1, tzinfo=timezone.utc))
    before = supports(info, target, datetime(2024, 3, 1, tzinfo=timezone.utc))
    assert (after.verdict, after.reason) == (Verdict.YES, "declares Wagtail 6")
    assert (before.verdict, before.reason) == (Verdict.LIKELY, "declares Wagtail 6, not 6.3")


def test_django_cms_classifiers():
    from packaging.version import Version

    target = Target(Version("4.1"), framework=DJANGO_CMS)
    info = ReleaseInfo(
        name="djangocms-text",
        version="0.5",
        classifiers=("Framework :: Django CMS :: 4.1",),
        requires_dist=("django-cms>=4.1",),
        requires_python=None,
    )
    assert supports(info, target).verdict is Verdict.YES
    assert supports(info, target).reason == "declares django CMS 4.1"


def test_framework_details():
    from packaging.version import Version

    assert WAGTAIL.is_lts(Version("6.3")) and not WAGTAIL.is_lts(Version("6.4"))
    assert WAGTAIL.next_feature(Version("7.0")) == Version("7.1")
    assert not DJANGO_CMS.is_lts(Version("4.1"))
    assert Status.UPGRADE.value == "upgrade"


def test_commands_name_the_framework_package(wagtail_project, capsys):
    args = [str(wagtail_project), "--framework", "wagtail", "--target", "6.3", "--emit"]
    assert cli.main([*args, "uv"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Wagtail 5.2.3 → 6.3,")
    assert "uv add 'wagtail>=6.3,<6.4' 'wagtail-with>=3.0'" in out
    assert cli.main([*args, "renovate"]) == 0
    rules = json.loads(capsys.readouterr().out)["packageRules"]
    assert (rules[0]["matchPackageNames"], rules[0]["allowedVersions"]) == (["wagtail"], "<5.3")
    assert rules[1]["groupName"] == "Wagtail 6.3"
