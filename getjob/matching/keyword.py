"""Penilaian kecocokan tanpa AI: filter keras + skor kata kunci (0-100)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from ..config import AppConfig
from ..db import utcnow
from ..sources.base import JobPosting
from ..textutil import TECH_TERMS, term_in_text

_YEARS_RE = re.compile(
    r"(?:minimal|min\.?|at least|minimum)?\s*(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:tahun|thn|years?|yrs?)",
    re.IGNORECASE,
)
_SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head|manager|director|vp)\b", re.IGNORECASE)
_TITLE_GENERIC = {"developer", "engineer", "programmer", "staff", "the", "and", "of", "for"}


@dataclass
class KeywordResult:
    score: int
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    reject_reason: str | None = None

    @property
    def rejected(self) -> bool:
        return self.reject_reason is not None


def _contains_any(text: str, words: list[str]) -> str | None:
    tl = text.lower()
    for w in words:
        if w and term_in_text(w, tl):
            return w
    return None


def required_years(text: str) -> int | None:
    years = [int(m.group(1)) for m in _YEARS_RE.finditer(text or "") if 0 < int(m.group(1)) <= 20]
    return min(years) if years else None


def hard_filter(cfg: AppConfig, job: JobPosting) -> str | None:
    s = cfg.search
    if not job.title:
        return "judul kosong"
    if (w := _contains_any(job.title, s.title_exclude)) is not None:
        return f"judul mengandung '{w}'"
    if s.title_must_include_any and _contains_any(job.title, s.title_must_include_any) is None:
        return "judul tidak mengandung kata wajib"
    if (w := _contains_any(job.company, s.company_exclude)) is not None:
        return f"perusahaan dikecualikan ('{w}')"
    if (w := _contains_any(job.description, s.description_exclude)) is not None:
        return f"deskripsi mengandung '{w}'"
    if job.posted_at and job.posted_at < utcnow() - timedelta(days=s.max_age_days + 1):
        return f"lowongan lebih lama dari {s.max_age_days} hari"
    return None


def keyword_score(cfg: AppConfig, job: JobPosting) -> KeywordResult:
    reject = hard_filter(cfg, job)
    if reject:
        return KeywordResult(score=0, reject_reason=reject)

    profile = cfg.profile
    title_l = job.title.lower()
    text = f"{job.title}\n{job.description}\n{' '.join(job.raw.get('skills') or job.raw.get('tags') or [])}"
    text_l = text.lower()
    reasons: list[str] = []

    # 1) Skill: istilah teknologi di lowongan yang dikuasai profil.
    profile_skills = {s.lower() for s in profile.skills}
    job_terms = {t for t in TECH_TERMS if term_in_text(t, text_l)}
    job_terms |= {s for s in profile_skills if term_in_text(s, text_l)}
    matched = sorted(t for t in job_terms if t in profile_skills)
    missing = sorted(t for t in job_terms if t not in profile_skills)
    if job_terms:
        coverage = len(matched) / len(job_terms)
        # Jangan terlalu menghukum lowongan yang menyebut banyak "nice to have".
        coverage = min(1.0, coverage * 1.25)
    else:
        coverage = 0.5
    reasons.append(f"skill cocok {len(matched)}/{len(job_terms) or '?'}")

    # 2) Judul vs kata kunci pencarian / headline profil.
    title_match = 0.0
    targets = [*cfg.search.keywords, profile.headline, profile.current_title]
    for kw in (k for k in targets if k):
        kw_l = kw.lower()
        if kw_l in title_l:
            title_match = 1.0
            break
        tokens = [t for t in re.findall(r"[a-z0-9+#.]+", kw_l) if t not in _TITLE_GENERIC and len(t) > 1]
        if tokens:
            hit = sum(1 for t in tokens if term_in_text(t, title_l)) / len(tokens)
            title_match = max(title_match, 0.75 * hit)
    reasons.append(f"judul cocok {int(title_match * 100)}%")

    # 3) Pengalaman yang diminta vs profil.
    penalty = 0.0
    req = required_years(job.description)
    if req is not None and req > profile.years_experience + 1:
        penalty += min(0.35, 0.1 * (req - profile.years_experience))
        reasons.append(f"minta {req} th pengalaman")
    if _SENIOR_RE.search(job.title) and profile.years_experience < 4:
        penalty += 0.15
        reasons.append("posisi senior")

    score = 100 * (0.55 * coverage + 0.45 * title_match) - 100 * penalty
    return KeywordResult(
        score=max(0, min(100, round(score))),
        matched=matched,
        missing=missing,
        reasons=reasons,
    )
