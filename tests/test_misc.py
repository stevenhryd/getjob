from pypdf import PdfReader

from getjob.cli import build_parser, main
from getjob.config import load_config
from getjob.db import Database, dedupe_key
from getjob.pdf import text_to_pdf


def test_text_to_pdf_roundtrip(tmp_path):
    text = "Yth. Tim (HR),\n\n" + "Kalimat panjang untuk tes pembungkusan baris. " * 40 + "\nSalam,\nBudi"
    path = text_to_pdf(text, tmp_path / "cl.pdf")
    extracted = "".join(p.extract_text() for p in PdfReader(str(path)).pages)
    assert "Yth. Tim (HR)," in extracted and "Budi" in extracted


def test_dedupe_key():
    assert dedupe_key("PT ACME Indonesia Tbk", "Python Developer") == dedupe_key("ACME Indonesia", "python  developer!")
    assert dedupe_key("A", "Dev") != dedupe_key("B", "Dev")


def test_kv_helpers():
    db = Database(":memory:")
    assert db.kv_incr("x") == 1 and db.kv_incr("x", 2) == 3
    db.kv_set("cmd", {"a": 1})
    assert db.kv_pop("cmd") == {"a": 1} and db.kv_pop("cmd") is None
    db.set_platform_state("linkedin", "ok")
    db.set_platform_paused("linkedin", True)
    assert db.platform_state("linkedin")["status"] == "ok" and db.platform_paused("linkedin")


def test_cli_init_creates_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    assert (tmp_path / "config.yaml").exists()
    env = (tmp_path / ".env").read_text()
    assert "GETJOB_DASHBOARD_PASSWORD=" in env and "GETJOB_SECRET_KEY=" in env
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.base_dir == tmp_path
    main(["init"])  # tidak menimpa
    assert (tmp_path / ".env").read_text() == env


def test_parser_commands():
    p = build_parser()
    args = p.parse_args(["apply", "--job", "3", "--job", "4", "--live"])
    assert args.job == [3, 4] and args.live
    assert p.parse_args(["login"]).platforms == []
    assert p.parse_args(["login", "linkedin", "glints"]).platforms == ["linkedin", "glints"]
