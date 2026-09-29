"""Perintah baris: getjob init | doctor | login | search | apply | worker | serve | start | status."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import secrets
import shutil
import sys
import threading
from pathlib import Path

from .config import EXAMPLE_CONFIG, PLATFORMS, AppConfig, ConfigError, dashboard_password, load_config

log = logging.getLogger("getjob")


def _setup_logging(cfg: AppConfig | None, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if cfg is not None:
        log_dir = cfg.data_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(log_dir / "getjob.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        )
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers, force=True)
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _load(args) -> AppConfig:
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(2)
    cfg.ensure_dirs()
    _setup_logging(cfg, getattr(args, "verbose", False))
    return cfg


def _db(cfg: AppConfig):
    from .db import Database

    return Database(cfg.db_path)


# ---------------------------------------------------------------- init
def cmd_init(args) -> None:
    target = Path(args.config or "config.yaml")
    if target.exists():
        print(f"• {target} sudah ada, tidak ditimpa.")
    else:
        shutil.copy(EXAMPLE_CONFIG, target)
        print(f"✓ {target} dibuat dari contoh. Edit bagian `profile` dan `search` dulu.")
    env = target.parent / ".env"
    if env.exists():
        print(f"• {env} sudah ada, tidak ditimpa.")
    else:
        password = secrets.token_urlsafe(12)
        env.write_text(
            "# Rahasia GetJob (jangan di-commit)\n"
            "ANTHROPIC_API_KEY=\n"
            f"GETJOB_DASHBOARD_PASSWORD={password}\n"
            f"GETJOB_SECRET_KEY={secrets.token_hex(32)}\n",
            encoding="utf-8",
        )
        env.chmod(0o600)
        print(f"✓ {env} dibuat. Password dashboard: {password}")
        print("  Isi ANTHROPIC_API_KEY di .env kalau ingin memakai AI.")
    (target.parent / "data").mkdir(exist_ok=True)
    print("✓ Folder data/ siap. Taruh CV Anda di data/cv.pdf (atau sesuaikan profile.resume_path).")
    print("\nLangkah berikutnya:\n  1. Edit config.yaml\n  2. getjob doctor\n  3. getjob login\n  4. getjob start")


# ---------------------------------------------------------------- doctor
def cmd_doctor(args) -> None:
    cfg = _load(args)
    ok = True

    def check(label: str, good: bool, detail: str = "", warn: bool = False) -> None:
        nonlocal ok
        mark = "✓" if good else ("!" if warn else "✗")
        if not good and not warn:
            ok = False
        print(f" {mark} {label}{': ' + detail if detail else ''}")

    print("Konfigurasi")
    p = cfg.profile
    check("Profil", bool(p.full_name and p.email and "@" in p.email), f"{p.full_name} <{p.email}>")
    if p.full_name == "Nama Lengkap Anda" or p.email == "anda@email.com":
        check("Profil diisi", False, "masih berisi contoh; edit bagian profile di config.yaml")
    check("Skill di profil", bool(p.skills), f"{len(p.skills)} skill", warn=True)
    resume = cfg.path(p.resume_path)
    from .matching import read_resume_text

    text = read_resume_text(resume) if resume.exists() else ""
    check("CV", resume.exists() and len(text) > 200, f"{resume} ({len(text)} karakter terbaca)" if resume.exists() else f"{resume} tidak ada")
    check("Kata kunci pencarian", bool(cfg.search.keywords), ", ".join(cfg.search.keywords))
    check("Password dashboard", bool(dashboard_password()), "GETJOB_DASHBOARD_PASSWORD di .env")
    check("Mode", True, f"{cfg.matching.mode}, dry_run={'YA' if cfg.apply.dry_run else 'tidak'}")

    print("\nAI")
    if not cfg.ai.enabled:
        check("AI", True, "dinonaktifkan di config (pakai kata kunci)", warn=True)
    elif not cfg.ai.api_key_present:
        check("ANTHROPIC_API_KEY", False, "belum diisi; program tetap jalan tanpa AI", warn=True)
    elif args.ai:
        from .db import Database
        from .matching import AIError, ClaudeAI

        ai = ClaudeAI(cfg, Database(":memory:"))
        try:
            reply = ai.answer(
                type("J", (), {"title": "Test", "company": "Test", "location": "", "description": "", "salary": None, "is_remote": None})(),
                "What is your first name?",
                "text",
            )
            check("Claude API", bool(reply), f"model {cfg.ai.model} menjawab: {reply[:40]!r}")
        except AIError as exc:
            check("Claude API", False, str(exc))
    else:
        check("ANTHROPIC_API_KEY", True, f"terisi (model {cfg.ai.model}; uji panggilan: getjob doctor --ai)")

    print("\nBrowser")
    try:
        from .browser import BrowserSession, display_available

        with BrowserSession(cfg, headless=True) as session:
            page = session.new_page()
            page.set_content("<p>ok</p>")
            check("Chromium (Playwright)", page.inner_text("p") == "ok")
        check("Layar (DISPLAY)", display_available(), "browser tampil di layar" if display_available() else "tidak ada; browser akan headless", warn=True)
    except Exception as exc:
        check("Chromium (Playwright)", False, f"{exc}. Jalankan: playwright install chromium")

    if not args.offline:
        print("\nKoneksi ke sumber lowongan")
        import httpx

        urls = {
            "linkedin": "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=python&location=Indonesia",
            "jobstreet": "https://id.jobstreet.com/api/jobsearch/v5/search?siteKey=ID-Main&keywords=python&page=1&pageSize=1",
            "glints": "https://glints.com/id",
            "kalibrr": "https://www.kalibrr.com/kjs/job_board/search?limit=1&offset=0&text=python",
            "remoteok": "https://remoteok.com/api",
            "remotive": "https://remotive.com/api/remote-jobs?limit=1",
            "greenhouse": "https://boards-api.greenhouse.io/v1/boards/gitlab/jobs",
            "lever": "https://api.lever.co/v0/postings/leverdemo?mode=json",
        }
        from .sources.base import USER_AGENT

        with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
            for name in cfg.enabled_sources():
                try:
                    r = client.get(urls[name])
                    check(name, r.status_code < 400, f"HTTP {r.status_code}", warn=name == "glints")
                except Exception as exc:
                    check(name, False, str(exc)[:120])

    db = _db(cfg)
    print("\nStatus login platform (dari percobaan terakhir)")
    for plat in PLATFORMS:
        st = db.platform_state(plat)
        print(f"   {plat:<11} {st.get('status', 'belum dicek')} {st.get('message', '')}")
    print("\n" + ("Semua pemeriksaan penting lolos." if ok else "Ada yang perlu diperbaiki (tanda ✗)."))


# ---------------------------------------------------------------- login
def cmd_login(args) -> None:
    cfg = _load(args)
    from .answers import AnswerEngine
    from .appliers import APPLIERS, ApplyContext
    from .browser import BrowserBusy, BrowserSession

    db = _db(cfg)
    unknown = [p for p in args.platforms if p not in PLATFORMS]
    if unknown:
        print(f"✗ Platform tidak dikenal: {', '.join(unknown)}. Pilihan: {', '.join(PLATFORMS)}")
        sys.exit(2)
    wanted = args.platforms or [p for p in ("linkedin", "jobstreet", "glints", "kalibrr") if p in cfg.enabled_sources()]
    ctx = ApplyContext(cfg, AnswerEngine(cfg, db))
    appliers = {p: APPLIERS[p](ctx) for p in wanted if p in APPLIERS and APPLIERS[p].requires_login}
    if not appliers:
        print("Tidak ada platform yang butuh login.")
        return
    try:
        with BrowserSession(cfg, headless=False, lock_timeout=30) as session:
            if session.headless:
                print("! Tidak ada layar (DISPLAY). Jalankan perintah ini dari desktop mini PC atau lewat VNC/Remote Desktop.")
            for name, applier in appliers.items():
                page = session.new_page()
                page.goto(applier.login_url, wait_until="domcontentloaded")
            print("\nBrowser terbuka dengan tab login untuk: " + ", ".join(appliers))
            print("Login di setiap tab (selesaikan OTP/captcha bila ada).")
            input("Tekan Enter di sini setelah selesai login semua... ")
            page = session.new_page()
            for name, applier in appliers.items():
                try:
                    logged = applier.check_login(page)
                except Exception as exc:
                    logged = False
                    print(f"  ! {name}: gagal memeriksa ({exc})")
                db.set_platform_state(name, "ok" if logged else "needs_login", "" if logged else "Login belum terdeteksi")
                print(f"  {'✓' if logged else '✗'} {name}: {'login OK' if logged else 'belum login'}")
    except BrowserBusy:
        print("✗ Browser sedang dipakai worker. Jeda dulu dari dashboard (atau tunggu siklus selesai), lalu ulangi.")
        sys.exit(1)


# ---------------------------------------------------------------- run once
def _pipeline(cfg: AppConfig):
    from .pipeline import Pipeline

    return Pipeline(cfg, _db(cfg))


def cmd_search(args) -> None:
    cfg = _load(args)
    stats = _pipeline(cfg).run_search(only=args.source)
    print(f"Ditemukan {stats['found']} lowongan, {stats['new']} baru, {stats.get('scored', 0)} dinilai.")
    for name, s in stats["sources"].items():
        print(f"  {name:<11} {s['found']:>4} ditemukan  {s['new']:>4} baru")
    for name, err in stats["errors"].items():
        print(f"  ! {name}: {err}")


def cmd_score(args) -> None:
    cfg = _load(args)
    n = _pipeline(cfg).score_pending()
    print(f"{n} lowongan dinilai.")


def cmd_apply(args) -> None:
    cfg = _load(args)
    if args.dry_run:
        cfg.apply.dry_run = True
    if args.live:
        cfg.apply.dry_run = False
    pipe = _pipeline(cfg)
    if args.job:
        from .db import Job, Status

        with pipe.db.session() as s:
            for jid in args.job:
                job = s.get(Job, jid)
                if job is not None:
                    job.status = Status.APPROVED
    stats = pipe.run_apply(job_ids=args.job, force=True, limit=args.limit)
    print(f"Diproses: {stats.get('attempted', 0)} {stats.get('outcomes', {})} {stats.get('skipped', '')}")


def cmd_status(args) -> None:
    cfg = _load(args)
    from sqlalchemy import func, select

    from .db import Job, Status

    db = _db(cfg)
    with db.session() as s:
        rows = dict(s.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all())
    for key, label in Status.LABELS.items():
        if rows.get(key):
            print(f"  {label:<20} {rows[key]}")
    print(f"  {'Total':<20} {sum(rows.values())}")


# ---------------------------------------------------------------- long running
def _serve(cfg: AppConfig, db, host: str | None, port: int | None) -> None:
    import uvicorn

    from .web import create_app

    if not dashboard_password():
        print("✗ GETJOB_DASHBOARD_PASSWORD belum diisi di .env (jalankan `getjob init`).", file=sys.stderr)
        sys.exit(2)
    app = create_app(cfg, db)
    h, p = host or cfg.dashboard.host, port or cfg.dashboard.port
    print(f"Dashboard: http://{'localhost' if h in ('0.0.0.0', '::') else h}:{p}")
    uvicorn.run(app, host=h, port=p, log_level="warning")


def cmd_serve(args) -> None:
    cfg = _load(args)
    _serve(cfg, _db(cfg), args.host, args.port)


def cmd_worker(args) -> None:
    cfg = _load(args)
    from .worker import Worker

    stop = threading.Event()
    try:
        Worker(cfg, _db(cfg)).run_forever(stop)
    except KeyboardInterrupt:
        stop.set()


def cmd_start(args) -> None:
    cfg = _load(args)
    from .worker import Worker

    db = _db(cfg)
    stop = threading.Event()
    t = threading.Thread(target=Worker(cfg, db).run_forever, args=(stop,), name="getjob-worker", daemon=True)
    t.start()
    try:
        _serve(cfg, db, args.host, args.port)
    finally:
        stop.set()
        t.join(timeout=30)


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="getjob", description="Pencari & pelamar kerja otomatis.")
    ap.add_argument("-c", "--config", help="path config.yaml (default: ./config.yaml atau $GETJOB_CONFIG)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="buat config.yaml & .env").set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="periksa konfigurasi, CV, AI, browser, koneksi")
    p.add_argument("--ai", action="store_true", help="uji panggilan Claude API sungguhan")
    p.add_argument("--offline", action="store_true", help="lewati cek koneksi")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("login", help="buka browser untuk login ke platform")
    p.add_argument("platforms", nargs="*", help=f"salah satu dari: {', '.join(PLATFORMS)} (default: semua yang aktif)")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("search", help="jalankan satu siklus pencarian + penilaian")
    p.add_argument("--source", action="append", help="hanya sumber ini (bisa diulang)")
    p.set_defaults(func=cmd_search)

    sub.add_parser("score", help="nilai lowongan berstatus baru").set_defaults(func=cmd_score)

    p = sub.add_parser("apply", help="kirim lamaran di antrian sekarang (abaikan jam aktif)")
    p.add_argument("--job", type=int, action="append", help="ID lowongan tertentu (bisa diulang)")
    p.add_argument("--limit", type=int)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="paksa mode uji coba")
    g.add_argument("--live", action="store_true", help="paksa kirim sungguhan")
    p.set_defaults(func=cmd_apply)

    sub.add_parser("status", help="ringkasan jumlah lowongan per status").set_defaults(func=cmd_status)

    for name, fn, helptext in (
        ("serve", cmd_serve, "jalankan dashboard saja"),
        ("start", cmd_start, "jalankan worker + dashboard (untuk dipakai 24 jam)"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--host")
        p.add_argument("--port", type=int)
        p.set_defaults(func=fn)
    sub.add_parser("worker", help="jalankan worker saja").set_defaults(func=cmd_worker)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        _setup_logging(None)
    args.func(args)


if __name__ == "__main__":
    main()
