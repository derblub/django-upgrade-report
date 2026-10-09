"""--baseline: what changed since an earlier JSON report."""

from __future__ import annotations

import json

import pytest

from django_upgrade_report import cli
from django_upgrade_report.diff import BaselineError, load_baseline


def run_json(project, capsys, *args) -> dict:
    assert cli.main([str(project), "-f", "json", *args]) in (0, 1)
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def baseline(project, capsys, tmp_path):
    """The report of the project fixture: ready, upgrade first, blocked."""
    path = tmp_path / "base.json"
    path.write_text(json.dumps(run_json(project, capsys)))
    return path


def test_nothing_changed(project, baseline, capsys):
    assert cli.main([str(project), "--baseline", str(baseline)]) == 0
    assert "No changes since " in capsys.readouterr().out
    assert cli.main([str(project), "--baseline", str(baseline), "--only-changes"]) == 0
    assert capsys.readouterr().out == ""
    assert cli.main([str(project), "--baseline", str(baseline), "--fail-on-change", "any"]) == 0


def test_every_kind_of_change(project, index, baseline, capsys):
    from conftest import release

    # django-blocked gets a release for 5.2, django-before a smaller step, a package is
    # added and one removed.
    index.packages["django-blocked"].append(
        release("django-blocked", "2.0", ">=4.2", ["4.2", "5.2"])
    )
    index.packages["django-before"].insert(
        2, release("django-before", "1.9", ">=4.2", ["4.2", "5.2"])
    )
    (project / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-before==1.0\ndjango-blocked==1.0\ndjango-lagging==1.0\n"
    )
    index._memory.clear()  # the same client answers every run of this test
    data = run_json(project, capsys, "--baseline", str(baseline))
    items = [
        (i["name"], i["kind"], i["from"], i["to"], i["direction"]) for i in data["changes"]["items"]
    ]
    assert items == [
        ("django-lagging", "new", None, "check", "new"),
        ("django-before", "upgrade", "2.0", "1.9", "better"),  # a smaller step
        ("django-blocked", "status", "blocked", "upgrade first", "better"),
        ("django-ready", "gone", "ready", None, "gone"),
    ]
    assert data["changes"]["target"] == "5.2"
    cli.main([str(project), "--baseline", str(baseline)])
    out = capsys.readouterr().out
    assert "Changes since " in out and "(4)" in out
    assert "✓ django-blocked  blocked → upgrade first  2.0 declares Django 5.2" in out
    assert "✓ django-before   2.0 → 1.9                now 1.9 instead of 2.0" in out
    args = [str(project), "--baseline", str(baseline), "--fail-on-change"]
    assert cli.main([*args, "worse"]) == 1  # django-lagging is new and needs a check
    assert cli.main([*args, "any"]) == 1


def test_worse_and_warnings(project, index, baseline, capsys):
    index.packages["django-before"] = [
        r | {"requires_dist": ["Django>=5.2"]} if r["version"] == "2.0" else r
        for r in index.packages["django-before"]
    ]
    (project / "requirements.txt").write_text(
        "Django>=4.2.7,<5.0\ndjango-ready==1.0\ndjango-before==1.0\ndjango-blocked==1.0\n"
    )
    index._memory.clear()
    data = run_json(project, capsys, "--baseline", str(baseline))
    kinds = [(i["kind"], i["direction"]) for i in data["changes"]["items"]]
    assert ("status", "worse") in kinds  # upgrade first -> with Django
    assert ("warning", "worse") in kinds  # Django is no longer pinned


def test_baseline_for_another_target(project, baseline, capsys):
    args = [str(project), "--baseline", str(baseline), "--target", "6.0"]
    assert cli.main([*args, "--fail-on-change", "any"]) == 0
    assert (
        "The baseline was for Django 5.2, this report for 6.0: nothing compared"
        in capsys.readouterr().out
    )


def test_a_package_not_checked_this_time_is_not_gone(project, index, baseline, capsys):
    from django_upgrade_report.pypi import PyPIError

    fetch = index._fetch

    def flaky(url):
        if "/django-ready/" in url:
            raise PyPIError("HTTP 503")
        return fetch(url)

    index._fetch = flaky
    index._memory.clear()
    assert cli.main([str(project), "--baseline", str(baseline), "--fail-on-change", "any"]) == 0
    out = capsys.readouterr().out
    assert "No changes since" in out and "Could not check django-ready" in out


def test_only_changes_writes_no_file_when_nothing_changed(project, baseline, tmp_path):
    out = tmp_path / "changes.txt"
    assert (
        cli.main([str(project), "--baseline", str(baseline), "--only-changes", "-o", str(out)]) == 0
    )
    assert not out.exists()
    assert (
        cli.main([str(project), "--baseline", str(baseline), "--only-changes", "-f", "html"]) == 2
    )


def test_baseline_from_an_older_version_without_new_fields(project, baseline, capsys):
    data = json.loads(baseline.read_text())
    del data["kind"]
    for p in data["packages"]:
        for key in ("prerelease", "majors_crossed", "changelog_url", "repository_url"):
            p.pop(key)
    baseline.write_text(json.dumps(data))
    assert cli.main([str(project), "--baseline", str(baseline)]) == 0
    assert "No changes since" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("{nope", "is not JSON"),
        ('{"schema_version": 2, "packages": []}', "schema_version 1"),
        ('{"schema_version": 1, "kind": "path", "packages": []}', "holds a path"),
        ('{"schema_version": 1}', "no list of packages"),
    ],
)
def test_broken_baselines(tmp_path, content, error):
    path = tmp_path / "base.json"
    path.write_text(content)
    with pytest.raises(BaselineError, match=error):
        load_baseline(path)


def test_missing_baseline_and_options_without_one(project, tmp_path, capsys):
    assert cli.main([str(project), "--baseline", str(tmp_path / "nope.json")]) == 2
    assert "cannot read the baseline" in capsys.readouterr().err
    assert cli.main([str(project), "--only-changes"]) == 2
    assert "--only-changes needs --baseline" in capsys.readouterr().err
