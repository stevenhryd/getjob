"""Tes form & applier dengan Chromium sungguhan terhadap halaman tiruan (tanpa internet)."""

from getjob.answers import AnswerEngine, JobContext
from getjob.appliers import ApplyContext, Outcome
from getjob.appliers.form import FormFiller
from getjob.appliers.platforms import GreenhouseApplier, LinkedInApplier
from getjob.db import Job

from .conftest import FIXTURES

JOB_CTX = JobContext(title="Backend Engineer", company="Remote Co", location="Remote", source="greenhouse",
                     cover_letter="Dear team, ...")


def test_form_filler_greenhouse_like(cfg, db, fake_ai, page):
    page.goto((FIXTURES / "greenhouse_form.html").as_uri())
    filler = FormFiller(page, AnswerEngine(cfg, db, ai=fake_ai), JOB_CTX,
                        resume_path=cfg.path(cfg.profile.resume_path), files_dir=cfg.files_dir)
    report = filler.fill(page.locator("#application-form"))
    assert report.unresolved == []
    assert page.input_value("#first_name") == "Budi"
    assert page.input_value("#last_name") == "Santoso"
    assert page.input_value("#email") == "anda@email.com"
    assert page.input_value("#phone") == "+6281234567890"
    assert page.input_value("#linkedin") == "https://www.linkedin.com/in/username"
    assert page.input_value("#salary") == "30000"  # lowongan luar negeri -> USD
    assert page.eval_on_selector("#exp", "e => e.options[e.selectedIndex].text") == "1-2 years"
    assert page.input_value("#why") == "I love building reliable backend systems."
    assert page.is_checked("#privacy") is True
    assert page.is_checked("#news") is False
    assert page.eval_on_selector("#gender", "e => e.options[e.selectedIndex].text") == "Decline to self-identify"
    assert page.eval_on_selector("#resume", "e => e.files[0].name") == "cv.txt"
    assert fake_ai.answers == ["Why do you want to work here? *"]


def _job(url: str, **kw) -> Job:
    j = Job(id=7, source="linkedin", external_id="1", url=url, title="Backend Engineer", company="ACME",
            location="Jakarta, Indonesia", description="Python", cover_letter="Surat", language="en", apply_method="linkedin")
    for k, v in kw.items():
        setattr(j, k, v)
    return j


def test_linkedin_easy_apply_dry_run(cfg, db, fake_ai, page):
    ctx = ApplyContext(cfg, AnswerEngine(cfg, db, ai=fake_ai), dry_run=True)
    result = LinkedInApplier(ctx).apply(page, _job((FIXTURES / "linkedin_job.html").as_uri()))
    assert result.outcome == Outcome.TESTED, result.message
    assert page.evaluate("window.discarded") is True
    assert page.evaluate("window.submitted") is None
    assert result.screenshot and (cfg.screenshots_dir / result.screenshot).exists()


def test_linkedin_easy_apply_submits(cfg, db, fake_ai, page):
    ctx = ApplyContext(cfg, AnswerEngine(cfg, db, ai=fake_ai), dry_run=False)
    result = LinkedInApplier(ctx).apply(page, _job((FIXTURES / "linkedin_job.html").as_uri()))
    assert result.outcome == Outcome.APPLIED, result.message
    sent = page.evaluate("window.submitted")
    assert sent == {
        "phone": "+6281234567890",
        "email": "anda@email.com",
        "yrs": "3",
        "reloc": "No",
        "spons": "Yes",
        "motiv": "",  # opsional -> tidak diisi AI
        "follow": False,
    }


def test_linkedin_unanswerable_goes_manual(cfg, db, page):
    # Tanpa AI dan nomor HP kosong: pertanyaan wajib tak terjawab -> tidak bisa lanjut -> manual.
    cfg.profile.phone = ""
    ctx = ApplyContext(cfg, AnswerEngine(cfg, db, ai=None), dry_run=False)
    result = LinkedInApplier(ctx).apply(page, _job((FIXTURES / "linkedin_job.html").as_uri()))
    assert result.outcome == Outcome.MANUAL
    assert page.evaluate("window.submitted") is None


def test_linkedin_logged_out_detected(cfg, db, page, tmp_path):
    html = tmp_path / "public.html"
    html.write_text("<html><body><a href='/login'>Sign in</a><h1>Job</h1></body></html>")
    ctx = ApplyContext(cfg, AnswerEngine(cfg, db), dry_run=True)
    result = LinkedInApplier(ctx).apply(page, _job(html.as_uri()))
    assert result.outcome == Outcome.NEEDS_LOGIN


def test_greenhouse_applier_submits(cfg, db, fake_ai, page):
    ctx = ApplyContext(cfg, AnswerEngine(cfg, db, ai=fake_ai), dry_run=False)
    job = _job((FIXTURES / "greenhouse_form.html").as_uri(), source="greenhouse", apply_method="greenhouse",
               apply_url=(FIXTURES / "greenhouse_form.html").as_uri(), location="Remote, APAC")
    result = GreenhouseApplier(ctx).apply(page, job)
    assert result.outcome == Outcome.APPLIED, result.message
    assert page.is_visible("#thanks")
