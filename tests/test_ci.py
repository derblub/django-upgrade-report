"""The pull request comment of the GitHub Action."""

from __future__ import annotations

import json

import pytest
from conftest import FakeGitHub

from django_upgrade_report import ci
from django_upgrade_report.client import FetchError

REPORT = {
    "target": "5.2",
    "packages": [{"name": "django-x", "status": "upgrade", "phase": "before", "upgrade_to": "2.0"}],
}


def test_creates_then_updates_one_comment():
    github = FakeGitHub([{"id": 1, "body": "someone else"}])
    assert ci.comment(github, "org/repo", 7, ".:5.2", REPORT, "## report") == "created"
    assert ci.comment(github, "org/repo", 7, ".:5.2", REPORT, "## report 2") == "updated"
    assert len(github.comments) == 2
    assert github.comments[1]["body"].startswith("<!-- django-upgrade-report:.:5.2 fp=")
    assert github.comments[1]["body"].endswith("\n## report 2")
    assert ("PATCH", "/repos/org/repo/issues/comments/2") in github.calls


def test_each_project_has_its_own_comment():
    github = FakeGitHub()
    ci.comment(github, "org/repo", 7, "api:5.2", REPORT, "api")
    ci.comment(github, "org/repo", 7, "web:5.2", REPORT, "web")
    assert len(github.comments) == 2


def test_on_change_leaves_an_unchanged_report_alone():
    github = FakeGitHub()
    ci.comment(github, "org/repo", 7, ".:5.2", REPORT, "## report")
    assert ci.comment(github, "org/repo", 7, ".:5.2", REPORT, "## new words", on_change=True) == (
        "unchanged"
    )
    moved = {**REPORT, "packages": [{**REPORT["packages"][0], "upgrade_to": "2.1"}]}
    assert ci.comment(github, "org/repo", 7, ".:5.2", moved, "## moved", on_change=True) == (
        "updated"
    )


def test_finds_its_comment_on_a_later_page():
    others = [{"id": i, "body": f"comment {i}"} for i in range(1, 151)]
    github = FakeGitHub(others)
    ci.comment(github, "org/repo", 7, ".:5.2", REPORT, "first")
    assert ci.comment(github, "org/repo", 7, ".:5.2", REPORT, "second") == "updated"
    assert len(github.comments) == 151


def test_long_reports_are_cut_at_a_line():
    text = "\n".join(f"| `pkg-{i}` | 1.0 | declares Django 5.2 |" for i in range(3000))
    body = ci.body(".:5.2", REPORT, text)
    assert len(body) < 65_536
    assert body.endswith("the whole of it is in the job summary._\n")
    assert "| 1.0 | declares Django 5.2 |\n\n_The report" in body


@pytest.fixture
def action_env(tmp_path, monkeypatch):
    (tmp_path / "event.json").write_text(json.dumps({"pull_request": {"number": 7}}))
    (tmp_path / "report.json").write_text(json.dumps(REPORT))
    (tmp_path / "report.md").write_text("## report")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "event.json"))
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_s3cr3t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    return [
        "comment",
        "--report",
        str(tmp_path / "report.json"),
        "--markdown",
        str(tmp_path / "report.md"),
    ]


def test_main_comments_on_the_pull_request(action_env, monkeypatch, capsys):
    github = FakeGitHub()
    monkeypatch.setattr(ci, "GitHub", lambda token: github)
    assert ci.main([*action_env, "--key", "backend"]) == 0
    assert capsys.readouterr().out == "django-upgrade-report: comment created on #7\n"
    assert github.comments[0]["body"].startswith("<!-- django-upgrade-report:backend fp=")


def test_a_fork_cannot_comment_and_the_job_goes_on(action_env, monkeypatch, capsys):
    monkeypatch.setattr(ci, "GitHub", lambda token: FakeGitHub(refuse=True))
    assert ci.main(action_env) == 0
    err = capsys.readouterr().err
    assert "HTTP 403" in err and "pull request from a fork" in err


@pytest.mark.parametrize("event", ["push", "schedule", ""])
def test_no_comment_outside_pull_requests(action_env, monkeypatch, capsys, event):
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setattr(ci, "GitHub", lambda token: pytest.fail("asked GitHub"))
    assert ci.main(action_env) == 0
    assert "is not a pull request event" in capsys.readouterr().out


def test_the_token_never_shows(monkeypatch):
    from conftest import http_error, serve

    serve(monkeypatch, http_error(401))
    github = ci.GitHub("ghs_s3cr3t")
    with pytest.raises(FetchError) as info:
        github.get("/repos/org/repo/issues/7/comments")
    assert "ghs_s3cr3t" not in str(info.value)
    assert github.redact("token ghs_s3cr3t") == "token ***"


def test_send_posts_json_and_reports_a_refusal(monkeypatch):
    from conftest import http_error, serve

    fake = serve(monkeypatch, {"id": 3})
    github = ci.GitHub("ghs_s3cr3t")
    assert github.send("POST", "/repos/org/repo/issues/7/comments", {"body": "hi"}) == {"id": 3}
    request = fake.requests[0]
    assert (request.get_method(), json.loads(request.data)) == ("POST", {"body": "hi"})
    assert request.get_header("Authorization") == "Bearer ghs_s3cr3t"
    serve(monkeypatch, http_error(403))
    with pytest.raises(FetchError, match="HTTP 403"):
        github.send("PATCH", "/repos/org/repo/issues/comments/3", {"body": "hi"})


def test_a_copy_by_a_person_is_left_alone():
    github = FakeGitHub()
    ci.comment(github, "org/repo", 7, ".", REPORT, "mine")
    github.comments[0]["user"] = {"type": "Bot"}
    copy = {"id": 99, "body": github.comments[0]["body"], "user": {"type": "User"}}
    github.comments.insert(0, copy)
    assert ci.comment(github, "org/repo", 7, ".", REPORT, "again") == "updated"
    assert copy["body"].endswith("mine") and github.comments[1]["body"].endswith("again")


def test_any_failure_leaves_the_job_alone(action_env, monkeypatch, capsys):
    class Broken(FakeGitHub):
        def get(self, path):
            raise RuntimeError("ghs_s3cr3t went wrong")

    monkeypatch.setattr(ci, "GitHub", lambda token: Broken())
    assert ci.main(action_env) == 0
    err = capsys.readouterr().err
    assert "no comment: *** went wrong" in err and "ghs_s3cr3t" not in err
