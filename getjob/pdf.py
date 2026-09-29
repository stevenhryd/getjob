"""Penulis PDF teks sederhana (untuk upload cover letter), tanpa dependensi tambahan."""

from __future__ import annotations

import textwrap
from pathlib import Path

PAGE_W, PAGE_H = 595, 842  # A4 dalam point
MARGIN = 60
FONT_SIZE = 11
LEADING = 15
WRAP = 92


def _escape(line: str) -> str:
    line = line.encode("latin-1", "replace").decode("latin-1")
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_to_pdf(text: str, path: Path) -> Path:
    lines: list[str] = []
    for para in (text or "").splitlines():
        lines.extend(textwrap.wrap(para, WRAP) or [""])
    per_page = int((PAGE_H - 2 * MARGIN) / LEADING)
    pages = [lines[i : i + per_page] for i in range(0, len(lines), per_page)] or [[""]]

    objects: list[bytes] = []
    n_pages = len(pages)
    # 1: catalog, 2: pages, 3: font, lalu pasangan (page, content)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, page_lines in enumerate(pages):
        content_id = 5 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        ops = [f"BT /F1 {FONT_SIZE} Tf {LEADING} TL {MARGIN} {PAGE_H - MARGIN} Td"]
        ops += [f"({_escape(l)}) Tj T*" for l in page_lines]
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path
