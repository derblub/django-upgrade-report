from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import release
from packaging.version import Version

import django_upgrade_report
from django_upgrade_report import cli
from django_upgrade_report.pypi import PyPIError
from django_upgrade_report.render import html


def test_text(project, capsys):
    assert cli.main([str(project)]) == 0
    out = capsys.readouterr().out
    assert "Django 4.2.7 → 5.2" in out
    assert "Upgrade first (1)" in out
    assert "Blocked (1)" in out
    assert "1 ready · 1 to upgrade · 0 to check · 1 blocked" in out
    assert "\033[" not in out  # no colours when not a terminal


def test_fail_on(project):
    assert cli.main([str(project), "--fail-on", "blocked"]) == 1


def test_json(project, capsys):
    cli.main([str(project), "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert data["target"] == "5.2"
    assert data["counts"] == {"ready": 1, "upgrade": 1, "check": 0, "blocked": 1}
    before = next(p for p in data["packages"] if p["name"] == "django-before")
    assert (before["upgrade_to"], before["phase"]) == ("2.0", "before")


def test_markdown(project, capsys):
    cli.main([str(project), "--format", "markdown"])
    out = capsys.readouterr().out
    assert out.startswith("## Django 4.2.7 → 5.2")
    assert "| `django-before` | 1.0 → 2.0 |" in out


def test_html_to_file(project, tmp_path):
    target = tmp_path / "report.html"
    assert cli.main([str(project), "--format", "html", "-o", str(target)]) == 0
    html = target.read_text()
    assert html.startswith("<!doctype html>")
    assert "django-blocked" in html


def test_missing_project(tmp_path, capsys):
    assert cli.main([str(tmp_path / "nope")]) == 2
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize("fmt", ["markdown", "html", "json"])
def test_attribution(project, capsys, fmt):
    cli.main([str(project), "--format", fmt])
    out = capsys.readouterr().out
    assert "Daniel Kurdoghlian" in out
    assert "Pushing Pixels" in out


def test_version_names_the_author(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "Daniel Kurdoghlian, Pushing Pixels" in capsys.readouterr().out


# --- exit codes: 0 ok, 1 only for --fail-on, 2 for every error


def test_fail_on_not_hit(project):
    assert cli.main([str(project), "--fail-on", "check"]) == 1
    (project / "requirements.txt").write_text("Django==4.2.7\ndjango-ready==1.0\n")
    assert cli.main([str(project), "--fail-on", "check"]) == 0


@pytest.mark.parametrize(
    ("package", "failing"),
    [
        ("django-ready==1.0", set()),
        ("django-lagging==1.0", {"check"}),
        ("django-before==1.0", {"check", "upgrade"}),
        ("django-blocked==1.0", {"check", "upgrade", "blocked"}),
    ],
)
def test_fail_on_is_a_threshold(project, package, failing):
    """Each value fails on its own status and on every worse one."""
    (project / "requirements.txt").write_text(f"Django==4.2.7\n{package}\n")
    for value in ("check", "upgrade", "blocked"):
        assert cli.main([str(project), "--fail-on", value]) == (1 if value in failing else 0)


def test_unknown_target(project, capsys):
    assert cli.main([str(project), "--target", "5.3"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: There is no Django 5.3")
    assert "Traceback" not in err


def test_unreadable_lockfile(project, capsys):
    (project / "uv.lock").write_text('version = 1\n<<<<<<< HEAD\n[[package]\nname = "django"\n')
    assert cli.main([str(project)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "uv.lock" in err


def test_index_failure(project, monkeypatch, capsys):
    class Broken:
        def project(self, name):
            raise PyPIError("could not fetch https://pypi.test/pypi/django/json: timed out")

        def redact(self, text):
            return text

    monkeypatch.setattr(cli, "PyPI", lambda *args, **kwargs: Broken())
    assert cli.main([str(project)]) == 2
    assert capsys.readouterr().err == (
        "error: could not fetch https://pypi.test/pypi/django/json: timed out\n"
    )


def test_private_index_token_is_never_printed(project, monkeypatch, capsys):
    """The real client, a fake HTTP layer: a 401 from a private index."""
    import io
    import urllib.error
    from email.message import Message

    from django_upgrade_report import client, pypi

    def urlopen(request, timeout=None):
        assert "s3cr3t" not in request.full_url
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", Message(), io.BytesIO())

    monkeypatch.setattr(cli, "PyPI", pypi.PyPI)
    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    url = "https://deploy:s3cr3t@pkgs.example.com/pypi"
    assert cli.main([str(project), "--no-cache", "--index-url", url]) == 2
    err = capsys.readouterr().err
    assert "HTTP 401" in err and "pkgs.example.com" in err
    assert "s3cr3t" not in err


# --- output


def test_output_creates_directories(project, tmp_path):
    target = tmp_path / "reports" / "nested" / "report.md"
    assert cli.main([str(project), "--format", "markdown", "-o", str(target)]) == 0
    assert target.read_bytes().decode("utf-8").startswith("## Django 4.2.7 → 5.2")


def test_output_is_a_directory(project, tmp_path, capsys):
    assert cli.main([str(project), "-o", str(tmp_path)]) == 2
    assert "is a directory" in capsys.readouterr().err


def test_output_not_writable(project, tmp_path, capsys):
    (tmp_path / "file").write_text("")
    assert cli.main([str(project), "--fail-on", "blocked", "-o", str(tmp_path / "file" / "x")]) == 2
    assert capsys.readouterr().err.startswith("error: cannot write")


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_output_file_is_utf8(project, tmp_path, monkeypatch, fmt):
    from pathlib import Path

    write_text = Path.write_text

    def windows_default(self, data, encoding=None, errors=None, newline=None):
        return write_text(self, data, encoding or "cp1252", errors, newline)

    monkeypatch.setattr(Path, "write_text", windows_default)
    target = tmp_path / f"report.{fmt}"
    assert cli.main([str(project), "--format", fmt, "-o", str(target)]) == 0
    assert "→" in target.read_bytes().decode("utf-8")


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_stdout_that_cannot_encode_arrows(project, monkeypatch, fmt):
    import io
    import sys

    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)
    assert cli.main([str(project), "--format", fmt]) == 0
    assert "→" in stdout.buffer.getvalue().decode("utf-8")


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_progress_reaches_the_total(project, monkeypatch):
    import sys

    stderr = Terminal()
    monkeypatch.setattr(sys, "stderr", stderr)
    assert cli.main([str(project)]) == 0
    shown = re.findall(r"Checking (\d+)/(\d+)", stderr.getvalue())
    assert {total for _, total in shown} == {"3"}  # Django itself is not checked
    assert sorted(int(done) for done, _ in shown) == [1, 2, 3]


def test_progress_is_thread_safe(monkeypatch):
    import sys
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(sys, "stderr", Terminal())
    progress = cli._Progress(total=500)
    with ThreadPoolExecutor(16) as pool:
        list(pool.map(progress, (f"p{i}" for i in range(500))))
    assert progress.done == 500


# --- what every renderer must show


@pytest.fixture
def busy_project(project, index):
    index.packages["django-star"] = [release("django-star", "1.0", ">=3.2,!=4.0.*,!=4.1.*")]
    (project / "requirements.txt").write_text(
        "Django==4.2.7\n"
        "django-ready>=1.0\n"  # unpinned: ready, with a note
        "django-star==1.0\n"
        "django-private==1.0\n"  # not on the index
        "requests==2.31.0\n"  # skipped
        "django-inhouse @ git+https://git.example.com/org/django-inhouse\n"
    )
    return project


@pytest.mark.parametrize("fmt", ["text", "markdown", "html", "json"])
def test_every_format_shows_everything(busy_project, capsys, fmt):
    assert cli.main([str(busy_project), "--format", fmt, "--target", "6.1"]) == 0
    out = capsys.readouterr().out
    assert "not released yet" in out  # warning
    assert "django-private" in out  # not on the index
    assert "django-inhouse" in out and "git.example.com/org/django-inhouse" in out  # external
    assert "version not pinned" in out  # note of a ready package
    if fmt != "json":
        assert "1 dependency without a Django requirement skipped" in out


def test_warnings_come_first(busy_project, capsys):
    for fmt, before in [
        ("text", "Check manually ("),
        ("markdown", "### "),
        ("html", '<div class="tiles">'),
    ]:
        cli.main([str(busy_project), "--format", fmt, "--target", "6.1"])
        out = capsys.readouterr().out
        assert out.index("not released yet") < out.index(before), fmt


def test_text_lists_ready_packages_with_notes(busy_project, capsys, index):
    index.packages["django-plain"] = [release("django-plain", "1.0", ">=4.2", ["5.2"])]
    text = (busy_project / "requirements.txt").read_text()
    (busy_project / "requirements.txt").write_text(text + "django-plain==1.0\n")
    cli.main([str(busy_project)])
    out = capsys.readouterr().out
    ready = out[out.index("Ready (2)") :]
    assert ready.splitlines()[1] == "  django-plain"
    row, note = ready.splitlines()[2:4]
    assert re.fullmatch(r"  ✓ django-ready\s+>=1.0\s+latest 1.0 declares Django 5.2", row)
    assert note.strip() == "version not pinned, add a lockfile for exact results"
    assert note.index("version") == row.index("latest")  # notes line up under the reason


def test_text_says_a_note_every_package_shares_once(project, capsys, index):
    index.packages["django-plain"] = [release("django-plain", "1.0", ">=4.2", ["5.2"])]
    (project / "requirements.txt").write_text("Django==4.2.7\ndjango-ready\ndjango-plain\n")
    cli.main([str(project)])
    out = capsys.readouterr().out
    ready = out[out.index("Ready (2)") :].splitlines()
    assert ready[1:3] == [
        "  Version not pinned, add a lockfile for exact results.",
        "  django-plain, django-ready",
    ]
    assert out.count("not pinned") == 1


def test_markdown_escapes_cells(busy_project, capsys):
    cli.main([str(busy_project), "--format", "markdown"])
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("| `django-star`"))
    assert r"!=4.0.\*,!=4.1.\*" in row
    assert r"\>=3.2" in row
    assert "**Not on the package index:** `django-private`" in out


def test_project_python(busy_project, capsys):
    (busy_project / ".python-version").write_text("3.12\n")
    cli.main([str(busy_project)])
    out = capsys.readouterr().out
    assert "· Python 3.12\n" in out.splitlines(keepends=True)[1]
    assert "Django 5.2 requires Python" not in out  # nothing to say when the Python fits
    cli.main([str(busy_project), "--format", "markdown"])
    assert "your project uses Python 3.12" in capsys.readouterr().out


def test_json_schema(busy_project, capsys):
    (busy_project / ".python-version").write_text("3.12\n")
    cli.main([str(busy_project), "--format", "json", "--target", "6.1"])
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == 1
    assert data["kind"] == "report"
    assert data["target_released"] is False
    assert data["project_python"] == "3.12"
    assert any("not released yet" in w for w in data["warnings"])
    assert data["external"] == [{"name": "django-inhouse", "source": data["external"][0]["source"]}]
    assert "git.example.com" in data["external"][0]["source"]
    assert data["not_on_index"] == ["django-private"]
    assert data["skipped_non_django"] == 1


# --- fixes from the launch review


def test_index_answering_json_without_info_is_a_clean_error(project, monkeypatch, capsys):
    """A proxy that answers {} used to crash with a KeyError traceback and exit 1."""
    from django_upgrade_report import client, pypi

    monkeypatch.setattr(cli, "PyPI", pypi.PyPI)
    monkeypatch.setattr(client.urllib.request, "urlopen", lambda r, timeout=None: io.BytesIO(b"{}"))
    url = "http://127.0.0.1:8765/noinfo"
    assert cli.main([str(project), "--no-cache", "--index-url", url, "--fail-on", "blocked"]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"error: could not fetch {url}/django/json: unexpected answer")
    assert "Traceback" not in err


def test_unexpected_exception_exits_2_without_the_token(project, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise TypeError("https://deploy:s3cr3t@pkgs.example.com/pypi went wrong")

    from django_upgrade_report import pypi

    monkeypatch.setattr(cli, "PyPI", pypi.PyPI)
    monkeypatch.setattr(cli, "analyse", boom)
    url = "https://deploy:s3cr3t@pkgs.example.com/pypi"
    assert cli.main([str(project), "--index-url", url, "--fail-on", "blocked"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: unexpected TypeError: https://***@pkgs.example.com/pypi")
    assert "s3cr3t" not in err and "/issues" in err


def test_lts_on_a_newer_django_does_not_fail(project, index, capsys):
    """Bug #4: the README's `target: lts` on a Django 6.x project."""
    index.packages["django-new"] = [
        release("django-new", "2.0", ">=4.2", ["5.2"]),
        release("django-new", "3.0", ">=6.0", ["6.0"]),
    ]
    (project / "requirements.txt").write_text("Django==6.0\ndjango-new==3.0\n")
    assert cli.main([str(project), "-t", "lts", "--fail-on", "blocked"]) == 0
    out = capsys.readouterr().out
    assert "Django 6.0 · health check" in out
    assert "the newest LTS, is older than your Django 6.0" in out
    assert "Blocked" not in out


def test_explicit_downgrade_exits_2(project, capsys):
    (project / "requirements.txt").write_text("Django==6.0\n")
    assert cli.main([str(project), "-t", "5.2"]) == 2
    assert "error: Django 5.2 is older than your Django 6.0" in capsys.readouterr().err


def test_from_sets_the_current_django(project, capsys):
    (project / "requirements.txt").write_text("Django>=4.2\ndjango-before==1.0\n")
    assert cli.main([str(project), "--from", "4.2"]) == 0
    out = capsys.readouterr().out
    assert "Django 4.2.7 → 5.2" in out
    assert "Upgrade first (1)" in out


def test_from_rejects_nonsense(project, capsys):
    assert cli.main([str(project), "--from", "banana"]) == 2
    assert "--from 'banana' is not a Django version" in capsys.readouterr().err


def test_private_index_packages_stay_off_pypi(project, index, capsys):
    (project / "requirements.txt").write_text(
        "--index-url https://tok:s3cret@pkgs.acme.example/simple\n"
        "Django==4.2.7\ndjango-before==1.0\nacme-private==1.0\n"
    )
    assert cli.main([str(project)]) == 0
    out = capsys.readouterr().out
    assert not any("acme" in url or "django-before" in url for url in index.requests)
    assert "acme-private (index https://pkgs.acme.example/simple)" in out
    assert "s3cret" not in out
    assert "pass its JSON API with --index-url" in out


def test_private_index_packages_are_checked_on_the_given_index(project, index, capsys):
    (project / "requirements.txt").write_text(
        "--index-url https://pkgs.acme.example/simple\nDjango==4.2.7\ndjango-before==1.0\n"
    )
    assert cli.main([str(project), "--index-url", "https://pkgs.acme.example/pypi"]) == 0
    assert "Upgrade first (1)" in capsys.readouterr().out
    assert any("/django-before/" in url for url in index.requests)


def test_ready_hint_mentions_unpinned_only_when_something_is(project, index, capsys):
    (project / "requirements.txt").write_text("Django==4.2.7\ndjango-ready==1.0\n")
    cli.main([str(project), "-v"])
    out = capsys.readouterr().out
    assert "Your version declares support for Django 5.2.\n" in out
    assert "unpinned" not in out
    (project / "requirements.txt").write_text("Django==4.2.7\ndjango-ready>=1.0\n")
    cli.main([str(project), "-v"])
    assert "(unpinned: the newest release your requirement allows)" in capsys.readouterr().out


@pytest.mark.parametrize("fmt", ["text", "html"])
def test_one_package_is_singular(project, capsys, fmt):
    (project / "requirements.txt").write_text("Django==4.2.7\ndjango-ready==1.0\n")
    cli.main([str(project), "--format", fmt])
    out = capsys.readouterr().out
    assert "1 Django-related package" in out
    assert "1 Django-related packages" not in out


def test_health_check_has_no_upgrade_first_section(project, index, capsys):
    (project / "requirements.txt").write_text("Django==6.0\ndjango-before==2.0\n")
    cli.main([str(project)])
    out = capsys.readouterr().out
    assert "health check" in out
    assert "Upgrade first" not in out and "before Django" not in out
    assert "Upgrade (1)" in out


# --- an index that mirrors PyPI ------------------------------------------------

MIRRORED = (
    "-i https://artifactory.acme.example/api/pypi/pypi-remote/simple\n"
    "Django==4.2.7\ndjango-before==1.0\ndjango-blocked==1.0\n"
)


def test_fail_on_never_passes_when_nothing_was_checked(project, index, capsys):
    """Regression: a mirror index made every package "not checked", and --fail-on passed."""
    (project / "requirements.txt").write_text(MIRRORED)
    assert cli.main([str(project), "--fail-on", "upgrade"]) == 2
    captured = capsys.readouterr()
    assert "No Django-related package was checked" in captured.out
    assert "--check-private-on-pypi" in captured.err
    assert not any("/django-before/" in url for url in index.requests)


def test_fail_on_never_passes_when_only_unrelated_packages_were_checked(project, index, capsys):
    """Regression: one package from PyPI, even an unrelated one, used to disable the guard."""
    private = 'source = { registry = "https://artifactory.acme.example/simple" }'
    public = 'source = { registry = "https://pypi.org/simple" }'
    (project / "uv.lock").write_text(
        "version = 1\n"
        f'[[package]]\nname = "django"\nversion = "4.2.7"\n{public}\n'
        f'[[package]]\nname = "django-before"\nversion = "1.0"\n{private}\n'
        f'[[package]]\nname = "requests"\nversion = "2.31.0"\n{public}\n'
    )
    assert cli.main([str(project), "--fail-on", "upgrade"]) == 2
    captured = capsys.readouterr()
    assert "No Django-related package was checked" in captured.out
    assert not any("/django-before/" in url for url in index.requests)


def test_check_private_on_pypi_looks_them_up(project, index, capsys):
    (project / "requirements.txt").write_text(MIRRORED)
    assert cli.main([str(project), "--check-private-on-pypi", "--fail-on", "blocked"]) == 1
    assert "Upgrade first (1)" in capsys.readouterr().out
    assert any("/django-before/" in url for url in index.requests)


def test_an_explicit_pypi_index_url_counts_as_opting_in(project, index, capsys):
    (project / "requirements.txt").write_text(MIRRORED)
    assert cli.main([str(project), "--index-url", "https://pypi.org/pypi"]) == 0
    assert "Upgrade first (1)" in capsys.readouterr().out


def test_a_public_mirror_is_pypi(project, index, capsys):
    (project / "requirements.txt").write_text(
        "-i https://pypi.tuna.tsinghua.edu.cn/simple\nDjango==4.2.7\ndjango-before==1.0\n"
    )
    assert cli.main([str(project), "--fail-on", "upgrade"]) == 1
    assert "not checked" not in capsys.readouterr().out


def test_fail_on_never_passes_an_incomplete_report(project, index, monkeypatch, capsys):
    from django_upgrade_report.pypi import PyPIError

    real = index.release

    def flaky(name, version):
        if name == "django-before":
            raise PyPIError("could not fetch django-before: HTTP 503 Backend is unhealthy")
        return real(name, version)

    monkeypatch.setattr(index, "release", flaky)
    assert cli.main([str(project)]) == 0  # the report is still shown
    out = capsys.readouterr().out
    assert "Could not check django-before" in out
    # django-blocked is known to block: that is a result, whatever django-before would say.
    assert cli.main([str(project), "--fail-on", "blocked"]) == 1
    (project / "requirements.txt").write_text("Django==4.2.7\ndjango-before==1.0\n")
    assert cli.main([str(project), "--fail-on", "blocked"]) == 2
    assert "could not be checked" in capsys.readouterr().err


def test_text_leaves_out_what_the_columns_and_sections_already_say(project, capsys):
    (project / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-before==1.0\ndjango-lagging==1.0\n"
    )
    cli.main([str(project)])
    out = capsys.readouterr().out
    # Not "2.0 declares Django 5.2": the version column says so.
    assert re.search(r"↑ django-before\s+1\.0 → 2\.0  crosses a major version\n", out)
    assert "? django-lagging  1.0  declares Django up to 4.2\n" in out  # no ", not 5.2"


def test_forks_show_where_they_come_from_in_every_format(project, capsys):
    fork = project / "vendor" / "taggit"
    fork.mkdir(parents=True)
    (fork / "pyproject.toml").write_text(
        '[project]\nname = "django-taggit"\nversion = "4.0+ours"\ndependencies = ["Django<5.0"]\n'
    )
    (project / "requirements.txt").write_text("Django==4.2.7\n-e ./vendor/taggit\n")
    cli.main([str(project)])
    out = capsys.readouterr().out
    assert "Your copy excludes Django 5.2. Fix its requirement" in out
    assert re.search(r"✗ django-taggit\s+4\.0\+ours\s+requires Django<5\.0\n\s+from path ", out)
    cli.main([str(project), "--format", "markdown"])
    assert "requires Django\\<5.0; from path ./vendor/taggit" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("where", "label"),
    [
        ("git https://github.com/acme/fork.git", "git github.com/acme/fork"),
        ("hg ssh://hg.example.com/repo", "hg hg.example.com/repo"),
        ("index https://pkgs.example.com/simple", "index https://pkgs.example.com/simple"),
        ("path ./vendor/app", "path ./vendor/app"),
    ],
)
def test_source_label(where, label):
    from django_upgrade_report.render import source_label

    assert source_label(where) == label


def test_json_says_what_django_has_instead(project, index, capsys):
    index.packages["django-jsonfield"] = [release("django-jsonfield", "1.4.1", ">=1.8", ["2.2"])]
    (project / "requirements.txt").write_text("Django==4.2.7\ndjango-jsonfield==1.4.1\n")
    cli.main([str(project), "--format", "json"])
    package = json.loads(capsys.readouterr().out)["packages"][0]
    assert package["built_into_django"] == {
        "since": "3.1",
        "replacement": "models.JSONField",
        "source": "https://docs.djangoproject.com/en/stable/releases/3.1/"
        "#jsonfield-for-all-supported-database-backends",
    }


@pytest.mark.parametrize("fmt", ["text", "markdown", "html", "json"])
def test_prerelease_in_every_format(project, index, capsys, fmt):
    index.packages["django-blocked"].append(release("django-blocked", "2.0rc1", ">=4.2", ["5.2"]))
    cli.main([str(project), "--format", fmt])
    out = capsys.readouterr().out
    assert "2.0rc1 declares Django 5.2 (pre-release)" in out
    if fmt == "json":
        p = next(p for p in json.loads(out)["packages"] if p["name"] == "django-blocked")
        assert p["prerelease"] == {
            "version": "2.0rc1",
            "reason": "declares Django 5.2",
            "uploaded": "2026-01-01T00:00:00+00:00",
        }
        assert all("prerelease" in p for p in json.loads(out)["packages"])


def test_changelog_link_and_step_size(project, index, capsys):
    index.packages["django-before"][-1]["project_urls"] = {
        "Changelog": "https://x.test/CHANGES (2).md"
    }
    cli.main([str(project), "--format", "markdown"])
    assert "crosses a major version · [changelog](https://x.test/CHANGES%20%282%29.md) |" in (
        capsys.readouterr().out
    )
    cli.main([str(project), "--format", "html"])
    assert '<a class="link" href="https://x.test/CHANGES%20%282%29.md">changelog</a>' in (
        capsys.readouterr().out
    )
    cli.main([str(project), "--format", "json"])
    p = next(
        p for p in json.loads(capsys.readouterr().out)["packages"] if p["name"] == "django-before"
    )
    assert (p["majors_crossed"], p["changelog_url"]) == (1, "https://x.test/CHANGES (2).md")
    assert p["repository_url"] is None
    cli.main([str(project)])
    assert "x.test" not in capsys.readouterr().out  # only with -v
    cli.main([str(project), "-v"])
    assert "changelog https://x.test/CHANGES%20%282%29.md" in capsys.readouterr().out


# --- offline, prefer-cache, hooks -------------------------------------------------


@pytest.fixture
def cached(project, index, monkeypatch, tmp_path):
    """Each run gets a fresh client over the same packages and a shared disk cache."""
    from conftest import FakePyPI

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    clients = []

    def make(url, cache_dir=None, mode="online"):
        fake = FakePyPI(index.packages)
        fake.cache_dir, fake.mode = cache_dir, mode
        clients.append(fake)
        return fake

    monkeypatch.setattr(cli, "PyPI", make)
    return clients


def test_offline_answers_from_the_cache(project, cached, capsys):
    assert cli.main([str(project)]) == 0
    online = capsys.readouterr().out
    assert cli.main([str(project), "--offline"]) == 0
    offline = capsys.readouterr().out
    assert cached[-1].requests == []  # never asked the index
    assert online == offline  # a fresh cache needs no notice


def test_old_cache_says_how_old(project, cached, capsys):
    cli.main([str(project)])
    old = time.time() - 3 * 24 * 3600
    for path in (Path(os.environ["XDG_CACHE_HOME"]) / "django-upgrade-report").iterdir():
        os.utime(path, (old, old))
    capsys.readouterr()
    day = datetime.fromtimestamp(old, timezone.utc).date()
    for flag in ("--offline", "--prefer-cache"):
        cli.main([str(project), flag])
        assert f"Answers from the cache, the oldest from {day}\n" in capsys.readouterr().out


def test_offline_without_a_cache(project, cached, capsys):
    assert cli.main([str(project), "--offline"]) == 2
    assert "not in the cache, run once without --offline" in capsys.readouterr().err
    assert cli.main([str(project), "--offline", "--no-cache"]) == 2
    assert "cannot go with --no-cache" in capsys.readouterr().err


def test_offline_with_a_package_missing_from_the_cache(project, cached, capsys):
    cli.main([str(project)])
    with (project / "requirements.txt").open("a") as f:
        f.write("django-lagging==1.0\n")
    capsys.readouterr()
    assert cli.main([str(project), "--offline", "--fail-on", "check"]) == 1  # django-before
    (project / "requirements.txt").write_text("Django==4.2.7\ndjango-lagging==1.0\n")
    assert cli.main([str(project), "--offline", "--fail-on", "blocked"]) == 2
    err = capsys.readouterr().err
    assert "(django-lagging): they are not in the cache. Run once without --offline" in err


def test_prefer_cache_fetches_only_what_is_missing(project, cached, capsys):
    cli.main([str(project)])
    with (project / "requirements.txt").open("a") as f:
        f.write("django-lagging==1.0\n")
    assert cli.main([str(project), "--prefer-cache"]) == 0
    assert {url.split("/")[4] for url in cached[-1].requests} == {"django-lagging"}


def test_offline_knows_what_is_not_on_the_index(project, cached, capsys):
    """A 404 is cached too: offline, a private package is "not on the index", not "failed"."""
    with (project / "requirements.txt").open("a") as f:
        f.write("django-private==1.0\n")
    cli.main([str(project)])
    capsys.readouterr()
    assert cli.main([str(project), "--offline", "--fail-on", "check"]) == 1
    assert "Not on the package index: django-private" in capsys.readouterr().out


def test_a_blocker_fails_even_when_another_package_is_unknown(project, cached, capsys):
    cli.main([str(project)])
    with (project / "requirements.txt").open("a") as f:
        f.write("django-lagging==1.0\n")
    for flags in ([], ["--errors-as-warnings"]):
        assert cli.main([str(project), "--offline", "--fail-on", "blocked", *flags]) == 1


def test_errors_as_warnings_never_exit_2(project, cached, capsys):
    assert cli.main([str(project), "--offline", "--errors-as-warnings"]) == 0
    assert capsys.readouterr().err.startswith("warning: could not fetch")
    cli.main([str(project)])
    assert cli.main([str(project), "--fail-on", "blocked", "--errors-as-warnings"]) == 1


def test_quiet_shows_only_what_blocks(project, capsys):
    assert cli.main([str(project), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Django 4.2.7 → 5.2\n")
    assert "Blocked (1)" in out and "django-blocked" in out
    assert "Upgrade first" not in out and "from requirements.txt" not in out
    assert out.rstrip().endswith("1 ready · 1 to upgrade · 0 to check · 1 blocked")


# --- --explain ----------------------------------------------------------------------


def test_explain_an_upgrade(project, capsys):
    assert cli.main([str(project), "--explain", "Django_Before"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("django-before against Django 5.2 · django-upgrade-report ")
    assert "Upgrade first" not in out  # the explanation instead of the report
    assert "  django-before 1.0 (pinned), from requirements.txt\n" in out
    assert "  classifiers: Django 4.1, 4.2: does not include 5.2\n" in out
    assert "  2.0 (2026-01-01): yes, declares Django 5.2\n" in out
    phase = "2.0 on your Django 4.2.7: yes, declares Django 4.2 → before Django"
    assert f"Before or with Django\n  {phase}\n" in out
    assert "Result\n  upgrade to 2.0 first, before Django: 2.0 declares Django 5.2\n" in out
    search = out.split("Releases looked at")[1].split("Before or with Django")[0]
    versions = [line.split()[0] for line in search.splitlines()[1:]]
    assert versions == sorted(versions, key=Version, reverse=True)  # newest first


def test_explain_several_and_the_ones_not_in_the_report(project, capsys):
    (project / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-blocked==1.0\nrequests==2.31.0\ndjango-private==1.0\n"
    )
    args = ["--explain", "django-blocked", "--explain", "requests", "--explain", "django-private"]
    assert cli.main([str(project), *args]) == 0
    blocks = capsys.readouterr().out.split("\n\n")
    assert "Result\n  blocked: latest 1.0 requires Django<5.0\n" in blocks[0]
    assert blocks[1].endswith(
        "Result\n  skipped: 2.31.0 has no Django requirement and no Framework :: Django classifier"
    )
    assert blocks[2].endswith("Result\n  not on the package index\n")


def test_explain_a_fork(project, capsys):
    fork = project / "vendor" / "taggit"
    fork.mkdir(parents=True)
    (fork / "pyproject.toml").write_text(
        '[project]\nname = "django-taggit"\nversion = "4.0"\ndependencies = ["Django<5.0"]\n'
    )
    (project / "requirements.txt").write_text("Django==4.2.7\n-e ./vendor/taggit\n")
    assert cli.main([str(project), "--explain", "django-taggit"]) == 0
    out = capsys.readouterr().out
    assert "  not from PyPI: path " in out
    assert "  requirement: Django<5.0: excludes every Django 5.2\n" in out
    assert "Result\n  blocked: requires Django<5.0\n" in out


def test_explain_a_name_that_is_not_a_dependency(project, capsys):
    assert cli.main([str(project), "--explain", "django-befor"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: --explain: django-befor is not among your dependencies, ")
    assert "did you mean django-before" in err


def test_explain_in_json_and_with_other_options(project, capsys):
    assert cli.main([str(project), "--explain", "django-before", "-f", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"]["upgrade"] == 1  # the report is still there
    sections = [line["section"] for line in data["explain"]["django-before"]]
    assert sections[0] == "inputs" and sections[-1] == "result"
    cli.main([str(project), "-f", "json"])
    assert json.loads(capsys.readouterr().out)["explain"] == {}
    for extra in (["-f", "markdown"], ["--fail-on", "blocked"], ["--quiet"]):
        assert cli.main([str(project), "--explain", "django-before", *extra]) == 2
        assert "error: --explain " in capsys.readouterr().err


def test_explain_a_package_from_another_index(project, capsys):
    (project / "requirements.txt").write_text(
        "--index-url https://pkgs.example.com/simple\nDjango==4.2.7\ndjango-inhouse==1.0\n"
    )
    assert cli.main([str(project), "--explain", "django-inhouse"]) == 0
    out = capsys.readouterr().out
    assert "not from PyPI: index https://pkgs.example.com/simple" in out
    assert "it comes from another index; pass --check-private-on-pypi" in out


def test_python_section(project, index, capsys):
    (project / ".python-version").write_text("3.10\n")
    before = index.packages["django-before"]  # 1.0, 1.5, 2.0, 2.1
    for r in before[:3]:
        r["requires_python"] = "<3.12"
    assert cli.main([str(project), "--python-target", "3.12"]) == 0
    out = capsys.readouterr().out
    section = out.split("Python 3.12 first (1)\n")[1].split("\n\n")[0]
    assert "These dependencies need something before they run on Python 3.12." in section
    assert "↑ django-before  1.0 → 2.1  1.0 requires Python <3.12" in section
    assert "say nothing about Python versions: django-blocked, django-ready" in section
    # The Django plan's step to 2.0 would not run on Python 3.12 either.
    assert "2.0 requires Python <3.12" in out.split("Upgrade first")[1]
    assert cli.main([str(project), "--python-target", "none"]) == 0
    assert "Python 3.12" not in capsys.readouterr().out


@pytest.mark.parametrize("value", ["three", "3", "2.7", "4.0"])
def test_python_target_is_checked_before_anything_is_fetched(project, index, capsys, value):
    assert cli.main([str(project), "--python-target", value]) == 2
    assert "is not a Python version such as 3.12" in capsys.readouterr().err
    assert index.requests == []


@pytest.fixture
def py_project(project, index):
    """Django 4.2.7 on Python 3.10; django-before 1.0 to 2.0 exclude Python 3.12."""
    (project / ".python-version").write_text("3.10\n")
    for r in index.packages["django-before"][:3]:
        r["requires_python"] = "<3.12"
    return project


@pytest.mark.parametrize("fmt", ["markdown", "html", "json"])
def test_python_section_in_every_format(py_project, capsys, fmt):
    assert cli.main([str(py_project), "--python-target", "3.12", "-f", fmt]) == 0
    out = capsys.readouterr().out
    if fmt == "json":
        python = json.loads(out)["python"]
        assert (python["target"], python["current"]) == ("3.12", "3.10")
        (row,) = python["packages"]
        assert (row["name"], row["status"], row["upgrade_to"]) == (
            "django-before",
            "upgrade",
            "2.1",
        )
        assert python["silent"] == ["django-blocked", "django-ready"]
        assert python["django_note"].startswith("No Django 4.2 release declares Python 3.12")
        return
    assert "Python 3.12 first" in out
    assert "These dependencies need something before they run on Python 3.12." in out
    assert "django-blocked, django-ready" in out
    if fmt == "markdown":
        assert "| `django-before` | 1.0 → 2.1 | 1.0 requires Python \\<3.12 |" in out


def test_json_python_is_null_without_a_newer_python(project, capsys):
    cli.main([str(project), "-f", "json"])
    assert json.loads(capsys.readouterr().out)["python"] is None


def test_fail_on_python(py_project, index):
    args = [str(py_project), "--python-target", "3.12"]
    assert cli.main([*args, "--fail-on-python", "upgrade"]) == 1
    assert cli.main([*args, "--fail-on-python", "blocked"]) == 0
    for r in index.packages["django-before"]:
        r["requires_python"] = "<3.12"
    index._memory.clear()  # the same client answers every run of this test
    assert cli.main([*args, "--fail-on-python", "blocked"]) == 1
    assert (
        cli.main([str(py_project), "--fail-on-python", "blocked", "--python-target", "none"]) == 0
    )


# --- the HTML checklist -------------------------------------------------------------


def test_html_checklist(project, capsys):
    cli.main([str(project), "-f", "html"])
    page = capsys.readouterr().out
    assert '<input type="checkbox" data-todo="django:django-before"' in page
    assert '<input type="checkbox" data-todo="django:django-blocked"' in page
    assert 'data-todo="django:django-ready"' not in page  # nothing to do for ready ones
    assert '<b><span id="done">0</span> / 2</b><span>done</span>' in page
    assert "<title>Django 4.2.7 → 5.2 · upgrade report</title>" in page
    assert "localStorage" in page and "<script src" not in page  # self-contained


def test_checklist_key_follows_the_plan(project, capsys):
    def key():
        cli.main([str(project), "-f", "html"])
        return re.search(r'data-checklist="([^"]+)"', capsys.readouterr().out).group(1)

    first = key()
    assert key() == first  # the same plan keeps its ticks
    (project / "requirements.txt").write_text("Django==4.2.7\ndjango-before==1.5\n")
    assert key() != first  # another plan starts unticked


def test_python_section_title_does_not_replace_the_page_title(py_project, capsys):
    cli.main([str(py_project), "--python-target", "3.12", "-f", "html"])
    page = capsys.readouterr().out
    assert "<title>Django 4.2.7 → 5.2 · upgrade report</title>" in page
    assert 'data-todo="python:django-before"' in page


# --- the interactive HTML report ------------------------------------------------------


def test_html_rows_carry_what_the_script_filters_by(project, capsys):
    cli.main([str(project), "-f", "html"])
    page = capsys.readouterr().out
    assert '<tr data-filter="before" data-search="django-before ' in page
    assert '<tr data-filter="blocked" data-search="django-blocked ' in page
    assert '<button type="button" data-chip="blocked" aria-pressed="false">blocked</button>' in page
    assert 'class="tile blocked" data-tile="blocked"' in page
    assert 'class="tile check zero">' in page  # nothing to filter by
    assert '<div class="toolbar" id="toolbar" role="search" hidden>' in page
    assert html.script_source() in page
    assert '<th data-sort="name">Package</th>' in page
    assert '<details class="more"><summary>details</summary><dl><dt>Links</dt><dd>' in page
    assert (
        '<a href="https://pypi.org/project/django-before/">PyPI</a>' in page
        and "<dt>Pin</dt><dd><code>django-before==2.0</code>"
        '<button type="button" class="copy" hidden>copy</button>'
        in page
    )


def test_html_embeds_the_json_report(project, capsys, monkeypatch):
    render = html.render

    def with_markup(report, static=False):
        report.warnings.append("</script><script>alert(1)</script>")
        return render(report, static)

    monkeypatch.setattr(html, "render", with_markup)
    cli.main([str(project), "-f", "html"])
    page = capsys.readouterr().out
    data = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', page, re.S)
    report = json.loads(data.group(1))
    assert report["kind"] == "report"
    assert report["warnings"][-1] == "</script><script>alert(1)</script>"
    assert page.count("<script") == 2  # the data and the script, nothing from the data


def test_html_static_has_no_script(project, capsys):
    assert cli.main([str(project), "-f", "html", "--static"]) == 0
    page = capsys.readouterr().out
    assert "<script" not in page
    assert 'id="toolbar"' not in page and 'id="done"' not in page
    assert '<tr data-filter="before"' in page  # the rows stay as they are
    assert '<details class="more">' in page and 'class="copy"' not in page
    assert cli.main([str(project), "--static"]) == 2
    assert "--static goes with --format html" in capsys.readouterr().err


def test_html_carries_the_pushing_pixels_signature(project, capsys):
    from urllib.parse import unquote

    assert cli.main([str(project), "-f", "html", "--static"]) == 0
    page = capsys.readouterr().out
    assert page.count('<a class="brand" href="https://pushingpixels.at"><svg class="pp-mark"') == 1
    icon = page.split('<link rel="icon" href="data:image/svg+xml,', 1)[1].split('"', 1)[0]
    assert "#" not in icon and " " not in icon  # a raw "#" would end the data URL
    assert unquote(icon).startswith("<svg ") and 'fill="#f4f4f5"' in unquote(icon)
    assert "Gradient" not in page  # one colour: the text's


def test_html_script_is_small_and_shipped_in_the_package():
    from importlib import resources

    assert (resources.files("django_upgrade_report.render") / "html_report.js").is_file()
    script = html.script_source()
    assert len(script.encode()) < 8 * 1024
    assert "// " not in script.replace("file://", "")  # the comments stay in the source


# --- direct dependencies ----------------------------------------------------------


def lock_with_a_transitive_package(project: Path) -> None:
    """django-before only comes in through django-ready."""
    (project / "requirements.txt").unlink()
    packages = "".join(
        f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        for name, version in (
            ("django", "4.2.7"),
            ("django-ready", "1.0"),
            ("django-before", "1.0"),
            ("django-blocked", "1.0"),
        )
    )
    (project / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "app"\nversion = "0.1.0"\n'
        'source = { virtual = "." }\n'
        'dependencies = [{ name = "django" }, { name = "django-ready" }, '
        '{ name = "django-blocked" }]\n' + packages
    )


def test_json_says_which_dependencies_are_direct(project, capsys):
    cli.main([str(project), "-f", "json"])
    assert {p["direct"] for p in json.loads(capsys.readouterr().out)["packages"]} == {True}
    lock_with_a_transitive_package(project)
    cli.main([str(project), "-f", "json"])
    found = {p["name"]: p["direct"] for p in json.loads(capsys.readouterr().out)["packages"]}
    assert found == {"django-ready": True, "django-before": False, "django-blocked": True}


def test_html_filters_direct_dependencies_only_when_some_are_not(project, capsys):
    cli.main([str(project), "-f", "html"])
    assert 'id="direct"' not in capsys.readouterr().out  # all of them are
    lock_with_a_transitive_package(project)
    cli.main([str(project), "-f", "html"])
    page = capsys.readouterr().out
    assert '<input type="checkbox" id="direct"> only direct dependencies' in page
    assert re.search(r'<tr data-filter="blocked" [^>]* data-direct ', page)
    assert not re.search(r'<tr data-filter="before" [^>]* data-direct ', page)


# --- -i ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["-f", "json"], "it cannot go with --format"),
        (["--fail-on", "blocked"], "it cannot go with --fail-on"),
        (["--emit", "uv"], "it cannot go with --emit"),
    ],
)
def test_interactive_goes_alone(project, capsys, args, message):
    assert cli.main([str(project), "-i", *args]) == 2
    assert message in capsys.readouterr().err


def test_interactive_needs_a_terminal(project, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_at_terminal", lambda: False)
    assert cli.main([str(project), "-i"]) == 2
    assert "-i needs a terminal" in capsys.readouterr().err


def test_interactive_needs_the_extra(project, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_at_terminal", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CI", raising=False)
    # As if Textual were not installed: importing the module that needs it fails.
    monkeypatch.setitem(sys.modules, "django_upgrade_report.tui", None)
    monkeypatch.delattr(django_upgrade_report, "tui", raising=False)
    assert cli.main([str(project), "-i", "--no-input"]) == 2
    assert "pip install 'django-upgrade-report[tui]'" in capsys.readouterr().err
