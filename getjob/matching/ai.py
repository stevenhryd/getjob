"""Integrasi Claude API: menilai kecocokan, menulis cover letter, menjawab pertanyaan form."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import anthropic

from ..config import AppConfig
from ..db import Database
from ..textutil import clip

log = logging.getLogger(__name__)


class AIError(RuntimeError):
    """Panggilan AI gagal untuk item ini (bisa dicoba lagi nanti)."""


class AIUnavailable(AIError):
    """AI tidak bisa dipakai sama sekali (tidak ada API key, kuota harian habis, key salah)."""


class JobLike(Protocol):
    title: str
    company: str
    location: str
    description: str
    salary: str | None
    is_remote: bool | None


@dataclass
class AIScore:
    score: int
    reason: str
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    language: str = "en"


SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "0-100"},
        "reason": {"type": "string"},
        "matched_skills": {"type": "array", "items": {"type": "string"}},
        "missing_skills": {"type": "array", "items": {"type": "string"}},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "language": {"type": "string", "enum": ["id", "en"]},
    },
    "required": ["score", "reason", "matched_skills", "missing_skills", "red_flags", "language"],
    "additionalProperties": False,
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def read_resume_text(path: Path) -> str:
    if not path.exists():
        return ""
    if path.suffix.lower() in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception as exc:  # PDF rusak / terenkripsi
        log.warning("Gagal membaca CV %s: %s", path, exc)
        return ""


def _supports_effort(model: str) -> bool:
    return not (model.startswith("claude-haiku") or "sonnet-4-5" in model or model.startswith("claude-3"))


def _supports_server_fallbacks(model: str) -> bool:
    return model.startswith(("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5", "claude-mythos-5"))


class ClaudeAI:
    def __init__(self, cfg: AppConfig, db: Database, client: Any | None = None):
        self.cfg = cfg
        self.ai = cfg.ai
        self.db = db
        self._client = client
        self._system: str | None = None

    # ---- setup ---------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.ai.enabled and (self._client is not None or self.ai.api_key_present)

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = anthropic.Anthropic(max_retries=3, timeout=180.0)
        return self._client

    def resume_text(self) -> str:
        p = self.cfg.profile
        text = read_resume_text(self.cfg.path(p.resume_path))
        if p.resume_path_en:
            en = read_resume_text(self.cfg.path(p.resume_path_en))
            if en:
                text = f"{text}\n\n--- CV (English) ---\n{en}"
        return clip(text, 30000)

    def system_prompt(self) -> str:
        if self._system is None:
            profile = self.cfg.profile.model_dump(exclude={"resume_path", "resume_path_en"})
            self._system = (
                "You help one job seeker in Indonesia evaluate job postings and complete job applications. "
                "Everything you write must be truthful and based only on the candidate profile and CV below: "
                "never invent employers, degrees, certifications, or years of experience. When the CV does not "
                "cover something, say so plainly or answer conservatively.\n\n"
                f"<candidate_profile>\n{json.dumps(profile, ensure_ascii=False, sort_keys=True, indent=1)}\n"
                "</candidate_profile>\n\n"
                f"<cv>\n{self.resume_text() or '(CV tidak tersedia, gunakan profil saja)'}\n</cv>"
            )
        return self._system

    # ---- low level -------------------------------------------------------------
    def _budget_key(self) -> str:
        today = datetime.now(ZoneInfo(self.cfg.schedule.timezone)).strftime("%Y-%m-%d")
        return f"ai_calls:{today}"

    def calls_today(self) -> int:
        return int(self.db.kv_get(self._budget_key(), 0) or 0)

    def _call(self, user: str, *, effort: str, max_tokens: int, schema: dict | None = None) -> str:
        if not self.available:
            raise AIUnavailable("AI nonaktif atau ANTHROPIC_API_KEY belum diisi")
        if self.calls_today() >= self.ai.max_calls_per_day:
            raise AIUnavailable(f"Kuota harian AI ({self.ai.max_calls_per_day} panggilan) habis")

        model = self.ai.model
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            # Profil + CV identik di setiap panggilan -> di-cache agar lebih murah.
            "system": [{"type": "text", "text": self.system_prompt(), "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        output_config: dict[str, Any] = {}
        if _supports_effort(model):
            output_config["effort"] = effort
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            kwargs["output_config"] = output_config
        if self.ai.use_fallbacks and _supports_server_fallbacks(model):
            # Jika model menolak (false positive safety), API mencoba ulang di model cadangan.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"

        try:
            resp = self.client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise AIUnavailable("ANTHROPIC_API_KEY tidak valid") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIUnavailable(f"API key tidak punya akses ke model {model}") from exc
        except anthropic.NotFoundError as exc:
            raise AIUnavailable(f"Model '{model}' tidak ditemukan; periksa ai.model di config.yaml") from exc
        except anthropic.RateLimitError as exc:
            raise AIError("Rate limit Claude API, dicoba lagi nanti") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("Tidak bisa terhubung ke Claude API") from exc

        self.db.kv_incr(self._budget_key())
        if resp.stop_reason == "refusal":
            raise AIError("Model menolak permintaan ini")
        if resp.stop_reason == "max_tokens":
            raise AIError("Jawaban AI terpotong (max_tokens)")
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    @staticmethod
    def _job_block(job: JobLike) -> str:
        remote = "ya" if job.is_remote else "tidak/tidak disebut"
        return (
            "<job>\n"
            f"Title: {job.title}\nCompany: {job.company}\nLocation: {job.location}\nRemote: {remote}\n"
            f"Salary: {job.salary or '-'}\n\nDescription:\n{clip(job.description, 12000)}\n"
            "</job>"
        )

    # ---- high level ---------------------------------------------------------------
    def score(self, job: JobLike) -> AIScore:
        p = self.cfg.profile
        prompt = (
            f"{self._job_block(job)}\n\n"
            "Rate how well this job fits the candidate, from 0 to 100:\n"
            "- 90-100: meets nearly all must-have requirements; a strong application.\n"
            "- 75-89: good fit with minor gaps.\n"
            "- 60-74: partial fit, still worth applying.\n"
            "- 40-59: weak fit.\n"
            "- 0-39: different field, far too senior, or the location/work authorization makes it unrealistic.\n"
            f"The candidate lives in {p.city or '-'}, {p.country}; willing to relocate: {p.willing_to_relocate}; "
            f"needs visa sponsorship abroad: {p.need_visa_sponsorship}. On-site roles in another city score low "
            "unless the candidate is willing to relocate; roles abroad that require local work authorization score "
            "low unless they offer sponsorship or are remote-friendly for Indonesia.\n"
            "Also return: `reason` (1-2 sentences in Bahasa Indonesia), `matched_skills` and `missing_skills` "
            "(short skill names), `red_flags` (e.g. unpaid, suspicious, requires a fee; empty if none), and "
            "`language` = the language the posting is written in ('id' or 'en')."
        )
        text = self._call(prompt, effort=self.ai.effort_scoring, max_tokens=8000, schema=SCORE_SCHEMA)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIError("Output penilaian AI bukan JSON valid") from exc
        return AIScore(
            score=max(0, min(100, int(data.get("score", 0)))),
            reason=str(data.get("reason", "")),
            matched=[str(x) for x in data.get("matched_skills", [])][:20],
            missing=[str(x) for x in data.get("missing_skills", [])][:20],
            red_flags=[str(x) for x in data.get("red_flags", [])][:10],
            language="id" if data.get("language") == "id" else "en",
        )

    def cover_letter(self, job: JobLike, language: str) -> str:
        lang = "Bahasa Indonesia (formal, natural)" if language == "id" else "English"
        prompt = (
            f"{self._job_block(job)}\n\n"
            f"Write a cover letter in {lang} for this job, 150-230 words, plain text (no markdown, no subject "
            "line, no placeholders like [Company]). Open with a greeting to the hiring team, connect 2-3 concrete "
            "things from the CV to the job's main requirements, and close with the candidate's full name. "
            "Use only facts from the profile and CV. Output only the letter."
        )
        return self._call(prompt, effort=self.ai.effort_writing, max_tokens=8000)

    def answer(self, job: JobLike, question: str, kind: str, options: list[str] | None = None) -> str:
        opts = ""
        if options:
            opts = "Options (answer with the exact text of one option):\n" + "\n".join(f"- {o}" for o in options)
            if kind == "checkbox_group":
                opts = (
                    "Options (answer with the exact text of every option that applies, separated by ' | '):\n"
                    + "\n".join(f"- {o}" for o in options)
                )
        rules = {
            "number": "Answer with a number only (digits, no units).",
            "textarea": "Answer in 2-5 sentences.",
        }.get(kind, "Keep the answer short (under 20 words) unless the question needs more.")
        prompt = (
            f"{self._job_block(job)}\n\n"
            "The application form for this job asks the question below. Answer it for the candidate, truthfully, "
            "based on the profile and CV. If the question asks for years of experience with a skill that the CV "
            "does not show, answer 0. Reply in the same language as the question.\n"
            f"Field type: {kind}\n{rules}\n{opts}\n\nQuestion: {question}"
        )
        text = self._call(prompt, effort=self.ai.effort_scoring, max_tokens=4000, schema=ANSWER_SCHEMA)
        try:
            return str(json.loads(text).get("answer", "")).strip()
        except json.JSONDecodeError as exc:
            raise AIError("Output jawaban AI bukan JSON valid") from exc
