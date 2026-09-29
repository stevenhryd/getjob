"""JobStreet Indonesia (SEEK): API JSON yang dipakai situsnya sendiri."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from ..textutil import html_to_text
from .base import JobPosting, JobSource

SEARCH_URL = "https://id.jobstreet.com/api/jobsearch/v5/search"
JOB_URL = "https://id.jobstreet.com/id/job/{id}"
PAGE_SIZE = 30


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _location(item: dict[str, Any]) -> str:
    locs = item.get("locations")
    if isinstance(locs, list) and locs:
        return ", ".join(str(l.get("label", "")) for l in locs if isinstance(l, dict) and l.get("label"))
    parts = [item.get("suburb"), item.get("location"), item.get("area")]
    return ", ".join(p for p in parts if p)


def _is_remote(item: dict[str, Any]) -> bool | None:
    wa = item.get("workArrangements") or {}
    data = wa.get("data") if isinstance(wa, dict) else wa
    labels = []
    for entry in data or []:
        if isinstance(entry, dict):
            label = entry.get("label")
            labels.append(label.get("text", "") if isinstance(label, dict) else str(label or ""))
    text = " ".join(labels).lower()
    if "remote" in text or "jarak jauh" in text:
        return True
    return None


def parse_search_json(data: dict[str, Any]) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    for item in data.get("data") or []:
        job_id = str(item.get("id") or "")
        if not job_id:
            continue
        advertiser = item.get("advertiser") or {}
        company = item.get("companyName") or advertiser.get("description") or ""
        bullets = item.get("bulletPoints") or []
        teaser = item.get("teaser") or ""
        desc = "\n".join([teaser, *[f"- {b}" for b in bullets]]).strip()
        work_types = item.get("workTypes") or ([item["workType"]] if item.get("workType") else [])
        jobs.append(
            JobPosting(
                source="jobstreet",
                external_id=job_id,
                url=JOB_URL.format(id=job_id),
                title=item.get("title") or "",
                company=company,
                location=_location(item),
                description=desc,
                salary=item.get("salaryLabel") or item.get("salary") or None,
                posted_at=_parse_dt(item.get("listingDate")),
                is_remote=_is_remote(item),
                apply_method="jobstreet",
                raw={"work_types": work_types},
            )
        )
    return jobs


def parse_detail_html(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one('[data-automation="jobAdDetails"]')
    return {
        "description": html_to_text(str(el)) if el else "",
        "link_out": '"isLinkOut":true' in html,
    }


class JobStreetSource(JobSource):
    name = "jobstreet"

    def search(self, keyword: str, location: str | None) -> list[JobPosting]:
        results: list[JobPosting] = []
        page = 1
        limit = self.config.search.max_results_per_query
        while len(results) < limit:
            params = {
                "siteKey": "ID-Main",
                "sourcesystem": "houston",
                "keywords": keyword,
                "page": page,
                "pageSize": PAGE_SIZE,
                "locale": "id-ID",
                "daterange": self.config.search.max_age_days,
            }
            if location and location.lower() != "indonesia":
                params["where"] = location
            resp = self.http.get(SEARCH_URL, params=params, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                break
            batch = parse_search_json(resp.json())
            if not batch:
                break
            results.extend(batch)
            page += 1
        return results[:limit]

    def fetch_details(self, job: JobPosting) -> JobPosting:
        resp = self.http.get(job.url)
        if resp.status_code != 200:
            return job
        data = parse_detail_html(resp.text)
        if data["description"]:
            job.description = data["description"]
        if data["link_out"]:
            job.apply_method = None  # melamar di situs perusahaan
        return job
