from contextlib import contextmanager

from sqlalchemy import select

from getjob.appliers import ApplyResult, Outcome
from getjob.db import Job, Status
from getjob.pipeline import Pipeline, within_hours
from getjob.sources.base import JobPosting, JobSource
from getjob.worker import CMD_APPLY, CMD_SEARCH, KEY_PAUSED, Worker

from .conftest import FakeAI

DESC = "We need Python, Django, PostgreSQL and Docker. Minimal 2 tahun pengalaman. " * 12


def posting(i, title, company="ACME", method="linkedin", source="linkedin", desc=DESC):
    return JobPosting(source=source, external_id=str(i), url=f"https://x/{i}", title=title, company=company,
                      location="Jakarta, Indonesia", description=desc, apply_method=method)


class FakeSource(JobSource):
    name = "linkedin"
    items: list[JobPosting] = []

    def queries(self):
        yield "python", None

    def search(self, keyword, location):
        return list(self.items)


class FakePage:
    def is_closed(self):
        return False


@contextmanager
def fake_browser():
    class Session:
        def new_page(self):
            return FakePage()

    yield Session()


class FakeApplier:
    requires_login = True
    queue: list[Outcome] = []
    seen: list[int] = []

    def __init__(self, ctx):
        self.ctx = ctx

    def apply(self, page, job):
        FakeApplier.seen.append(job.id)
        outcome = FakeApplier.queue.pop(0) if FakeApplier.queue else Outcome.APPLIED
        return ApplyResult(outcome, f"hasil {outcome.value}", filled=[("Phone", "+62", "profil")])

    def shot(self, *a):
        return None


def make_pipeline(cfg, db, ai, items):
    FakeSource.items = items
    FakeApplier.queue, FakeApplier.seen = [], []
    return Pipeline(
        cfg, db, ai=ai, browser_factory=fake_browser, sleep=lambda s: None,
        source_classes={"linkedin": FakeSource},
        applier_classes={"linkedin": FakeApplier, "jobstreet": FakeApplier},
    )


def statuses(db, key="title"):
    with db.session() as s:
        return {getattr(j, key): j.status for j in s.scalars(select(Job))}


def test_search_scores_and_routes_hybrid(cfg, db):
    ai = FakeAI(scores={"Python Developer": 90, "Backend Engineer": 70, "Django Dev": 40, "Python Remote": 88,
                        "Python Flagged": 95},
                red_flags={"Python Flagged": ["meminta biaya pendaftaran"]})
    items = [
        posting(1, "Python Developer"),
        posting(2, "Backend Engineer"),
        posting(3, "Django Dev"),
        posting(4, "Python Remote", method=None),          # lamaran eksternal
        posting(5, "Head of Engineering"),                 # difilter judul
        posting(6, "Python Developer", company="PT ACME Tbk", method="jobstreet"),  # duplikat lintas platform
        posting(7, "Python Flagged"),
    ]
    pipe = make_pipeline(cfg, db, ai, items)
    stats = pipe.run_search()
    assert stats["new"] == 7
    st = statuses(db)
    assert statuses(db, "external_id")["1"] == Status.APPROVED
    assert st["Backend Engineer"] == Status.REVIEW
    assert st["Django Dev"] == Status.SKIPPED
    assert st["Python Remote"] == Status.MANUAL
    assert st["Head of Engineering"] == Status.SKIPPED
    assert st["Python Flagged"] == Status.REVIEW
    with db.session() as s:
        dup = s.scalar(select(Job).where(Job.external_id == "6"))
        assert dup.status == Status.SKIPPED and "Duplikat" in dup.status_reason
        approved = s.scalar(select(Job).where(Job.external_id == "1"))
        assert approved.cover_letter == "Cover letter en untuk Python Developer"
        assert approved.score_source == "ai"
    # pencarian kedua tidak menambah duplikat
    assert pipe.run_search()["new"] == 0


def test_mode_manual_and_auto(cfg, db):
    ai = FakeAI(scores={"Python Developer": 95, "Backend Engineer": 65})
    cfg.matching.mode = "manual"
    pipe = make_pipeline(cfg, db, ai, [posting(1, "Python Developer")])
    pipe.run_search()
    assert statuses(db)["Python Developer"] == Status.REVIEW
    cfg.matching.mode = "auto"
    FakeSource.items = [posting(2, "Backend Engineer", company="Other")]
    pipe.run_search()
    assert statuses(db)["Backend Engineer"] == Status.APPROVED


