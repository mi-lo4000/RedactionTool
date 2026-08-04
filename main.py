from __future__ import annotations
import os
import sys
import re
import random
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import pymupdf as fitz 

# ── Konfiguration ──────────────────────────────────────────────────────────
DEFAULT_OUTPUT_SUBDIR = "Redacted"
REDACT_COLOR = (0, 0, 0)

COLUMNS_TO_REDACT = [
    {"name": "Boks venstre for 25", "redact_text": r"^25$", "use_regex": True,
     "skip_if_next": r"^[Kk]ilo", "only_moms_column": True, "text_pad": (100, 2, -50, 2)},
    {"name": "Boks højre for 25", "redact_text": r"^25$", "use_regex": True,
     "skip_if_next": r"^[Kk]ilo", "only_moms_column": True, "text_pad": (-48, 2, 80, 2)},
    {"name": "Faktura Totaler", "redact_text": ["Subtotal", "beløb", "Momsbeløb", "inkl."],
     "use_regex": False, "text_pad": (-90, 2, 130, 2)},
]

# ── Logik ──────────────────────────────────────────────────────────────────

def resolve_target_pages(col: dict, total_pages: int) -> list[int]:
    raw = col.get("pages")
    if raw is None: return list(range(total_pages))
    if raw == "last": return [total_pages - 1]
    if raw == "first": return [0]
    return [i for i in raw if isinstance(i, int) and 0 <= i < total_pages]

def _word_to_the_right(words, i, max_gap=40.0):
    """Finder ordet umiddelbart til HØJRE for words[i] i samme vandrette bånd.

    Vi bruger rene koordinater i stedet for block/line-numre, fordi PyMuPDF ofte
    giver '25' og 'Kilo' hvert sit linjenummer (og returnerer dem i vilkårlig
    rækkefølge), når cellerne er tegnet som separate tekst-operationer.
    """
    w = words[i]
    y_mid = (w[1] + w[3]) / 2
    tol = max((w[3] - w[1]) * 0.6, 3.0)
    best = None
    for j, o in enumerate(words):
        if j == i or not o[4].strip():
            continue
        if abs((o[1] + o[3]) / 2 - y_mid) > tol:   # ikke samme linje/bånd
            continue
        if o[0] < w[2] - 1:                        # ikke til højre for ordet
            continue
        if o[0] - w[2] > max_gap:                  # for langt væk (anden kolonne)
            continue
        if best is None or o[0] < best[0]:
            best = o
    return best

def find_moms_column_x(doc, tol=15.0):
    """Finder x-intervallet for 'Moms %'-kolonnen ud fra overskriften.

    Overskriften står kun på den første side, så vi leder gennem dokumentet og
    genbruger resultatet på alle sider. Returnerer None hvis den ikke findes.
    """
    for page in doc:
        for b in page.get_text("blocks"):
            t = b[4].strip().lower()
            if t.startswith("moms") and len(t) < 12:      # 'Moms\n%' - ikke 'Momsbeløb ...'
                return (b[0] - tol, b[2] + tol)
    return None

def _next_word_matches(words, i, pattern) -> bool:
    """True hvis ordet lige til højre matcher pattern (fx 'Kilo' efter '25')."""
    if pattern.search(words[i][4].strip()):        # sammenklistret, fx '25Kilo'
        return True
    nxt = _word_to_the_right(words, i)
    return bool(nxt and pattern.search(nxt[4].strip()))

