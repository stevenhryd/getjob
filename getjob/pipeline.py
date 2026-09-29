"""Pipeline utama: cari -> saring -> nilai -> putuskan -> lamar."""

from __future__ import annotations

import logging
import random
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .answers import AnswerEngine
from .appliers import APPLIERS, ApplyContext, ApplyResult, Outcome
from .browser import BrowserBusy, BrowserSession
from .config import AppConfig
from .db import Database, Job, Run, Status, days_ago, dedupe_key, utcnow
from .matching import AIError, AIUnavailable, ClaudeAI, keyword_score, template_cover_letter
from .sources import SOURCE_CLASSES, HttpClient, JobPosting, SourceContext
from .textutil import detect_language

log = logging.getLogger(__name__)

ACTIVE_STATUSES = (Status.REVIEW, Status.APPROVED, Status.APPLYING, Status.TESTED, *Status.SENT)


def to_posting(job: Job) -> JobPosting:
    return JobPosting(
        source=job.source,
        external_id=job.external_id,
        url=job.url,
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        apply_url=job.apply_url,
        apply_method=job.apply_method,
        is_remote=job.is_remote,
        salary=job.salary,
        posted_at=job.posted_at,
        raw=dict(job.raw or {}),
    )


def within_hours(spec: str, tz_name: str, now: datetime | None = None) -> bool:
    now_local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name))
    start_s, _, end_s = spec.partition("-")
    sh, sm = (int(x) for x in start_s.strip().split(":"))
    eh, em = (int(x) for x in end_s.strip().split(":"))
    cur = now_local.hour * 60 + now_local.minute
    start, end = sh * 60 + sm, eh * 60 + em
    return start <= cur < end if start <= end else (cur >= start or cur < end)


