#!/usr/bin/env bash
# Menjalankan GetJob (worker + dashboard) dan otomatis restart kalau berhenti.
# Dipakai oleh deploy/getjob-autostart.desktop.
cd "$(dirname "$0")/.." || exit 1
# shellcheck disable=SC1091
source .venv/bin/activate
mkdir -p data/logs
while true; do
  echo "$(date '+%F %T') memulai getjob" >> data/logs/run.log
  getjob start >> data/logs/run.log 2>&1
  echo "$(date '+%F %T') getjob berhenti (kode $?), restart dalam 30 detik" >> data/logs/run.log
  sleep 30
done
