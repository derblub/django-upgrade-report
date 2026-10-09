"""The HTTP side of the PyPI client, against a fake ``urlopen``: no network."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import urllib.error

import pytest
from conftest import Response, http_error, serve

from django_upgrade_report import pypi
from django_upgrade_report.pypi import PyPI, PyPIError

PROJECT = {
    "info": {
        "name": "django-x",
        "version": "1.0",
        "classifiers": [],
        "requires_dist": ["Django>=4.2"],
        "requires_python": None,
    },
    "releases": {"1.0": [{"upload_time_iso_8601": "2026-01-01T00:00:00Z"}]},
}


def test_project(monkeypatch, sleeps):
    fake = serve(monkeypatch, PROJECT)
    project = PyPI("https://pypi.test/pypi/").project("django-x")
    assert project.name == "django-x"
    assert fake.requests[0].full_url == "https://pypi.test/pypi/django-x/json"
    assert fake.requests[0].get_header("User-agent") == pypi.USER_AGENT
    assert fake.requests[0].get_header("Authorization") is None
    assert sleeps == []


def test_not_found(monkeypatch, sleeps):
    fake = serve(monkeypatch, http_error(404))
    assert PyPI("https://pypi.test/pypi").project("nope") is None
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    "failure",
    [
        http_error(500),
        http_error(503),
        http_error(429),
        urllib.error.URLError(OSError("Name or service not known")),
        TimeoutError("timed out"),
        ConnectionResetError(104, "Connection reset by peer"),
        http.client.RemoteDisconnected("Remote end closed connection"),
        Response(b"", fail=http.client.IncompleteRead(b'{"inf', 100)),
        b'{"info": {"na',  # truncated JSON
        b"<html>proxy login</html>",
    ],
    ids=lambda f: type(f).__name__,
)
def test_retries_transient_failures(monkeypatch, sleeps, failure):
    fake = serve(monkeypatch, failure, PROJECT)
    assert PyPI("https://pypi.test/pypi").project("django-x").name == "django-x"
    assert len(fake.requests) == 2
    assert len(sleeps) == 1


def test_gives_up_with_one_clear_error(monkeypatch, sleeps):
    fake = serve(monkeypatch, TimeoutError("timed out"))
    with pytest.raises(PyPIError) as info:
        PyPI("https://pypi.test/pypi").project("django-x")
    assert len(fake.requests) == pypi.ATTEMPTS == 4
    assert str(info.value) == (
        "could not fetch https://pypi.test/pypi/django-x/json: timed out (tried 4 times)"
    )
    assert isinstance(info.value, OSError)
    # Exponential backoff with jitter.
    assert 1 <= sleeps[0] < 2 <= sleeps[1] < 3 and 4 <= sleeps[2] < 5


def test_not_json_names_the_likely_cause(monkeypatch, sleeps):
    serve(monkeypatch, b"<html></html>")
    with pytest.raises(PyPIError, match="not JSON .is this a PyPI JSON API URL"):
        PyPI("https://pypi.test/simple").project("django-x")


@pytest.mark.parametrize("code", [401, 403, 400])
def test_client_errors_are_not_retried(monkeypatch, sleeps, code):
    fake = serve(monkeypatch, http_error(code))
    with pytest.raises(PyPIError, match=f"HTTP {code}"):
        PyPI("https://pypi.test/pypi").project("django-x")
    assert len(fake.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [("7", 7.0), ("0", 0.0), ("3600", 30.0), ("soon", None), ("nan", None)],
)
def test_retry_after(monkeypatch, sleeps, retry_after, expected):
    serve(monkeypatch, http_error(429, {"Retry-After": retry_after}), PROJECT)
    PyPI("https://pypi.test/pypi").project("django-x")
    if expected is None:
        assert 1 <= sleeps[0] < 2  # falls back to the backoff
    else:
        assert sleeps == [expected]


def test_retry_after_http_date(monkeypatch, sleeps):
    serve(monkeypatch, http_error(503, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), PROJECT)
    PyPI("https://pypi.test/pypi").project("django-x")
    assert sleeps == [0.0]  # in the past


def test_credentials_become_basic_auth(monkeypatch, sleeps):
    fake = serve(monkeypatch, PROJECT)
    client = PyPI("https://deploy:s3cr%40t@pkgs.example.com:8443/pypi/")
    client.project("django-x")
    request = fake.requests[0]
    assert request.full_url == "https://pkgs.example.com:8443/pypi/django-x/json"
    expected = base64.b64encode(b"deploy:s3cr@t").decode()
    assert request.get_header("Authorization") == f"Basic {expected}"
    assert client.index_url == "https://pkgs.example.com:8443/pypi"


def test_token_only_userinfo(monkeypatch, sleeps):
    fake = serve(monkeypatch, PROJECT)
    PyPI("https://TOKEN123@pkgs.example.com/pypi").project("django-x")
    header = fake.requests[0].get_header("Authorization")
    assert base64.b64decode(header.split()[1]) == b"TOKEN123:"


@pytest.mark.parametrize(
    "failure",
    [http_error(401), urllib.error.URLError("s3cr3t-T0KEN appears in the reason")],
)
def test_errors_never_show_the_token(monkeypatch, sleeps, failure):
    serve(monkeypatch, failure)
    client = PyPI("https://deploy:s3cr3t-T0KEN@pkgs.example.com/pypi")
    with pytest.raises(PyPIError) as info:
        client.project("django-x")
    assert "s3cr3t-T0KEN" not in str(info.value)
    assert "pkgs.example.com" in str(info.value)
    assert client.redact("x s3cr3t-T0KEN y") == "x *** y"


def test_invalid_url_is_a_clean_error(monkeypatch, sleeps):
    with pytest.raises(PyPIError, match="not an http"):
        PyPI("pypi.org/pypi").project("django-x")


def test_credentials_stay_out_of_the_cache(monkeypatch, sleeps, tmp_path):
    serve(monkeypatch, PROJECT)
    PyPI("https://u:secret@pypi.test/pypi", cache_dir=tmp_path).project("django-x")
    cached = PyPI("https://pypi.test/pypi", cache_dir=tmp_path)
    serve(monkeypatch, TimeoutError())  # would fail if it asked the network
    assert cached.project("django-x").name == "django-x"
    assert all("secret" not in path.read_text() for path in tmp_path.iterdir())


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"message": "Not authorised"},
        {"info": {"version": "1.0"}},
        {"info": {"name": "django-x"}},
        {"info": {"name": "django-x", "version": "1.0"}, "releases": []},
        {"info": {"name": "django-x", "version": "1.0"}, "releases": {"1.0": "x"}},
        [1, 2],
        b"null",
    ],
    ids=[
        "empty",
        "error-object",
        "no-name",
        "no-version",
        "releases-list",
        "files-str",
        "array",
        "null",
    ],
)
def test_json_that_is_not_a_project_is_a_clean_error(monkeypatch, sleeps, answer):
    fake = serve(monkeypatch, answer)
    with pytest.raises(PyPIError, match="unexpected answer.*is this a PyPI JSON API URL"):
        PyPI("https://pypi.test/pypi").project("django-x")
    assert len(fake.requests) == 1  # retrying does not change the answer


def test_upload_times_without_time_zone_are_utc(monkeypatch, sleeps):
    naive = json.loads(json.dumps(PROJECT))
    naive["releases"] = {
        "1.0": [{"upload_time_iso_8601": "2026-01-01T00:00:00"}],
        "1.1": [{"upload_time_iso_8601": "2026-02-01T00:00:00.123456Z"}],
        "1.2": [{"upload_time_iso_8601": "not a date"}],
    }
    serve(monkeypatch, naive)
    releases = PyPI("https://pypi.test/pypi").project("django-x").releases
    assert [r.uploaded.tzinfo is not None for r in releases[:2]] == [True, True]
    assert releases[0].uploaded < releases[1].uploaded
    assert releases[2].uploaded is None


# --- what the cache keeps (format v2) ---------------------------------------------

RICH = {
    "info": {
        "name": "django-x",
        "version": "2.0",
        "classifiers": ["Framework :: Django :: 5.2"],
        "requires_dist": ["Django>=4.2"],
        "requires_python": ">=3.10",
        "project_urls": {"Changelog": "https://x.test/changes", "Broken": None, "": "x"},
        "home_page": "https://github.com/org/django-x",
        "description": "Supports Django 4.2, Django 5.2 and django>=6.0. Not Django 23.1.",
    },
    "urls": [
        {"filename": "django_x-2.0-cp312-cp312-manylinux_2_17_x86_64.whl", "packagetype": "x"},
        {"filename": "django_x-2.0-py3-none-any.whl", "packagetype": "bdist_wheel"},
        {"filename": "not-a-wheel.whl", "packagetype": "bdist_wheel"},
        {"filename": "django_x-2.0.tar.gz", "packagetype": "sdist"},
    ],
    "releases": {
        "1.0": [
            {
                "filename": "django_x-1.0.tar.gz",
                "upload_time_iso_8601": "2025-01-01T00:00:00Z",
                "requires_python": None,
            }
        ],
        "2.0": [
            {
                "filename": "django_x-2.0-py3-none-any.whl",
                "upload_time_iso_8601": "2026-01-02T00:00:00Z",
                "requires_python": ">=3.10",
            },
            {
                "filename": "django_x-2.0-cp312-cp312-win_amd64.whl",
                "upload_time_iso_8601": "2026-01-01T00:00:00Z",
                "requires_python": ">=3.10",
            },
        ],
    },
}


def test_cache_keeps_links_mentions_and_wheels(monkeypatch, sleeps, tmp_path):
    serve(monkeypatch, RICH)
    cold = PyPI("https://pypi.test/pypi", cache_dir=tmp_path).project("django-x")
    serve(monkeypatch, TimeoutError())  # a warm cache must not ask
    warm = PyPI("https://pypi.test/pypi", cache_dir=tmp_path).project("django-x")
    assert cold == warm
    info = warm.latest
    assert info.project_urls == (("Changelog", "https://x.test/changes"),)
    assert info.home_page == "https://github.com/org/django-x"
    assert info.django_mentions == ("4.2", "5.2", "6.0")
    assert info.wheel_tags == ("cp312-cp312-manylinux_2_17_x86_64", "py3-none-any")
    assert info.has_sdist
    old, new = warm.releases
    assert (old.requires_python, old.wheel_tags, old.has_sdist) == (None, (), True)
    assert not new.has_sdist
    assert new.requires_python == ">=3.10"
    assert new.wheel_tags == ("py3-none-any",)  # Windows wheels say nothing about Linux
    assert new.uploaded.day == 1  # still the earliest file
    (cached,) = tmp_path.iterdir()
    assert "Supports Django" not in cached.read_text()  # the README is not kept


def test_cache_of_an_older_format_is_not_read(monkeypatch, sleeps, tmp_path):
    url = "https://pypi.test/pypi/django-x/json"
    legacy = tmp_path / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    legacy.write_text(json.dumps(PROJECT | {"info": PROJECT["info"] | {"version": "0.1"}}))
    fake = serve(monkeypatch, PROJECT)
    assert (
        PyPI("https://pypi.test/pypi", cache_dir=tmp_path).project("django-x").latest.version
        == "1.0"
    )
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Works with Django 4.2 and Django 5.2.3, tested on django==5.1", ["4.2", "5.1", "5.2"]),
        ("Django version 6.0, django v3.2", ["3.2", "6.0"]),
        ("django-filter 23.2, Django REST framework 3.15, Django 30.1", []),
        ("Requires Django >= 4.2, Django~=5.0 or Django: 5.1", ["4.2", "5.0", "5.1"]),
        ("Django<5.0, Django!=4.1, python-django 3.2, my.django 2.2", []),
        (None, []),
        (["Django 5.2"], []),
    ],
)
def test_django_mentions(text, found):
    assert pypi.django_mentions(text) == found


def test_metadata_that_is_not_text_is_ignored(monkeypatch, sleeps):
    odd = json.loads(json.dumps(PROJECT))
    odd["info"] |= {"project_urls": ["x"], "home_page": 3, "description": {"a": 1}}
    odd["urls"] = [{"filename": 7}, "x", {"filename": "y.whl"}]
    serve(monkeypatch, odd)
    info = PyPI("https://pypi.test/pypi").project("django-x").latest
    assert (info.project_urls, info.home_page, info.django_mentions) == ((), None, ())
    assert (info.wheel_tags, info.has_sdist) == ((), False)


def test_unknown_links_and_files_stay_unknown(monkeypatch, sleeps):
    """Old setuptools wrote UNKNOWN; a mirror without ``urls`` does not know the files."""
    legacy = json.loads(json.dumps(PROJECT))
    legacy["info"] |= {"home_page": "UNKNOWN", "project_urls": {"Homepage": "UNKNOWN"}}
    serve(monkeypatch, legacy)
    info = PyPI("https://pypi.test/pypi").project("django-x").latest
    assert (info.home_page, info.project_urls, info.has_sdist) == (None, (), None)
