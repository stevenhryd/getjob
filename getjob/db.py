"""Database SQLite (SQLAlchemy 2.0): lowongan, log kejadian, bank jawaban, state."""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    """Waktu UTC tanpa tzinfo (format penyimpanan di DB)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Status:
    NEW = "new"
    SKIPPED = "skipped"
    REVIEW = "review"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    TESTED = "tested"
    MANUAL = "manual"
    FAILED = "failed"
    DISMISSED = "dismissed"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"

    LABELS = {
        NEW: "Baru",
        SKIPPED: "Dilewati",
        REVIEW: "Menunggu review",
        APPROVED: "Antrian kirim",
        APPLYING: "Sedang dikirim",
        APPLIED: "Terkirim",
        TESTED: "Uji coba OK",
        MANUAL: "Perlu manual",
        FAILED: "Gagal",
        DISMISSED: "Ditolak saya",
        INTERVIEW: "Interview",
        OFFER: "Tawaran",
        REJECTED: "Ditolak perusahaan",
    }
    # Status yang berarti lamaran sudah terkirim (untuk statistik & dedupe).
    SENT = (APPLIED, INTERVIEW, OFFER, REJECTED)
    # Status yang boleh diubah manual oleh user dari dashboard.
    USER_SETTABLE = (APPROVED, APPLIED, DISMISSED, INTERVIEW, OFFER, REJECTED, MANUAL)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(191))
    url: Mapped[str] = mapped_column(Text)
    apply_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    apply_method: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    is_remote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    salary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    found_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    status: Mapped[str] = mapped_column(String(20), default=Status.NEW, index=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    keyword_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    score_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_skills: Mapped[list] = mapped_column(JSON, default=list)
    missing_skills: Mapped[list] = mapped_column(JSON, default=list)
    red_flags: Mapped[list] = mapped_column(JSON, default=list)
    cover_letter: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    screenshot: Mapped[str | None] = mapped_column(String(300), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(400), default="", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    @property
    def status_label(self) -> str:
        return Status.LABELS.get(self.status, self.status)

    @property
    def platform(self) -> str | None:
        return self.apply_method


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")
    kind: Mapped[str] = mapped_column(String(32), default="info", index=True)
    message: Mapped[str] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)


class QAAnswer(Base):
    """Bank jawaban untuk pertanyaan form lamaran (dipelajari dari AI / diedit user)."""

    __tablename__ = "qa_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(400), unique=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(16), default="ai")  # ai | user
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class KV(Base):
    """Key-value untuk state runtime: pause, perintah dari dashboard, status login platform."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # search | apply
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        url = "sqlite://" if str(path) == ":memory:" else f"sqlite:///{self.path}"
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        if str(path) == ":memory:":
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, **kwargs)
        event.listen(self.engine, "connect", _sqlite_pragmas)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ---- KV ---------------------------------------------------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        with self.session() as s:
            row = s.get(KV, key)
            return default if row is None else row.value

    def kv_set(self, key: str, value: Any) -> None:
        with self.session() as s:
            row = s.get(KV, key)
            if row is None:
                s.add(KV(key=key, value=value))
            else:
                row.value = value

    def kv_pop(self, key: str) -> Any:
        with self.session() as s:
            row = s.get(KV, key)
            if row is None:
                return None
            value = row.value
            s.delete(row)
            return value

    def kv_incr(self, key: str, by: int = 1) -> int:
        with self.session() as s:
            row = s.get(KV, key)
            if row is None:
                s.add(KV(key=key, value=by))
                return by
            row.value = int(row.value or 0) + by
            return row.value

    # ---- Events -----------------------------------------------------------
    def log(self, message: str, *, kind: str = "info", level: str = "info", job_id: int | None = None) -> None:
        with self.session() as s:
            s.add(Event(message=message[:4000], kind=kind, level=level, job_id=job_id))

    # ---- Platform state -----------------------------------------------------
    def platform_state(self, platform: str) -> dict:
        return self.kv_get(f"platform:{platform}", {}) or {}

    def set_platform_state(self, platform: str, status: str, message: str = "") -> None:
        state = self.platform_state(platform)
        state.update({"status": status, "message": message, "checked_at": utcnow().isoformat()})
        self.kv_set(f"platform:{platform}", state)

    def platform_paused(self, platform: str) -> bool:
        return bool(self.platform_state(platform).get("paused"))

    def set_platform_paused(self, platform: str, paused: bool) -> None:
        state = self.platform_state(platform)
        state["paused"] = paused
        self.kv_set(f"platform:{platform}", state)

    # ---- Counting ---------------------------------------------------------------
    def count_applied_since(self, since_utc: datetime, platform: str | None = None) -> int:
        with self.session() as s:
            q = select(func.count(Job.id)).where(Job.applied_at >= since_utc)
            if platform:
                q = q.where(Job.apply_method == platform)
            return int(s.scalar(q) or 0)


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


# ---- Util waktu lokal ----------------------------------------------------------


def local_day_start_utc(tz_name: str, now_utc: datetime | None = None) -> datetime:
    """Awal hari ini (zona waktu lokal) dalam UTC naive."""
    tz = ZoneInfo(tz_name)
    now_local = (now_utc or utcnow()).replace(tzinfo=timezone.utc).astimezone(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc).replace(tzinfo=None)


def to_local(dt: datetime | None, tz_name: str) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))


def days_ago(days: int) -> datetime:
    return utcnow() - timedelta(days=days)


_NORM_RE = re.compile(r"[^a-z0-9+#]+")
_COMPANY_SUFFIX_RE = re.compile(r"\b(pt|tbk|cv|inc|ltd|llc|co|corp|corporation|company|limited|persero)\b")


def dedupe_key(company: str, title: str) -> str:
    c = _COMPANY_SUFFIX_RE.sub(" ", (company or "").lower())
    c = _NORM_RE.sub(" ", c).strip()
    t = _NORM_RE.sub(" ", (title or "").lower()).strip()
    return f"{' '.join(c.split())}|{' '.join(t.split())}"
