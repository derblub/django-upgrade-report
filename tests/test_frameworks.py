"""Wagtail and django CMS as what the report plans: --framework."""

from __future__ import annotations

import json

import pytest
from conftest import DJANGO, FakePyPI, release

from django_upgrade_report import cli
from django_upgrade_report.analysis import Phase, Status, Target, Verdict, supports
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


def test_target_wagtail_needs_a_newer_django(wagtail_project, wagtail_index, capsys):
    (wagtail_project / "requirements.txt").write_text("Django==3.2.25\nwagtail==5.2.3\n")
    wagtail_index.packages["wagtail"][-1] = release("wagtail", "7.0", ">=4.2,<6.1")
    data = report(wagtail_project, capsys, "--target", "7.0")
    assert (
        "Wagtail 7.0 requires Django>=4.2,<6.1, not your Django 3.2.25: upgrade Django first, "
        "run django-upgrade-report --target 4.2"
    ) in data["warnings"]


def test_target_wagtail_runs_on_your_django(wagtail_project, capsys):
    data = report(wagtail_project, capsys, "--target", "6.3")
    assert "Wagtail 6.3.1 requires Django>=4.2: your Django 4.2.7 is fine" in data["notices"]
    assert not any("requires Django" in w for w in data["warnings"])


def test_django_reports_have_no_framework_fit(project, capsys):
    assert cli.main([str(project), "-f", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert not any("requires Django" in n for n in data["notices"] + data["warnings"])


def test_django_cms_plugins_by_classifier_or_name():
    from django_upgrade_report.analysis import is_django_related

    def info(name, classifiers=()):
        return ReleaseInfo(name, "1.0", classifiers, (), None)

    assert is_django_related(info("djangocms-text", ("Framework :: Django CMS",)), DJANGO_CMS)
    assert is_django_related(info("djangocms_link"), DJANGO_CMS)  # only the name says so
    assert not is_django_related(info("django-filter", ("Framework :: Django",)), DJANGO_CMS)
    assert not is_django_related(info("wagtail-localize"), DJANGO_CMS)


# --- golden tests: real PyPI metadata, recorded by fixtures/record.py -----------


def golden(recorded, framework, target, *pins):
    from django_upgrade_report.analysis import analyse
    from django_upgrade_report.frameworks import FRAMEWORKS
    from django_upgrade_report.sources import Dependency, DependencySet

    ds = {name: Dependency(name, version) for name, version in (p.split("==") for p in pins)}
    return analyse(DependencySet("golden", ds), recorded, target, framework=FRAMEWORKS[framework])


def by_name(report, name):
    return next(p for p in report.packages if p.name == name)


def test_golden_wagtail_plugins(recorded):
    """wagtail-localize 1.12 still runs on Wagtail 5.2; wagtail-modeladmin 2.3.0 needs 7.0."""
    report = golden(
        recorded,
        "wagtail",
        "7.0",
        "django==4.2.20",
        "wagtail==5.2.8",
        "wagtail-localize==1.11",
        "wagtail-modeladmin==2.0.0",
        "wagtail-grapple==0.31.0",
    )
    localize, modeladmin = (
        by_name(report, "wagtail-localize"),
        by_name(report, "wagtail-modeladmin"),
    )
    assert (localize.status, localize.phase, localize.target_version) == (
        Status.UPGRADE,
        Phase.BEFORE,
        "1.12",
    )
    assert (modeladmin.phase, modeladmin.target_version) == (Phase.WITH, "2.3.0")
    assert modeladmin.reason == "2.3.0 declares Wagtail 7"
    assert by_name(report, "wagtail-grapple").status is Status.READY
    assert "Wagtail 7.0.9 requires Django>=4.2: your Django 4.2.20 is fine" in report.notices


def test_golden_wagtail_on_an_old_django(recorded):
    report = golden(recorded, "wagtail", "7.0", "django==3.2.25", "wagtail==5.2.8")
    assert any(
        "upgrade Django first, run django-upgrade-report --target 4.2" in w for w in report.warnings
    )


def test_golden_django_cms(recorded):
    """django CMS has no LTS: auto is its newest release. djangocms-text has classifiers only."""
    report = golden(
        recorded,
        "django-cms",
        "auto",
        "django==5.2.17",
        "django-cms==4.1.0",
        "djangocms-text==0.9.2",
    )
    assert report.target == "5.1"
    text = by_name(report, "djangocms-text")
    assert (text.status, text.phase, text.target_version) == (Status.UPGRADE, Phase.BEFORE, "0.9.4")
    assert text.reason == "0.9.4 declares django CMS 5.1"


def test_questions_are_about_the_framework(wagtail_index):
    from django_upgrade_report.prompts import ask_missing
    from django_upgrade_report.sources import Dependency, DependencySet

    asked = []

    def ask(question, options):
        asked.append((question, options))
        return 0 if len(asked) == 1 else None

    deps = DependencySet(
        "test",
        {
            "django": Dependency("django", "4.2.7"),
            "wagtail": Dependency("wagtail", None, spec=">=5.2,<6.0"),
        },
        python="3.12",
    )
    answers = ask_missing(deps, wagtail_index, "auto", None, ask, WAGTAIL)
    assert asked[0] == (
        "Wagtail is not pinned (wagtail>=5.2,<6.0). Which version do you run?",
        ["5.2.3"],
    )
    assert answers.current == "5.2.3"
    assert asked[1][0] == "Wagtail 7.0 skips the 6.3 LTS. Which target?"


def test_tracking_issue_names_the_framework():
    from django_upgrade_report import ci

    plan = {
        "framework": "wagtail",
        "target": "7.0",
        "source": "requirements.txt",
        "packages": [
            {
                "name": "wagtail-with",
                "status": "upgrade",
                "phase": "with",
                "current": "2.0",
                "upgrade_to": "3.0",
                "reason": "3.0 declares Wagtail 7",
            }
        ],
    }
    assert ci.issue_title(plan, ".") == "Wagtail 7.0 upgrade plan"
    body = ci.issue_body(plan, ".")
    assert "What your dependencies need for Wagtail 7.0" in body
    assert "upgrade with Wagtail, 3.0 declares Wagtail 7" in body
    assert ci.issue_title({"kind": "path", "target": "7.0", "steps": [plan]}, ".").startswith(
        "Wagtail"
    )
    assert ci.issue_title({"target": "5.2"}, ".") == "Django 5.2 upgrade plan"
