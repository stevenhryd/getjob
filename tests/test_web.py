import pytest
from fastapi.testclient import TestClient

from getjob.db import Event, Job, QAAnswer, Status, utcnow
from getjob.pipeline import Pipeline
from getjob.web import create_app

from .conftest import FakeAI


@pytest.fixture
def client(cfg, db):
    pipe = Pipeline(cfg, db, ai=FakeAI())
    app = create_app(cfg, db, pipeline=pipe, password="rahasia")
    with db.session() as s:
        s.add_all([
            Job(source="linkedin", external_id="1", url="https://x/1", title="Python Developer", company="ACME",
                status=Status.REVIEW, score=72, score_source="ai", score_reason="Cocok", cover_letter="Halo",
                apply_method="linkedin", matched_skills=["python"], missing_skills=["go"]),
            Job(source="jobstreet", external_id="2", url="https://x/2", title="Data Engineer", company="Beta",
                status=Status.APPLIED, score=88, applied_at=utcnow(), apply_method="jobstreet"),
            Job(source="remoteok", external_id="3", url="https://x/3", title="Remote Dev", company="Gamma",
                status=Status.MANUAL, score=81),
        ])
        s.add(Event(message="Tes event", kind="apply"))
    return TestClient(app)


def login(c):
    r = c.post("/login", data={"password": "rahasia"}, follow_redirects=False)
    assert r.status_code == 303


def test_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.post("/login", data={"password": "salah"}).status_code == 401
    assert client.get("/health").json()["ok"] is True


def test_login_rate_limit(client):
    for _ in range(5):
        client.post("/login", data={"password": "salah"})
    assert client.post("/login", data={"password": "rahasia"}).status_code == 429


def test_pages_render(client):
    login(client)
    for path in ("/", "/review", "/jobs", "/jobs?status=active&q=python&min_score=50", "/jobs/1", "/answers",
                 "/activity", "/settings", "/api/stats"):
        r = client.get(path)
        assert r.status_code == 200, path
    home = client.get("/").text
    assert "Mode uji coba (dry run) aktif" in home
    assert "Worker tidak berjalan" in home
    assert "Python Developer" in client.get("/review").text
    stats = client.get("/api/stats").json()
    assert stats["funnel"][0]["value"] == 3
    assert sum(d["value"] for d in stats["per_day"]) == 1


def test_review_approve_and_dismiss(client, db):
    login(client)
    r = client.post("/jobs/1/action", data={"action": "approve", "cover_letter": "Surat baru"}, follow_redirects=False)
    assert r.status_code == 303
    with db.session() as s:
        job = s.get(Job, 1)
        assert job.status == Status.APPROVED and job.cover_letter == "Surat baru"
    client.post("/jobs/1/action", data={"action": "set_status", "status": Status.INTERVIEW})
    with db.session() as s:
        assert s.get(Job, 1).status == Status.INTERVIEW
    # lowongan eksternal tidak bisa masuk antrian otomatis
    client.post("/jobs/3/action", data={"action": "approve"})
    with db.session() as s:
        assert s.get(Job, 3).status == Status.MANUAL
    client.post("/jobs/bulk", data={"action": "dismiss", "ids": ["1", "3"]})
    with db.session() as s:
        assert s.get(Job, 1).status == Status.DISMISSED


def test_control_and_answers(client, db):
    login(client)
    client.post("/control", data={"action": "search"})
    assert db.kv_get("cmd:search") is True
    client.post("/control", data={"action": "pause"})
    assert db.kv_get("paused") is True
    client.post("/answers", data={"action": "add", "question": "Ekspektasi gaji?", "answer": "12 juta"})
    with db.session() as s:
        row = s.query(QAAnswer).one()
        assert row.origin == "user" and row.key == "ekspektasi gaji"
        rid = row.id
    client.post("/answers", data={"action": "update", "answer_id": str(rid), "answer": "13 juta"})
    with db.session() as s:
        assert s.get(QAAnswer, rid).answer == "13 juta"


def test_screenshot_path_is_confined(client, cfg):
    login(client)
    (cfg.screenshots_dir / "ok.png").write_bytes(b"\x89PNG")
    assert client.get("/screenshots/ok.png").status_code == 200
    assert client.get("/screenshots/..%2Fgetjob.db").status_code == 404


def test_back_redirect_stays_local(client):
    login(client)
    r = client.post("/control", data={"action": "resume"}, headers={"referer": "https://evil.example/phish"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"
    r = client.post("/control", data={"action": "resume"}, headers={"referer": "http://testserver/jobs?status=review"},
                    follow_redirects=False)
    assert r.headers["location"] == "/jobs?status=review"