class Pipeline:
    def __init__(
        self,
        cfg: AppConfig,
        db: Database,
        *,
        ai: ClaudeAI | None = None,
        http: HttpClient | None = None,
        browser_factory: Callable[[], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        source_classes: dict | None = None,
        applier_classes: dict | None = None,
    ):
        self.cfg = cfg
        self.db = db
        self.ai = ai if ai is not None else ClaudeAI(cfg, db)
        self.engine = AnswerEngine(cfg, db, self.ai)
        self._http = http
        self.browser_factory = browser_factory or (lambda: BrowserSession(cfg))
        self.sleep = sleep
        self.source_classes = source_classes or SOURCE_CLASSES
        self.applier_classes = applier_classes or APPLIERS
        self._ai_off_reason: str | None = None

    @property
    def http(self) -> HttpClient:
        if self._http is None:
            self._http = HttpClient()
        return self._http

    # ================================================================ util
    def _tz(self) -> str:
        return self.cfg.schedule.timezone

    def _today(self) -> str:
        return datetime.now(ZoneInfo(self._tz())).strftime("%Y-%m-%d")

    def _start_run(self, kind: str) -> int:
        with self.db.session() as s:
            run = Run(kind=kind)
            s.add(run)
            s.flush()
            return run.id

    def _finish_run(self, run_id: int, summary: dict, error: str | None = None) -> None:
        with self.db.session() as s:
            run = s.get(Run, run_id)
            if run:
                run.finished_at = utcnow()
                run.summary = summary
                run.ok = error is None
                run.error = error
                run.duration_s = (run.finished_at - run.started_at).total_seconds()

    # ================================================================ search
    def run_search(self, only: Iterable[str] | None = None) -> dict:
        run_id = self._start_run("search")
        only_set = set(only or [])
        names = [
            n for n in self.cfg.enabled_sources() if n in self.source_classes and (not only_set or n in only_set)
        ]
        stats: dict[str, Any] = {"found": 0, "new": 0, "sources": {}, "errors": {}}
        needs_browser = any(self.source_classes[n].needs_browser for n in names)
        error = None
        try:
            try:
                cm = self.browser_factory() if needs_browser else nullcontext(None)
                with cm as session:
                    self._search_with(names, session, stats)
            except BrowserBusy as exc:
                self.db.log(f"Browser sedang dipakai, Glints dilewati: {exc}", kind="search", level="warning")
                stats["errors"]["browser"] = str(exc)
                self._search_with([n for n in names if not self.source_classes[n].needs_browser], None, stats)
        except Exception as exc:  # jangan biarkan worker mati
            log.exception("Siklus pencarian gagal")
            error = f"{type(exc).__name__}: {exc}"
            self.db.log(f"Siklus pencarian gagal: {error}", kind="search", level="error")
        self._finish_run(run_id, stats, error)
        return stats

    def _search_with(self, names: list[str], session: Any, stats: dict) -> None:
        ctx = SourceContext(self.cfg, self.http, page_factory=session.new_page if session else None)
        for name in names:
            cls = self.source_classes[name]
            if cls.needs_browser and session is None:
                continue
            self._search_source(name, ctx, stats)
        stats["scored"] = self.score_pending(ctx)

    def _search_source(self, name: str, ctx: SourceContext, stats: dict) -> None:
        src = self.source_classes[name](ctx)
        interval = getattr(src, "min_interval_hours", 0)
        last = self.db.kv_get(f"source_last:{name}")
        if interval and last and utcnow() - datetime.fromisoformat(last) < timedelta(hours=interval):
            return
        found = new = 0
        for kw, loc in src.queries():
            try:
                postings = src.search(kw, loc)
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                stats["errors"][name] = msg
                self.db.log(f"[{name}] gagal mencari '{kw}': {msg}", kind="search", level="warning")
                continue
            found += len(postings)
            new += sum(1 for p in postings if self.upsert(p))
        self.db.kv_set(f"source_last:{name}", utcnow().isoformat())
        stats["found"] += found
        stats["new"] += new
        stats["sources"][name] = {"found": found, "new": new}
        self.db.log(f"[{name}] {found} lowongan ditemukan, {new} baru", kind="search")

    def upsert(self, p: JobPosting) -> bool:
        """Simpan lowongan baru. Return True kalau baru."""
        with self.db.session() as s:
            exists = s.scalar(select(Job.id).where(Job.source == p.source, Job.external_id == p.external_id))
            if exists:
                return False
            s.add(
                Job(
                    source=p.source,
                    external_id=p.external_id,
                    url=p.url,
                    apply_url=p.apply_url,
                    apply_method=p.apply_method,
                    title=p.title[:300],
                    company=(p.company or "")[:300],
                    location=(p.location or "")[:300],
                    is_remote=p.is_remote,
                    salary=(p.salary or None) and str(p.salary)[:200],
                    description=p.description or "",
                    posted_at=p.posted_at,
                    dedupe_key=dedupe_key(p.company, p.title),
                    raw=p.raw or {},
                )
            )
            return True

    # ================================================================ scoring
    def score_pending(self, ctx: SourceContext | None = None, limit: int = 500) -> int:
        ctx = ctx or SourceContext(self.cfg, self.http)
        with self.db.session() as s:
            ids = list(s.scalars(select(Job.id).where(Job.status == Status.NEW).order_by(Job.found_at).limit(limit)))
        for job_id in ids:
            try:
                self.score_job(job_id, ctx)
            except Exception as exc:
                log.exception("Gagal menilai job %s", job_id)
                self.db.log(f"Gagal menilai lowongan: {exc}", kind="score", level="error", job_id=job_id)
        return len(ids)

    def _is_duplicate(self, s, job: Job) -> int | None:
        if not job.dedupe_key or job.dedupe_key.startswith("|"):
            return None
        return s.scalar(
            select(Job.id).where(
                Job.dedupe_key == job.dedupe_key,
                Job.id != job.id,
                Job.status.in_(ACTIVE_STATUSES),
                Job.found_at >= days_ago(self.cfg.matching.dedupe_days),
            )
        )

    def _skip(self, job: Job, reason: str, score: int | None = None) -> None:
        job.status = Status.SKIPPED
        job.status_reason = reason
        if score is not None:
            job.score = score
            job.keyword_score = score
            job.score_source = "keyword"

    def score_job(self, job_id: int, ctx: SourceContext) -> str:
        m = self.cfg.matching
        with self.db.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return "missing"
            posting = to_posting(job)
            kw = keyword_score(self.cfg, posting)
            if kw.rejected:
                self._skip(job, f"Filter: {kw.reject_reason}", 0)
                return job.status
            if (dup := self._is_duplicate(s, job)) is not None:
                self._skip(job, f"Duplikat dari lowongan #{dup}")
                return job.status
            if kw.score < m.prefilter_min_score:
                self._skip(job, f"Skor kata kunci rendah ({kw.score})", kw.score)
                return job.status

        # Lengkapi deskripsi di luar sesi DB (bisa lama).
        if len(posting.description) < 600:
            src_cls = self.source_classes.get(posting.source)
            if src_cls is not None and (not src_cls.needs_browser or ctx.has_browser):
                try:
                    posting = src_cls(ctx).fetch_details(posting)
                except Exception as exc:
                    log.info("fetch_details %s gagal: %s", posting.url, exc)
            kw = keyword_score(self.cfg, posting)

        language = detect_language(f"{posting.title}\n{posting.description}")
        score, source = kw.score, "keyword"
        reason = "Skor kata kunci: " + ", ".join(kw.reasons)
        matched, missing, red_flags = kw.matched, kw.missing, []
        if self.ai.available and self._ai_off_reason is None:
            try:
                res = self.ai.score(posting)
                score, source, reason = res.score, "ai", res.reason
                matched, missing, red_flags, language = res.matched, res.missing, res.red_flags, res.language
            except AIUnavailable as exc:
                self._ai_off_reason = str(exc)
                self.db.log(f"AI tidak dipakai: {exc}. Memakai skor kata kunci.", kind="ai", level="warning")
            except AIError as exc:
                self.db.log(f"AI gagal menilai, pakai skor kata kunci: {exc}", kind="ai", level="warning", job_id=job_id)

        can_auto = self._can_auto_apply(posting)
        if score < m.review_threshold:
            status = Status.SKIPPED
            status_reason = f"Skor {score} di bawah ambang review ({m.review_threshold})"
        elif not can_auto:
            status = Status.MANUAL
            status_reason = "Cocok, tapi harus dilamar manual (lamaran eksternal / auto_apply dimatikan)"
        elif red_flags:
            status = Status.REVIEW
            status_reason = "Perlu dicek: " + "; ".join(red_flags)
        elif m.mode == "auto" or (m.mode == "hybrid" and score >= m.auto_apply_threshold):
            status = Status.APPROVED
            status_reason = "Otomatis masuk antrian kirim"
        else:
            status = Status.REVIEW
            status_reason = "Menunggu persetujuan Anda"

        letter = None
        if status in (Status.APPROVED, Status.REVIEW, Status.MANUAL):
            letter = self.make_cover_letter(posting, language, matched)

        with self.db.session() as s:
            job = s.get(Job, job_id)
            job.description = posting.description
            job.apply_method = posting.apply_method
            job.apply_url = posting.apply_url
            job.language = language
            job.keyword_score = kw.score
            job.score = score
            job.score_source = source
            job.score_reason = reason
            job.matched_skills = matched
            job.missing_skills = missing
            job.red_flags = red_flags
            job.status = status
            job.status_reason = status_reason
            if letter:
                job.cover_letter = letter
        if status != Status.SKIPPED:
            self.db.log(
                f"Skor {score} ({source}) -> {Status.LABELS[status]}: {posting.title} @ {posting.company}",
                kind="score",
                job_id=job_id,
            )
        return status

    def _can_auto_apply(self, posting: JobPosting) -> bool:
        if posting.apply_method not in self.applier_classes:
            return False
        toggle = self.cfg.sources.get(posting.source) if posting.source in self.source_classes else None
        return bool(toggle is None or toggle.auto_apply)

    def make_cover_letter(self, posting: Any, language: str, skills: list[str] | None = None) -> str:
        if self.ai.available and self._ai_off_reason is None:
            try:
                return self.ai.cover_letter(posting, language)
            except AIUnavailable as exc:
                self._ai_off_reason = str(exc)
            except AIError as exc:
                log.info("Cover letter AI gagal: %s", exc)
        return template_cover_letter(self.cfg, posting.title, posting.company, language, skills or [])

    def regenerate_cover_letter(self, job_id: int) -> str | None:
        with self.db.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return None
            posting, lang, skills = to_posting(job), job.language or "en", list(job.matched_skills or [])
        letter = self.make_cover_letter(posting, lang, skills)
        with self.db.session() as s:
            s.get(Job, job_id).cover_letter = letter
        return letter

    # ================================================================ apply
    def _count_key(self, platform: str) -> str:
        return f"apply_count:{self._today()}:{platform}"

    def applied_today(self, platform: str | None = None) -> int:
        return int(self.db.kv_get(self._count_key(platform or "_total"), 0) or 0)

    def _limit_left(self, platform: str) -> int:
        a = self.cfg.apply
        per = a.daily_limits.get(platform, a.daily_limit_total)
        return max(0, min(per - self.applied_today(platform), a.daily_limit_total - self.applied_today()))

    def _platform_ready(self, platform: str | None) -> bool:
        if platform not in self.applier_classes:
            return False
        state = self.db.platform_state(platform)
        # Perlu login / captcha: tunggu sampai user menjalankan `getjob login` atau reset di dashboard.
        if state.get("paused") or state.get("status") in ("needs_login", "blocked"):
            return False
        return self._limit_left(platform) > 0

    def run_apply(self, *, job_ids: list[int] | None = None, force: bool = False, limit: int | None = None) -> dict:
        a = self.cfg.apply
        stats: dict[str, Any] = {"attempted": 0, "outcomes": {}}
        if not force and not within_hours(a.active_hours, self._tz()):
            stats["skipped"] = f"di luar jam aktif ({a.active_hours})"
            return stats
        with self.db.session() as s:
            q = select(Job).where(Job.status == Status.APPROVED)
            if job_ids:
                q = q.where(Job.id.in_(job_ids))
            queue = list(s.scalars(q.order_by(Job.score.desc().nullslast(), Job.found_at)))
        queue = [j for j in queue if self._platform_ready(j.apply_method)]
        if limit:
            queue = queue[:limit]
        if not queue:
            return stats

        run_id = self._start_run("apply")
        error = None
        try:
            with self.browser_factory() as session:
                page = session.new_page()
                blocked: set[str] = set()
                for i, job in enumerate(queue):
                    platform = job.apply_method
                    if stats["attempted"] >= a.max_per_run:
                        break
                    if platform in blocked or self._limit_left(platform) <= 0:
                        continue
                    if page.is_closed():
                        page = session.new_page()
                    result = self._apply_one(page, job)
                    stats["attempted"] += 1
                    stats["outcomes"][result.outcome.value] = stats["outcomes"].get(result.outcome.value, 0) + 1
                    if result.outcome in (Outcome.NEEDS_LOGIN, Outcome.BLOCKED):
                        blocked.add(platform)
                        status = "needs_login" if result.outcome == Outcome.NEEDS_LOGIN else "blocked"
                        self.db.set_platform_state(platform, status, result.message)
                    elif result.outcome in (Outcome.APPLIED, Outcome.TESTED, Outcome.ALREADY, Outcome.MANUAL):
                        self.db.set_platform_state(platform, "ok", "")
                    if result.outcome in (Outcome.APPLIED, Outcome.TESTED) and i < len(queue) - 1:
                        lo, hi = a.delay_seconds
                        self.sleep(random.uniform(lo, hi))
        except BrowserBusy as exc:
            stats["skipped"] = str(exc)
        except Exception as exc:
            log.exception("Siklus lamaran gagal")
            error = f"{type(exc).__name__}: {exc}"
            self.db.log(f"Siklus lamaran gagal: {error}", kind="apply", level="error")
        self._finish_run(run_id, stats, error)
        return stats

    def _apply_one(self, page: Any, job: Job) -> ApplyResult:
        platform = job.apply_method
        with self.db.session() as s:
            row = s.get(Job, job.id)
            row.status = Status.APPLYING
            row.attempts += 1
            job = row
        if not job.cover_letter:
            job.cover_letter = self.make_cover_letter(job, job.language or "en", list(job.matched_skills or []))
        applier = self.applier_classes[platform](ApplyContext(self.cfg, self.engine, dry_run=self.cfg.apply.dry_run))
        try:
            result = applier.apply(page, job)
        except Exception as exc:
            log.exception("Applier %s error", platform)
            result = ApplyResult(Outcome.FAILED, f"{type(exc).__name__}: {str(exc)[:300]}", applier.shot(page, job, "error"))
        self._record(job.id, platform, result, job.cover_letter)
        return result

    def _record(self, job_id: int, platform: str, result: ApplyResult, cover_letter: str | None) -> None:
        o = result.outcome
        with self.db.session() as s:
            job = s.get(Job, job_id)
            if cover_letter and not job.cover_letter:
                job.cover_letter = cover_letter
            job.status_reason = result.message
            if result.screenshot:
                job.screenshot = result.screenshot
            if o == Outcome.APPLIED:
                job.status, job.applied_at, job.last_error = Status.APPLIED, utcnow(), None
            elif o == Outcome.TESTED:
                job.status, job.last_error = Status.TESTED, None
            elif o == Outcome.ALREADY:
                job.status = Status.APPLIED
            elif o == Outcome.MANUAL:
                job.status = Status.MANUAL
            elif o in (Outcome.NEEDS_LOGIN, Outcome.BLOCKED):
                job.status = Status.APPROVED
                job.attempts = max(0, job.attempts - 1)
            else:
                job.last_error = result.message
                job.status = Status.APPROVED if job.attempts < self.cfg.apply.max_attempts else Status.FAILED
            title, company, status = job.title, job.company, job.status
        if o in (Outcome.APPLIED, Outcome.TESTED):
            self.db.kv_incr(self._count_key(platform))
            self.db.kv_incr(self._count_key("_total"))
        filled = ", ".join(f"{q[:40]}={v[:30]}" for q, v, _ in result.filled[:12])
        level = "info" if o in (Outcome.APPLIED, Outcome.TESTED, Outcome.ALREADY) else "warning"
        self.db.log(
            f"[{platform}] {Status.LABELS.get(status, status)}: {title} @ {company}. {result.message}"
            + (f" | Diisi: {filled}" if filled else ""),
            kind="apply",
            level=level,
            job_id=job_id,
        )