def apply_text_redacts(page, col, redact_color, page_idx, log=print, inspect=False,
                       moms_x=None) -> int:
    raw = col["redact_text"]
    texts = [raw] if isinstance(raw, str) else list(raw)
    pad = col.get("text_pad", (2, 2, 2, 2))
    use_regex = col.get("use_regex", False)
    n = 0
    
    # Hvis Inspect Mode er til, printer vi alt indholdet på siden én gang pr. kolonne-tjek
    if inspect:
        log(f"\n--- INSPECT MODE: Rå data på side {page_idx + 1} ---")
        blocks = page.get_text("blocks")
        for b in blocks:
            # b[4] er teksten, b[:4] er koordinaterne (x0, y0, x1, y1)
            log(f"POS: {tuple(round(c,1) for c in b[:4])} | TEXT: '{b[4].strip()}'")
        log("-----------------------------------------------\n")

    if use_regex:
        words = page.get_text("words")
        skip_pat = re.compile(col["skip_if_next"]) if col.get("skip_if_next") else None
        xr = moms_x if col.get("only_moms_column") else None
        for s in texts:
            pattern = re.compile(s)
            for i, w in enumerate(words):
                if not pattern.search(w[4]):
                    continue
                # Guard 1: ankeret skal stå i Moms %-kolonnen
                if xr and not (xr[0] <= w[0] <= xr[1]):
                    if inspect: log(f"  ⊘ x={w[0]:.1f} udenfor Moms-kolonnen {xr[0]:.1f}-{xr[1]:.1f}")
                    continue
                # Guard 2: '25 Kilo' er ikke et anker
                if skip_pat and _next_word_matches(words, i, skip_pat):
                    if inspect: log(f"  ⊘ x={w[0]:.1f} efterfulgt af Kilo")
                    continue
                r = (fitz.Rect(w[:4]) + (-pad[0], -pad[1], pad[2], pad[3])) & page.rect
                if r.get_area() > 0:
                    if inspect: log(f"  ▪ anker x={w[0]:.1f} -> boks x={r.x0:.1f}-{r.x1:.1f} y={r.y0:.1f}")
                    page.add_redact_annot(r, fill=redact_color); n += 1
    else:
        for s in texts:
            for h in page.search_for(str(s).strip()):
                r = (h + (-pad[0], -pad[1], pad[2], pad[3])) & page.rect
                if r.get_area() > 0:
                    page.add_redact_annot(r, fill=redact_color); n += 1

    if n: log(f"  ✓ Side {page_idx + 1} | '{col['name']}' | {n} boks(er) fundet")
    return n

# ── GUI Applikation ────────────────────────────────────────────────────────

