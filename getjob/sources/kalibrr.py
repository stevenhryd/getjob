"""Kalibrr: API JSON publik job board."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..textutil import html_to_text
from .base import JobPosting, JobSource

SEARCH_URL = "https://www.kalibrr.com/kjs/job_board/search"
PAGE_SIZE = 30


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _salary(item: dict[str, Any]) -> str | None:
    lo, hi = item.get("base_salary"), item.get("maximum_salary")
    cur = item.get("salary_currency") or "IDR"
    if lo and hi:
        return f"{cur} {int(lo):,} - {int(hi):,}".replace(",", ".")
    if lo:
        return f"{cur} {int(lo):,}".replace(",", ".")
    return None


def parse_search_json(data: dict[str, Any]) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    for item in data.get("jobs") or []:
        job_id = str(item.get("id") or "")
        if not job_id:
            continue
        company = item.get("company") or {}
        code = company.get("code") or _slug(company.get("name") or "company")
        loc = (item.get("google_location") or {}).get("address_components") or {}
        location = ", ".join(x for x in [loc.get("city"), loc.get("region")] if x)
        desc = "\n\n".join(
            x for x in [html_to_text(item.get("description")), html_to_text(item.get("qualifications"))] if x
        )
        title = item.get("name") or ""
        external = item.get("application_url") or item.get("external_application_url")
        jobs.append(
            JobPosting(
                source="kalibrr",
                external_id=job_id,
                url=f"https://www.kalibrr.com/c/{code}/jobs/{job_id}/{_slug(title)}",
                title=title,
                company=company.get("name") or item.get("company_name") or "",
                location=location,
                description=desc,
                salary=_salary(item),
                posted_at=_parse_dt(item.get("activation_date")),
                is_remote=bool(item.get("is_work_from_home")) or None,
                apply_method=None if external else "kalibrr",
                apply_url=external or None,
                raw={"tenure": item.get("tenure"), "work_experience": item.get("work_experience")},
            )
        )
    return jobs


class KalibrrSource(JobSource):
    name = "kalibrr"

    def search(self, keyword: str, location: str | None) -> list[JobPosting]:
        results: list[JobPosting] = []
        offset = 0
        limit = self.config.search.max_results_per_query
        while len(results) < limit:
            params: dict[str, Any] = {
                "limit": PAGE_SIZE,
                "offset": offset,
                "text": keyword,
                "sort_field": "activation_date",
                "sort_direction": "desc",
            }
            if location and location.lower() != "indonesia":
                params["location"] = location
            resp = self.http.get(SEARCH_URL, params=params, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                break
            batch = parse_search_json(resp.json())
            if not batch:
                break
            results.extend(batch)
            offset += PAGE_SIZE
        return results[:limit]
