# GetJob: Pencari & Pelamar Kerja Otomatis

GetJob berjalan 24 jam di mini PC Anda. Program ini **mencari lowongan** di LinkedIn, JobStreet, Glints, Kalibrr, dan papan kerja remote, lalu **menilai kecocokannya** dengan CV Anda (memakai Claude AI atau kata kunci). Lowongan yang cocok **dilamar otomatis**, dan semua hasilnya bisa dipantau lewat **dashboard web** dari HP.

```
 Sumber lowongan ──► Saring & nilai ──► Keputusan hybrid ──► Kirim lamaran ──► Dashboard
 LinkedIn, JobStreet   filter judul,     skor ≥ 80: otomatis   isi form, upload    status, grafik,
 Glints, Kalibrr,      duplikat, skor    skor 60–79: review    CV, cover letter,   review, jawaban
 RemoteOK, Remotive,   kata kunci, AI    < 60: dilewati        jawab pertanyaan    form, log
 Greenhouse, Lever
```

> **Status:** kerangka dan program sudah lengkap dan lolos 48 test otomatis, termasuk simulasi alur LinkedIn Easy Apply dan form Greenhouse di Chromium sungguhan. Yang **belum** diuji adalah koneksi ke situs asli, karena lingkungan pengembangannya tidak punya akses ke situs lowongan. Uji pertama ke situs asli dilakukan di mini PC Anda dengan **mode dry run** (lihat [Langkah 5](#5-uji-coba-dry-run)).

---

## Fitur

| Bagian | Isi |
|---|---|
| **Pencarian** | LinkedIn (endpoint publik, filter Easy Apply), JobStreet (API JSON), Glints (lewat browser), Kalibrr (API JSON), RemoteOK, Remotive, Greenhouse & Lever (daftar perusahaan pilihan Anda) |
| **Penyaringan** | Kata yang dikecualikan di judul/perusahaan/deskripsi, umur lowongan, dan deteksi **duplikat lintas platform** (lowongan yang sama di LinkedIn dan JobStreet hanya dilamar sekali) |
| **Penilaian** | Skor 0–100 dari **Claude** (alasan, skill yang cocok/kurang, *red flag* seperti lowongan berbayar). Tanpa API key, skor dihitung dari kata kunci. |
| **Mode hybrid** | Skor ≥ 80 dikirim otomatis, 60–79 menunggu persetujuan 1 klik, di bawahnya dilewati. Lowongan dengan *red flag* selalu masuk review. |
| **Pelamar otomatis** | LinkedIn Easy Apply, JobStreet Quick Apply, Glints, Kalibrr, Greenhouse, Lever: isi form multi-langkah, upload CV/cover letter, jawab pertanyaan screening |
| **Jawaban form** | Diambil berurutan dari aturan Anda di config, jawaban yang Anda edit di dashboard, profil (nama, gaji, pengalaman per skill, dll.), lalu AI. Jawaban dari AI disimpan supaya tidak ditanyakan ulang. |
| **Cover letter** | Ditulis AI per lowongan, dalam bahasa yang sama dengan lowongannya (Indonesia/Inggris). Bisa diedit sebelum dikirim. |
| **Pengaman** | Mode **dry run** (isi form tanpa mengirim), batas harian per platform, jam aktif, jeda acak antar lamaran, batas percobaan per siklus, deteksi captcha/logout (platform otomatis dijeda), kuota AI harian |
| **Dashboard** | Ringkasan dan grafik, antrian review, daftar lowongan dengan filter, detail beserta screenshot proses melamar, bank jawaban, log aktivitas, status login platform. Nyaman dibuka di HP. |

## Isi repo

```
getjob/
├── config.py          # skema config.yaml (pydantic)
├── db.py              # SQLite: jobs, events, qa_answers, kv, runs
├── sources/           # pencari lowongan per platform
├── matching/          # skor kata kunci + integrasi Claude (skor, cover letter, jawaban)
├── answers.py         # mesin jawaban pertanyaan form
├── appliers/          # pengisi form generik + alur per platform (selector ada di platforms.py)
├── pipeline.py        # cari → saring → nilai → putuskan → lamar
├── worker.py          # penjadwal 24 jam + perintah dari dashboard
├── browser.py         # Chromium dengan profil login tersimpan
├── web/               # dashboard FastAPI (templates + static, tanpa CDN)
└── cli.py             # perintah `getjob ...`
config.example.yaml    # contoh konfigurasi (dengan penjelasan)
deploy/                # service systemd & autostart desktop
scripts/               # install.sh, run.sh
docs/RENCANA.md        # rencana & arsitektur lengkap
tests/                 # 48 test (pytest), termasuk form tiruan di Chromium
```

---

## Instalasi di mini PC (Linux)

Kebutuhan: Linux dengan desktop (Ubuntu 22.04/24.04 atau sejenisnya), Python 3.10+, dan git.

### 1. Ambil kode & instal

```bash
cd ~
git clone https://github.com/stevenhryd/getjob.git
cd getjob
git checkout claude/ecstatic-planck-llf7ua   # sampai branch ini di-merge ke main
./scripts/install.sh
```

`install.sh` membuat virtualenv `.venv`, memasang dependensi dan Chromium untuk Playwright, lalu menjalankan `getjob init`. Perintah init membuat `config.yaml` dan `.env`, sekaligus **mencetak password dashboard** Anda.

Setelah itu aktifkan virtualenv setiap kali membuka terminal baru:

```bash
source ~/getjob/.venv/bin/activate
```

### 2. Isi konfigurasi

1. Taruh CV Anda (PDF) di `data/cv.pdf`. Opsional: CV bahasa Inggris di `data/cv-en.pdf`, lalu isi `profile.resume_path_en`.
2. Edit `config.yaml`. Bagian yang **wajib** diisi:
   - `profile`: nama, email, HP, kota, skill, `skill_years`, ekspektasi gaji, link LinkedIn/GitHub. Semua jawaban form diambil dari sini, jadi isi sejujur dan selengkap mungkin.
   - `search.keywords`, `search.locations`, dan `search.title_exclude`.
   - `sources.greenhouse.boards` dan `sources.lever.companies`: perusahaan remote/luar negeri yang Anda incar. Nama board-nya terlihat di URL, misalnya `job-boards.greenhouse.io/<board>`.
3. Edit `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...        # opsional; kosong = tanpa AI
   GETJOB_DASHBOARD_PASSWORD=...        # sudah dibuat oleh `getjob init`
   GETJOB_SECRET_KEY=...
   ```

### 3. Periksa

```bash
getjob doctor          # cek profil, CV, browser, koneksi ke setiap sumber
getjob doctor --ai     # sekaligus uji 1 panggilan Claude API
```

### 4. Login ke platform (sekali saja)

Jalankan dari **desktop mini PC** (langsung, atau lewat Remote Desktop/VNC):

```bash
getjob login                 # buka tab login LinkedIn, JobStreet, Glints, Kalibrr
getjob login linkedin        # atau satu platform saja
```

Login di setiap tab, selesaikan OTP/captcha kalau diminta, lalu tekan Enter di terminal. Sesi tersimpan di `data/browser/`, dan program memakai sesi itu untuk melamar. **Password akun tidak pernah disimpan program.**

### 5. Uji coba (dry run)

Secara default `apply.dry_run: true`. Dalam mode ini program mengisi form sampai tombol **Kirim** terakhir, **tidak** mengirimnya, lalu menyimpan screenshot.

```bash
getjob search                # satu siklus pencarian + penilaian
getjob apply --limit 3       # coba isi 3 lamaran dari antrian (dry run)
```

Buka dashboard, lalu cek lowongan berstatus **"Uji coba OK"** dan screenshot-nya. Kalau ada yang berstatus **"Perlu manual"**, alasannya tertulis di detail lowongan (misalnya pertanyaan yang belum terjawab). Pertanyaan seperti itu bisa Anda tambahkan jawabannya di menu **Jawaban form**.

Kalau hasilnya sudah benar, ubah `apply.dry_run: false` di `config.yaml`, lalu restart program. Lowongan yang tadi lolos uji coba bisa dikirim ulang lewat **Pengaturan → Kirim ulang hasil uji coba**.

### 6. Jalankan 24 jam

```bash
getjob start                 # worker + dashboard dalam satu proses
```

Supaya otomatis menyala setiap mini PC hidup, pilih **salah satu**:

**A. Autostart desktop (disarankan, browser tampil di layar):**
```bash
mkdir -p ~/.config/autostart
cp deploy/getjob-autostart.desktop ~/.config/autostart/
```
Aktifkan juga *auto-login* di pengaturan Ubuntu (Settings → Users → Automatic Login), supaya sesi desktop langsung jalan setelah mini PC restart. Log tersimpan di `data/logs/run.log`.

**B. Service systemd (tanpa layar):** set `browser.headless: true` di config, lalu:
```bash
mkdir -p ~/.config/systemd/user
cp deploy/getjob.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now getjob
sudo loginctl enable-linger $USER      # tetap jalan walau belum login
journalctl --user -u getjob -f         # lihat log
```

### 7. Buka dashboard dari HP (Tailscale)

Tailscale membuat jaringan pribadi terenkripsi antara mini PC dan HP Anda, tanpa perlu membuka port router.

1. Di mini PC: `curl -fsSL https://tailscale.com/install.sh | sh`, lalu `sudo tailscale up`.
2. Di HP: pasang aplikasi **Tailscale** dan login dengan akun yang sama.
3. Buka `http://<nama-mini-pc>:8000` (atau `http://100.x.y.z:8000`; IP-nya terlihat di aplikasi Tailscale).

Dashboard dilindungi password dari `.env`. **Jangan** melakukan port-forward 8000 ke internet.

---

## Pemakaian sehari-hari

| Yang dilihat | Di mana |
|---|---|
| Berapa lamaran terkirim, grafik 30 hari, corong lamaran | **Ringkasan** |
| Lowongan skor 60–79 yang butuh persetujuan (cover letter bisa diedit dulu) | **Review** |
| Semua lowongan, filter status/sumber/skor, aksi massal | **Lowongan** |
| Lowongan yang harus dilamar manual (lamaran eksternal); cover letter tinggal disalin | **Lowongan → status "Perlu manual"** |
| Mengubah status setelah dihubungi HR: Interview / Tawaran / Ditolak perusahaan | Detail lowongan → **Ubah status** |
| Memperbaiki jawaban form yang dibuat AI | **Jawaban form** |
| Platform yang minta login ulang / captcha, jeda per platform | **Pengaturan** |

Tombol **Cari sekarang** dan **Kirim antrian sekarang** di Ringkasan menjalankan siklus tanpa menunggu jadwal. Tombol **Jeda jadwal** menghentikan semua proses otomatis sementara.

Kalau sebuah platform berstatus **"Perlu login"** atau **"Captcha/verifikasi"**, program berhenti melamar di platform itu. Jalankan `getjob login <platform>` di mini PC, lalu klik **Reset status** di Pengaturan.

## Perintah lengkap

```
getjob init                     buat config.yaml & .env
getjob doctor [--ai]            periksa semuanya
getjob login [platform ...]     login ke platform (browser tampil)
getjob search [--source X]      satu siklus pencarian + penilaian
getjob score                    nilai lowongan yang belum dinilai
getjob apply [--job ID] [--limit N] [--dry-run | --live]
getjob status                   jumlah lowongan per status
getjob start                    worker + dashboard (untuk 24 jam)
getjob worker | getjob serve    jalankan terpisah
```

## Biaya AI (perkiraan)

Default model adalah `claude-opus-5-5` ($4 per 1 juta token input, $20 per 1 juta token output). Profil dan CV dikirim di setiap panggilan, tapi di-*cache* sehingga lebih murah.

| Aktivitas | Perkiraan per item |
|---|---|
| Menilai 1 lowongan | ± $0,03–0,05 |
| Menulis 1 cover letter | ± $0,03–0,05 |
| Menjawab 1 pertanyaan form baru | ± $0,01–0,02 (setelah itu tersimpan di bank jawaban, gratis) |

Contoh: 60 lowongan dinilai dan 25 cover letter per hari ≈ **$3–4/hari**. Angka ini kasar, jadi pantau pemakaian sebenarnya di console Anthropic. Ada tiga cara menekan biaya:
- Naikkan `matching.prefilter_min_score`, supaya lebih sedikit lowongan yang sampai ke AI.
- Ganti `ai.model` ke `claude-sonnet-5-5` (sekitar ½ biaya) atau `claude-haiku-4-5` (sekitar ¼ biaya), dengan kualitas penilaian yang sedikit menurun.
- `ai.max_calls_per_day` (default 150) adalah batas keras harian. Setelah batas tercapai, program otomatis beralih ke penilaian kata kunci.

## Risiko yang perlu Anda ketahui

- **Syarat layanan platform.** LinkedIn, JobStreet, Glints, dan Kalibrr melarang otomatisasi di ketentuan mereka. Akun **bisa dibatasi atau diblokir**, dan LinkedIn yang paling ketat. Program ini sengaja memakai batas harian, jam aktif, dan jeda acak, lalu berhenti saat menemukan captcha. Program **tidak** mencoba mengakali deteksi bot. Turunkan `apply.daily_limits` kalau ingin lebih aman.
- **Tampilan situs berubah.** Selector tombol ada di `getjob/appliers/platforms.py`. Kalau suatu platform mulai sering berstatus "Perlu manual", lihat screenshot di detail lowongan lalu sesuaikan daftar teks/selector di file itu.
- **Jawaban form.** AI hanya menjawab berdasarkan profil dan CV, tapi tetap cek menu **Jawaban form** secara berkala. Semua jawaban tersimpan dan bisa Anda koreksi.
- **Data pribadi.** `config.yaml`, `.env`, dan folder `data/` (termasuk sesi login browser) tidak pernah di-commit (sudah ada di `.gitignore`). Jangan bagikan folder `data/browser/`.

## Pengembangan

```bash
source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
python -m pytest -q
```

Rencana lengkap, keputusan desain, dan ide pengembangan lanjutan ada di [`docs/RENCANA.md`](docs/RENCANA.md).
