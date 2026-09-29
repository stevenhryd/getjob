"""Kerangka umum sumber lowongan."""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Iterable
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:  # pragma: no cover
    from playwright.sync_api import Page

    from ..config import AppConfig

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)


@dataclass
class JobPosting:
    source: str
    external_id: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    description: str = ""
    apply_url: str | None = None
    apply_method: str | None = None
    is_remote: bool | None = None
    salary: str | None = None
    posted_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class SourceError(RuntimeError):
    pass


class HttpClient:
    """httpx dengan retry sederhana & jeda sopan antar request."""

    def __init__(
        self,
        *,
        min_delay: float = 1.0,
        max_delay: float = 2.5,
        retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            timeout=httpx.Timeout(25.0, connect=10.0),
            follow_redirects=True,
            transport=transport,
        )
        self.min_delay, self.max_delay, self.retries = min_delay, max_delay, retries
        self._sleep = sleep
        self._last = 0.0

    def _polite_wait(self) -> None:
        wait = random.uniform(self.min_delay, self.max_delay) - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            self._polite_wait()
            try:
                resp = self.client.request(method, url, **kwargs)
                self._last = time.monotonic()
            except httpx.HTTPError as exc:
                self._last = time.monotonic()
                last_exc = exc
                self._sleep(2 ** attempt * 2)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else 2 ** attempt * 5
                last_exc = SourceError(f"HTTP {resp.status_code} dari {urlparse(url).netloc}")
                self._sleep(min(delay, 60))
                continue
            return resp
        raise SourceError(str(last_exc) if last_exc else f"Gagal mengakses {url}")

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self.client.close()


class SourceContext:
    """Sumber daya bersama selama satu siklus pencarian."""

    def __init__(self, config: "AppConfig", http: HttpClient, page_factory: Callable[[], "Page"] | None = None):
        self.config = config
        self.http = http
        self._page_factory = page_factory
        self._page: "Page | None" = None

    @property
    def has_browser(self) -> bool:
        return self._page_factory is not None

    def page(self) -> "Page":
        if self._page_factory is None:
            raise SourceError("Sumber ini butuh browser, tapi browser tidak tersedia.")
        if self._page is None or self._page.is_closed():
            self._page = self._page_factory()
        return self._page


class JobSource(ABC):
    name: str = ""
    needs_browser: bool = False

    def __init__(self, ctx: SourceContext):
        self.ctx = ctx
        self.config = ctx.config

    @property
    def http(self) -> HttpClient:
        return self.ctx.http

    def queries(self) -> Iterable[tuple[str, str | None]]:
        """Pasangan (kata kunci, lokasi) yang dicari. Default: kata kunci x lokasi."""
        locations = self.config.search.locations or [None]
        for kw in self.config.search.keywords:
            for loc in locations:
                yield kw, loc

    @abstractmethod
    def search(self, keyword: str, location: str | None) -> list[JobPosting]:
        ...

    def fetch_details(self, job: JobPosting) -> JobPosting:
        """Lengkapi deskripsi (opsional). Default: tidak melakukan apa-apa."""
        return job


def apply_method_for_url(url: str | None) -> str | None:
    """Tentukan applier berdasarkan URL lamaran eksternal (untuk lowongan remote)."""
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if host.endswith("greenhouse.io"):
        return "greenhouse"
    if host == "jobs.lever.co":
        return "lever"
    return None
