"""LinkedIn: pencarian lewat endpoint publik (guest), tanpa login."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from bs4 import BeautifulSoup

from ..textutil import html_to_text
from .base import JobPosting, JobSource

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
VIEW_URL = "https://www.linkedin.com/jobs/view/{id}/"
PAGE_SIZE = 25

_ID_RE = re.compile(r"jobPosting:(\d+)")


def parse_search_html(html: str) -> list[JobPosting]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[JobPosting] = []
    for card in soup.select("[data-entity-urn]"):
        m = _ID_RE.search(card.get("data-entity-urn", ""))
        if not m:
            continue
        job_id = m.group(1)
        title_el = card.select_one(".base-search-card__title") or card.select_one("h3")
        company_el = card.select_one(".base-search-card__subtitle") or card.select_one("h4")
        loc_el = card.select_one(".job-search-card__location")
        salary_el = card.select_one(".job-search-card__salary-info")
        time_el = card.select_one("time")
        posted = None
        if time_el and time_el.get("datetime"):
            try:
                posted = datetime.fromisoformat(time_el["datetime"])
            except ValueError:
                posted = None
        location = loc_el.get_text(" ", strip=True) if loc_el else ""
        jobs.append(
            JobPosting(
                source="linkedin",
                external_id=job_id,
                url=VIEW_URL.format(id=job_id),
                title=title_el.get_text(" ", strip=True) if title_el else "",
                company=company_el.get_text(" ", strip=True) if company_el else "",
                location=location,
                salary=salary_el.get_text(" ", strip=True) if salary_el else None,
                posted_at=posted,
                is_remote=("remote" in location.lower()) or None,
                apply_method="linkedin",
            )
        )
    return jobs


def parse_detail_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    criteria = {}
    for item in soup.select(".description__job-criteria-item"):
        k = item.select_one("h3")
        v = item.select_one("span")
        if k and v:
            criteria[k.get_text(strip=True)] = v.get_text(strip=True)
    offsite = soup.select_one("[data-tracking-control-name*='apply-link-offsite']") is not None
    return {
        "description": html_to_text(str(desc_el)) if desc_el else "",
        "criteria": criteria,
        "offsite_apply": offsite,
    }


class LinkedInSource(JobSource):
    name = "linkedin"

    def queries(self) -> Iterable[tuple[str, str | None]]:
        loc = self.config.sources.linkedin.location
        for kw in self.config.search.keywords:
            yield kw, loc

    def search(self, keyword: str, location: str | None) -> list[JobPosting]:
        cfg = self.config.sources.linkedin
        params = {
            "keywords": keyword,
            "location": location or "Indonesia",
            "f_TPR": f"r{self.config.search.max_age_days * 86400}",
            "sortBy": "DD",
        }
        if cfg.easy_apply_only:
            params["f_AL"] = "true"
        if cfg.remote_only:
            params["f_WT"] = "2"
        results: list[JobPosting] = []
        start = 0
        while len(results) < self.config.search.max_results_per_query:
            resp = self.http.get(SEARCH_URL, params={**params, "start": start})
            if resp.status_code != 200 or not resp.text.strip():
                break
            page = parse_search_html(resp.text)
            if not page:
                break
            results.extend(page)
            start += PAGE_SIZE
        return results[: self.config.search.max_results_per_query]

    def fetch_details(self, job: JobPosting) -> JobPosting:
        resp = self.http.get(DETAIL_URL.format(id=job.external_id))
        if resp.status_code != 200:
            return job
        data = parse_detail_html(resp.text)
        job.description = data["description"] or job.description
        job.raw["criteria"] = data["criteria"]
        if data["offsite_apply"]:
            job.apply_method = None  # bukan Easy Apply
        return job
