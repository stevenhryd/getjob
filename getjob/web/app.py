"""Dashboard web (FastAPI + Jinja2). Semua halaman dilindungi password."""

from __future__ import annotations

import hmac
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from starlette.middleware.sessions import SessionMiddleware

from .. import __version__
from ..config import AppConfig, dashboard_password, secret_key
from ..appliers import AUTO_PLATFORMS
from ..db import Database, Event, Job, QAAnswer, Run, Status, local_day_start_utc, to_local, utcnow
from ..textutil import normalize_question
from ..worker import CMD_APPLY, CMD_SCORE, CMD_SEARCH, KEY_BUSY, KEY_HEARTBEAT, KEY_NEXT_APPLY, KEY_NEXT_SEARCH, KEY_PAUSED

HERE = Path(__file__).parent
PAGE_SIZE = 50
PLATFORM_NAMES = {
    "linkedin": "LinkedIn",
    "jobstreet": "JobStreet",
    "glints": "Glints",
    "kalibrr": "Kalibrr",
    "remoteok": "RemoteOK",
    "remotive": "Remotive",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
}
STATUS_TONE = {
    Status.APPLIED: "good",
    Status.INTERVIEW: "good",
    Status.OFFER: "good",
    Status.TESTED: "info",
    Status.APPROVED: "info",
    Status.APPLYING: "info",
    Status.REVIEW: "warning",
    Status.MANUAL: "serious",
    Status.FAILED: "critical",
    Status.REJECTED: "muted",
    Status.DISMISSED: "muted",
    Status.SKIPPED: "muted",
    Status.NEW: "muted",
}


class _LoginLimiter:
    def __init__(self, max_fail: int = 5, window: int = 600):
        self.max_fail, self.window = max_fail, window
        self.fails: dict[str, list[float]] = defaultdict(list)

    def blocked(self, ip: str) -> bool:
        now = time.time()
        self.fails[ip] = [t for t in self.fails[ip] if now - t < self.window]
        return len(self.fails[ip]) >= self.max_fail

    def fail(self, ip: str) -> None:
        self.fails[ip].append(time.time())

    def reset(self, ip: str) -> None:
        self.fails.pop(ip, None)


