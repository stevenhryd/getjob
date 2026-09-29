import httpx

from getjob.sources import HttpClient, SourceContext
from getjob.sources.glints import parse_graphql_payload
from getjob.sources.jobstreet import JobStreetSource, parse_detail_html as js_detail, parse_search_json as js_search
from getjob.sources.kalibrr import parse_search_json as kalibrr_parse
from getjob.sources.linkedin import LinkedInSource, parse_detail_html, parse_search_html
from getjob.sources.remote import (
    keyword_relevant,
    parse_greenhouse,
    parse_lever,
    parse_remoteok,
    parse_remotive,
)

from .conftest import fixture_json, fixture_text


def _http(handler) -> HttpClient:
    return HttpClient(min_delay=0, max_delay=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_linkedin_parse_search():
    jobs = parse_search_html(fixture_text("linkedin_search.html"))
    assert [j.external_id for j in jobs] == ["4011111111", "4022222222"]
    first = jobs[0]
    assert first.title == "Backend Engineer (Python)"
    assert first.company == "PT ACME Indonesia"
    assert first.url == "https://www.linkedin.com/jobs/view/4011111111/"
    assert first.apply_method == "linkedin"
    assert first.posted_at.year == 2026
    assert jobs[1].is_remote is True
    assert jobs[1].salary.startswith("IDR")


def test_linkedin_detail():
    data = parse_detail_html(fixture_text("linkedin_detail.html"))
    assert "3+ years experience with Python" in data["description"]
    assert data["criteria"]["Employment type"] == "Full-time"
    assert data["offsite_apply"] is False


def test_linkedin_search_paginates_until_empty(cfg):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.params["start"])
        assert request.url.params["f_AL"] == "true"
        body = fixture_text("linkedin_search.html") if request.url.params["start"] == "0" else ""
        return httpx.Response(200, text=body)

    src = LinkedInSource(SourceContext(cfg, _http(handler)))
    jobs = src.search("python", "Indonesia")
    assert len(jobs) == 2
    assert calls == ["0", "25"]


def test_jobstreet_parse_and_search(cfg):
    jobs = js_search(fixture_json("jobstreet_search.json"))
    assert len(jobs) == 2
    j = jobs[0]
    assert j.company == "PT Maju Jaya"
    assert j.location == "Jakarta Selatan, Jakarta Raya"
    assert j.is_remote is True
    assert "Django & FastAPI" in j.description
    assert j.url == "https://id.jobstreet.com/id/job/88001122"
    assert jobs[1].location == "Bekasi, Jawa Barat"

    pages = []

    def handler(request):
        pages.append(request.url.params["page"])
        if request.url.params["page"] == "1":
            return httpx.Response(200, json=fixture_json("jobstreet_search.json"))
        return httpx.Response(200, json={"data": []})

    found = JobStreetSource(SourceContext(cfg, _http(handler))).search("python", "Jakarta")
    assert len(found) == 2 and pages == ["1", "2"]


def test_jobstreet_detail():
    data = js_detail(fixture_text("jobstreet_detail.html"))
    assert "Membangun API dengan Python" in data["description"]
    assert data["link_out"] is False


def test_kalibrr_parse():
    jobs = kalibrr_parse(fixture_json("kalibrr_search.json"))
    assert jobs[0].company == "XYZ Tech"
    assert jobs[0].url == "https://www.kalibrr.com/c/xyz-tech/jobs/250001/software-engineer-backend"
    assert "scalable" in jobs[0].description and "- Python" in jobs[0].description
    assert jobs[0].salary == "IDR 10.000.000 - 20.000.000"
    assert jobs[0].apply_method == "kalibrr"
    assert jobs[1].apply_method is None  # lamaran eksternal


def test_glints_parse():
    jobs = parse_graphql_payload(fixture_json("glints_graphql.json"))
    assert len(jobs) == 2
    j = jobs[0]
    assert j.title == "Backend Developer" and j.company == "Startup Keren"
    assert j.is_remote is True
    assert "Python" in j.raw["skills"]
    assert "Pengalaman: 1-3 tahun" in j.description
    assert j.url.endswith("/backend-developer/0f9e8d7c-1111-2222-3333-444455556666")
    assert jobs[1].apply_method is None


def test_remote_parsers():
    rok = parse_remoteok(fixture_json("remoteok.json"))
    assert len(rok) == 2  # elemen legal dibuang
    assert rok[0].apply_method == "greenhouse"
    assert rok[1].apply_method is None

    rem = parse_remotive(fixture_json("remotive.json"))
    assert rem[0].company == "Async Ltd" and rem[0].is_remote

    gh = parse_greenhouse("gitlab", fixture_json("greenhouse.json"))
    assert gh[0].external_id == "gitlab:4455"
    assert gh[0].apply_url == "https://job-boards.greenhouse.io/gitlab/jobs/4455"
    assert "Ruby and Go experience" in gh[0].description

    lv = parse_lever("xendit", fixture_json("lever.json"))
    assert lv[0].apply_url.endswith("/apply")
    assert "PostgreSQL" in lv[0].description
    assert lv[0].posted_at is not None


def test_keyword_relevant():
    kws = ["python developer", "data analyst"]
    assert keyword_relevant(kws, "Senior Python Engineer")
    assert keyword_relevant(kws, "Analyst", "data sql")
    assert not keyword_relevant(kws, "Marketing Manager", "marketing")


def test_http_retries_on_429():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 2:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json={"ok": True})

    resp = _http(handler).get("https://example.com/api")
    assert resp.status_code == 200 and len(attempts) == 2
