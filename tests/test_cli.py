from __future__ import annotations

import io
import json
import re

import pytest
from conftest import release

from django_upgrade_report import cli
from django_upgrade_report.pypi import PyPIError


@pytest.fixture
def project(tmp_path, index, monkeypatch):
    (tmp_path / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-ready==1.0\ndjango-before==1.0\ndjango-blocked==1.0\n"
    )
    monkeypatch.setattr(cli, "PyPI", lambda *args, **kwargs: index)
    return tmp_path


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
    assert cli.main([str(project), "--fail-on", "blocked"]) == 2
    assert "could not be checked" in capsys.readouterr().err


def test_text_leaves_out_what_the_columns_and_sections_already_say(project, capsys):
    (project / "requirements.txt").write_text(
        "Django==4.2.7\ndjango-before==1.0\ndjango-lagging==1.0\n"
    )
    cli.main([str(project)])
    out = capsys.readouterr().out
    assert re.search(r"↑ django-before\s+1\.0 → 2\.0\n", out)  # not "2.0 declares Django 5.2"
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