def create_app(cfg: AppConfig, db: Database, pipeline: Any | None = None, password: str | None = None) -> FastAPI:
    app = FastAPI(title="GetJob", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=secret_key(), session_cookie="getjob_session",
                       max_age=14 * 24 * 3600, same_site="lax")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    tz = cfg.schedule.timezone
    pw = password if password is not None else dashboard_password()
    limiter = _LoginLimiter()

    if pipeline is None:
        from ..pipeline import Pipeline

        pipeline = Pipeline(cfg, db)

    # ---- template helpers ------------------------------------------------------------
    def fmt_local(dt: datetime | None, fmt: str = "%d %b %H:%M") -> str:
        d = to_local(dt, tz)
        return d.strftime(fmt) if d else "-"

    def ago(dt: datetime | None) -> str:
        if dt is None:
            return "-"
        secs = int((utcnow() - dt).total_seconds())
        if secs < 0:
            secs = -secs
            unit = _humanize(secs)
            return f"dalam {unit}"
        return f"{_humanize(secs)} lalu"

    def iso_local(value: str | None) -> str:
        if not value:
            return "-"
        try:
            return fmt_local(datetime.fromisoformat(value))
        except ValueError:
            return value

    env = templates.env
    env.filters["local"] = fmt_local
    env.filters["iso_local"] = iso_local
    env.filters["ago"] = ago
    env.filters["platform"] = lambda s: PLATFORM_NAMES.get(s or "", s or "-")
    env.globals.update(
        Status=Status,
        STATUS_TONE=STATUS_TONE,
        PLATFORM_NAMES=PLATFORM_NAMES,
        version=__version__,
        cfg=cfg,
    )

    def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        ctx.setdefault("flash", request.session.pop("flash", None))
        ctx.setdefault("nav", name.split(".")[0])
        ctx["review_count"] = _count(Job.status == Status.REVIEW)
        return templates.TemplateResponse(request, name, ctx)

    def flash(request: Request, msg: str) -> None:
        request.session["flash"] = msg

    def back(request: Request, default: str = "/") -> RedirectResponse:
        # Kembali ke halaman asal, tapi hanya path lokal (bukan URL situs lain).
        ref = urlsplit(request.headers.get("referer") or "")
        target = default
        if ref.path.startswith("/") and ref.netloc in ("", request.url.netloc):
            target = ref.path + (f"?{ref.query}" if ref.query else "")
        return RedirectResponse(target, status_code=303)

    def _count(*where) -> int:
        with db.session() as s:
            return int(s.scalar(select(func.count(Job.id)).where(*where)) or 0)

    # ---- auth ------------------------------------------------------------------------
    def require_user(request: Request) -> None:
        if not request.session.get("user"):
            raise HTTPException(status_code=303, headers={"Location": "/login"})

    @app.get("/health")
    def health() -> JSONResponse:
        hb = db.kv_get(KEY_HEARTBEAT)
        return JSONResponse({"ok": True, "worker_heartbeat": hb})

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None, "configured": bool(pw)})

    @app.post("/login")
    def login(request: Request, password: str = Form(...)):
        ip = request.client.host if request.client else "?"
        if not pw:
            return templates.TemplateResponse(request, "login.html", {"error": "Password belum diatur.", "configured": False}, status_code=400)
        if limiter.blocked(ip):
            return templates.TemplateResponse(
                request, "login.html", {"error": "Terlalu banyak percobaan. Coba lagi 10 menit lagi.", "configured": True},
                status_code=429,
            )
        if not hmac.compare_digest(password.encode(), pw.encode()):
            limiter.fail(ip)
            return templates.TemplateResponse(request, "login.html", {"error": "Password salah.", "configured": True}, status_code=401)
        limiter.reset(ip)
        request.session["user"] = "owner"
        return RedirectResponse("/", status_code=303)

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ---- overview --------------------------------------------------------------------------
    def worker_state() -> dict:
        hb = db.kv_get(KEY_HEARTBEAT)
        hb_dt = datetime.fromisoformat(hb) if hb else None
        alive = bool(hb_dt and utcnow() - hb_dt < timedelta(seconds=90))
        nxt_s, nxt_a = db.kv_get(KEY_NEXT_SEARCH), db.kv_get(KEY_NEXT_APPLY)
        return {
            "alive": alive,
            "heartbeat": hb_dt,
            "busy": db.kv_get(KEY_BUSY),
            "paused": bool(db.kv_get(KEY_PAUSED, False)),
            "next_search": datetime.fromisoformat(nxt_s) if nxt_s else None,
            "next_apply": datetime.fromisoformat(nxt_a) if nxt_a else None,
            "pending_cmds": [k for k in (CMD_SEARCH, CMD_APPLY) if db.kv_get(k)],
        }

    def platform_states() -> list[dict]:
        rows = []
        for p in ("linkedin", "jobstreet", "glints", "kalibrr", "greenhouse", "lever"):
            st = db.platform_state(p)
            src_enabled = p in cfg.enabled_sources()
            rows.append({"platform": p, "enabled": src_enabled, **st})
        return rows

    def chart_data() -> dict:
        day0 = local_day_start_utc(tz)
        start = day0 - timedelta(days=29)
        with db.session() as s:
            applied = s.execute(select(Job.applied_at, Job.apply_method).where(Job.applied_at >= start)).all()
            status_counts = dict(s.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all())
            total = sum(status_counts.values())
        per_day: dict[str, int] = {}
        for i in range(30):
            d = to_local(start + timedelta(days=i, hours=12), tz)
            per_day[d.strftime("%Y-%m-%d")] = 0
        per_platform: dict[str, int] = defaultdict(int)
        for applied_at, method in applied:
            key = to_local(applied_at, tz).strftime("%Y-%m-%d")
            if key in per_day:
                per_day[key] += 1
            per_platform[PLATFORM_NAMES.get(method or "", method or "Lainnya")] += 1
        sent = sum(status_counts.get(k, 0) for k in Status.SENT)
        matched = total - status_counts.get(Status.SKIPPED, 0) - status_counts.get(Status.NEW, 0)
        funnel = [
            {"label": "Ditemukan", "value": total},
            {"label": "Cocok (lolos skor)", "value": matched},
            {"label": "Terkirim", "value": sent},
            {"label": "Interview", "value": status_counts.get(Status.INTERVIEW, 0) + status_counts.get(Status.OFFER, 0)},
            {"label": "Tawaran", "value": status_counts.get(Status.OFFER, 0)},
        ]
        return {
            "per_day": [{"label": k, "value": v} for k, v in per_day.items()],
            "per_platform": sorted(({"label": k, "value": v} for k, v in per_platform.items()), key=lambda x: -x["value"]),
            "funnel": funnel,
        }

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_user)])
    def overview(request: Request):
        day0 = local_day_start_utc(tz)
        week = utcnow() - timedelta(days=7)
        applied_today = int(db.kv_get(pipeline._count_key("_total"), 0) or 0)
        tiles = {
            "applied_today": applied_today,
            "sent_total": _count(Job.status.in_(Status.SENT)),
            "review": _count(Job.status == Status.REVIEW),
            "approved": _count(Job.status == Status.APPROVED),
            "manual": _count(Job.status == Status.MANUAL),
            "interview": _count(Job.status.in_((Status.INTERVIEW, Status.OFFER))),
            "found_week": _count(Job.found_at >= week),
            "found_today": _count(Job.found_at >= day0),
        }
        with db.session() as s:
            events = list(s.scalars(select(Event).order_by(Event.id.desc()).limit(12)))
            recent = list(
                s.scalars(select(Job).where(Job.status.in_(Status.SENT)).order_by(Job.applied_at.desc().nullslast()).limit(8))
            )
        return render(
            request,
            "overview.html",
            tiles=tiles,
            worker=worker_state(),
            platforms=platform_states(),
            events=events,
            recent=recent,
            charts=json.dumps(chart_data()),
            ai_available=pipeline.ai.available,
            ai_calls=pipeline.ai.calls_today(),
        )

    @app.get("/api/stats", dependencies=[Depends(require_user)])
    def api_stats():
        return JSONResponse(chart_data())

    # ---- control -------------------------------------------------------------------------------
    @app.post("/control", dependencies=[Depends(require_user)])
    def control(request: Request, action: str = Form(...), platform: str = Form("")):
        if action == "search":
            db.kv_set(CMD_SEARCH, True)
            flash(request, "Pencarian dijadwalkan; worker menjalankannya dalam beberapa detik.")
        elif action == "apply":
            db.kv_set(CMD_APPLY, True)
            flash(request, "Pengiriman antrian dijadwalkan (mengabaikan jam aktif).")
        elif action == "rescore":
            db.kv_set(CMD_SCORE, True)
            flash(request, "Penilaian ulang lowongan baru dijadwalkan.")
        elif action == "pause":
            db.kv_set(KEY_PAUSED, True)
            flash(request, "Jadwal otomatis dijeda.")
        elif action == "resume":
            db.kv_set(KEY_PAUSED, False)
            flash(request, "Jadwal otomatis dilanjutkan.")
        elif action in ("platform_pause", "platform_resume") and platform:
            db.set_platform_paused(platform, action == "platform_pause")
            flash(request, f"{PLATFORM_NAMES.get(platform, platform)} {'dijeda' if action == 'platform_pause' else 'aktif lagi'}.")
        elif action == "platform_reset" and platform:
            db.set_platform_state(platform, "unknown", "Direset dari dashboard")
            flash(request, f"Status {PLATFORM_NAMES.get(platform, platform)} direset.")
        elif action == "requeue_tested":
            with db.session() as s:
                n = 0
                for job in s.scalars(select(Job).where(Job.status == Status.TESTED)):
                    job.status, job.status_reason = Status.APPROVED, "Dimasukkan lagi ke antrian dari uji coba"
                    n += 1
            flash(request, f"{n} lowongan hasil uji coba dimasukkan ke antrian kirim.")
        return back(request)

    # ---- jobs ------------------------------------------------------------------------------------
    @app.get("/jobs", response_class=HTMLResponse, dependencies=[Depends(require_user)])
    def jobs(request: Request, status: str = "", source: str = "", q: str = "", min_score: str = "", page: int = 1):
        where = []
        if status == "active":
            where.append(Job.status.notin_((Status.SKIPPED, Status.DISMISSED, Status.NEW)))
        elif status == "sent":
            where.append(Job.status.in_(Status.SENT))
        elif status:
            where.append(Job.status == status)
        if source:
            where.append(Job.source == source)
        if q:
            like = f"%{q}%"
            where.append(or_(Job.title.ilike(like), Job.company.ilike(like), Job.location.ilike(like)))
        if min_score.isdigit():
            where.append(Job.score >= int(min_score))
        total = _count(*where)
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = max(1, min(page, pages))
        with db.session() as s:
            rows = list(
                s.scalars(
                    select(Job).where(*where).order_by(Job.found_at.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
                )
            )
        return render(
            request, "jobs.html", jobs=rows, total=total, page=page, pages=pages,
            filters={"status": status, "source": source, "q": q, "min_score": min_score},
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse, dependencies=[Depends(require_user)])
    def job_detail(request: Request, job_id: int):
        with db.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise HTTPException(404, "Lowongan tidak ditemukan")
            events = list(s.scalars(select(Event).where(Event.job_id == job_id).order_by(Event.id.desc()).limit(50)))
        return render(request, "job_detail.html", job=job, events=events, nav="jobs")

    def _set_status(job_id: int, status: str, reason: str) -> Job:
        with db.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise HTTPException(404)
            job.status, job.status_reason = status, reason
            if status == Status.APPLIED and job.applied_at is None:
                job.applied_at = utcnow()
            if status == Status.APPROVED:
                job.attempts = 0
            return job

    @app.post("/jobs/{job_id}/action", dependencies=[Depends(require_user)])
    def job_action(request: Request, job_id: int, action: str = Form(...), cover_letter: str = Form(None),
                   notes: str = Form(None), status: str = Form("")):
        if cover_letter is not None:
            with db.session() as s:
                job = s.get(Job, job_id)
                if job is not None:
                    job.cover_letter = cover_letter.strip()
        if action == "approve":
            job = _set_status(job_id, Status.APPROVED, "Disetujui dari dashboard")
            if job.apply_method not in AUTO_PLATFORMS:
                _set_status(job_id, Status.MANUAL, "Tidak ada applier otomatis untuk lowongan ini")
                flash(request, "Lowongan ini harus dilamar manual (lamaran eksternal).")
            else:
                db.log("Disetujui dari dashboard", kind="user", job_id=job_id)
                flash(request, "Disetujui. Lowongan masuk antrian kirim.")
        elif action == "apply_now":
            _set_status(job_id, Status.APPROVED, "Kirim sekarang dari dashboard")
            db.kv_set(CMD_APPLY, {"job_ids": [job_id]})
            flash(request, "Dijadwalkan untuk dikirim sekarang.")
        elif action == "dismiss":
            _set_status(job_id, Status.DISMISSED, "Ditolak dari dashboard")
            db.log("Ditolak dari dashboard", kind="user", job_id=job_id)
            flash(request, "Lowongan ditolak.")
        elif action == "set_status" and status in Status.USER_SETTABLE:
            _set_status(job_id, status, "Diubah manual dari dashboard")
            db.log(f"Status diubah ke {Status.LABELS[status]}", kind="user", job_id=job_id)
            flash(request, f"Status diubah ke {Status.LABELS[status]}.")
        elif action == "notes":
            with db.session() as s:
                job = s.get(Job, job_id)
                if job is not None:
                    job.notes = (notes or "").strip()
            flash(request, "Catatan disimpan.")
        elif action == "save_letter":
            flash(request, "Cover letter disimpan.")
        elif action == "regen_letter":
            letter = pipeline.regenerate_cover_letter(job_id)
            flash(request, "Cover letter dibuat ulang." if letter else "Gagal membuat cover letter.")
        return back(request, f"/jobs/{job_id}")

    @app.post("/jobs/bulk", dependencies=[Depends(require_user)])
    async def jobs_bulk(request: Request):
        form = await request.form()
        action = form.get("action")
        ids = [int(x) for x in form.getlist("ids") if str(x).isdigit()]
        target = {"approve": Status.APPROVED, "dismiss": Status.DISMISSED}.get(str(action))
        if target and ids:
            with db.session() as s:
                for job in s.scalars(select(Job).where(Job.id.in_(ids))):
                    job.status, job.status_reason = target, "Aksi massal dari dashboard"
                    if target == Status.APPROVED:
                        job.attempts = 0
            flash(request, f"{len(ids)} lowongan diperbarui.")
        return back(request, "/jobs")

    @app.get("/review", response_class=HTMLResponse, dependencies=[Depends(require_user)])
    def review(request: Request):
        with db.session() as s:
            rows = list(
                s.scalars(select(Job).where(Job.status == Status.REVIEW).order_by(Job.score.desc().nullslast()).limit(100))
            )
        return render(request, "review.html", jobs=rows)

    @app.get("/screenshots/{name}", dependencies=[Depends(require_user)])
    def screenshot(name: str):
        path = (cfg.screenshots_dir / Path(name).name).resolve()
        if not path.is_file() or path.parent != cfg.screenshots_dir.resolve():
            raise HTTPException(404)
        return FileResponse(path)

    # ---- answers ---------------------------------------------------------------------------------
    @app.get("/answers", response_class=HTMLResponse, dependencies=[Depends(require_user)])
    def answers(request: Request, q: str = ""):
        with db.session() as s:
            query = select(QAAnswer)
            if q:
                query = query.where(or_(QAAnswer.question.ilike(f"%{q}%"), QAAnswer.answer.ilike(f"%{q}%")))
            rows = list(s.scalars(query.order_by(QAAnswer.updated_at.desc()).limit(300)))
        return render(request, "answers.html", answers=rows, q=q)

    @app.post("/answers", dependencies=[Depends(require_user)])
    def answer_save(request: Request, action: str = Form(...), answer_id: str = Form(""), question: str = Form(""),
                    answer: str = Form("")):
        with db.session() as s:
            if action == "add" and question.strip() and answer.strip():
                key = normalize_question(question)
                row = s.scalar(select(QAAnswer).where(QAAnswer.key == key))
                if row is None:
                    s.add(QAAnswer(key=key, question=question.strip(), answer=answer.strip(), origin="user"))
                else:
                    row.answer, row.origin = answer.strip(), "user"
                flash(request, "Jawaban ditambahkan.")
            elif action == "update" and answer_id.isdigit():
                row = s.get(QAAnswer, int(answer_id))
                if row is not None:
                    row.answer, row.origin = answer.strip(), "user"
                    flash(request, "Jawaban diperbarui (dikunci sebagai jawaban Anda).")
            elif action == "delete" and answer_id.isdigit():
                row = s.get(QAAnswer, int(answer_id))
                if row is not None:
                    s.delete(row)
                    flash(request, "Jawaban dihapus.")
        return back(request, "/answers")

    # ---- activity & settings ----------------------------------------------------------------------
    @app.get("/activity", response_class=HTMLResponse, dependencies=[Depends(require_user)])
    def activity(request: Request, level: str = "", kind: str = "", page: int = 1):
        where = []
        if level:
            where.append(Event.level == level)
        if kind:
            where.append(Event.kind == kind)
        with db.session() as s:
            total = int(s.scalar(select(func.count(Event.id)).where(*where)) or 0)
            pages = max(1, math.ceil(total / 100))
            page = max(1, min(page, pages))
            rows = list(s.scalars(select(Event).where(*where).order_by(Event.id.desc()).offset((page - 1) * 100).limit(100)))
        return render(request, "activity.html", events=rows, page=page, pages=pages, filters={"level": level, "kind": kind})

    @app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(require_user)])
    def settings(request: Request):
        with db.session() as s:
            runs = list(s.scalars(select(Run).order_by(Run.id.desc()).limit(20)))
        return render(
            request,
            "settings.html",
            platforms=platform_states(),
            runs=runs,
            worker=worker_state(),
            ai_available=pipeline.ai.available,
            ai_calls=pipeline.ai.calls_today(),
            applied_today={p: pipeline.applied_today(p) for p in cfg.apply.daily_limits},
            applied_total_today=pipeline.applied_today(),
        )

    return app


def _humanize(secs: int) -> str:
    if secs < 60:
        return f"{secs} dtk"
    if secs < 3600:
        return f"{secs // 60} mnt"
    if secs < 86400:
        return f"{secs // 3600} jam"
    return f"{secs // 86400} hari"
