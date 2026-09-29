"""Konfigurasi applier per platform.

Selector & teks tombol di sini adalah bagian yang paling mungkin berubah ketika situs
memperbarui tampilannya. Kalau lamaran di suatu platform mulai gagal, cek screenshot
di dashboard lalu sesuaikan daftar di file ini.
"""

from __future__ import annotations

import re
from typing import Any

from ..db import Job
from .base import Applier


class LinkedInApplier(Applier):
    platform = "linkedin"
    login_url = "https://www.linkedin.com/login"
    home_url = "https://www.linkedin.com/feed/"
    apply_button_selectors = [
        "button.jobs-apply-button[aria-label*='Easy Apply']",
        "button.jobs-apply-button[aria-label*='Lamar Mudah']",
        "button.jobs-apply-button[aria-label*='Lamar Cepat']",
        "button[data-live-test-job-apply-button]",
    ]
    apply_button_texts = ["Easy Apply", "Lamar Mudah", "Lamar Cepat"]
    root_selector = "div.jobs-easy-apply-modal, div[data-test-modal-id='easy-apply-modal'], div[role='dialog']"
    root_required = True
    next_selectors = [
        "button[aria-label='Continue to next step']",
        "button[data-easy-apply-next-button]",
        "button[aria-label*='Lanjut ke langkah berikutnya']",
    ]
    review_selectors = ["button[aria-label='Review your application']", "button[aria-label*='Tinjau lamaran']"]
    submit_selectors = ["button[aria-label='Submit application']", "button[aria-label*='Kirim lamaran']"]
    submit_texts = ["Submit application", "Kirim lamaran"]
    success_texts = ["Your application was sent", "Application sent", "Lamaran Anda telah terkirim", "Lamaran terkirim"]
    already_texts = ["See application", "Lihat lamaran"]
    # LinkedIn sudah menyimpan CV terakhir; upload hanya bila diminta/ada error.
    upload_optional_resume = False

    def start_url(self, job: Job) -> str:
        return job.url

    def is_logged_out(self, page: Any) -> bool:
        if re.search(r"/login|/authwall|/signup|/uas/", page.url):
            return True
        return page.locator("#global-nav, header.global-nav").count() == 0

    def discard(self, page: Any) -> None:
        dismiss = self.find_button(page, ["button[aria-label='Dismiss']", "button[aria-label='Tutup']"], [])
        if dismiss is None:
            return
        dismiss.click()
        page.wait_for_timeout(1000)
        confirm = self.find_button(
            page, ["button[data-control-name='discard_application_confirm_btn']"], ["Discard", "Buang"]
        )
        if confirm is not None:
            confirm.click()
            page.wait_for_timeout(800)


class JobStreetApplier(Applier):
    platform = "jobstreet"
    login_url = "https://id.jobstreet.com/id/oauth/login"
    home_url = "https://id.jobstreet.com/id/"
    next_texts = ["Lanjutkan", "Lanjut", "Continue", "Next"]
    submit_texts = ["Kirim lamaran", "Kirim Lamaran", "Submit application", "Kirim"]
    success_texts = [
        "Lamaran Anda telah dikirim",
        "Lamaran Anda sudah terkirim",
        "Lamaran telah terkirim",
        "Your application has been sent",
        "Application sent",
    ]
    success_url_re = re.compile(r"/apply/success|/success", re.I)
    already_texts = ["Anda telah melamar", "You applied", "Sudah melamar"]
    root_selector = "main"

    def start_url(self, job: Job) -> str:
        return job.url.rstrip("/") + "/apply"

    def is_logged_out(self, page: Any) -> bool:
        if re.search(r"/oauth|/login|/sign-?in|authenticate", page.url, re.I):
            return True
        header_login = page.locator("header").get_by_role("link", name=re.compile(r"^\s*(Masuk|Sign in)\s*$", re.I))
        return header_login.count() > 0 and header_login.first.is_visible()

    def before_fill(self, page: Any, root: Any) -> None:
        # Pilih "Tulis surat lamaran" supaya cover letter bisa diisi.
        scope = root or page
        opt = scope.get_by_label(re.compile(r"Tulis surat lamaran|Write a cover letter", re.I))
        if opt.count() and opt.first.is_visible() and not opt.first.is_checked():
            try:
                opt.first.check(force=True)
                page.wait_for_timeout(600)
            except Exception:
                pass


