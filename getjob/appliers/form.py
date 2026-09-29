"""Pengisi form generik: membaca semua field di halaman/modal lalu mengisinya."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..answers import AnswerEngine, FormField, JobContext, choose_option
from ..pdf import text_to_pdf

log = logging.getLogger(__name__)

# Dijalankan di browser. Argumen: elemen root (atau null untuk seluruh dokumen).
EXTRACT_JS = r"""
(root) => {
  const scope = root || document;
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  };
  let counter = window.__gjCounter || 0;
  const mark = (el) => { if (!el.dataset.gj) { counter += 1; el.dataset.gj = 'f' + counter; } return el.dataset.gj; };
  const textOf = (el) => {
    if (!el) return '';
    const c = el.cloneNode(true);
    c.querySelectorAll('input,select,textarea,option,script,style,[aria-hidden=true]').forEach(n => n.remove());
    return clean(c.textContent);
  };
  const byIds = (ids) => clean((ids || '').split(/\s+/).map(id => textOf(document.getElementById(id))).join(' '));
  const labelFor = (el) => {
    if (el.labels && el.labels.length) { const t = textOf(el.labels[0]); if (t) return t; }
    const lb = el.getAttribute('aria-labelledby'); if (lb) { const t = byIds(lb); if (t) return t; }
    const al = el.getAttribute('aria-label'); if (al) return clean(al);
    const wrap = el.closest('label'); if (wrap) { const t = textOf(wrap); if (t) return t; }
    return '';
  };
  const groupLabel = (el) => {
    const fs = el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg) { const t = textOf(lg); if (t) return t; } }
    const grp = el.closest('[role=radiogroup],[role=group]');
    if (grp) {
      const lb = grp.getAttribute('aria-labelledby'); if (lb) { const t = byIds(lb); if (t) return t; }
      const al = grp.getAttribute('aria-label'); if (al) return clean(al);
    }
    return '';
  };
  const contextLabel = (el) => {
    let node = el.parentElement;
    for (let i = 0; i < 4 && node; i++, node = node.parentElement) {
      const t = textOf(node);
      if (t && t.length < 300) return t;
    }
    return '';
  };
  const isRequired = (el, label) => !!(el.required || el.getAttribute('aria-required') === 'true'
      || /\*\s*$/.test(label) || /\((required|wajib)\)/i.test(label));
  const PLACEHOLDER = /^(select|pilih|choose|--|please select)/i;
  const out = [];
  const groupsDone = new Set();
  for (const el of scope.querySelectorAll('input, select, textarea')) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search', 'password'].includes(type)) continue;
    if (el.disabled) continue;
    if (type === 'radio' || type === 'checkbox') {
      const lab = el.labels && el.labels[0];
      if (!visible(el) && !(lab && visible(lab))) continue;
      const name = el.name;
      const sameName = name ? Array.from(scope.querySelectorAll(`input[type=${type}][name="${CSS.escape(name)}"]`)) : [el];
      if (type === 'radio' || sameName.length > 1) {
        const key = type + ':' + (name || mark(el));
        if (groupsDone.has(key)) continue;
        groupsDone.add(key);
        const options = sameName.map(m => ({id: mark(m), label: labelFor(m) || m.value, value: m.value, checked: m.checked}));
        const box = el.closest('fieldset') || (el.parentElement && el.parentElement.parentElement) || el;
        const label = groupLabel(el) || contextLabel(box);
        out.push({id: mark(el), kind: type === 'radio' ? 'radio' : 'checkbox_group', label, name: name || '',
                  required: sameName.some(m => m.required) || isRequired(el, label),
                  value: options.filter(o => o.checked).map(o => o.label).join(' | '), options});
      } else {
        const label = labelFor(el) || contextLabel(el);
        out.push({id: mark(el), kind: 'checkbox', label, name: name || '', required: isRequired(el, label),
                  value: el.checked ? 'true' : '', options: []});
      }
      continue;
    }
    if (type !== 'file' && !visible(el)) continue;
    if (type !== 'file' && el.readOnly) continue;
    let label = labelFor(el) || el.getAttribute('placeholder') || '';
    if (!label) label = contextLabel(el);
    const f = {id: mark(el), kind: type, label: clean(label), name: el.name || el.id || '',
               required: isRequired(el, label), value: '', options: [],
               placeholder: el.getAttribute('placeholder') || '', accept: el.getAttribute('accept') || '',
               multiple: !!el.multiple};
    if (tag === 'select') {
      f.kind = 'select';
      f.options = Array.from(el.options)
        .filter(o => !o.disabled && clean(o.textContent) && o.value !== '' && !PLACEHOLDER.test(clean(o.textContent)))
        .map(o => ({id: '', label: clean(o.textContent), value: o.value, checked: o.selected}));
      const sel = el.options[el.selectedIndex];
      f.value = sel && sel.value !== '' && !PLACEHOLDER.test(clean(sel.textContent)) ? clean(sel.textContent) : '';
    } else if (tag === 'textarea') {
      f.kind = 'textarea'; f.value = el.value;
    } else if (type === 'file') {
      f.value = el.files && el.files.length ? el.files[0].name : '';
    } else if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list') {
      f.kind = 'combobox'; f.value = el.value;
    } else {
      if (!['text', 'email', 'tel', 'url', 'number', 'date'].includes(type)) f.kind = 'text';
      f.value = el.value;
    }
    out.push(f);
  }
  window.__gjCounter = counter;
  return out;
}
"""

RESUME_RE = re.compile(r"resume|résumé|resumé|\bcv\b|curriculum|riwayat hidup", re.I)
COVER_RE = re.compile(r"cover|surat lamaran|motivation", re.I)
CONSENT_RE = re.compile(
    r"agree|consent|accept|privacy|terms|acknowledge|certify|confirm|setuju|menyetujui|syarat|kebijakan|benar", re.I
)
OPT_OUT_RE = re.compile(r"follow|subscribe|newsletter|job alert|marketing|updates|promo|berlangganan|ikuti", re.I)


@dataclass
class FillReport:
    filled: list[tuple[str, str, str]] = field(default_factory=list)  # (label, value, origin)
    unresolved: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "FillReport") -> None:
        self.filled += other.filled
        self.unresolved = other.unresolved
        self.errors += other.errors


class FormFiller:
    def __init__(
        self,
        page: Any,
        engine: AnswerEngine,
        job: JobContext,
        *,
        resume_path: Path | None,
        files_dir: Path,
        job_key: str = "job",
        upload_optional_resume: bool = True,
    ):
        self.page = page
        self.engine = engine
        self.job = job
        self.resume_path = resume_path if resume_path and resume_path.exists() else None
        self.files_dir = files_dir
        self.job_key = job_key
        self.upload_optional_resume = upload_optional_resume
        self._cover_pdf: Path | None = None

    # ---- ekstraksi ---------------------------------------------------------------
    def extract(self, root: Any | None = None) -> list[FormField]:
        raw = root.evaluate(EXTRACT_JS) if root is not None else self.page.evaluate(EXTRACT_JS, None)
        return [FormField.from_dict(d) for d in raw]

    def _loc(self, fid: str):
        return self.page.locator(f'[data-gj="{fid}"]').first

    # ---- pengisian -----------------------------------------------------------------
    def fill(self, root: Any | None = None, *, aggressive: bool = False, passes: int = 2) -> FillReport:
        """Isi field kosong. `aggressive` juga mengisi field opsional dengan AI."""
        report = FillReport()
        for _ in range(passes):
            before = len(report.filled)
            step = self._fill_once(root, aggressive=aggressive)
            report.merge(step)
            if len(report.filled) == before:
                break
            self.page.wait_for_timeout(500)  # field baru bisa muncul setelah memilih opsi
        return report

    def _fill_once(self, root: Any | None, *, aggressive: bool) -> FillReport:
        report = FillReport()
        for f in self.extract(root):
            try:
                if f.kind == "file":
                    self._fill_file(f, report, aggressive)
                elif f.kind == "checkbox":
                    self._fill_checkbox(f, report)
                elif f.value.strip():
                    continue  # sudah terisi (mis. email dari profil platform)
                else:
                    self._fill_answer(f, report, aggressive)
            except Exception as exc:  # satu field gagal tidak boleh menggagalkan semua
                log.debug("Gagal mengisi %s: %s", f.label, exc)
                report.errors.append(f"{f.label}: {exc}")
                if f.required:
                    report.unresolved.append(f.question)
        return report

    def _fill_file(self, f: FormField, report: FillReport, aggressive: bool) -> None:
        if f.value:
            return
        hint = f"{f.label} {f.name} {f.accept}"
        if COVER_RE.search(hint):
            if not (f.required or aggressive) or not self.job.cover_letter:
                return
            path: Path | None = self._cover_letter_pdf()
        elif f.required or aggressive or (RESUME_RE.search(hint) and self.upload_optional_resume):
            if not (f.required or RESUME_RE.search(hint)):
                return  # file opsional yang tidak jelas untuk apa
            path = self.resume_path
            if path is None:
                if f.required:
                    report.unresolved.append(f"{f.question} (file CV tidak ditemukan)")
                return
        else:
            return
        self._loc(f.id).set_input_files(str(path))
        report.filled.append((f.question or "file", path.name, "file"))

    def _fill_checkbox(self, f: FormField, report: FillReport) -> None:
        loc = self._loc(f.id)
        checked = f.value == "true"
        text = f.question
        if OPT_OUT_RE.search(text) and not CONSENT_RE.search(text):
            want = False
        elif CONSENT_RE.search(text) or f.required:
            want = True
        else:
            return
        if want != checked:
            self._set_checked(loc, want)
            report.filled.append((text, "centang" if want else "tidak dicentang", "aturan"))

    def _set_checked(self, loc: Any, want: bool) -> None:
        try:
            loc.set_checked(want, force=True, timeout=3000)
        except Exception:
            loc.evaluate("e => e.click()")

    def _fill_answer(self, f: FormField, report: FillReport, aggressive: bool) -> None:
        allow_ai = f.required or aggressive
        answer, origin = self.engine.resolve(f, self.job, allow_ai=allow_ai)
        if answer is None or str(answer).strip() == "":
            if f.required:
                report.unresolved.append(f.question)
            return
        answer = str(answer).strip()
        loc = self._loc(f.id)
        if f.kind in ("select", "radio"):
            opt = choose_option(answer, f.options)
            if opt is None:
                if f.required:
                    report.unresolved.append(f"{f.question} (jawaban '{answer}' tidak ada di pilihan)")
                return
            if f.kind == "select":
                loc.select_option(value=opt.value)
            else:
                self._set_checked(self._loc(opt.id), True)
            report.filled.append((f.question, opt.label, origin))
        elif f.kind == "checkbox_group":
            picked = []
            for part in [p.strip() for p in answer.split("|") if p.strip()]:
                opt = choose_option(part, f.options)
                if opt:
                    self._set_checked(self._loc(opt.id), True)
                    picked.append(opt.label)
            if picked:
                report.filled.append((f.question, ", ".join(picked), origin))
            elif f.required:
                report.unresolved.append(f.question)
        elif f.kind == "combobox":
            loc.fill(answer)
            self.page.wait_for_timeout(1200)
            options = self.page.locator("[role=option]:visible")
            if options.count():
                match = options.filter(has_text=answer.split(",")[0])
                (match.first if match.count() else options.first).click()
            else:
                loc.press("ArrowDown")
                loc.press("Enter")
            report.filled.append((f.question, answer, origin))
        else:
            if f.kind == "number":
                m = re.search(r"\d+(?:\.\d+)?", answer.replace(",", "."))
                if not m:
                    if f.required:
                        report.unresolved.append(f"{f.question} (butuh angka)")
                    return
                answer = m.group(0)
            loc.fill(answer)
            report.filled.append((f.question, answer if len(answer) < 80 else answer[:77] + "...", origin))

    def _cover_letter_pdf(self) -> Path:
        if self._cover_pdf is None:
            self._cover_pdf = text_to_pdf(self.job.cover_letter, self.files_dir / f"cover-letter-{self.job_key}.pdf")
        return self._cover_pdf
