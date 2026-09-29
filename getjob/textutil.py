"""Utilitas teks: HTML ke teks, deteksi bahasa, normalisasi."""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    # Beberapa API mengirim HTML yang di-escape (&lt;p&gt;).
    if "&lt;" in value and "<" not in value:
        value = html.unescape(value)
    soup = BeautifulSoup(value, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "tr"]):
        tag.insert_after("\n")
    for li in soup.find_all("li"):
        li.insert_before("- ")
    text = soup.get_text()
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return _NL_RE.sub("\n\n", text).strip()


def clip(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


_ID_WORDS = {
    "yang", "dan", "untuk", "dengan", "kami", "anda", "pengalaman", "minimal", "tahun", "kerja",
    "mampu", "memiliki", "atau", "dalam", "dari", "pendidikan", "lowongan", "persyaratan",
    "tanggung", "jawab", "diutamakan", "bersedia", "kualifikasi", "gaji", "perusahaan", "di",
}
_EN_WORDS = {
    "the", "and", "for", "with", "you", "we", "experience", "years", "work", "able", "have",
    "or", "in", "of", "our", "requirements", "responsibilities", "preferred", "team", "will",
    "is", "are", "to", "a", "skills", "role",
}
_WORD_RE = re.compile(r"[a-zA-Z]+")


def detect_language(text: str) -> str:
    """Tebakan kasar: 'id' (Indonesia) atau 'en' (Inggris)."""
    words = [w.lower() for w in _WORD_RE.findall(text or "")[:1500]]
    if not words:
        return "en"
    id_hits = sum(1 for w in words if w in _ID_WORDS)
    en_hits = sum(1 for w in words if w in _EN_WORDS)
    return "id" if id_hits > en_hits * 0.8 else "en"


def normalize_question(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[*:?\s]+", " ", text)
    text = re.sub(r"[^\w\s+#./-]", "", text)
    return " ".join(text.split())[:380]


def term_in_text(term: str, text_lower: str) -> bool:
    """Cek apakah istilah (mis. 'c++', 'node.js', 'go') muncul sebagai kata utuh."""
    t = term.lower().strip()
    if not t:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])"
    # '+' dan '#' bukan batas kata: 'c' tidak boleh cocok dengan 'c++' / 'c#'.
    if t[-1].isalnum():
        pattern += r"(?![+#])"
    return re.search(pattern, text_lower) is not None


# Kosakata teknologi untuk mendeteksi skill yang diminta lowongan (mode tanpa AI).
TECH_TERMS = [
    "python", "java", "javascript", "typescript", "golang", "rust", "c++", "c#", "php", "ruby",
    "kotlin", "swift", "dart", "scala", "matlab", "sql", "nosql", "bash", "shell", "perl",
    "django", "flask", "fastapi", "spring", "spring boot", "laravel", "codeigniter", "rails",
    "express", "nestjs", "node.js", "nodejs", "react", "react native", "next.js", "nextjs", "vue",
    "vue.js", "nuxt", "angular", "svelte", "jquery", "tailwind", "bootstrap", "flutter", ".net",
    "asp.net", "graphql", "rest api", "restful", "grpc", "microservices", "html", "css", "sass",
    "postgresql", "postgres", "mysql", "mariadb", "sqlite", "oracle", "sql server", "mongodb",
    "redis", "elasticsearch", "cassandra", "dynamodb", "firebase", "supabase", "bigquery",
    "snowflake", "redshift", "clickhouse", "kafka", "rabbitmq", "airflow", "dbt", "spark",
    "hadoop", "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch", "keras", "nlp",
    "computer vision", "machine learning", "deep learning", "llm", "data analysis", "statistics",
    "power bi", "tableau", "looker", "excel", "etl", "data warehouse", "docker", "kubernetes",
    "k8s", "helm", "terraform", "ansible", "jenkins", "gitlab ci", "github actions", "ci/cd",
    "aws", "gcp", "google cloud", "azure", "linux", "nginx", "git", "jira", "agile", "scrum",
    "selenium", "cypress", "playwright", "jest", "pytest", "junit", "appium", "postman",
    "figma", "android", "ios", "unity", "blockchain", "solidity", "sap", "salesforce",
    "networking", "cisco", "mikrotik", "active directory", "windows server", "security",
    "penetration testing", "devops", "sre", "prometheus", "grafana", "celery", "websocket",
]
