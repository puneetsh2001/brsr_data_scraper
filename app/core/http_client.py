"""
Shared HTTP client for all public sources (NSE, BSE, MCA, company sites).

Features
  * retries with exponential backoff + jitter on transient failures
    (429, 500-504, 520-530 CDN errors, connection errors, timeouts)
  * honours Retry-After
  * per-host rate limiting (minimum interval between requests)
  * optional session warm-up (e.g. NSE cookies); re-warmed on 401/403
  * optional on-disk response cache with TTL (for metadata/API calls)
  * atomic, checksummed file downloads that never overwrite existing files
    unless explicitly asked to

Only publicly accessible resources are requested. The client never tries to
bypass authentication, CAPTCHAs or access restrictions: 401/403 after a
session refresh is reported as an access limitation, not retried endlessly.
"""

import hashlib
import json
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.config import settings as default_settings
from app.core.logging_setup import get_logger


RETRY_STATUSES = {429, 500, 502, 503, 504, *range(520, 531)}
AUTH_STATUSES = {401, 403}


class AccessDenied(Exception):
    """Resource requires authentication / is restricted (not bypassed)."""


class DownloadExists(Exception):
    """Target file exists and overwrite was not requested."""


@dataclass
class CachedResponse:
    """Minimal response object returned for cache hits."""
    url: str
    status_code: int
    headers: dict
    content: bytes
    from_cache: bool = True

    @property
    def text(self):
        return self.content.decode(
            self.headers.get("x-cache-encoding", "utf-8"), errors="replace")

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class HttpClient:
    def __init__(self, warmup_url=None, referer=None, cache_namespace=None,
                 settings=None, logger=None, extra_headers=None):
        self.settings = settings or default_settings
        self.log = logger or get_logger(__name__, run_name="http")
        self.warmup_url = warmup_url
        self.cache_dir = (self.settings.cache_dir / cache_namespace
                          if cache_namespace else self.settings.cache_dir)
        self._last_request = {}
        self._lock = threading.Lock()
        self.stats = {"requests": 0, "retries": 0, "cache_hits": 0,
                      "failures": 0, "warmups": 0}

        self.headers = {
            "User-Agent": self.settings.http_user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            self.headers["Referer"] = referer
        if extra_headers:
            self.headers.update(extra_headers)
        self.session = self._new_session()
        self._warmed = False

    # ------------------------------------------------------------------
    def _new_session(self):
        session = requests.Session()
        session.headers.update(self.headers)
        return session

    def warm_up(self):
        """Fetch the warm-up page so the session holds required cookies."""
        if not self.warmup_url:
            return
        self.session = self._new_session()
        self._throttle(self.warmup_url)
        response = self.session.get(self.warmup_url,
                                    timeout=self.settings.http_timeout)
        self.stats["warmups"] += 1
        self._warmed = response.ok
        self.log.info("session warm-up", extra={"fields": {
            "url": self.warmup_url, "status": response.status_code,
            "cookies": len(self.session.cookies)}})

    def _throttle(self, url):
        host = urlparse(url).netloc
        interval = self.settings.min_interval_for(host)
        with self._lock:
            # perf_counter: time.monotonic has ~15 ms resolution on Windows,
            # which let the real gap fall slightly below the interval
            last = self._last_request.get(host)
            if last is not None:
                while (remaining := interval - (time.perf_counter() - last)) > 0:
                    time.sleep(remaining)
            self._last_request[host] = time.perf_counter()

    def _backoff(self, attempt, response=None):
        retry_after = None
        if response is not None:
            header = response.headers.get("Retry-After")
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    try:
                        retry_after = max(0.0, (
                            parsedate_to_datetime(header)
                            - datetime.now(timezone.utc)).total_seconds())
                    except (TypeError, ValueError):
                        retry_after = None
        if retry_after is None:
            retry_after = min(self.settings.http_backoff_max,
                              self.settings.http_backoff_base ** attempt)
            retry_after *= random.uniform(0.75, 1.25)      # jitter
        return min(retry_after, self.settings.http_backoff_max)

    # ---- cache ---------------------------------------------------------
    def _cache_key(self, url, params):
        raw = url + "?" + json.dumps(params or {}, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_read(self, key):
        meta_path = self.cache_dir / f"{key}.json"
        body_path = self.cache_dir / f"{key}.body"
        if not (meta_path.exists() and body_path.exists()):
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        age_hours = (time.time() - meta["stored_at"]) / 3600
        if age_hours > self.settings.http_cache_ttl_hours:
            return None
        return CachedResponse(url=meta["url"], status_code=meta["status"],
                              headers=meta["headers"],
                              content=body_path.read_bytes())

    def _cache_write(self, key, response):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / f"{key}.body").write_bytes(response.content)
        (self.cache_dir / f"{key}.json").write_text(json.dumps({
            "url": response.url, "status": response.status_code,
            "headers": {"content-type": response.headers.get(
                "content-type", ""), "x-cache-encoding":
                response.encoding or "utf-8"},
            "stored_at": time.time(),
        }), encoding="utf-8")

    # ---- requests -----------------------------------------------------
    def get(self, url, params=None, use_cache=False, timeout=None):
        """
        GET with retries. Returns a requests.Response (or CachedResponse).
        Raises AccessDenied for 401/403 that persist after a session
        refresh, and requests.HTTPError for other final failures.
        """

        if use_cache and self.settings.http_cache_enabled:
            key = self._cache_key(url, params)
            cached = self._cache_read(key)
            if cached is not None:
                self.stats["cache_hits"] += 1
                return cached

        if self.warmup_url and not self._warmed:
            self.warm_up()

        refreshed = False
        last_error = None
        max_retries = self.settings.http_max_retries

        for attempt in range(max_retries + 1):
            self._throttle(url)
            self.stats["requests"] += 1
            try:
                response = self.session.get(
                    url, params=params,
                    timeout=timeout or self.settings.http_timeout)
            except (requests.ConnectionError, requests.Timeout) as error:
                last_error = error
                response = None
            else:
                if response.status_code in AUTH_STATUSES:
                    if self.warmup_url and not refreshed:
                        refreshed = True
                        self.log.warning("access denied - refreshing "
                                         "session", extra={"fields": {
                                             "url": url,
                                             "status": response.status_code}})
                        self.warm_up()
                        continue
                    self.stats["failures"] += 1
                    raise AccessDenied(
                        f"HTTP {response.status_code} for {url} - resource "
                        "is restricted; not bypassed")
                if response.status_code not in RETRY_STATUSES:
                    if response.status_code >= 400:
                        self.stats["failures"] += 1
                        response.raise_for_status()
                    if use_cache and self.settings.http_cache_enabled:
                        self._cache_write(self._cache_key(url, params),
                                          response)
                    return response
                last_error = requests.HTTPError(
                    f"HTTP {response.status_code}", response=response)

            if attempt == max_retries:
                break
            wait = self._backoff(attempt + 1, response)
            self.stats["retries"] += 1
            self.log.warning("transient failure - retrying", extra={
                "fields": {"url": url, "attempt": attempt + 1,
                           "wait_seconds": round(wait, 2),
                           "error": str(last_error)}})
            time.sleep(wait)

        self.stats["failures"] += 1
        self.log.error("request failed after retries", extra={"fields": {
            "url": url, "attempts": max_retries + 1,
            "error": str(last_error)}})
        raise last_error

    def download(self, url, destination, validate=None, overwrite=False):
        """
        Download url to destination atomically.

        validate(content: bytes) may raise to reject the payload or return
        replacement bytes (e.g. sanitised XML). Existing files are never
        replaced unless overwrite=True. Returns a dict with status, path,
        bytes and sha256.
        """

        destination = Path(destination)
        if destination.exists() and destination.stat().st_size > 0 \
                and not overwrite:
            return {"status": "ALREADY_DOWNLOADED", "path": str(destination),
                    "bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination)}

        response = self.get(url)
        content = response.content
        if not content:
            raise ValueError("downloaded file is empty")
        if validate is not None:
            replacement = validate(content)
            if isinstance(replacement, (bytes, bytearray)):
                content = bytes(replacement)

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        temporary.write_bytes(content)
        temporary.replace(destination)
        return {"status": "SUCCESS", "path": str(destination),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest()}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nse_api_client(cache_namespace="nse_api"):
    """Client for www.nseindia.com APIs (needs the listing-page cookies)."""
    return HttpClient(warmup_url=default_settings.nse_brsr_listing_page,
                      referer=default_settings.nse_brsr_listing_page,
                      cache_namespace=cache_namespace,
                      extra_headers={"Accept":
                                     "application/json,text/plain,*/*"})


def nse_archive_client():
    """Client for nsearchives.nseindia.com files (XBRL, CSV, ZIP)."""
    return HttpClient(referer="https://www.nseindia.com/",
                      cache_namespace="nse_archive")
