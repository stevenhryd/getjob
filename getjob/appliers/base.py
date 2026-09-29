"""Kerangka applier: alur 'wizard' (klik Lamar -> isi -> Lanjut ... -> Kirim) untuk semua platform."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from ..answers import AnswerEngine, JobContext
from ..browser import human_pause, screenshot
from ..config import AppConfig
from ..db import Job
from .form import FillReport, FormFiller

log = logging.getLogger(__name__)


class Outcome(str, Enum):
    APPLIED = "applied"        # terkirim
    TESTED = "tested"          # dry-run: form terisi sampai tombol kirim, tidak dikirim
    ALREADY = "already"        # sudah pernah dilamar di platform
    MANUAL = "manual"          # butuh tindakan manual (lamaran eksternal / pertanyaan tak terjawab)
    FAILED = "failed"          # error tak terduga (akan dicoba lagi)
    NEEDS_LOGIN = "needs_login"
    BLOCKED = "blocked"        # captcha / verifikasi keamanan


@dataclass
class ApplyResult:
    outcome: Outcome
    message: str = ""
    screenshot: str | None = None
    filled: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class ApplyContext:
    cfg: AppConfig
    engine: AnswerEngine
    dry_run: bool = True


CHALLENGE_URL_RE = re.compile(r"checkpoint|captcha|/challenge|/authwall", re.I)
LOGIN_URL_RE = re.compile(r"/login|/signin|/sign-in|/oauth|/authenticate|/uas/login", re.I)
CAPTCHA_FRAME_RE = re.compile(r"recaptcha/.*bframe|hcaptcha|challenges\.cloudflare\.com|arkoselabs|funcaptcha", re.I)
ERROR_SELECTORS = (
    ".artdeco-inline-feedback--error",
    "[role=alert]",
    "[aria-invalid=true]",
    ".field-error",
    ".error-message",
    "[class*='errorMessage']",
    "[class*='error-text']",
)


def _text_re(texts: list[str]) -> re.Pattern:
    return re.compile(r"^\s*(" + "|".join(re.escape(t) for t in texts) + r")\s*$", re.I)


class Applier:
    platform = ""
    login_url = ""
    home_url = ""
    requires_login = True

    # --- konfigurasi alur wizard (dioverride per platform) ---
    apply_button_selectors: list[str] = []
    apply_button_texts: list[str] = []
    root_selector: str | None = None
    root_required = False  # True: berhenti kalau root (mis. modal) tidak muncul
    next_selectors: list[str] = []
    next_texts: list[str] = ["Next", "Continue", "Lanjut", "Lanjutkan", "Selanjutnya", "Berikutnya"]
    review_selectors: list[str] = []
    review_texts: list[str] = ["Review", "Tinjau"]
    submit_selectors: list[str] = []
    submit_texts: list[str] = ["Submit application", "Submit", "Kirim lamaran", "Kirim"]
    success_texts: list[str] = []
    success_url_re: re.Pattern | None = None
    already_texts: list[str] = []
    upload_optional_resume = True

    def __init__(self, ctx: ApplyContext):
        self.ctx = ctx
        self.cfg = ctx.cfg

    # ---- hooks --------------------------------------------------------------------
    def start_url(self, job: Job) -> str:
        return job.apply_url or job.url

    def is_logged_out(self, page: Any) -> bool:
        return bool(LOGIN_URL_RE.search(page.url))

    def before_fill(self, page: Any, root: Any) -> None:
        pass

    def discard(self, page: Any) -> None:
        pass

    def check_login(self, page: Any) -> bool:
        """Buka beranda platform dan periksa status login."""
        if not self.requires_login:
            return True
        page.goto(self.home_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        return not self.is_logged_out(page) and not CHALLENGE_URL_RE.search(page.url)

    # ---- util --------------------------------------------------------------------
    def job_context(self, job: Job) -> JobContext:
        return JobContext(
            title=job.title,
            company=job.company,
            location=job.location,
            description=job.description,
            salary=job.salary,
            is_remote=job.is_remote,
            source=job.source,
            cover_letter=job.cover_letter or "",
        )

    def resume_for(self, job: Job):
        p = self.cfg.profile
        if job.language == "en" and p.resume_path_en:
            path = self.cfg.path(p.resume_path_en)
            if path.exists():
                return path
        return self.cfg.path(p.resume_path)

    def find_button(self, scope: Any, selectors: list[str], texts: list[str]) -> Any | None:
        for sel in selectors:
            loc = scope.locator(sel)
            for i in range(min(loc.count(), 5)):
                el = loc.nth(i)
                if el.is_visible() and el.is_enabled():
                    return el
        if texts:
            rx = _text_re(texts)
            for role in ("button", "link"):
                loc = scope.get_by_role(role, name=rx)
                for i in range(min(loc.count(), 5)):
                    el = loc.nth(i)
                    if el.is_visible() and el.is_enabled():
                        return el
        return None

    def text_visible(self, page: Any, texts: list[str]) -> bool:
        if not texts:
            return False
        rx = re.compile("|".join(re.escape(t) for t in texts), re.I)
        loc = page.get_by_text(rx)
        for i in range(min(loc.count(), 5)):
            if loc.nth(i).is_visible():
                return True
        return False

    def challenge(self, page: Any) -> str | None:
        if CHALLENGE_URL_RE.search(page.url):
            return f"Halaman verifikasi keamanan: {page.url}"
        for frame in page.frames:
            if frame.url and CAPTCHA_FRAME_RE.search(frame.url):
                try:
                    el = frame.frame_element()
                    box = el.bounding_box()
                except Exception:
                    continue
                if box and box["width"] >= 250 and box["height"] >= 250 and el.is_visible():
                    return "Captcha muncul"
        return None

    def visible_errors(self, scope: Any) -> list[str]:
        errors: list[str] = []
        for sel in ERROR_SELECTORS:
            loc = scope.locator(sel)
            for i in range(min(loc.count(), 8)):
                el = loc.nth(i)
                try:
                    if el.is_visible():
                        t = (el.inner_text(timeout=1000) or el.get_attribute("aria-label") or "").strip()
                        if t:
                            errors.append(t[:150])
                except Exception:
                    continue
        return list(dict.fromkeys(errors))

    def shot(self, page: Any, job: Job, tag: str) -> str | None:
        return screenshot(page, self.cfg.screenshots_dir, f"job{job.id}-{self.platform}-{tag}")

    def _root(self, page: Any) -> Any | None:
        if not self.root_selector:
            return None
        loc = page.locator(self.root_selector)
        for i in range(min(loc.count(), 5)):
            if loc.nth(i).is_visible():
                return loc.nth(i)
        return None

    @staticmethod
    def _signature(page: Any, root: Any | None) -> str:
        try:
            text = (root or page.locator("body")).inner_text(timeout=3000)
        except Exception:
            text = ""
        return f"{page.url}|{text[:3000]}"

    def _manual(self, page: Any, job: Job, message: str, report: FillReport | None = None) -> ApplyResult:
        shot = self.shot(page, job, "manual")
        try:
            self.discard(page)
        except Exception:
            pass
        return ApplyResult(Outcome.MANUAL, message, shot, report.filled if report else [])

    # ---- alur utama -------------------------------------------------------------------
    def apply(self, page: Any, job: Job) -> ApplyResult:
        start = self.start_url(job)
        start_host = urlparse(start).netloc
        page.goto(start, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        if (c := self.challenge(page)) is not None:
            return ApplyResult(Outcome.BLOCKED, c, self.shot(page, job, "blocked"))
        if self.requires_login and self.is_logged_out(page):
            return ApplyResult(Outcome.NEEDS_LOGIN, f"Belum login ke {self.platform}", self.shot(page, job, "login"))
        if self.text_visible(page, self.already_texts):
            return ApplyResult(Outcome.ALREADY, "Sudah pernah dilamar di platform ini")

        if self.apply_button_selectors or self.apply_button_texts:
            btn = self.find_button(page, self.apply_button_selectors, self.apply_button_texts)
            if btn is None:
                return self._manual(page, job, "Tombol lamar cepat tidak ditemukan (kemungkinan lamaran di situs perusahaan)")
            pages_before = len(page.context.pages)
            btn.click()
            page.wait_for_timeout(2500)
            if len(page.context.pages) > pages_before:
                for extra in page.context.pages[pages_before:]:
                    extra.close()
                return self._manual(page, job, "Lamaran dibuka di situs perusahaan (eksternal)")
            new_host = urlparse(page.url).netloc
            if new_host != start_host and not LOGIN_URL_RE.search(page.url):
                return self._manual(page, job, f"Diarahkan ke situs eksternal: {new_host}")
            if self.is_logged_out(page):
                return ApplyResult(Outcome.NEEDS_LOGIN, f"Belum login ke {self.platform}", self.shot(page, job, "login"))

        filler = FormFiller(
            page,
            self.ctx.engine,
            self.job_context(job),
            resume_path=self.resume_for(job),
            files_dir=self.cfg.files_dir,
            job_key=str(job.id),
            upload_optional_resume=self.upload_optional_resume,
        )
        report = FillReport()
        stuck = 0
        for _step in range(self.cfg.apply.max_steps):
            if (c := self.challenge(page)) is not None:
                shot = self.shot(page, job, "blocked")
                return ApplyResult(Outcome.BLOCKED, c, shot, report.filled)
            root = self._root(page)
            if self.root_selector and root is None and _step == 0:
                page.wait_for_timeout(2000)
                root = self._root(page)
            if root is None and self.root_required:
                return self._manual(page, job, "Jendela form lamaran tidak terbuka", report)
            scope = root or page
            self.before_fill(page, root)
            step_report = filler.fill(root, aggressive=stuck > 0)
            report.merge(step_report)
            human_pause(0.4, 1.0)

            submit = self.find_button(scope, self.submit_selectors, self.submit_texts)
            if submit is not None:
                if report.unresolved:
                    return self._manual(
                        page, job, "Pertanyaan wajib belum terjawab: " + "; ".join(report.unresolved[:5]), report
                    )
                if self.ctx.dry_run:
                    shot = self.shot(page, job, "dryrun")
                    try:
                        self.discard(page)
                    except Exception:
                        pass
                    return ApplyResult(Outcome.TESTED, "Uji coba: form lengkap, tidak dikirim (dry_run)", shot, report.filled)
                submit.click()
                page.wait_for_timeout(3500)
                if self._succeeded(page, root):
                    return ApplyResult(Outcome.APPLIED, "Lamaran terkirim", self.shot(page, job, "sent"), report.filled)
                errors = self.visible_errors(self._root(page) or page)
                if errors:
                    stuck += 1
                    if stuck >= 2:
                        return self._manual(page, job, "Form ditolak saat dikirim: " + "; ".join(errors[:4]), report)
                    continue
                return ApplyResult(
                    Outcome.APPLIED, "Tombol kirim diklik (konfirmasi tidak terdeteksi, cek screenshot)",
                    self.shot(page, job, "sent"), report.filled,
                )

            nxt = self.find_button(scope, self.review_selectors, self.review_texts) or self.find_button(
                scope, self.next_selectors, self.next_texts
            )
            if nxt is None:
                return self._manual(page, job, "Tombol lanjut/kirim tidak ditemukan", report)
            sig = self._signature(page, root)
            nxt.click()
            page.wait_for_timeout(1800)
            errors = self.visible_errors(self._root(page) or page)
            if errors or self._signature(page, self._root(page)) == sig:
                stuck += 1
                if stuck >= 3:
                    detail = "; ".join(errors[:4] or report.unresolved[:4]) or "tidak ada perubahan"
                    return self._manual(page, job, f"Form tidak bisa dilanjutkan: {detail}", report)
            else:
                stuck = 0
        return self._manual(page, job, "Terlalu banyak langkah di form lamaran", report)

    def _succeeded(self, page: Any, root: Any | None) -> bool:
        if self.success_url_re and self.success_url_re.search(page.url):
            return True
        return self.text_visible(page, self.success_texts)
