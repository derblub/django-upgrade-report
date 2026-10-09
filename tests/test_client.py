"""The generic JSON client that the PyPI client and later the GitHub client share."""

from __future__ import annotations

import os
import time

import pytest
from conftest import http_error, serve

from django_upgrade_report import client
from django_upgrade_report.client import FetchError, JsonClient

URL = "https://api.test/x"


def limited(reset_in: float | None = None, **headers: str) -> Exception:
    """GitHub's answer when a rate limit is used up."""
    if reset_in is not None:
        headers["X-RateLimit-Reset"] = str(int(time.time() + reset_in))
    return http_error(403, {"X-RateLimit-Remaining": "0", **headers})


def test_any_json_is_an_answer(monkeypatch, sleeps):
    """An API that answers with a list (GitHub's contents API) is not rejected."""
    serve(monkeypatch, [{"name": "ci.yml"}])
    assert JsonClient("https://api.test")._get(URL) == [{"name": "ci.yml"}]


def test_null_is_not_taken_for_not_found(monkeypatch, sleeps):
    fake = serve(monkeypatch, b"null")
    with pytest.raises(FetchError, match="unexpected answer, null"):
        JsonClient("https://api.test")._get(URL)
    assert len(fake.requests) == 1


def test_extra_headers_are_sent(monkeypatch, sleeps):
    fake = serve(monkeypatch, {})
    JsonClient("https://api.test", headers={"Authorization": "Bearer t0k"})._get(URL)
    assert fake.requests[0].get_header("Authorization") == "Bearer t0k"
    assert fake.requests[0].get_header("User-agent") == client.USER_AGENT


def test_server_errors_are_retried_and_not_found_is_none(monkeypatch, sleeps):
    fake = serve(monkeypatch, http_error(503), {"ok": True})
    assert JsonClient("https://api.test")._get(URL) == {"ok": True}
    serve(monkeypatch, http_error(404))
    assert JsonClient("https://api.test")._get(URL) is None
    assert len(fake.requests) == 2


def test_errors_never_show_the_credentials(monkeypatch, sleeps):
    serve(monkeypatch, http_error(401))
    api = JsonClient("https://me:s3cr3t@api.test")
    with pytest.raises(FetchError) as info:
        api._get("https://api.test/x?token=s3cr3t")
    assert "s3cr3t" not in str(info.value)


# --- rate limits ------------------------------------------------------------------


def test_rate_limit_waits_for_the_reset(monkeypatch, sleeps):
    fake = serve(monkeypatch, limited(reset_in=10), {"ok": True})
    assert JsonClient("https://api.test")._get(URL) == {"ok": True}
    assert len(fake.requests) == 2
    assert 8 <= sleeps[0] <= 10


def test_rate_limit_far_away_fails_at_once(monkeypatch, sleeps):
    fake = serve(monkeypatch, limited(reset_in=2700))
    with pytest.raises(FetchError, match="HTTP 403 Nope, rate limit exceeded"):
        JsonClient("https://api.test")._get(URL)
    assert len(fake.requests) == 1
    assert sleeps == []


def test_rate_limit_reset_already_passed_backs_off(monkeypatch, sleeps):
    """A reset in the past (a fast local clock) must not mean asking again at once."""
    serve(monkeypatch, limited(reset_in=-60), {})
    JsonClient("https://api.test")._get(URL)
    assert 1 <= sleeps[0] < 2


def test_secondary_rate_limit_honours_retry_after(monkeypatch, sleeps):
    secondary = http_error(403, {"Retry-After": "12", "X-RateLimit-Remaining": "4999"})
    fake = serve(monkeypatch, secondary, {})
    JsonClient("https://api.test")._get(URL)
    assert len(fake.requests) == 2
    assert sleeps == [12.0]


def test_forbidden_without_rate_limit_is_not_retried(monkeypatch, sleeps):
    fake = serve(monkeypatch, http_error(403, {"X-RateLimit-Remaining": "12"}))
    with pytest.raises(FetchError, match="HTTP 403"):
        JsonClient("https://api.test")._get(URL)
    assert len(fake.requests) == 1


