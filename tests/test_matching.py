import json
from datetime import timedelta

import pytest

from getjob.db import utcnow
from getjob.matching import AIError, AIUnavailable, ClaudeAI, keyword_score, template_cover_letter
from getjob.matching.keyword import required_years
from getjob.sources.base import JobPosting
from getjob.textutil import detect_language, html_to_text, term_in_text

from .conftest import fake_message


def job(title, desc="", company="ACME", **kw):
    return JobPosting(source="linkedin", external_id=title, url="u", title=title, company=company, description=desc, **kw)


def test_keyword_score_orders_relevant_jobs(cfg):
    good = keyword_score(cfg, job("Backend Developer", "Python, Django, PostgreSQL, Docker. Minimal 2 tahun pengalaman."))
    meh = keyword_score(cfg, job("Frontend Developer", "React, TypeScript, Next.js, Figma"))
    senior = keyword_score(cfg, job("Senior Backend Developer", "Python. Minimum 8 years of experience."))
    assert good.score > senior.score > 0
    assert good.score > meh.score
    assert "python" in good.matched and "react" in meh.missing


def test_hard_filters(cfg):
    assert keyword_score(cfg, job("Head of Engineering")).reject_reason
    cfg.search.company_exclude = ["Scam Corp"]
    assert "dikecualikan" in keyword_score(cfg, job("Python Developer", company="PT Scam Corp")).reject_reason
    old = job("Python Developer", posted_at=utcnow() - timedelta(days=60))
    assert "lebih lama" in keyword_score(cfg, old).reject_reason


def test_required_years_and_terms():
    assert required_years("Minimal 3 tahun pengalaman, 5+ years preferred") == 3
    assert required_years("no numbers") is None
    assert term_in_text("c++", "we use c++ and rust")
    assert not term_in_text("c", "we use c++ only")
    assert term_in_text("node.js", "experience with node.js")
    assert not term_in_text("java", "javascript developer")


def test_text_utils():
    assert detect_language("Kami mencari developer yang memiliki pengalaman minimal 2 tahun") == "id"
    assert detect_language("We are looking for an engineer with experience in Python") == "en"
    assert html_to_text("<p>Hello<br>World</p><ul><li>A</li></ul>") == "Hello\nWorld\n- A"


def test_template_cover_letter(cfg):
    letter = template_cover_letter(cfg, "Python Developer", "ACME", "id", ["python", "django"])
    assert "Budi Santoso" in letter and "ACME" in letter and "python, django" in letter
    assert "{" not in letter
    en = template_cover_letter(cfg, "Engineer", "", "en", [])
    assert en.startswith("Dear your company Hiring Team")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        outer = self

        class Messages:
            def create(self, **kw):
                outer.calls.append(kw)
                r = outer.responses.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r

        self.beta = type("Beta", (), {"messages": Messages()})()


def test_claude_score_request_shape(cfg, db):
    cfg.ai.enabled = True
    payload = {"score": 87, "reason": "Cocok", "matched_skills": ["python"], "missing_skills": [], "red_flags": [], "language": "id"}
    client = FakeClient([fake_message(json.dumps(payload))])
    ai = ClaudeAI(cfg, db, client=client)
    res = ai.score(job("Python Developer", "Django"))
    assert res.score == 87 and res.language == "id"
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Budi Santoso" in kw["system"][0]["text"]
    assert "thinking" not in kw
    assert ai.calls_today() == 1


def test_claude_haiku_skips_effort_and_fallbacks(cfg, db):
    cfg.ai.enabled = True
    cfg.ai.model = "claude-haiku-4-5"
    client = FakeClient([fake_message("Surat")])
    ClaudeAI(cfg, db, client=client).cover_letter(job("Dev"), "id")
    kw = client.calls[0]
    assert "fallbacks" not in kw and "output_config" not in kw


def test_claude_refusal_and_budget(cfg, db):
    cfg.ai.enabled = True
    ai = ClaudeAI(cfg, db, client=FakeClient([fake_message("", stop_reason="refusal")]))
    with pytest.raises(AIError):
        ai.cover_letter(job("Dev"), "en")
    cfg.ai.max_calls_per_day = 1
    with pytest.raises(AIUnavailable):
        ai.cover_letter(job("Dev"), "en")


def test_claude_unavailable_without_key(cfg, db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    cfg.ai.enabled = True
    ai = ClaudeAI(cfg, db)
    assert not ai.available
    with pytest.raises(AIUnavailable):
        ai.score(job("Dev"))
