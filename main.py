"""
DedactTool — redact regions in PDFs using text-found coordinates (PyMuPDF).

GUI: run with no arguments (double-click or `python main.py`).
CLI batch: `python main.py "C:/path/to/folder"`

Build .exe (after `pip install pyinstaller`): run `build_exe.bat` or:
  pyinstaller --onefile --windowed --name DedactTool --collect-all pymupdf main.py

Requirements: pip install pymupdf
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import pymupdf as fitz  # PyPI package "fitz" is unrelated; use pymupdf explicitly

# ── Default configuration (edit for your layout) ─────────────────────────────

DEFAULT_OUTPUT_SUBDIR = "dedacted"
OUTPUT_NAME_SUFFIX = "_dedact"  # e.g. invoice.pdf → invoice_dedact.pdf
REDACT_COLOR = (0, 0, 0)

# Inspect mode: max pages to print per PDF (None = all). Use 1 while tuning new columns.
INSPECT_MAX_PAGES: int | None = 3

# Black boxes use ONLY coordinates PyMuPDF finds in each PDF (per page). No hard-coded x/y.
# Use Inspect mode to copy the exact strings as they appear in the file.
#
# Each rule needs EITHER:
#
#   text_zone — one rectangle per page, built from text coordinates only:
#     Single anchor: find, then expand (l,t,r,b) pts, match "first"|"union",
#                   optional y0_to/y1_to/x0_to/x1_to (snap to page edges).
#     Start + end:  find_start and find_end — box is the smallest axis-aligned rectangle
#                   that contains BOTH matches (works for column/table blocks). Same expand/snap.
#
#   redact_text — black out each search hit (string or list); text_pad, text_match_mode, full_width
#
# Optional on any rule: pages — "last", "first", or [0, 2, ...] (0-based); default = all pages.
COLUMNS_TO_REDACT = [
    # 1. BOKS: Til venstre for 25 (100 bred, 50 afstand)
    {
        "name": "Boks til venstre for 25",
        "redact_text": r"^25$", 
        "use_regex": True,
        # (Venstre udvidelse, Top, Højre indsnævring, Bund)
        "text_pad": (100, 2, -50, 2), 
    },

    # 2. BOKS: Til højre for 25 (100 bred, 50 afstand)
    {
        "name": "Boks til højre for 25",
        "redact_text": r"^25$",
        "use_regex": True,
        # Her bruger vi negative tal til venstre for at "skubbe" boksen forbi tallet
        # Venstre: -58.6 (rykker start 50 pts efter x1)
        # Højre: 150 (gør boksen 100 pts bred fra det punkt)
        "text_pad": (-48, 2, 80, 2),
    },
    # 3. BOKS: Opsummering i bunden (Subtotal, Momspligtigt beløb, Momsbeløb, I alt)
    {
        "name": "Faktura Totaler",
        "redact_text": ["Subtotal", "beløb", "Momsbeløb", "inkl."],
        "use_regex": False,
        "text_match_mode": "exact",
        # Matematikken her:
        # Venstre (-90): Vi starter 90 pts EFTER teksten (altså ved ca. x=533)
        # Højre (130): Vi slutter 130 pts EFTER teksten (altså ved ca. x=573)
        # Det giver en boks på 40 points bredde, som dækker tallene perfekt.
        "text_pad": (-90, 2, 130, 2),
    },
]

def get_page_position(page_idx: int, total_pages: int) -> str:
    if total_pages == 1:
        return "first"
    if page_idx == 0:
        return "first"
    if page_idx == total_pages - 1:
        return "last"
    return "middle"


def resolve_target_pages(col: dict, total_pages: int) -> list[int]:
    """Which page indices (0-based) this column applies to."""
    if total_pages <= 0:
        return []
    raw = col.get("pages")
    if raw is None:
        return list(range(total_pages))
    if raw == "last":
        return [total_pages - 1]
    if raw == "first":
        return [0]
    return [i for i in raw if isinstance(i, int) and 0 <= i < total_pages]


def _column_uses_text_redact(col: dict) -> bool:
    rt = col.get("redact_text")
    if rt is None:
        return False
    if isinstance(rt, str):
        return bool(rt.strip())
    if isinstance(rt, (list, tuple)):
        return any(str(x).strip() for x in rt)
    return False


def _text_pad_tuple(col: dict) -> tuple[float, float, float, float]:
    pad = col.get("text_pad", (2.0, 2.0, 2.0, 2.0))
    if not isinstance(pad, (list, tuple)) or len(pad) != 4:
        return (2.0, 2.0, 2.0, 2.0)
    return tuple(float(x) for x in pad)


import re # Sørg for at denne står i toppen af din main.py fil!

def apply_text_redacts(
    page: fitz.Page,
    col: dict,
    redact_color: tuple,
    *,
    page_idx: int,
    total_pages: int,
    log=print,
) -> int:
    """Redact regions around each search_for hit, with Regex support. Returns number of rectangles added."""
    raw = col["redact_text"]
    texts = [raw] if isinstance(raw, str) else list(raw)
    pad = _text_pad_tuple(col)
    mode = col.get("text_match_mode", "exact")
    use_regex = col.get("use_regex", False) # Nyt flag til at slå RegEx til
    
    position = get_page_position(page_idx, total_pages)
    pr = page.rect
    n = 0
    
    if use_regex:
        # --- REGEX LØSNING ---
        words = page.get_text("words") # Henter alle ord med deres koordinater
        for s in texts:
            pattern = re.compile(s)
            for w in words:
                text_string = w[4]
                if pattern.search(text_string): # Matcher ordet mod dit regex
                    h = fitz.Rect(w[:4]) # x0, y0, x1, y1 fra ordet
                    if mode == "full_width":
                        r = fitz.Rect(pr.x0, h.y0 - pad[1], pr.x1, h.y1 + pad[3])
                    else:
                        r = fitz.Rect(h.x0 - pad[0], h.y0 - pad[1], h.x1 + pad[2], h.y1 + pad[3])
                    r = r & pr
                    if r.get_area() > 0:
                        page.add_redact_annot(r, fill=redact_color)
                        n += 1
    else:
        # --- ORIGINAL STANDARD LØSNING ---
        flags = col.get("search_flags", 0)
        if not isinstance(flags, int):
            flags = 0
        for s in texts:
            s = str(s).strip()
            if not s:
                continue
            try:
                hits = page.search_for(s, flags=flags)
            except TypeError:
                hits = page.search_for(s)
            for h in hits:
                if mode == "full_width":
                    r = fitz.Rect(pr.x0, h.y0 - pad[1], pr.x1, h.y1 + pad[3])
                else:
                    r = fitz.Rect(h.x0 - pad[0], h.y0 - pad[1], h.x1 + pad[2], h.y1 + pad[3])
                r = r & pr
                if r.get_area() > 0:
                    page.add_redact_annot(r, fill=redact_color)
                    n += 1

    if n:
        log(f"  ✓ Page {page_idx + 1} ({position:6s}) | '{col['name']}' | text: {n} box(es)")
    else:
        log(f"  ⚠ Page {page_idx + 1} | '{col['name']}' | redact_text: no matches on page")
    return n


def _column_uses_text_zone(col: dict) -> bool:
    tz = col.get("text_zone")
    if not isinstance(tz, dict):
        return False
    if str(tz.get("find", "")).strip():
        return True
    if str(tz.get("find_start", "")).strip() or str(tz.get("find_end", "")).strip():
        return True
    return False


def _redact_rule_kind(col: dict) -> str | None:
    has_r = _column_uses_text_redact(col)
    has_z = _column_uses_text_zone(col)
    if has_r and has_z:
        return "invalid"
    if has_r:
        return "redact_text"
    if has_z:
        return "text_zone"
    return None


def _expand_tuple_4(raw, default: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return default
    return tuple(float(x) for x in raw)


def _search_union_rect(page: fitz.Page, query: str, flags: int, match_mode: str) -> fitz.Rect | None:
    """Return one rect: first hit, or union of all hits. None if no matches."""
    try:
        hits = page.search_for(query, flags=flags)
    except TypeError:
        hits = page.search_for(query)
    if not hits:
        return None
    if match_mode == "first":
        return hits[0]
    u = hits[0]
    for h in hits[1:]:
        u |= h
    return u


def apply_text_zone_redact(
    page: fitz.Page,
    col: dict,
    redact_color: tuple,
    *,
    page_idx: int,
    total_pages: int,
    log=print,
) -> bool:
    """
    One rectangle from find (single) or from bounding find_start + find_end (dual), then expand/snap.
    """
    tz = col["text_zone"]
    el, et, er, eb = _expand_tuple_4(tz.get("expand"), (3.0, 3.0, 3.0, 3.0))
    match_mode = tz.get("match", "union")
    flags = tz.get("search_flags", col.get("search_flags", 0))
    if not isinstance(flags, int):
        flags = 0
    position = get_page_position(page_idx, total_pages)
    pr = page.rect

    find_start = str(tz.get("find_start", "")).strip()
    find_end = str(tz.get("find_end", "")).strip()
    find_single = str(tz.get("find", "")).strip()

    if find_start or find_end:
        if not find_start or not find_end:
            log(
                f"  ✗ Page {page_idx + 1} | '{col.get('name')}' | text_zone: "
                f"set both find_start and find_end (or use find alone)"
            )
            return False
        a = _search_union_rect(page, find_start, flags, match_mode)
        b = _search_union_rect(page, find_end, flags, match_mode)
        if a is None:
            log(
                f"  ⚠ Page {page_idx + 1} | '{col.get('name')}' | text_zone: no match for start {find_start!r}"
            )
            return False
        if b is None:
            log(
                f"  ⚠ Page {page_idx + 1} | '{col.get('name')}' | text_zone: no match for end {find_end!r}"
            )
            return False
        x0 = min(a.x0, b.x0) - el
        y0 = min(a.y0, b.y0) - et
        x1 = max(a.x1, b.x1) + er
        y1 = max(a.y1, b.y1) + eb
    elif find_single:
        u = _search_union_rect(page, find_single, flags, match_mode)
        if u is None:
            log(f"  ⚠ Page {page_idx + 1} | '{col.get('name')}' | text_zone: no match for {find_single!r}")
            return False
        x0, y0, x1, y1 = u.x0 - el, u.y0 - et, u.x1 + er, u.y1 + eb
    else:
        log(
            f"  ✗ Page {page_idx + 1} | '{col.get('name')}' | text_zone: "
            f"set find=... or find_start=... + find_end=..."
        )
        return False

    if tz.get("y0_to") == "page_top":
        y0 = pr.y0
    if tz.get("y1_to") == "page_bottom":
        y1 = pr.y1
    if tz.get("x0_to") == "page_left":
        x0 = pr.x0
    if tz.get("x1_to") == "page_right":
        x1 = pr.x1

    rect = fitz.Rect(x0, y0, x1, y1) & pr
    if rect.get_area() <= 0:
        log(f"  ✗ Page {page_idx + 1} | '{col.get('name')}' | text_zone: empty after clip")
        return False

    page.add_redact_annot(rect, fill=redact_color)
    mode_note = "start→end" if (find_start and find_end) else "single"
    log(
        f"  ✓ Page {page_idx + 1} ({position:6s}) | '{col['name']}' | text_zone ({mode_note}) "
        f"x:[{rect.x0:.1f}–{rect.x1:.1f}] y:[{rect.y0:.1f}–{rect.y1:.1f}]"
    )
    return True


def inspect_page(doc: fitz.Document, page_index: int = 0, log=print) -> None:
    page = doc[page_index]
    words = page.get_text("words")
    log(f"\n=== Page {page_index + 1} — {page.rect.width:.0f} x {page.rect.height:.0f} pts ===")
    for w in sorted(words, key=lambda w: (round(w[1] / 10) * 10, w[0])):
        log(f"  x0={w[0]:6.1f}  y0={w[1]:6.1f}  x1={w[2]:6.1f}  y1={w[3]:6.1f}  → '{w[4]}'")


def redact_columns(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    columns: list | None = None,
    inspect_mode: bool = False,
    redact_color: tuple = REDACT_COLOR,
    log=print,
) -> bool:
    """
    Apply redactions from COLUMNS_TO_REDACT. Coordinates come from text search on each PDF only.
    """
    cols = columns if columns is not None else COLUMNS_TO_REDACT
    input_path = Path(input_path)
    output_path = Path(output_path)

    try:
        doc = fitz.open(input_path)
    except Exception as e:
        log(f"✗ Could not open {input_path.name}: {e}")
        return False

    total_pages = len(doc)

    try:
        if inspect_mode:
            n = total_pages if INSPECT_MAX_PAGES is None else min(total_pages, INSPECT_MAX_PAGES)
            for i in range(n):
                inspect_page(doc, i, log=log)
            if INSPECT_MAX_PAGES is not None and total_pages > INSPECT_MAX_PAGES:
                log(f"\n… {total_pages - INSPECT_MAX_PAGES} more page(s) not shown (INSPECT_MAX_PAGES={INSPECT_MAX_PAGES}).")
            log("\nInspect mode — no redaction applied.")
            return True

        for col in cols:
            kind = _redact_rule_kind(col)
            if kind == "invalid":
                log(
                    f"  ✗ Rule '{col.get('name', '?')}' has both redact_text and text_zone — use only one."
                )
                continue
            if kind is None:
                log(
                    f"  ✗ Rule '{col.get('name', '?')}' needs text_zone{{find:...}} or redact_text: ..."
                )
                continue

            target_pages = resolve_target_pages(col, total_pages)

            if kind == "redact_text":
                for page_idx in target_pages:
                    if page_idx >= total_pages:
                        continue
                    page = doc[page_idx]
                    apply_text_redacts(
                        page,
                        col,
                        redact_color,
                        page_idx=page_idx,
                        total_pages=total_pages,
                        log=log,
                    )
            else:
                for page_idx in target_pages:
                    if page_idx >= total_pages:
                        continue
                    page = doc[page_idx]
                    apply_text_zone_redact(
                        page,
                        col,
                        redact_color,
                        page_idx=page_idx,
                        total_pages=total_pages,
                        log=log,
                    )

        for page in doc:
            page.apply_redactions()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), garbage=4, deflate=True)
        log(f"✓ Saved → {output_path.name}")
        return True
    except Exception as e:
        log(f"✗ Error processing {input_path.name}: {e}")
        log(traceback.format_exc())
        return False
    finally:
        doc.close()


def iter_input_pdfs(folder: Path, output_subdir_name: str) -> list[Path]:
    """PDFs directly in `folder`, excluding anything inside the output subfolder."""
    out_dir = folder / output_subdir_name
    pdfs = []
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() != ".pdf":
            continue
        if p.resolve().parent == out_dir.resolve():
            continue
        pdfs.append(p)
    return pdfs


def output_path_for_input(input_pdf: Path, output_dir: Path) -> Path:
    stem = input_pdf.stem
    if stem.lower().endswith(OUTPUT_NAME_SUFFIX.lower()):
        stem = stem[: -len(OUTPUT_NAME_SUFFIX)]
    return output_dir / f"{stem}{OUTPUT_NAME_SUFFIX}.pdf"


def batch_redact_folder(
    folder: str | os.PathLike[str],
    *,
    output_subdir: str = DEFAULT_OUTPUT_SUBDIR,
    inspect_mode: bool = False,
    log=print,
) -> tuple[int, int]:
    """
    Process all PDFs in `folder`; writes to `folder / output_subdir /`.
    Returns (success_count, total_count).
    """
    folder = Path(folder).resolve()
    if not folder.is_dir():
        log(f"✗ Not a folder: {folder}")
        return 0, 0

    output_dir = folder / output_subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfs = iter_input_pdfs(folder, output_subdir)
    if not pdfs:
        log(f"No PDF files found in {folder}")
        return 0, 0

    log(f"Output folder: {output_dir}\nFound {len(pdfs)} PDF(s).\n")
    ok = 0
    for pdf in pdfs:
        log(f"--- {pdf.name} ---")
        dest = output_path_for_input(pdf, output_dir)
        if redact_columns(
            pdf,
            dest,
            inspect_mode=inspect_mode,
            log=log,
        ):
            ok += 1
        log("")

    log(f"Done: {ok}/{len(pdfs)} file(s) processed successfully.")
    return ok, len(pdfs)


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    root = tk.Tk()
    root.title("DedactTool")
    root.minsize(520, 400)

    folder_var = tk.StringVar()
    inspect_var = tk.BooleanVar(value=False)

    def log(msg: str) -> None:
        log_box.configure(state="normal")
        log_box.insert("end", msg + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")
        root.update_idletasks()

    def browse() -> None:
        path = filedialog.askdirectory(title="Select folder with PDFs")
        if path:
            folder_var.set(path)

    def run_batch() -> None:
        path = folder_var.get().strip()
        if not path:
            messagebox.showwarning("DedactTool", "Choose a folder first.")
            return
        run_btn.configure(state="disabled")
        browse_btn.configure(state="disabled")
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")

        def work() -> None:
            try:
                batch_redact_folder(path, inspect_mode=inspect_var.get(), log=log)
            finally:
                run_btn.configure(state="normal")
                browse_btn.configure(state="normal")

        root.after(100, work)

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Folder with PDFs:").grid(row=0, column=0, sticky="w")
    ent = ttk.Entry(frm, textvariable=folder_var, width=50)
    ent.grid(row=1, column=0, sticky="ew", pady=(4, 8))
    browse_btn = ttk.Button(frm, text="Browse…", command=browse)
    browse_btn.grid(row=1, column=1, padx=(8, 0), pady=(4, 8), sticky="e")

    ttk.Checkbutton(frm, text="Inspect mode (print word positions, no redaction)", variable=inspect_var).grid(
        row=2, column=0, columnspan=2, sticky="w", pady=(0, 8)
    )

    run_btn = ttk.Button(frm, text="Process all PDFs", command=run_batch)
    run_btn.grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 8))

    ttk.Label(frm, text="Log:").grid(row=4, column=0, sticky="w")
    log_box = scrolledtext.ScrolledText(frm, height=16, state="disabled", wrap="word", font=("Consolas", 9))
    log_box.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(4, 0))

    frm.columnconfigure(0, weight=1)
    frm.rowconfigure(5, weight=1)

    root.mainloop()


def main() -> None:
    if len(sys.argv) > 1:
        folder = sys.argv[1]
        batch_redact_folder(folder, inspect_mode=os.environ.get("DEDACT_INSPECT", "").lower() in ("1", "true", "yes"))
        return
    run_gui()


if __name__ == "__main__":
    main()
