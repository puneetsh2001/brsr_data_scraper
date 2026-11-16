"""Offline tests for app.core.http_client against a local HTTP server."""

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import Settings
from app.core.http_client import AccessDenied, HttpClient

STATE = {"flaky": 0, "ratelimited": 0, "hits": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        STATE["hits"].append((self.path, time.perf_counter()))
        if self.path == "/flaky":
            STATE["flaky"] += 1
            return self._send(503) if STATE["flaky"] <= 2 else \
                self._send(200, b"ok-after-retries")
        if self.path == "/cdn530":
            return self._send(530)
        if self.path == "/ratelimited":
            STATE["ratelimited"] += 1
            if STATE["ratelimited"] == 1:
                return self._send(429, headers={"Retry-After": "1"})
            return self._send(200, b"after-retry-after")
        if self.path == "/missing":
            return self._send(404)
        if self.path == "/forbidden":
            return self._send(403)
        if self.path.startswith("/data"):
            return self._send(200, b"<xbrl>data</xbrl>")
        return self._send(200, b"hello")


def hits(path):
    return sum(1 for p, _ in STATE["hits"] if p == path)


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def client(tmp_path):
    cfg = Settings(http_backoff_base=0.05, http_backoff_max=2.0,
                   http_max_retries=3, http_min_interval_default=0.3,
                   cache_dir=tmp_path / "cache", log_dir=tmp_path / "logs",
                   http_cache_ttl_hours=1.0)
    return HttpClient(settings=cfg, cache_namespace="test")


def test_retry_on_503(server, client):
    r = client.get(server + "/flaky")
    assert r.status_code == 200 and STATE["flaky"] == 3


def test_persistent_530_raises_after_retries(server, client):
    with pytest.raises(Exception):
        client.get(server + "/cdn530")
    assert hits("/cdn530") == 4


def test_retry_after_honoured(server, client):
    start = time.monotonic()
    r = client.get(server + "/ratelimited")
    assert r.status_code == 200 and time.monotonic() - start >= 0.9


def test_404_not_retried(server, client):
    with pytest.raises(Exception):
        client.get(server + "/missing")
    assert hits("/missing") == 1


def test_403_is_access_denied_not_bypassed(server, client):
    with pytest.raises(AccessDenied):
        client.get(server + "/forbidden")
    assert hits("/forbidden") == 1


def test_per_host_rate_limit(server, client):
    client.get(server + "/a")
    client.get(server + "/b")
    times = [t for p, t in STATE["hits"] if p in ("/a", "/b")]
    # server arrival times carry thread-scheduling jitter
    assert times[1] - times[0] >= 0.25


def test_throttle_guarantees_interval(client):
    for _ in range(5):
        start = time.perf_counter()
        client._throttle("http://example.test/x")
        client._throttle("http://example.test/y")
        assert time.perf_counter() - start >= 0.3
    # a different host is not delayed by this one
    start = time.perf_counter()
    client._throttle("http://other.test/x")
    assert time.perf_counter() - start < 0.05


def test_cache_hit(server, client):
    client.get(server + "/cached", use_cache=True)
    before = len(STATE["hits"])
    r2 = client.get(server + "/cached", use_cache=True)
    assert len(STATE["hits"]) == before
    assert getattr(r2, "from_cache", False) and r2.text == "hello"


def test_download_atomic_no_overwrite(server, client, tmp_path):
    dest = tmp_path / "dl" / "file.xml"
    res = client.download(server + "/data1", dest,
                          validate=lambda c: c.replace(b"data", b"DATA"))
    assert res["status"] == "SUCCESS" and len(res["sha256"]) == 64
    assert dest.read_bytes() == b"<xbrl>DATA</xbrl>"
    res2 = client.download(server + "/data2", dest)
    assert res2["status"] == "ALREADY_DOWNLOADED"
    assert dest.read_bytes() == b"<xbrl>DATA</xbrl>"
    assert not list(dest.parent.glob("*.part"))
