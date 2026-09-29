from __future__ import annotations

from ..config import AppConfig
from .ai import AIError, AIScore, AIUnavailable, ClaudeAI, read_resume_text
from .keyword import KeywordResult, hard_filter, keyword_score


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def template_cover_letter(cfg: AppConfig, title: str, company: str, language: str, skills: list[str]) -> str:
    """Cover letter dari template di config (dipakai saat AI tidak tersedia)."""
    p = cfg.profile
    template = cfg.cover_letter.template_id if language == "id" else cfg.cover_letter.template_en
    top_skills = skills[:4] or p.skills[:4]
    values = _SafeDict(
        full_name=p.full_name,
        headline=p.headline or p.current_title or ("profesional IT" if language == "id" else "software professional"),
        years=f"{p.years_experience:g}",
        title=title,
        company=company or ("perusahaan Anda" if language == "id" else "your company"),
        skills=", ".join(top_skills) if top_skills else "-",
        phone=p.phone,
        email=p.email,
        city=p.city,
    )
    return template.format_map(values).strip()


__all__ = [
    "AIError",
    "AIScore",
    "AIUnavailable",
    "ClaudeAI",
    "KeywordResult",
    "hard_filter",
    "keyword_score",
    "read_resume_text",
    "template_cover_letter",
]