def test_keyword_fallback_without_ai(cfg, db):
    ai = FakeAI()
    ai.available = False
    pipe = make_pipeline(cfg, db, ai, [posting(1, "Backend Developer"), posting(2, "Marketing Staff", desc="Sales target")])
    pipe.run_search()
    with db.session() as s:
        jobs = {j.title: j for j in s.scalars(select(Job))}
    assert jobs["Backend Developer"].score_source == "keyword"
    assert jobs["Backend Developer"].status in (Status.APPROVED, Status.REVIEW)
    letter = jobs["Backend Developer"].cover_letter  # template (deskripsi terdeteksi berbahasa Indonesia)
    assert letter.startswith("Yth. Tim Rekrutmen ACME") and "Budi Santoso" in letter
    assert jobs["Marketing Staff"].status == Status.SKIPPED


def test_apply_outcomes_limits_and_login(cfg, db):
    ai = FakeAI(scores={f"Python Developer {i}": 90 for i in range(6)})
    items = [posting(i, f"Python Developer {i}", company=f"Co{i}") for i in range(6)]
    pipe = make_pipeline(cfg, db, ai, items)
    pipe.run_search()
    cfg.apply.daily_limits["linkedin"] = 3
    FakeApplier.queue = [Outcome.APPLIED, Outcome.FAILED, Outcome.MANUAL]
    stats = pipe.run_apply()
    # Hanya lamaran terkirim yang dihitung ke batas harian: 3 terkirim lalu berhenti.
    assert stats["attempted"] == 5
    assert stats["outcomes"] == {"applied": 3, "failed": 1, "manual": 1}
    assert pipe.applied_today("linkedin") == 3 and pipe.applied_today() == 3
    counts = list(statuses(db).values())
    assert counts.count(Status.APPLIED) == 3
    assert counts.count(Status.MANUAL) == 1
    assert counts.count(Status.APPROVED) == 2  # 1 gagal (dicoba lagi) + 1 tersisa karena batas
    assert pipe.run_apply()["attempted"] == 0  # batas harian tercapai

    cfg.apply.daily_limits["linkedin"] = 10
    FakeApplier.queue = [Outcome.NEEDS_LOGIN]
    stats = pipe.run_apply()
    assert stats["outcomes"] == {"needs_login": 1}  # platform langsung diblok untuk siklus ini
    assert db.platform_state("linkedin")["status"] == "needs_login"
    assert list(statuses(db).values()).count(Status.APPROVED) == 2

    assert pipe.run_apply()["attempted"] == 0  # menunggu login ulang
    db.set_platform_state("linkedin", "ok")
    cfg.apply.max_per_run = 1
    assert pipe.run_apply()["attempted"] == 1

    db.set_platform_paused("linkedin", True)
    assert pipe.run_apply()["attempted"] == 0


def test_dry_run_counts_and_status(cfg, db):
    ai = FakeAI(scores={"Python Developer": 90})
    pipe = make_pipeline(cfg, db, ai, [posting(1, "Python Developer")])
    pipe.run_search()
    FakeApplier.queue = [Outcome.TESTED]
    pipe.run_apply()
    assert statuses(db)["Python Developer"] == Status.TESTED


def test_active_hours():
    from datetime import datetime, timezone

    noon_wib = datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc)  # 12:00 WIB
    night_wib = datetime(2026, 9, 29, 17, 0, tzinfo=timezone.utc)  # 00:00 WIB
    assert within_hours("08:00-21:00", "Asia/Jakarta", noon_wib)
    assert not within_hours("08:00-21:00", "Asia/Jakarta", night_wib)
    assert within_hours("22:00-02:00", "Asia/Jakarta", night_wib)


def test_worker_tick_handles_commands(cfg, db):
    ai = FakeAI(scores={"Python Developer": 90})
    pipe = make_pipeline(cfg, db, ai, [posting(1, "Python Developer")])
    worker = Worker(cfg, db, pipe)
    from getjob.db import utcnow
    from datetime import timedelta

    later = utcnow() + timedelta(hours=1)
    db.kv_set(KEY_PAUSED, True)
    db.kv_set(CMD_SEARCH, True)
    state = {"next_search": later, "next_apply": later}
    worker.tick(state)
    assert statuses(db)["Python Developer"] == Status.APPROVED
    assert db.kv_get(CMD_SEARCH) is None
    db.kv_set(CMD_APPLY, {"job_ids": [1]})
    worker.tick(state)
    assert statuses(db)["Python Developer"] == Status.APPLIED
    assert FakeApplier.seen == [1]
