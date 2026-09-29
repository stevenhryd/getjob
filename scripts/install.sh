#!/usr/bin/env bash
# Instalasi GetJob di Linux (Ubuntu/Debian). Jalankan dari folder repo: ./scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "✗ Butuh Python 3.10 atau lebih baru." >&2
  exit 1
fi
if ! "$PY" -m venv --help >/dev/null 2>&1; then
  echo "✗ Modul venv belum ada. Jalankan: sudo apt install python3-venv" >&2
  exit 1
fi

[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

echo "→ Memasang Chromium untuk Playwright..."
python -m playwright install chromium
echo "  Kalau Chromium gagal jalan karena library sistem kurang, jalankan:"
echo "  sudo $(pwd)/.venv/bin/python -m playwright install-deps chromium"

getjob init
chmod +x scripts/run.sh

cat <<'MSG'

✓ Instalasi selesai.
Berikutnya:
  source .venv/bin/activate
  1. Taruh CV di data/cv.pdf dan edit config.yaml (bagian profile & search)
  2. (opsional) isi ANTHROPIC_API_KEY di .env
  3. getjob doctor
  4. getjob login
  5. getjob search && getjob apply --limit 3     # uji coba (dry run)
  6. getjob start                                # jalan 24 jam
MSG
