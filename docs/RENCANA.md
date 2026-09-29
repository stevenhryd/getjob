# Rencana & arsitektur GetJob

Dokumen ini mencatat kebutuhan yang sudah dikonfirmasi, rancangan sistem, dan hal-hal yang sengaja belum dikerjakan.

## 1. Kebutuhan yang sudah dikonfirmasi

| Topik | Keputusan |
|---|---|
| Platform lamaran | LinkedIn Easy Apply; JobStreet, Glints, Kalibrr; remote/luar negeri (RemoteOK, Remotive, Greenhouse, Lever). Email HRD **tidak** dipilih. |
| Mode kirim | **Hybrid**: skor tinggi dikirim otomatis, skor sedang di-approve 1 klik, skor rendah dilewati |
| AI | Tidak ada preferensi. Dipakai **Claude API** dengan cadangan kata kunci kalau tidak ada API key. |
| Tempat jalan | Mini PC **Linux dengan desktop**, menyala 24 jam |
| Bidang | IT / Software / Data |
| Akses dashboard | Dari mana saja (Tailscale), dilindungi password |
| Notifikasi | Cukup dashboard |
| Serah terima | Kerangka dan program jadi di-push ke branch, lalu di-`git pull` user untuk dilanjutkan |

## 2. Arsitektur

```
                         ┌──────────────────────── getjob start (satu proses) ───────────────────────┐
                         │                                                                           │
 config.yaml + .env ───► │  Worker (thread)                           Dashboard (FastAPI, :8000)     │ ◄── HP via Tailscale
                         │   ├─ tiap 3 jam: run_search()               ├─ Ringkasan & grafik         │
                         │   ├─ tiap 20 mnt: run_apply()               ├─ Review / Lowongan / Detail │
                         │   └─ perintah dari dashboard (kv: cmd:*)    ├─ Jawaban form / Aktivitas   │
                         │                     │                       └─ Pengaturan (jeda, reset)   │
                         │                     ▼                                    ▲                │
                         │   Pipeline ──► SQLite (data/getjob.db) ◄─────────────────┘                │
                         └───────┬───────────────────────────────────────────────────────────────────┘
                                 │
     ┌───────────────────────────┼───────────────────────────────────┐
     ▼                           ▼                                   ▼
 sources/*                  matching/*                          appliers/*
 httpx: LinkedIn guest,     keyword.py: filter keras,           Chromium (Playwright) dengan profil
 JobStreet, Kalibrr,        skor skill/judul/pengalaman         login tersimpan (data/browser)
 RemoteOK, Remotive,        ai.py: Claude (skor JSON,           form.py: baca semua field → AnswerEngine
 Greenhouse, Lever          cover letter, jawaban form)         → isi; platforms.py: alur per situs
 browser: Glints
```

### Alur satu lowongan

```
NEW ──filter/duplikat/skor kata kunci rendah──► SKIPPED
 │
 ├─ fetch_details (deskripsi lengkap) → skor AI (atau kata kunci)
 │
 ├─ skor < review_threshold ─────────────────► SKIPPED
 ├─ tidak ada applier otomatis ──────────────► MANUAL   (+ cover letter untuk disalin)
 ├─ ada red flag / mode manual / skor sedang ► REVIEW   ──user approve──► APPROVED
 └─ skor ≥ auto_apply_threshold ─────────────► APPROVED
                                                  │ run_apply (jam aktif, batas harian, jeda acak)
                                                  ▼
                    APPLYING ──► APPLIED | TESTED (dry run) | MANUAL | FAILED (retry s/d max_attempts)
                                 NEEDS_LOGIN / captcha → kembali APPROVED, platform dijeda sampai login ulang
 User (dashboard): INTERVIEW / OFFER / REJECTED / DISMISSED
```

### Keputusan desain penting

