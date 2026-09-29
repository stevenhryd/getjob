from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from getjob.config import EXAMPLE_CONFIG, AppConfig
from getjob.db import Database
from getjob.matching.ai import AIScore

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str):
    return json.loads(fixture_text(name))


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    raw = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    cv = tmp_path / "cv.txt"
    cv.write_text("Backend developer. Python, Django, FastAPI, PostgreSQL, Docker, AWS. 3 tahun pengalaman.", encoding="utf-8")
    raw["profile"].update(full_name="Budi Santoso", email="anda@email.com", resume_path=str(cv))
    raw["data_dir"] = str(tmp_path / "data")
    raw["ai"]["enabled"] = False
    raw["apply"]["delay_seconds"] = [0, 0]
    raw["apply"]["active_hours"] = "00:00-23:59"
    config = AppConfig.model_validate(raw)
    config.base_dir = tmp_path
    config.ensure_dirs()
    return config


@pytest.fixture
def db() -> Database:
    return Database(":memory:")


class FakeAI:
    """Pengganti ClaudeAI untuk tes (tanpa jaringan)."""

    def __init__(self, scores: dict[str, int] | None = None, red_flags: dict[str, list] | None = None):
        self.scores = scores or {}
        self.red_flags = red_flags or {}
        self.available = True
        self.answers: list[str] = []

    def score(self, job) -> AIScore:
        return AIScore(
            score=self.scores.get(job.title, 50),
            reason=f"alasan untuk {job.title}",
            matched=["python"],
            missing=["go"],
            red_flags=self.red_flags.get(job.title, []),
            language="en",
        )

    def cover_letter(self, job, language: str) -> str:
        return f"Cover letter {language} untuk {job.title}"

    def answer(self, job, question, kind, options=None) -> str:
        self.answers.append(question)
        return "I love building reliable backend systems."

    def calls_today(self) -> int:
        return 0


@pytest.fixture
def fake_ai() -> FakeAI:
    return FakeAI()


def fake_message(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


@pytest.fixture(scope="session")
def browser():
    """Chromium sungguhan untuk tes form. Dilewati kalau browser Playwright belum terpasang."""
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Chromium Playwright tidak tersedia: {exc}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    p = ctx.new_page()
    p.set_default_timeout(5000)
    yield p
    ctx.close()