# --- cache ------------------------------------------------------------------------


class Expiring(JsonClient):
    def _ttl(self, url):
        return None if url.endswith("/forever") else 60


@pytest.mark.parametrize(("path", "fresh"), [("/forever", True), ("/hourly", False)])
def test_ttl_per_url(monkeypatch, sleeps, tmp_path, path, fresh):
    url = f"https://api.test{path}"
    serve(monkeypatch, {"v": 1})
    Expiring("https://api.test", cache_dir=tmp_path)._get(url)
    (cached,) = tmp_path.iterdir()
    old = time.time() - 3600
    os.utime(cached, (old, old))
    fake = serve(monkeypatch, {"v": 2})
    assert Expiring("https://api.test", cache_dir=tmp_path)._get(url) == {"v": 1 if fresh else 2}
    assert len(fake.requests) == (0 if fresh else 1)


class Strict(JsonClient):
    def _validate(self, data):
        if not isinstance(data, dict) or "v" not in data:
            raise client.UnexpectedAnswer("unexpected answer")


def test_a_cache_entry_that_is_not_valid_is_fetched_again(monkeypatch, sleeps, tmp_path):
    serve(monkeypatch, {"other": "shape"})
    JsonClient("https://api.test", cache_dir=tmp_path)._get(URL)
    fake = serve(monkeypatch, {"v": 2})
    assert Strict("https://api.test", cache_dir=tmp_path)._get(URL) == {"v": 2}
    assert len(fake.requests) == 1


def test_an_answer_is_kept_in_memory(monkeypatch, sleeps):
    fake = serve(monkeypatch, {"v": 1})
    api = JsonClient("https://api.test")
    assert api._get(URL) == api._get(URL)
    assert len(fake.requests) == 1


# --- offline and prefer-cache -----------------------------------------------------


def stale(cache_dir):
    old = time.time() - 30 * 24 * 3600
    for path in cache_dir.iterdir():
        os.utime(path, (old, old))
    return old


def test_offline_reads_old_answers_and_never_the_network(monkeypatch, sleeps, tmp_path):
    serve(monkeypatch, {"v": 1})
    Expiring("https://api.test", cache_dir=tmp_path)._get("https://api.test/hourly")
    old = stale(tmp_path)
    fake = serve(monkeypatch, {"v": 2})
    api = Expiring("https://api.test", cache_dir=tmp_path, mode=client.OFFLINE)
    assert api._get("https://api.test/hourly") == {"v": 1}
    assert api.oldest_cached == pytest.approx(old)
    with pytest.raises(client.NotCached, match="not in the cache"):
        api._get("https://api.test/other")
    assert fake.requests == []


def test_prefer_cache_asks_only_for_what_is_missing(monkeypatch, sleeps, tmp_path):
    serve(monkeypatch, {"v": 1})
    Expiring("https://api.test", cache_dir=tmp_path)._get("https://api.test/hourly")
    stale(tmp_path)
    fake = serve(monkeypatch, {"v": 2})
    api = Expiring("https://api.test", cache_dir=tmp_path, mode=client.PREFER_CACHE)
    assert api._get("https://api.test/hourly") == {"v": 1}
    assert api._get("https://api.test/other") == {"v": 2}
    assert [r.full_url for r in fake.requests] == ["https://api.test/other"]


def test_not_found_is_cached_for_offline_runs(monkeypatch, sleeps, tmp_path):
    serve(monkeypatch, http_error(404))
    assert JsonClient("https://api.test", cache_dir=tmp_path)._get(URL) is None
    fake = serve(monkeypatch, {"v": 1})
    assert JsonClient("https://api.test", cache_dir=tmp_path, mode=client.OFFLINE)._get(URL) is None
    assert JsonClient("https://api.test", cache_dir=tmp_path)._get(URL) is None  # fresh 24 h
    assert fake.requests == []