1. **Login lewat profil browser persisten, bukan menyimpan password.** `getjob login` membuka Chromium dan Anda login sendiri, termasuk OTP/captcha. Program tidak pernah menyimpan password, dan 2FA tetap berfungsi.
2. **Satu pengisi form generik untuk semua platform.** `appliers/form.py` membaca semua input/select/radio/checkbox/file di halaman atau modal (lewat label, `aria-*`, fieldset legend) lalu meminta jawaban ke `AnswerEngine`. Bagian khusus per platform hanya berupa daftar selector/teks tombol di `platforms.py`, jadi mudah diperbaiki saat situs berubah.
3. **Jawaban form berlapis dan dipelajari.** Urutannya: aturan config → jawaban yang diedit user → profil → jawaban AI tersimpan → AI. Dengan begitu pertanyaan yang sama tidak memanggil AI dua kali, dan user bisa mengoreksi.
4. **Aman secara default.** `dry_run: true`, batas harian per platform, `max_per_run`, jam aktif, dan jeda acak. Captcha atau logout langsung menghentikan platform tersebut, dan program **tidak** mencoba mengakali captcha maupun deteksi bot.
5. **AI opsional.** Tanpa `ANTHROPIC_API_KEY` semuanya tetap berjalan: skor kata kunci plus template cover letter. Pertanyaan form yang tidak bisa dijawab membuat lowongan berstatus "Perlu manual".
6. **Claude API**: `claude-opus-5-5` (bisa diganti di config), output terstruktur (JSON schema) untuk skor dan jawaban, prompt caching untuk profil dan CV, fallback server-side saat model menolak, dan kuota panggilan harian.
7. **SQLite + satu proses.** Tidak perlu Redis, Postgres, atau Docker, cocok untuk mini PC. Dashboard dan worker berkomunikasi lewat tabel `kv` (perintah, heartbeat, status platform).

## 3. Status pengujian

- **Otomatis (48 test):** parser setiap sumber (dari contoh respons), skor kata kunci, mesin jawaban, pemetaan opsi (rentang tahun, ya/tidak, EEO), request ke Claude (bentuk parameter, refusal, kuota), pipeline (hybrid/auto/manual, duplikat, batas harian, dry run, login kedaluwarsa), worker, dashboard (login, rate limit, semua halaman, aksi), dan PDF cover letter.
- **Chromium sungguhan terhadap halaman tiruan:** alur LinkedIn Easy Apply multi-langkah (dry run, kirim, pertanyaan tak terjawab → manual, deteksi logout) dan form Greenhouse.
- **Belum diuji ke situs asli.** Lingkungan pengembangan tidak punya akses ke situs lowongan. Format API atau tampilan situs bisa berbeda dari asumsi, jadi uji pertama wajib memakai dry run. Kalau ada yang meleset, kirimkan pesan error dari `getjob search` atau screenshot dari dashboard.

## 4. Titik yang paling mungkin perlu disesuaikan

| Gejala | File |
|---|---|
| Sumber X selalu 0 lowongan / error | `getjob/sources/<x>.py` (URL/parameter API atau parser) |
| Lowongan di platform X selalu "Perlu manual: tombol ... tidak ditemukan" | `getjob/appliers/platforms.py` (teks/selector tombol) |
| Pertanyaan form tertentu salah dijawab | Dashboard → Jawaban form, atau `answers:` di config, atau `BUILTIN_RULES` di `getjob/answers.py` |
| Terlalu banyak / sedikit lowongan lolos | `matching.*_threshold`, `search.title_exclude`, `prefilter_min_score` |

## 5. Ide pengembangan lanjutan (belum dikerjakan)

- Kanal **email HRD** (kirim CV lewat Gmail untuk loker "kirim CV ke ...").
- Notifikasi Telegram (ringkasan harian, peringatan login kedaluwarsa).
- Sumber tambahan: Indeed, Dealls, KitaLulus, Karir.com, Ashby, Workable.
- CV yang disesuaikan per lowongan (PDF dibuat otomatis).
- Tampilan noVNC di dashboard untuk login/captcha dari HP.
- Pelacakan balasan HR otomatis (membaca email masuk).
