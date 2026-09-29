"""Sumber lowongan remote/luar negeri: RemoteOK, Remotive, Greenhouse, Lever."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from ..textutil import html_to_text
from .base import JobPosting, JobSource, apply_method_for_url

GENERIC_TOKENS = {
    "developer", "engineer", "programmer", "senior", "junior", "lead", "staff", "principal",
    "specialist", "intern", "remote", "the", "and", "for", "sr", "jr", "mid", "level",
}
_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")


def keyword_relevant(keywords: list[str], title: str, extra: str = "") -> bool:
    """Saring kasar untuk sumber yang tidak mendukung pencarian kata kunci."""
    hay = f"{title} {extra}".lower()
    hay_tokens = set(_TOKEN_RE.findall(hay))
    for kw in keywords:
        kw_l = kw.lower().strip()
        if kw_l and kw_l in title.lower():
            return True
        tokens = [t for t in _TOKEN_RE.findall(kw_l) if len(t) >= 2 and t not in GENERIC_TOKENS]
        if tokens and any(t in hay_tokens for t in tokens):
            return True
    return False


def _from_epoch(value: Any, ms: bool = False) -> datetime | None:
    try:
        ts = float(value) / (1000 if ms else 1)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


class _SinglePassSource(JobSource):
    """Sumber yang diambil sekali per siklus lalu disaring lokal."""

    def queries(self) -> Iterable[tuple[str, str | None]]:
        yield "*", None


# ---- RemoteOK ------------------------------------------------------------------


def parse_remoteok(data: list[dict[str, Any]]) -> list[JobPosting]:
    jobs = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id") or not item.get("position"):
            continue  # elemen pertama berisi pemberitahuan legal
        apply_url = item.get("apply_url") or item.get("url")
        lo, hi = item.get("salary_min"), item.get("salary_max")
        jobs.append(
            JobPosting(
                source="remoteok",
                external_id=str(item["id"]),
                url=item.get("url") or f"https://remoteok.com/remote-jobs/{item['id']}",
                title=item.get("position") or "",
                company=item.get("company") or "",
                location=item.get("location") or "Remote",
                description=html_to_text(item.get("description")),
                apply_url=apply_url,
                apply_method=apply_method_for_url(apply_url),
                is_remote=True,
                salary=f"USD {lo:,} - {hi:,}/tahun" if lo and hi else None,
                posted_at=_from_epoch(item.get("epoch")) or _iso(item.get("date")),
                raw={"tags": item.get("tags") or []},
            )
        )
    return jobs


class RemoteOKSource(_SinglePassSource):
    name = "remoteok"
    min_interval_hours = 2

    def search(self, keyword: str, location: str | None) -> list[JobPosting]:
        tags = self.config.sources.remoteok.tags
        urls = [f"https://remoteok.com/api?tag={t}" for t in tags] or ["https://remoteok.com/api"]
        jobs: dict[str, JobPosting] = {}
        for url in urls:
            resp = self.http.get(url, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                continue
            for job in parse_remoteok(resp.json()):
                if keyword_relevant(self.config.search.keywords, job.title, " ".join(job.raw["tags"])):
                    jobs[job.external_id] = job
        return list(jobs.values())


# ---- Remotive ------------------------------------------------------------------


def parse_remotive(data: dict[str, Any]) -> list[JobPosting]:
    jobs = []
    for item in data.get("jobs") or []:
        if not item.get("id"):
            continue
        jobs.append(
            JobPosting(
                source="remotive",
                external_id=str(item["id"]),
                url=item.get("url") or "",
                title=item.get("title") or "",
                company=item.get("company_name") or "",
                location=item.get("candidate_required_location") or "Remote",
                description=html_to_text(item.get("description")),
                is_remote=True,
                salary=item.get("salary") or None,
                posted_at=_iso(item.get("publication_date")),
                raw={"tags": item.get("tags") or [], "job_type": item.get("job_type")},
            )
        )
    return jobs


_EXT_LINK_RE = re.compile(r"https://(?:boards|job-boards)\.greenhouse\.io/[^\s\"'<>]+|https://jobs\.lever\.co/[^\s\"'<>]+")


class RemotiveSource(_SinglePassSource):
    name = "remotive"
    # Remotive meminta API tidak dipanggil terlalu sering.
    min_interval_hours = 6

    def search(self, keyword: str, location: str | None) -> list[JobPosting]:
        params = {"category": self.config.sources.remotive.category, "limit": 200}
        resp = self.http.get("https://remotive.com/api/remote-jobs", params=params)
        if resp.status_code != 200:
            return []
        return [
            j
            for j in parse_remotive(resp.json())
            if keyword_relevant(self.config.search.keywords, j.title, " ".join(j.raw.get("tags") or []))
        ]

    def fetch_details(self, job: JobPosting) -> JobPosting:
        # Cari link lamaran ke Greenhouse/Lever di halaman Remotive.
        if not job.url:
            return job
        resp = self.http.get(job.url)
        if resp.status_code == 200:
            m = _EXT_LINK_RE.search(resp.text)
            if m:
                job.apply_url = m.group(0).rstrip("\\")
                job.apply_method = apply_method_for_url(job.apply_url)
        return job


# ---- Greenhouse ------------------------------------------------------------------


def parse_greenhouse(board: str, data: dict[str, Any]) -> list[JobPosting]:
    jobs = []
    for item in data.get("jobs") or []:
        if not item.get("id"):
            continue
        job_id = str(item["id"])
        location = (item.get("location") or {}).get("name") or ""
        jobs.append(
            JobPosting(
                source="greenhouse",
                external_id=f"{board}:{job_id}",
                url=item.get("absolute_url") or f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}",
                apply_url=f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}",
                apply_method="greenhouse",
                title=item.get("title") or "",
                company=item.get("company_name") or board.replace("-", " ").title(),
                location=location,
                description=html_to_text(item.get("content")),
                is_remote=("remote" in location.lower()) or None,
                posted_at=_iso(item.get("updated_at")),
                raw={"board": board},
            )
        )
    return jobs


class GreenhouseSource(_SinglePassSource):
    name = "greenhouse"

    def search(self, keyword: str, location: str | None) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for board in self.config.sources.greenhouse.boards:
            resp = self.http.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"})
            if resp.status_code != 200:
                continue
            jobs.extend(
                j for j in parse_greenhouse(board, resp.json()) if keyword_relevant(self.config.search.keywords, j.title)
            )
        return jobs


# ---- Lever ------------------------------------------------------------------------


def parse_lever(company: str, data: list[dict[str, Any]]) -> list[JobPosting]:
    jobs = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        cats = item.get("categories") or {}
        lists = "\n\n".join(
            f"{l.get('text', '')}\n{html_to_text(l.get('content'))}" for l in item.get("lists") or [] if isinstance(l, dict)
        )
        desc = "\n\n".join(x for x in [item.get("descriptionPlain"), lists, item.get("additionalPlain")] if x)
        workplace = str(item.get("workplaceType") or "").lower()
        jobs.append(
            JobPosting(
                source="lever",
                external_id=f"{company}:{item['id']}",
                url=item.get("hostedUrl") or f"https://jobs.lever.co/{company}/{item['id']}",
                apply_url=item.get("applyUrl") or f"https://jobs.lever.co/{company}/{item['id']}/apply",
                apply_method="lever",
                title=item.get("text") or "",
                company=company.replace("-", " ").title(),
                location=cats.get("location") or "",
                description=desc.strip(),
                is_remote=(workplace == "remote") or None,
                posted_at=_from_epoch(item.get("createdAt"), ms=True),
                raw={"commitment": cats.get("commitment"), "team": cats.get("team")},
            )
        )
    return jobs


class LeverSource(_SinglePassSource):
    name = "lever"

    def search(self, keyword: str, location: str | None) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for company in self.config.sources.lever.companies:
            resp = self.http.get(f"https://api.lever.co/v0/postings/{company}", params={"mode": "json"})
            if resp.status_code != 200:
                continue
            jobs.extend(
                j for j in parse_lever(company, resp.json()) if keyword_relevant(self.config.search.keywords, j.title)
            )
        return jobs
