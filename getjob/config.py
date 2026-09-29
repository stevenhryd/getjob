"""Pemuatan & validasi konfigurasi (config.yaml + .env)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = PROJECT_ROOT / "config.example.yaml"
DEFAULT_CONFIG = Path("config.yaml")

# Nama platform yang dikenal program.
PLATFORMS = ("linkedin", "jobstreet", "glints", "kalibrr", "greenhouse", "lever")
SOURCES = ("linkedin", "jobstreet", "glints", "kalibrr", "remoteok", "remotive", "greenhouse", "lever")


class Profile(BaseModel):
    full_name: str
    email: str
    phone: str = ""
    city: str = ""
    country: str = "Indonesia"
    headline: str = ""
    summary: str = ""
    years_experience: float = 0
    skills: list[str] = Field(default_factory=list)
    # Tahun pengalaman per skill, contoh: {python: 3, django: 2}
    skill_years: dict[str, float] = Field(default_factory=dict)
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    current_company: str = ""
    current_title: str = ""
    education: str = ""
    expected_salary_idr: int | None = None  # per bulan
    expected_salary_usd: int | None = None  # per tahun (lowongan luar negeri)
    notice_period: str = "1 bulan"
    notice_period_days: int = 30
    willing_to_relocate: bool = False
    need_visa_sponsorship: bool = True
    authorized_countries: list[str] = Field(default_factory=lambda: ["Indonesia"])
    languages: list[str] = Field(default_factory=lambda: ["Indonesia", "English"])
    gender: str = ""  # kosong = "Prefer not to say"
    resume_path: str = "data/cv.pdf"
    resume_path_en: str | None = None

    @property
    def first_name(self) -> str:
        return self.full_name.split()[0] if self.full_name.strip() else ""

    @property
    def last_name(self) -> str:
        parts = self.full_name.split()
        return " ".join(parts[1:]) if len(parts) > 1 else parts[0] if parts else ""


class SearchConfig(BaseModel):
    keywords: list[str] = Field(default_factory=lambda: ["python developer"])
    locations: list[str] = Field(default_factory=lambda: ["Indonesia"])
    max_age_days: int = 14
    max_results_per_query: int = 50
    # Kalau diisi, judul lowongan wajib mengandung salah satu kata ini.
    title_must_include_any: list[str] = Field(default_factory=list)
    title_exclude: list[str] = Field(default_factory=list)
    description_exclude: list[str] = Field(default_factory=list)
    company_exclude: list[str] = Field(default_factory=list)


class SourceToggle(BaseModel):
    enabled: bool = True
    auto_apply: bool = True


class LinkedInSource(SourceToggle):
    easy_apply_only: bool = True
    location: str = "Indonesia"
    remote_only: bool = False


class RemoteOKSource(SourceToggle):
    tags: list[str] = Field(default_factory=list)


class RemotiveSource(SourceToggle):
    category: str = "software-dev"


class GreenhouseSource(SourceToggle):
    boards: list[str] = Field(default_factory=list)


class LeverSource(SourceToggle):
    companies: list[str] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    linkedin: LinkedInSource = Field(default_factory=LinkedInSource)
    jobstreet: SourceToggle = Field(default_factory=SourceToggle)
    glints: SourceToggle = Field(default_factory=SourceToggle)
    kalibrr: SourceToggle = Field(default_factory=SourceToggle)
    remoteok: RemoteOKSource = Field(default_factory=RemoteOKSource)
    remotive: RemotiveSource = Field(default_factory=RemotiveSource)
    greenhouse: GreenhouseSource = Field(default_factory=lambda: GreenhouseSource(enabled=False))
    lever: LeverSource = Field(default_factory=lambda: LeverSource(enabled=False))

    def get(self, name: str) -> SourceToggle:
        return getattr(self, name)


class MatchingConfig(BaseModel):
    # hybrid: skor >= auto_apply_threshold dikirim otomatis, >= review_threshold masuk antrian review
    # auto:   semua skor >= review_threshold dikirim otomatis
    # manual: semua skor >= review_threshold masuk antrian review
    mode: Literal["hybrid", "auto", "manual"] = "hybrid"
    auto_apply_threshold: int = 80
    review_threshold: int = 60
    # Lowongan dengan skor kata kunci di bawah ini langsung dilewati (hemat biaya AI).
    prefilter_min_score: int = 20
    # Abaikan lowongan yang sama (perusahaan + judul) yang sudah dilamar dalam N hari.
    dedupe_days: int = 45


class ApplyConfig(BaseModel):
    # dry_run = isi form sampai tombol kirim terakhir, TIDAK dikirim. Matikan setelah uji coba OK.
    dry_run: bool = True
    daily_limit_total: int = 40
    daily_limits: dict[str, int] = Field(
        default_factory=lambda: {
            "linkedin": 15,
            "jobstreet": 15,
            "glints": 15,
            "kalibrr": 15,
            "greenhouse": 10,
            "lever": 10,
        }
    )
    delay_seconds: tuple[int, int] = (60, 180)
    active_hours: str = "08:00-21:00"
    max_attempts: int = 2
    max_steps: int = 12
    # Batas percobaan (termasuk yang gagal/manual) per siklus, supaya tidak "membombardir" platform.
    max_per_run: int = 10

    @field_validator("active_hours")
    @classmethod
    def _check_hours(cls, v: str) -> str:
        start, _, end = v.partition("-")
        for part in (start, end):
            hh, _, mm = part.strip().partition(":")
            if not (hh.isdigit() and mm.isdigit()):
                raise ValueError("active_hours harus berformat HH:MM-HH:MM")
        return v


class ScheduleConfig(BaseModel):
    search_interval_minutes: int = 180
    apply_interval_minutes: int = 20
    timezone: str = "Asia/Jakarta"


class BrowserConfig(BaseModel):
    headless: bool = False
    slow_mo_ms: int = 0
    timeout_ms: int = 30000
    executable_path: str | None = None
    lock_timeout_seconds: int = 5


class AIConfig(BaseModel):
    enabled: bool = True
    model: str = "claude-opus-5-5"
    effort_scoring: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    effort_writing: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_calls_per_day: int = 150
    use_fallbacks: bool = True

    @property
    def api_key_present(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class CoverLetterConfig(BaseModel):
    template_id: str = (
        "Yth. Tim Rekrutmen {company},\n\n"
        "Saya {full_name}, {headline} dengan pengalaman {years} tahun. Saya tertarik melamar posisi "
        "{title} di {company}. Keahlian saya di {skills} relevan dengan kebutuhan posisi ini.\n\n"
        "Saya terbiasa bekerja dalam tim, cepat belajar, dan fokus pada hasil. CV saya terlampir "
        "untuk pertimbangan Bapak/Ibu. Terima kasih atas waktu dan perhatiannya.\n\n"
        "Hormat saya,\n{full_name}\n{phone} | {email}"
    )
    template_en: str = (
        "Dear {company} Hiring Team,\n\n"
        "I am {full_name}, a {headline} with {years} years of experience. I am excited to apply for "
        "the {title} role at {company}. My experience with {skills} maps well to what this role needs.\n\n"
        "I enjoy collaborating with teams, learn quickly, and care about shipping reliable work. "
        "My resume is attached for your consideration. Thank you for your time.\n\n"
        "Best regards,\n{full_name}\n{phone} | {email}"
    )


class AnswerRule(BaseModel):
    pattern: str  # regex (tidak peka huruf besar/kecil) terhadap teks pertanyaan
    answer: str


class DashboardConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class AppConfig(BaseModel):
    profile: Profile
    search: SearchConfig = Field(default_factory=SearchConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    cover_letter: CoverLetterConfig = Field(default_factory=CoverLetterConfig)
    answers: list[AnswerRule] = Field(default_factory=list)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    data_dir: str = "data"

    # Diisi saat load: folder tempat config.yaml berada (untuk path relatif).
    base_dir: Path = Field(default_factory=Path.cwd, exclude=True)

    def path(self, p: str | Path) -> Path:
        p = Path(p).expanduser()
        return p if p.is_absolute() else (self.base_dir / p)

    @property
    def data_path(self) -> Path:
        return self.path(self.data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_path / "getjob.db"

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_path / "browser"

    @property
    def screenshots_dir(self) -> Path:
        return self.data_path / "screenshots"

    @property
    def files_dir(self) -> Path:
        return self.data_path / "files"

    def enabled_sources(self) -> list[str]:
        return [s for s in SOURCES if self.sources.get(s).enabled]

    def ensure_dirs(self) -> None:
        for d in (self.data_path, self.browser_profile_dir, self.screenshots_dir, self.files_dir):
            d.mkdir(parents=True, exist_ok=True)


class ConfigError(RuntimeError):
    pass


def load_env(base_dir: Path | None = None) -> None:
    env_file = (base_dir or Path.cwd()) / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


def load_config(path: str | Path | None = None) -> AppConfig:
    path = Path(path or os.environ.get("GETJOB_CONFIG") or DEFAULT_CONFIG).expanduser().resolve()
    if not path.exists():
        raise ConfigError(
            f"File konfigurasi tidak ditemukan: {path}\n"
            "Jalankan `getjob init` untuk membuat config.yaml dari contoh."
        )
    load_env(path.parent)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    try:
        cfg = AppConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError
        raise ConfigError(f"config.yaml tidak valid:\n{exc}") from exc
    cfg.base_dir = path.parent
    return cfg


def dashboard_password() -> str | None:
    return os.environ.get("GETJOB_DASHBOARD_PASSWORD") or None


_PROCESS_SECRET = secrets.token_hex(32)


def secret_key() -> str:
    # Tanpa GETJOB_SECRET_KEY, sesi login dashboard hilang setiap program restart.
    return os.environ.get("GETJOB_SECRET_KEY") or _PROCESS_SECRET
