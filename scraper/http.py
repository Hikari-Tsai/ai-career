"""Polite HTTP client: identifies itself, rate-limits per host, honours Retry-After."""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import requests

USER_AGENT = "Mozilla/5.0 (compatible; ai-career-bot/0.1; +https://github.com/Hikari-Tsai/ai-career)"
MIN_INTERVAL = 1.0  # seconds between requests to the same host
MAX_RETRIES = 3
BACKOFF = (10, 30)  # seconds to wait after a 429/5xx without Retry-After
TRIP_AFTER = 2  # requests that exhaust retries on 429 before we stop calling the host this run

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"})
_last_hit: dict[str, float] = {}
_throttled: dict[str, int] = {}  # host -> number of requests that ended rate-limited
_lock = threading.Lock()


class FetchError(RuntimeError):
    pass


class RateLimited(FetchError):
    """The host keeps answering 429; we back off for the rest of this run."""


def host_tripped(url: str) -> bool:
    return _throttled.get(urlparse(url).netloc, 0) >= TRIP_AFTER


# Tenants of these platforms share infrastructure and rate limits, so pace them as one host.
SHARED_PLATFORMS = ("myworkdayjobs.com", "greenhouse.io", "lever.co", "eightfold.ai", "oraclecloud.com", "smartrecruiters.com")


def _bucket(host: str) -> str:
    return next((p for p in SHARED_PLATFORMS if host == p or host.endswith("." + p)), host)


# Slower pacing for platforms that rate-limit automated clients hard.
PLATFORM_INTERVAL = {"myworkdayjobs.com": 3.0}


def _throttle(host: str) -> None:
    host = _bucket(host)
    interval = PLATFORM_INTERVAL.get(host, MIN_INTERVAL)
    with _lock:
        wait = _last_hit.get(host, 0) + interval - time.monotonic()
        _last_hit[host] = time.monotonic() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)


def request(method: str, url: str, *, json=None, params=None, headers=None, timeout=30) -> requests.Response:
    host = urlparse(url).netloc
    if host_tripped(url):
        raise RateLimited(f"{host}: rate limited earlier in this run, skipping")
    last_status = None
    for attempt in range(MAX_RETRIES):
        _throttle(host)
        try:
            resp = _session.request(method, url, json=json, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES - 1:
                raise FetchError(f"{method} {url}: {exc}") from exc
            time.sleep(2 ** attempt)
            continue
        if resp.status_code in (429, 500, 502, 503, 504):
            last_status = resp.status_code
            if attempt == MAX_RETRIES - 1:
                break
            retry_after = resp.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.isdigit() else BACKOFF[min(attempt, len(BACKOFF) - 1)]
            time.sleep(min(delay, 60))
            continue
        if resp.status_code == 403:  # the site is refusing automated clients: respect it
            with _lock:
                _throttled[host] = _throttled.get(host, 0) + 1
            raise RateLimited(f"{method} {url}: HTTP 403")
        if resp.status_code >= 400:
            raise FetchError(f"{method} {url}: HTTP {resp.status_code}")
        with _lock:
            _throttled.pop(host, None)
        return resp
    if last_status == 429:
        with _lock:
            _throttled[host] = _throttled.get(host, 0) + 1
        raise RateLimited(f"{method} {url}: HTTP 429 after {MAX_RETRIES} attempts")
    raise FetchError(f"{method} {url}: HTTP {last_status} after {MAX_RETRIES} attempts")


def get_json(url: str, **kw):
    return request("GET", url, **kw).json()


def post_json(url: str, payload, **kw):
    return request("POST", url, json=payload, **kw).json()


def get_text(url: str, **kw) -> str:
    return request("GET", url, **kw).text