def run_gui():
    root = tk.Tk()
    root.title("RedactionTool")
    root.geometry("1000x950")

    folder_var = tk.StringVar()
    inspect_var = tk.BooleanVar(value=False) # Holder styr på Inspect Mode
    state = {"doc": None, "tk_img": None, "page_idx": 0, "total_pages": 0}

    def log(msg: str):
        log_box.configure(state="normal")
        log_box.insert("end", msg + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")
        root.update_idletasks()

    def render_page():
        if not state["doc"]: return
        try:
            idx = state["page_idx"]
            page = state["doc"][idx]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
            img_data = pix.tobytes("png")
            img = tk.PhotoImage(data=img_data)
            preview_canvas.delete("all")
            preview_canvas.create_image(0, 0, anchor="nw", image=img)
            preview_canvas.configure(scrollregion=(0, 0, img.width(), img.height()))
            state["tk_img"] = img 
            page_label.configure(text=f"Side {idx + 1} ud af {state['total_pages']}")
        except Exception as e:
            log(f"Fejl ved visning af side: {e}")

    def skift_side(retning):
        if not state["doc"]: return
        ny_side = state["page_idx"] + retning
        if 0 <= ny_side < state["total_pages"]:
            state["page_idx"] = ny_side
            render_page()

    def start_preview():
        path = folder_var.get().strip()
        pdfs = [p for p in Path(path).glob("*.pdf") if DEFAULT_OUTPUT_SUBDIR not in str(p)]
        if not pdfs: return messagebox.showwarning("Fejl", "Ingen PDF-filer fundet.")
        
        # Ryd log hvis inspect er valgt for bedre overblik
        if inspect_var.get():
            log_box.configure(state="normal")
            log_box.delete("1.0", "end")
            log_box.configure(state="disabled")

        chosen = random.choice(pdfs)
        log(f"\n--- Preview: {chosen.name} ---")
        if state["doc"]: state["doc"].close()
        
        try:
            doc = fitz.open(chosen)
            is_inspecting = inspect_var.get()
            moms_x = find_moms_column_x(doc)
            if is_inspecting:
                log(f"Moms-kolonne fundet: {moms_x}" if moms_x else "ADVARSEL: Moms-kolonne IKKE fundet")
            
            for col in COLUMNS_TO_REDACT:
                for p_idx in resolve_target_pages(col, len(doc)):
                    apply_text_redacts(doc[p_idx], col, REDACT_COLOR, p_idx, log=log,
                                       inspect=is_inspecting, moms_x=moms_x)
            
            for page in doc: page.apply_redactions()
            state["doc"] = doc
            state["total_pages"] = len(doc)
            state["page_idx"] = 0
            render_page()
        except Exception as e:
            log(f"Kunne ikke åbne preview: {e}")

    def run_batch():
        path = folder_var.get().strip()
        if not path: return messagebox.showwarning("Mangler mappe", "Vælg en mappe først.")
        pdfs = [p for p in Path(path).glob("*.pdf") if DEFAULT_OUTPUT_SUBDIR not in str(p)]
        log(f"\n🚀 Behandler {len(pdfs)} filer...")
        out_dir = Path(path) / DEFAULT_OUTPUT_SUBDIR
        out_dir.mkdir(exist_ok=True)
        
        for pdf in pdfs:
            try:
                doc = fitz.open(pdf)
                moms_x = find_moms_column_x(doc)
                for col in COLUMNS_TO_REDACT:
                    for p_idx in resolve_target_pages(col, len(doc)):
                        apply_text_redacts(doc[p_idx], col, REDACT_COLOR, p_idx, log=lambda x: None,
                                           inspect=False, moms_x=moms_x)
                for page in doc: page.apply_redactions()
                doc.save(out_dir / pdf.name, garbage=4, deflate=True)
                doc.close()
                log(f"  ✓ {pdf.name}")
            except Exception as e: log(f"  ✗ FEJL i {pdf.name}: {e}")
        log("\n✅ Færdig!")

    # --- UI Layout ---
    main = ttk.Frame(root, padding=10)
    main.pack(fill="both", expand=True)

    top = ttk.Frame(main)
    top.pack(fill="x", pady=5)
    ttk.Label(top, text="Mappe:").pack(side="left")
    ttk.Entry(top, textvariable=folder_var).pack(side="left", fill="x", expand=True, padx=5)
    ttk.Button(top, text="Gennemse...", command=lambda: folder_var.set(filedialog.askdirectory())).pack(side="left")

    btns = ttk.Frame(main)
    btns.pack(fill="x", pady=5)
    ttk.Button(btns, text="Kør Batch", command=run_batch).pack(side="left", padx=5)
    ttk.Button(btns, text="Vis tilfældig (Preview)", command=start_preview).pack(side="left", padx=5)
    
    # Den vigtige knap til dig:
    ttk.Checkbutton(btns, text="Inspect Mode (Vis koordinater & rå tekst)", variable=inspect_var).pack(side="right", padx=10)

    prev_f = ttk.LabelFrame(main, text="Forhåndsvisning", padding=5)
    prev_f.pack(fill="both", expand=True, pady=5)
    
    nav = ttk.Frame(prev_f)
    nav.pack(fill="x", pady=2)
    ttk.Button(nav, text="<- Forrige", command=lambda: skift_side(-1)).pack(side="left")
    page_label = ttk.Label(nav, text="Side 0 ud af 0")
    page_label.pack(side="left", padx=20)
    ttk.Button(nav, text="Næste ->", command=lambda: skift_side(1)).pack(side="right")

    preview_canvas = tk.Canvas(prev_f, bg="gray90", height=450)
    vsb = ttk.Scrollbar(prev_f, orient="vertical", command=preview_canvas.yview)
    hsb = ttk.Scrollbar(prev_f, orient="horizontal", command=preview_canvas.xview)
    preview_canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    vsb.pack(side="right", fill="y")
    hsb.pack(side="bottom", fill="x")
    preview_canvas.pack(fill="both", expand=True)

    log_box = scrolledtext.ScrolledText(main, height=12, state="disabled", font=("Consolas", 9))
    log_box.pack(fill="x", side="bottom", pady=5)

    root.mainloop()

if __name__ == "__main__":
    run_gui()
