"""Glints: dibuka lewat browser (dilindungi anti-bot), hasil diambil dari respons GraphQL situsnya."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import quote_plus

from ..textutil import clip
from .base import JobPosting, JobSource, SourceError

EXPLORE_URL = (
    "https://glints.com/id/opportunities/jobs/explore?keyword={kw}"
    "&country=ID&locationName=All+Cities%2FProvinces&sortBy=LATEST"
)
JOB_URL = "https://glints.com/id/opportunities/jobs/{slug}/{id}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "job"


def _iter_job_lists(node: Any) -> Iterable[list]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "jobsInPage" and isinstance(value, list):
                yield value
            else:
                yield from _iter_job_lists(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_job_lists(value)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _salary(item: dict) -> str | None:
    for s in item.get("salaries") or []:
        lo, hi, cur = s.get("minAmount"), s.get("maxAmount"), s.get("CurrencyCode") or "IDR"
        if lo or hi:
            return f"{cur} {int(lo or 0):,} - {int(hi or 0):,}".replace(",", ".")
    return None


def parse_graphql_payload(payload: Any) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    for items in _iter_job_lists(payload):
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            job_id = str(item["id"])
            title = item.get("title") or ""
            company = (item.get("company") or {}).get("name") or ""
            city = (item.get("city") or {}).get("name") or ""
            loc = (item.get("location") or {}).get("formattedName") or city
            skills = [
                (s.get("skill") or {}).get("name")
                for s in item.get("skills") or []
                if isinstance(s, dict) and (s.get("skill") or {}).get("name")
            ]
            yrs_min, yrs_max = item.get("minYearsOfExperience"), item.get("maxYearsOfExperience")
            category = (item.get("hierarchicalJobCategory") or {}).get("name") or ""
            desc_parts = [
                f"Kategori: {category}" if category else "",
                f"Skill: {', '.join(skills)}" if skills else "",
                f"Pengalaman: {yrs_min}-{yrs_max} tahun" if yrs_min is not None else "",
                f"Pendidikan: {item.get('educationLevel')}" if item.get("educationLevel") else "",
            ]
            external = any(
                "external" in k.lower() and isinstance(v, str) and v.startswith("http") for k, v in item.items()
            )
            arrangement = str(item.get("workArrangementOption") or "").upper()
            jobs.append(
                JobPosting(
                    source="glints",
                    external_id=job_id,
                    url=JOB_URL.format(slug=_slug(title), id=job_id),
                    title=title,
                    company=company,
                    location=loc,
                    description="\n".join(p for p in desc_parts if p),
                    salary=_salary(item),
                    posted_at=_parse_dt(item.get("updatedAt") or item.get("createdAt")),
                    is_remote=True if arrangement == "REMOTE" else None,
                    apply_method=None if external else "glints",
                    raw={"skills": skills, "arrangement": arrangement},
                )
            )
    return jobs


class GlintsSource(JobSource):
    name = "glints"
    needs_browser = True

    def queries(self) -> Iterable[tuple[str, str | None]]:
        for kw in self.config.search.keywords:
            yield kw, None

    def search(self, keyword: str, location: str | None) -> list[JobPosting]:
        if not self.ctx.has_browser:
            raise SourceError("Glints butuh browser (dilewati karena browser sedang dipakai/tidak tersedia).")
        page = self.ctx.page()
        captured: list[Any] = []

        def on_response(resp) -> None:
            if "graphql" not in resp.url:
                return
            post = resp.request.post_data or ""
            if "searchJobs" not in resp.url and "searchJobs" not in post:
                return
            try:
                captured.append(resp.json())
            except Exception:
                pass

        page.on("response", on_response)
        try:
            page.goto(EXPLORE_URL.format(kw=quote_plus(keyword)), wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            for _ in range(3):
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(1500)
        finally:
            page.remove_listener("response", on_response)

        seen: set[str] = set()
        jobs: list[JobPosting] = []
        for payload in captured:
            for job in parse_graphql_payload(payload):
                if job.external_id not in seen:
                    seen.add(job.external_id)
                    jobs.append(job)
        return jobs[: self.config.search.max_results_per_query]

    def fetch_details(self, job: JobPosting) -> JobPosting:
        if not self.ctx.has_browser:
            return job
        page = self.ctx.page()
        page.goto(job.url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        text = ""
        for sel in ('[class*="JobDescription"]', '[class*="DescriptionContainer"]', "main"):
            loc = page.locator(sel).first
            if loc.count():
                text = loc.inner_text(timeout=5000)
                if len(text) > 200:
                    break
        if text:
            job.description = clip(f"{job.description}\n\n{text}".strip(), 12000)
        return job
