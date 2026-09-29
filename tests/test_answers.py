from getjob.answers import AnswerEngine, FormField, JobContext, Option, choose_option
from getjob.config import AnswerRule
from getjob.db import QAAnswer


def field(label, kind="text", options=None, required=True):
    return FormField(id="x", kind=kind, label=label, required=required,
                     options=[Option(id=str(i), label=o, value=o) for i, o in enumerate(options or [])])


JOB = JobContext(title="Backend Engineer", company="ACME", location="Jakarta, Indonesia", source="linkedin",
                 cover_letter="Surat lamaran saya")
REMOTE_JOB = JobContext(title="Engineer", company="Remote Co", location="Worldwide", source="remoteok")


def test_builtin_profile_answers(cfg, db):
    e = AnswerEngine(cfg, db)
    assert e.deterministic(field("First Name *"), JOB)[0] == "Budi"
    assert e.deterministic(field("Last name"), JOB)[0] == "Santoso"
    assert e.deterministic(field("Nama lengkap"), JOB)[0] == "Budi Santoso"
    assert e.deterministic(field("Nama perusahaan saat ini"), JOB)[0] == "PT Contoh Teknologi"
    assert e.deterministic(field("Email address"), JOB)[0] == "anda@email.com"
    assert e.deterministic(field("Nomor HP / WhatsApp"), JOB)[0] == "+6281234567890"
    assert e.deterministic(field("Expected salary"), JOB)[0] == "15000000"
    assert e.deterministic(field("Expected salary"), REMOTE_JOB)[0] == "30000"
    assert e.deterministic(field("How many years of work experience do you have with Python?"), JOB)[0] == "3"
    assert e.deterministic(field("How many years of experience do you have with Kubernetes?"), JOB)[0] == "0"
    assert e.deterministic(field("Are you willing to relocate?", "radio", ["Yes", "No"]), JOB)[0] == "No"
    assert e.deterministic(field("Cover letter", "textarea"), JOB)[0] == "Surat lamaran saya"
    assert e.deterministic(field("Gender", "select", ["Male", "Female", "Prefer not to say"]), JOB)[0] == "Prefer not to say"
    yes_no = field("Do you have at least 5 years of experience with Django?", "radio", ["Yes", "No"])
    assert e.deterministic(yes_no, JOB)[0] == "No"


def test_user_rules_and_bank_precedence(cfg, db):
    cfg.answers = [AnswerRule(pattern="salary", answer="Negotiable")]
    e = AnswerEngine(cfg, db)
    assert e.deterministic(field("Expected salary"), JOB) == ("Negotiable", "config")
    with db.session() as s:
        s.add(QAAnswer(key="what is your first name", question="What is your first name?", answer="Bud", origin="user"))
    assert e.deterministic(field("What is your first name?"), JOB) == ("Bud", "bank(user)")


def test_ai_answers_are_cached(cfg, db, fake_ai):
    e = AnswerEngine(cfg, db, ai=fake_ai)
    f = field("Why do you want to join us?", "textarea")
    ans, origin = e.resolve(f, JOB)
    assert origin == "ai" and ans
    ans2, origin2 = e.resolve(f, JOB)
    assert origin2 == "bank(ai)" and ans2 == ans
    assert len(fake_ai.answers) == 1
    # field opsional tidak memanggil AI
    assert e.resolve(field("Tell us a fun fact", "textarea", required=False), JOB, allow_ai=False) == (None, "")


def test_choose_option():
    opts = [Option(id=str(i), label=l) for i, l in enumerate(["Less than 1 year", "1-2 years", "3-5 years", "5+ years"])]
    assert choose_option("2", opts).label == "1-2 years"
    assert choose_option("4", opts).label == "3-5 years"
    assert choose_option("7", opts).label == "5+ years"
    assert choose_option("0", opts).label == "Less than 1 year"
    yn = [Option(id="1", label="Ya"), Option(id="2", label="Tidak")]
    assert choose_option("Yes", yn).label == "Ya"
    assert choose_option("No", yn).label == "Tidak"
    eeo = [Option(id="1", label="Male"), Option(id="2", label="I don't wish to answer")]
    assert choose_option("Decline to self-identify", eeo).label == "I don't wish to answer"
    assert choose_option("Jakarta", [Option(id="1", label="Jakarta, Indonesia")]).label == "Jakarta, Indonesia"
    assert choose_option("Mars", yn) is None
