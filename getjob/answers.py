"""Mesin jawaban untuk pertanyaan di form lamaran.

Urutan sumber jawaban:
1. Aturan user di config.yaml (`answers:`)
2. Bank jawaban yang diedit user di dashboard
3. Aturan bawaan dari profil (nama, email, gaji, pengalaman, dll.)
4. Bank jawaban hasil AI sebelumnya
5. Tanya AI (lalu disimpan ke bank jawaban)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select

from .config import AppConfig, Profile
from .db import Database, QAAnswer
from .matching.ai import AIError, ClaudeAI
from .textutil import TECH_TERMS, normalize_question, term_in_text

log = logging.getLogger(__name__)


@dataclass
class Option:
    id: str
    label: str
    value: str = ""
    checked: bool = False


@dataclass
class FormField:
    id: str
    kind: str
    label: str
    name: str = ""
    required: bool = False
    value: str = ""
    options: list[Option] = field(default_factory=list)
    placeholder: str = ""
    accept: str = ""
    multiple: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FormField":
        return cls(
            id=d["id"],
            kind=d.get("kind", "text"),
            label=d.get("label", "") or "",
            name=d.get("name", "") or "",
            required=bool(d.get("required")),
            value=d.get("value", "") or "",
            options=[Option(**{k: o.get(k, "") for k in ("id", "label", "value", "checked")}) for o in d.get("options") or []],
            placeholder=d.get("placeholder", "") or "",
            accept=d.get("accept", "") or "",
            multiple=bool(d.get("multiple")),
        )

    @property
    def question(self) -> str:
        return self.label or self.placeholder or self.name


@dataclass
class JobContext:
    """Data lowongan yang dibutuhkan untuk menjawab (duck-typed ke Job/JobPosting)."""

    title: str
    company: str
    location: str = ""
    description: str = ""
    salary: str | None = None
    is_remote: bool | None = None
    source: str = ""
    cover_letter: str = ""


YES_WORDS = {"yes", "ya", "iya", "y", "true", "bersedia", "sure"}
NO_WORDS = {"no", "tidak", "n", "false", "tidak bersedia", "nope"}
DECLINE_RE = re.compile(r"decline|prefer not|not (to )?(say|disclose|wish)|tidak ingin|rather not|don.t wish", re.I)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _yes(v: bool) -> str:
    return "Yes" if v else "No"


def _is_international(job: JobContext) -> bool:
    if job.source in ("remoteok", "remotive", "greenhouse", "lever"):
        return True
    return "indonesia" not in (job.location or "").lower() and bool(job.location)


# ---- aturan bawaan -----------------------------------------------------------------

Rule = tuple[re.Pattern, Callable[[Profile, JobContext, FormField], "str | None"]]


def _salary(p: Profile, job: JobContext, f: FormField) -> str | None:
    q = f.question.lower()
    usd = ("usd" in q or "$" in q or _is_international(job)) and p.expected_salary_usd
    if usd:
        return str(p.expected_salary_usd)
    if p.expected_salary_idr:
        return str(p.expected_salary_idr)
    return None


def _skill_in_question(q: str, p: Profile) -> str | None:
    ql = q.lower()
    for s in sorted({*p.skills, *p.skill_years.keys()}, key=len, reverse=True):
        if term_in_text(s, ql):
            return s.lower()
    for t in sorted(TECH_TERMS, key=len, reverse=True):
        if term_in_text(t, ql):
            return t
    return None


def _years(p: Profile, job: JobContext, f: FormField) -> str | None:
    q = f.question
    skill = _skill_in_question(q, p)
    if skill is None:
        years = p.years_experience
    else:
        by_skill = {k.lower(): v for k, v in p.skill_years.items()}
        known = {s.lower() for s in p.skills}
        years = by_skill.get(skill, p.years_experience if skill in known else 0)
    if f.kind in ("radio", "select") and _has_yes_no(f.options):
        nums = [float(n.replace(",", ".")) for n in _NUM_RE.findall(q)]
        needed = nums[0] if nums else 1
        return _yes(years >= needed)
    return str(int(round(years)))


def _has_yes_no(options: list[Option]) -> bool:
    labels = {o.label.strip().lower() for o in options}
    return bool(labels & YES_WORDS) and bool(labels & NO_WORDS)


def _authorized(p: Profile, job: JobContext, f: FormField) -> str:
    loc = (job.location or "").lower()
    ok = any(c.lower() in loc for c in p.authorized_countries) or (not loc or "remote" in loc and not _is_international(job))
    return _yes(ok)


def _how_heard(p: Profile, job: JobContext, f: FormField) -> str:
    return {"linkedin": "LinkedIn", "jobstreet": "JobStreet", "glints": "Glints", "kalibrr": "Kalibrr"}.get(
        job.source, "Job board"
    )


BUILTIN_RULES: list[Rule] = [
    (re.compile(r"cover letter|surat lamaran|motivation letter|additional information|anything else|informasi tambahan|pesan untuk"),
     lambda p, j, f: j.cover_letter or None if f.kind == "textarea" else None),
    (re.compile(r"first name|given name|nama depan"), lambda p, j, f: p.first_name),
    (re.compile(r"last name|surname|family name|nama belakang"), lambda p, j, f: p.last_name),
    (re.compile(r"^(full |legal |your )?name$|nama lengkap|^nama$"), lambda p, j, f: p.full_name),
    (re.compile(r"e-?mail"), lambda p, j, f: p.email),
    (re.compile(r"country code|kode negara|phone country"),
     lambda p, j, f: "Indonesia (+62)" if f.options else "+62"),
    (re.compile(r"phone|mobile|telepon|telp|no\.? ?hp|nomor hp|whatsapp|handphone|ponsel"), lambda p, j, f: p.phone),
    (re.compile(r"linkedin"), lambda p, j, f: p.linkedin_url or None),
    (re.compile(r"github"), lambda p, j, f: p.github_url or None),
    (re.compile(r"portfolio|website|personal site|blog|other url|situs"),
     lambda p, j, f: p.portfolio_url or p.github_url or p.linkedin_url or None),
    (re.compile(r"salary|gaji|compensation|expected pay|remuneration|\bctc\b|pay expectation"), _salary),
    (re.compile(r"notice period|masa pemberitahuan|kapan bisa mulai|available to start|earliest start|start date|bisa bergabung"),
     lambda p, j, f: str(p.notice_period_days) if f.kind == "number" else p.notice_period),
    (re.compile(r"how many years|years? of (work )?experience|tahun pengalaman|pengalaman.*tahun|experience.*years?|do you have .*experience"), _years),
    (re.compile(r"current (company|employer)|perusahaan (saat ini|sekarang|terakhir)|^company$|^org$"),
     lambda p, j, f: p.current_company or None),
    (re.compile(r"current (job )?(title|position|role)|jabatan (saat ini|sekarang|terakhir)|^headline$"),
     lambda p, j, f: p.current_title or p.headline or None),
    (re.compile(r"relocat|pindah domisili|bersedia ditempatkan|willing to move"), lambda p, j, f: _yes(p.willing_to_relocate)),
    (re.compile(r"sponsor|visa"), lambda p, j, f: _yes(p.need_visa_sponsorship)),
    (re.compile(r"authori[sz]ed to work|legally (authori|able|eligible)|work permit|right to work|izin kerja|eligible to work"),
     _authorized),
    (re.compile(r"\bcity\b|kota|domisili|where are you (located|based)|current location|^location$|lokasi"),
     lambda p, j, f: p.city or None),
    (re.compile(r"\bcountry\b|negara"), lambda p, j, f: p.country),
    (re.compile(r"highest (level of )?education|pendidikan terakhir|degree|gelar|education level"),
     lambda p, j, f: p.education or None),
    (re.compile(r"gender|jenis kelamin|sex\b"), lambda p, j, f: p.gender or "Prefer not to say"),
    (re.compile(r"race|ethnic|veteran|disabilit|hispanic|latino|pronoun|sexual orientation"),
     lambda p, j, f: "Decline to self-identify"),
    (re.compile(r"how did you (hear|find|learn)|dari mana anda (tahu|mengetahui)|referral source"), _how_heard),
]


def choose_option(answer: str, options: list[Option]) -> Option | None:
    """Petakan jawaban bebas ke salah satu opsi pilihan."""
    if not options:
        return None
    a = (answer or "").strip().lower()
    if not a:
        return None
    labels = [(o, o.label.strip().lower()) for o in options]
    for o, l in labels:
        if l == a or o.value.strip().lower() == a:
            return o
    # ya/tidak
    if a in YES_WORDS or a in NO_WORDS:
        want = YES_WORDS if a in YES_WORDS else NO_WORDS
        for o, l in labels:
            if l in want or l.split(",")[0].split(" ")[0] in want:
                return o
    if DECLINE_RE.search(a):
        for o, l in labels:
            if DECLINE_RE.search(l):
                return o
    # angka ke rentang ("1-3 years", "5+", "Less than 1 year")
    nums = _NUM_RE.findall(a)
    if nums and len(a) <= 20:
        n = float(nums[0].replace(",", "."))
        best = None
        for o, l in labels:
            ln = [float(x.replace(",", ".")) for x in _NUM_RE.findall(l)]
            if not ln:
                continue
            if len(ln) >= 2 and ln[0] <= n <= ln[1]:
                return o
            if len(ln) == 1:
                if ("+" in l or "more" in l or "lebih" in l or "above" in l) and n >= ln[0]:
                    best = o
                elif ("less" in l or "kurang" in l or "under" in l or "<" in l) and n < ln[0]:
                    return o
                elif ln[0] == n:
                    return o
        if best:
            return best
    for o, l in labels:
        if l and (l in a or a in l):
            return o
    return None


class AnswerEngine:
    def __init__(self, cfg: AppConfig, db: Database, ai: ClaudeAI | None = None):
        self.cfg = cfg
        self.db = db
        self.ai = ai
        self._user_rules = []
        for r in cfg.answers:
            try:
                self._user_rules.append((re.compile(r.pattern, re.I), r.answer))
            except re.error:
                log.warning("Regex jawaban tidak valid di config: %s", r.pattern)

    def _cache_get(self, key: str, origin: str) -> str | None:
        with self.db.session() as s:
            row = s.scalar(select(QAAnswer).where(QAAnswer.key == key, QAAnswer.origin == origin))
            if row:
                row.uses += 1
                return row.answer
        return None

    def _cache_put(self, key: str, question: str, answer: str) -> None:
        with self.db.session() as s:
            row = s.scalar(select(QAAnswer).where(QAAnswer.key == key))
            if row is None:
                s.add(QAAnswer(key=key, question=question[:2000], answer=answer, origin="ai", uses=1))

    def deterministic(self, f: FormField, job: JobContext) -> tuple[str | None, str]:
        q = f.question
        if not q:
            return None, ""
        for rx, ans in self._user_rules:
            if rx.search(q):
                return ans, "config"
        key = normalize_question(q)
        if (a := self._cache_get(key, "user")) is not None:
            return a, "bank(user)"
        ql = normalize_question(q)
        for rx, fn in BUILTIN_RULES:
            if rx.search(ql):
                try:
                    a = fn(self.cfg.profile, job, f)
                except Exception:  # aturan tidak boleh menjatuhkan proses lamaran
                    a = None
                if a:
                    return a, "profil"
                break
        if (a := self._cache_get(key, "ai")) is not None:
            return a, "bank(ai)"
        return None, ""

    def resolve(self, f: FormField, job: JobContext, *, allow_ai: bool = True) -> tuple[str | None, str]:
        answer, origin = self.deterministic(f, job)
        if answer is not None or not allow_ai or not self.ai or not self.ai.available or not f.question:
            return answer, origin
        opts = [o.label for o in f.options] or None
        try:
            answer = self.ai.answer(job, f.question, f.kind, opts)
        except AIError as exc:
            log.info("AI gagal menjawab '%s': %s", f.question, exc)
            return None, ""
        if answer:
            self._cache_put(normalize_question(f.question), f.question, answer)
            return answer, "ai"
        return None, ""
