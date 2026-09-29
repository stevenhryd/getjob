"""Sesi browser Playwright dengan profil persisten (login tersimpan)."""

from __future__ import annotations

import logging
import os
import random
import sys
import time
from pathlib import Path

from filelock import FileLock, Timeout

from .config import AppConfig

log = logging.getLogger(__name__)


class BrowserBusy(RuntimeError):
    """Profil browser sedang dipakai proses lain (mis. `getjob login`)."""


def display_available() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


class BrowserSession:
    """Context manager: satu Chromium dengan profil tersimpan di data/browser.

    Semua akun (LinkedIn, JobStreet, Glints, Kalibrr) login di profil yang sama,
    jadi cukup login sekali lewat `getjob login`.
    """

    def __init__(self, cfg: AppConfig, *, headless: bool | None = None, lock_timeout: float | None = None):
        self.cfg = cfg
        want_headless = cfg.browser.headless if headless is None else headless
        if not want_headless and not display_available():
            log.warning("Tidak ada DISPLAY; browser dijalankan headless.")
            want_headless = True
        self.headless = want_headless
        self.lock = FileLock(str(cfg.data_path / "browser.lock"))
        self.lock_timeout = cfg.browser.lock_timeout_seconds if lock_timeout is None else lock_timeout
        self._pw = None
        self.context = None

    def __enter__(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright

        self.cfg.ensure_dirs()
        try:
            self.lock.acquire(timeout=self.lock_timeout)
        except Timeout as exc:
            raise BrowserBusy("Browser sedang dipakai proses lain (misalnya `getjob login`).") from exc
        try:
            self._pw = sync_playwright().start()
            kwargs = dict(
                user_data_dir=str(self.cfg.browser_profile_dir),
                headless=self.headless,
                slow_mo=self.cfg.browser.slow_mo_ms,
                viewport={"width": 1366, "height": 900},
                locale="id-ID",
                timezone_id=self.cfg.schedule.timezone,
            )
            if self.cfg.browser.executable_path:
                kwargs["executable_path"] = self.cfg.browser.executable_path
            self.context = self._pw.chromium.launch_persistent_context(**kwargs)
            self.context.set_default_timeout(self.cfg.browser.timeout_ms)
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def new_page(self):
        assert self.context is not None
        return self.context.new_page()

    def __exit__(self, *exc) -> None:
        try:
            if self.context is not None:
                self.context.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self.context = None
        self._pw = None
        if self.lock.is_locked:
            self.lock.release()


def human_pause(a: float = 0.6, b: float = 1.8) -> None:
    time.sleep(random.uniform(a, b))


def screenshot(page, directory: Path, name: str) -> str | None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.png"
        page.screenshot(path=str(path), full_page=False)
        return path.name
    except Exception as exc:
        log.debug("Screenshot gagal: %s", exc)
        return None
