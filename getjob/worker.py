"""Worker: menjalankan pencarian & pengiriman lamaran sesuai jadwal, plus perintah dari dashboard."""

from __future__ import annotations

import logging
import random
import threading
from datetime import datetime, timedelta

from .config import AppConfig
from .db import Database, utcnow
from .pipeline import Pipeline

log = logging.getLogger(__name__)

CMD_SEARCH = "cmd:search"
CMD_APPLY = "cmd:apply"
CMD_SCORE = "cmd:score"
KEY_PAUSED = "paused"
KEY_HEARTBEAT = "worker:heartbeat"
KEY_NEXT_SEARCH = "worker:next_search"
KEY_NEXT_APPLY = "worker:next_apply"
KEY_BUSY = "worker:busy"


class Worker:
    def __init__(self, cfg: AppConfig, db: Database, pipeline: Pipeline | None = None):
        self.cfg = cfg
        self.db = db
        self.pipeline = pipeline or Pipeline(cfg, db)

    def _schedule(self, key: str, minutes: int) -> datetime:
        jitter = random.uniform(-0.1, 0.1) * minutes
        at = utcnow() + timedelta(minutes=minutes + jitter)
        self.db.kv_set(key, at.isoformat())
        return at

    def _busy(self, what: str | None) -> None:
        self.db.kv_set(KEY_BUSY, what)

    def tick(self, state: dict) -> None:
        """Satu putaran loop (dipisah agar mudah dites)."""
        now = utcnow()
        self.db.kv_set(KEY_HEARTBEAT, now.isoformat())
        paused = bool(self.db.kv_get(KEY_PAUSED, False))

        force_search = self.db.kv_pop(CMD_SEARCH)
        force_apply = self.db.kv_pop(CMD_APPLY)
        force_score = self.db.kv_pop(CMD_SCORE)

        if force_score:
            self._busy("menilai ulang")
            self.pipeline.score_pending()

        if force_search or (not paused and now >= state["next_search"]):
            self._busy("mencari lowongan")
            stats = self.pipeline.run_search()
            log.info("Pencarian selesai: %s", stats)
            state["next_search"] = self._schedule(KEY_NEXT_SEARCH, self.cfg.schedule.search_interval_minutes)

        if force_apply or (not paused and now >= state["next_apply"]):
            self._busy("mengirim lamaran")
            job_ids = force_apply.get("job_ids") if isinstance(force_apply, dict) else None
            stats = self.pipeline.run_apply(force=bool(force_apply), job_ids=job_ids)
            if stats.get("attempted"):
                log.info("Lamaran: %s", stats)
            state["next_apply"] = self._schedule(KEY_NEXT_APPLY, self.cfg.schedule.apply_interval_minutes)
        self._busy(None)

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        state = {"next_search": utcnow(), "next_apply": utcnow() + timedelta(minutes=2)}
        self.db.kv_set(KEY_NEXT_SEARCH, state["next_search"].isoformat())
        self.db.kv_set(KEY_NEXT_APPLY, state["next_apply"].isoformat())
        self.db.log("Worker dimulai", kind="system")
        while not stop.is_set():
            try:
                self.tick(state)
            except Exception as exc:  # worker tidak boleh mati karena satu error
                log.exception("Worker error")
                self.db.log(f"Worker error: {exc}", kind="system", level="error")
                self._busy(None)
            stop.wait(5)
        self.db.log("Worker berhenti", kind="system")