class GlintsApplier(Applier):
    platform = "glints"
    login_url = "https://glints.com/id/login"
    home_url = "https://glints.com/id/opportunities/jobs/explore"
    apply_button_texts = ["Lamar Cepat", "Lamar Sekarang", "Lamar", "Easy Apply", "Apply", "Apply Now"]
    root_selector = "[role='dialog']"
    next_texts = ["Lanjut", "Selanjutnya", "Next", "Continue"]
    submit_texts = ["Kirim Lamaran", "Kirim", "Submit Application", "Submit"]
    success_texts = ["Lamaran terkirim", "Lamaran berhasil", "Berhasil melamar", "Application submitted", "Lamaranmu"]
    already_texts = ["Sudah Dilamar", "Already Applied", "Lamaran Terkirim"]

    def is_logged_out(self, page: Any) -> bool:
        if re.search(r"/login|/signup", page.url):
            return True
        btn = page.locator("header, nav").get_by_role("link", name=re.compile(r"^\s*(Masuk|Login|Log in)\s*$", re.I))
        return btn.count() > 0 and btn.first.is_visible()


class KalibrrApplier(Applier):
    platform = "kalibrr"
    login_url = "https://www.kalibrr.com/login"
    home_url = "https://www.kalibrr.com/home"
    apply_button_texts = ["Apply Now", "Lamar Sekarang", "Apply for this job", "Apply", "Lamar"]
    root_selector = "[role='dialog']"
    next_texts = ["Next", "Continue", "Lanjut", "Selanjutnya"]
    submit_texts = ["Submit Application", "Submit", "Kirim Lamaran", "Kirim"]
    success_texts = ["Application submitted", "successfully applied", "Lamaran terkirim", "Lamaran berhasil"]
    already_texts = ["You have already applied", "Already applied", "Sudah melamar"]

    def is_logged_out(self, page: Any) -> bool:
        if re.search(r"/login|/signup", page.url):
            return True
        btn = page.get_by_role("link", name=re.compile(r"^\s*(Log in|Login|Masuk|Sign in)\s*$", re.I))
        return btn.count() > 0 and btn.first.is_visible()


class GreenhouseApplier(Applier):
    platform = "greenhouse"
    requires_login = False
    root_selector = "form#application-form, form#application_form, form[action*='applications']"
    submit_selectors = ["button[type=submit]#submit_app", "input#submit_app"]
    submit_texts = ["Submit application", "Submit Application", "Submit"]
    success_texts = ["Thank you for applying", "Thanks for applying", "Application submitted", "received your application"]
    success_url_re = re.compile(r"confirmation|thank", re.I)

    def before_fill(self, page: Any, root: Any) -> None:
        if root is None:  # form tersembunyi di balik tombol "Apply"
            btn = self.find_button(page, [], ["Apply", "Apply for this job", "Apply Now"])
            if btn is not None:
                btn.click()
                page.wait_for_timeout(1500)


class LeverApplier(Applier):
    platform = "lever"
    requires_login = False
    root_selector = "form#application-form, form.application-form, form[action*='apply'], form"
    submit_selectors = ["#btn-submit", "button[data-qa='btn-submit']"]
    submit_texts = ["Submit application", "Submit Application", "Submit"]
    success_texts = ["Application submitted", "Thanks for applying", "Thank you for applying"]
    success_url_re = re.compile(r"/thanks", re.I)

    def start_url(self, job: Job) -> str:
        url = job.apply_url or job.url
        return url if url.rstrip("/").endswith("/apply") else url.rstrip("/") + "/apply"
