// Dashboard GetJob: grafik SVG ringan + utilitas kecil. Tanpa library eksternal.
(function () {
  "use strict";
  const SVGNS = "http://www.w3.org/2000/svg";

  function el(tag, attrs, parent) {
    const node = document.createElementNS(SVGNS, tag);
    for (const k in attrs || {}) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function niceMax(v) {
    if (v <= 4) return 4;
    const exp = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * exp >= v) return m * exp;
    return 10 * exp;
  }

  function fmt(n) {
    return Number(n).toLocaleString("id-ID");
  }

  function makeTooltip(box) {
    const tip = document.createElement("div");
    tip.className = "tooltip";
    tip.setAttribute("role", "status");
    const strong = document.createElement("strong");
    const span = document.createElement("span");
    tip.append(strong, span);
    box.appendChild(tip);
    return {
      show(value, label, x, y) {
        strong.textContent = value;
        span.textContent = label;
        const w = box.clientWidth;
        tip.style.opacity = "1";
        const tw = tip.offsetWidth;
        tip.style.left = Math.max(0, Math.min(w - tw, x - tw / 2)) + "px";
        tip.style.top = Math.max(0, y - tip.offsetHeight - 8) + "px";
      },
      hide() { tip.style.opacity = "0"; },
    };
  }

  function tableView(box, rows, head) {
    const d = document.createElement("details");
    d.className = "table-view";
    const s = document.createElement("summary");
    s.textContent = "Lihat sebagai tabel";
    d.appendChild(s);
    const t = document.createElement("table");
    const tr = document.createElement("tr");
    for (const h of head) { const th = document.createElement("th"); th.textContent = h; tr.appendChild(th); }
    t.appendChild(tr);
    for (const r of rows) {
      const row = document.createElement("tr");
      const a = document.createElement("td"); a.textContent = r.label;
      const b = document.createElement("td"); b.className = "num"; b.textContent = fmt(r.value);
      row.append(a, b); t.appendChild(row);
    }
    d.appendChild(t);
    box.appendChild(d);
  }

  // Kolom vertikal (satu seri): lamaran per hari.
  function columnChart(box, data, opts) {
    box.textContent = "";
    const total = data.reduce((a, d) => a + d.value, 0);
    if (!data.length || total === 0) {
      const p = document.createElement("div");
      p.className = "chart-empty";
      p.textContent = opts.empty || "Belum ada data.";
      box.appendChild(p);
      return;
    }
    const W = Math.max(280, box.clientWidth), H = 200;
    const m = { top: 18, right: 8, bottom: 24, left: 30 };
    const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
    const max = niceMax(Math.max(...data.map(d => d.value)));
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.title || "" }, box);
    const g = el("g", { transform: `translate(${m.left},${m.top})` }, svg);
    for (let i = 0; i <= 4; i++) {
      const v = (max / 4) * i, y = ih - (v / max) * ih;
      if (i > 0) el("line", { x1: 0, x2: iw, y1: y, y2: y, class: "grid-line" }, g);
      const t = el("text", { x: -6, y: y + 4, "text-anchor": "end", class: "tick" }, g);
      t.textContent = fmt(Math.round(v));
    }
    el("line", { x1: 0, x2: iw, y1: ih, y2: ih, class: "baseline" }, g);
    const band = iw / data.length;
    const bw = Math.min(24, Math.max(3, band - 2));
    const tip = makeTooltip(box);
    let maxIdx = 0;
    data.forEach((d, i) => { if (d.value > data[maxIdx].value) maxIdx = i; });
    data.forEach((d, i) => {
      const x = i * band + (band - bw) / 2;
      const h = (d.value / max) * ih;
      const hit = el("rect", { x: i * band, y: 0, width: band, height: ih, class: "hit", tabindex: 0 }, g);
      let bar = null;
      if (d.value > 0) {
        const y = ih - h, r = Math.min(4, h, bw / 2);
        bar = el("path", {
          class: "bar",
          d: `M${x},${ih} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${ih} Z`,
        }, g);
      }
      const label = opts.labelFmt ? opts.labelFmt(d.label) : d.label;
      const show = () => {
        if (bar) bar.classList.add("hover");
        tip.show(fmt(d.value) + " " + (opts.unit || ""), label, m.left + x + bw / 2, m.top + ih - h);
      };
      const hide = () => { if (bar) bar.classList.remove("hover"); tip.hide(); };
      hit.addEventListener("pointerenter", show);
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("focus", show);
      hit.addEventListener("blur", hide);
      const every = Math.ceil(data.length / Math.max(2, Math.floor(iw / 56)));
      if (i % every === 0 || i === data.length - 1) {
        const t = el("text", { x: i * band + band / 2, y: ih + 16, "text-anchor": "middle", class: "tick" }, g);
        t.textContent = label;
      }
      if (i === maxIdx && d.value > 0) {
        const t = el("text", { x: x + bw / 2, y: ih - h - 5, "text-anchor": "middle", class: "value-label" }, g);
        t.textContent = fmt(d.value);
      }
    });
    tableView(box, data.map(d => ({ label: opts.labelFmt ? opts.labelFmt(d.label) : d.label, value: d.value })), [opts.catHead || "Tanggal", opts.valHead || "Jumlah"]);
  }

  // Batang horizontal (satu seri) dengan nilai di ujung batang.
  function barChart(box, data, opts) {
    box.textContent = "";
    if (!data.length || data.every(d => d.value === 0)) {
      const p = document.createElement("div");
      p.className = "chart-empty";
      p.textContent = opts.empty || "Belum ada data.";
      box.appendChild(p);
      return;
    }
    const W = Math.max(280, box.clientWidth);
    const rowH = 30, thick = 16;
    const labelW = Math.min(150, W * 0.38);
    const valueW = 52;
    const H = data.length * rowH + 4;
    const iw = W - labelW - valueW;
    const max = Math.max(...data.map(d => d.value)) || 1;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.title || "" }, box);
    el("line", { x1: labelW, x2: labelW, y1: 0, y2: H, class: "baseline" }, svg);
    const tip = makeTooltip(box);
    data.forEach((d, i) => {
      const y = i * rowH + (rowH - thick) / 2;
      const t = el("text", { x: labelW - 10, y: y + thick / 2 + 4, "text-anchor": "end", class: "cat-label" }, svg);
      t.textContent = d.label;
      const w = (d.value / max) * iw;
      const hit = el("rect", { x: 0, y: i * rowH, width: W, height: rowH, class: "hit", tabindex: 0 }, svg);
      let bar = null;
      if (w > 0) {
        const x0 = labelW + 1, x1 = labelW + 1 + w, r = Math.min(4, w, thick / 2);
        bar = el("path", {
          class: "bar",
          d: `M${x0},${y} H${x1 - r} Q${x1},${y} ${x1},${y + r} V${y + thick - r} Q${x1},${y + thick} ${x1 - r},${y + thick} H${x0} Z`,
        }, svg);
      }
      const v = el("text", { x: labelW + w + 8, y: y + thick / 2 + 4, class: "value-label" }, svg);
      v.textContent = fmt(d.value);
      const show = () => { if (bar) bar.classList.add("hover"); tip.show(fmt(d.value), d.label, labelW + w / 2, i * rowH); };
      const hide = () => { if (bar) bar.classList.remove("hover"); tip.hide(); };
      hit.addEventListener("pointerenter", show);
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("focus", show);
      hit.addEventListener("blur", hide);
    });
    tableView(box, data, [opts.catHead || "Kategori", opts.valHead || "Jumlah"]);
  }

  function renderCharts() {
    const src = document.getElementById("chart-data");
    if (!src) return;
    const data = JSON.parse(src.textContent);
    const dayFmt = (s) => { const p = s.split("-"); return p[2] + "/" + p[1]; };
    const perDay = document.getElementById("chart-per-day");
    if (perDay) columnChart(perDay, data.per_day, { title: "Lamaran terkirim per hari", unit: "lamaran", labelFmt: dayFmt, empty: "Belum ada lamaran terkirim dalam 30 hari terakhir." });
    const funnel = document.getElementById("chart-funnel");
    if (funnel) barChart(funnel, data.funnel, { title: "Corong lamaran", catHead: "Tahap" });
    const plat = document.getElementById("chart-platform");
    if (plat) barChart(plat, data.per_platform, { title: "Terkirim per platform", catHead: "Platform", empty: "Belum ada lamaran terkirim." });
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderCharts, 150); });
  document.addEventListener("DOMContentLoaded", () => {
    renderCharts();

    // Konfirmasi untuk tombol berisiko.
    document.querySelectorAll("[data-confirm]").forEach((btn) => {
      btn.addEventListener("click", (e) => { if (!confirm(btn.getAttribute("data-confirm"))) e.preventDefault(); });
    });

    // Salin teks (cover letter).
    document.querySelectorAll("[data-copy]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = document.querySelector(btn.getAttribute("data-copy"));
        if (!target) return;
        navigator.clipboard.writeText(target.value || target.textContent).then(() => {
          const old = btn.textContent;
          btn.textContent = "Tersalin";
          setTimeout(() => (btn.textContent = old), 1500);
        });
      });
    });

    // Pilih semua checkbox di tabel lowongan.
    const all = document.getElementById("select-all");
    if (all) all.addEventListener("change", () => {
      document.querySelectorAll("input[name=ids]").forEach((c) => (c.checked = all.checked));
    });
  });
})();
