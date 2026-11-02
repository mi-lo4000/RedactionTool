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

# Farve pr. tilstand
COLOR_STANDARD = (0, 0, 0)   # sort
COLOR_EAK      = (0, 0, 0)   # sort

COLUMNS_TO_REDACT = [
    {"name": "Boks venstre for 25", "redact_text": r"^25$", "use_regex": True,
     "skip_if_next": r"^[Kk]ilo", "only_moms_column": True, "text_pad": (100, 2, -50, 2)},
    {"name": "Boks højre for 25", "redact_text": r"^25$", "use_regex": True,
     "skip_if_next": r"^[Kk]ilo", "only_moms_column": True, "text_pad": (-48, 2, 80, 2)},
    {"name": "Faktura Totaler", "redact_text": ["Subtotal", "beløb", "Momsbeløb", "inkl."],
     "use_regex": False, "text_pad": (-90, 2, 130, 2)},
]

# Indstillinger for EAK-tilstanden
EAK_SETTINGS = {
    "anchor": r"EAK",       # ordet der starter mønstret (case-insensitivt)
    "max_digits": 10,       # STOP når så mange cifre er dækket (ikke et krav)
    "pad": (2, 1, 2, 1),    # (venstre, top, højre, bund) ekstra margin i punkter
    "max_gap": 20.0,        # max vandret afstand mellem to ord der hører sammen
    "max_width": 120.0,     # hård bremse: boksen må aldrig blive bredere end dette
    "connector": r"^(kode|nr\.?|:|-)$",   # tillades mellem 'EAK' og koden
    "clip_to_neighbours": True,  # boksen trækkes tilbage fra alle andre ord
    "neighbour_gap": 1.0,        # afstand i punkter der holdes til naboord
}

# Et ord regnes kun som en del af EAK-koden hvis det er rene cifre
# (evt. med . / -). Ord med komma, fx '1,00' eller '395,00kg', hører til
# Antal-kolonnen og stopper boksen.
EAK_CODE_WORD = re.compile(r"^[\d.\-/]*\d[\d.\-/]*$")

# ── Logik ──────────────────────────────────────────────────────────────────

def resolve_target_pages(col: dict, total_pages: int) -> list[int]:
    raw = col.get("pages")
    if raw is None: return list(range(total_pages))
    if raw == "last": return [total_pages - 1]
    if raw == "first": return [0]
    return [i for i in raw if isinstance(i, int) and 0 <= i < total_pages]

def _index_to_the_right(words, i, max_gap=40.0):
    """Returnerer INDEKSET på ordet umiddelbart til HØJRE for words[i]
    i samme vandrette bånd, ellers None.

    Vi bruger rene koordinater i stedet for block/line-numre, fordi PyMuPDF ofte
    giver ordene hvert sit linjenummer (og i vilkårlig rækkefølge), når cellerne
    er tegnet som separate tekst-operationer.
    """
    w = words[i]
    y_mid = (w[1] + w[3]) / 2
    tol = max((w[3] - w[1]) * 0.6, 3.0)
    best_idx = None
    for j, o in enumerate(words):
        if j == i or not o[4].strip():
            continue
        if abs((o[1] + o[3]) / 2 - y_mid) > tol:   # ikke samme linje/bånd
            continue
        if o[0] < w[2] - 1:                        # ikke til højre for ordet
            continue
        if o[0] - w[2] > max_gap:                  # for langt væk (anden kolonne)
            continue
        if best_idx is None or o[0] < words[best_idx][0]:
            best_idx = j
    return best_idx

def _word_to_the_right(words, i, max_gap=40.0):
    idx = _index_to_the_right(words, i, max_gap)
    return words[idx] if idx is not None else None

def find_moms_column_x(doc, tol=15.0):
    """Finder x-intervallet for 'Moms %'-kolonnen ud fra overskriften."""
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

def _count_digits(s: str) -> int:
    return sum(c.isdigit() for c in s)

def _clip_away_from_neighbours(padded, core, words, keep: set[int], gap=1.0):
    """Trækker boksen tilbage, så den aldrig rører et ord der ikke er en del af fundet.

    `core` er den rå ramme om selve EAK-teksten, `padded` er den samme ramme med
    margin lagt til. For hvert fremmed ord der overlapper `padded`, flyttes den
    kant der peger mod ordet, ind foran det. Resultatet kan aldrig blive mindre
    end `core`, så selve koden bliver altid dækket.
    """
    out = fitz.Rect(padded)
    for k, o in enumerate(words):
        if k in keep or not o[4].strip():
            continue
        r = fitz.Rect(o[:4])
        if (out & r).get_area() <= 0:
            continue
        if r.x0 >= core.x1:                     # naboord til højre
            out.x1 = min(out.x1, r.x0 - gap)
        elif r.x1 <= core.x0:                   # naboord til venstre
            out.x0 = max(out.x0, r.x1 + gap)
        elif r.y1 <= core.y0:                   # linje ovenover
            out.y0 = max(out.y0, r.y1 + gap)
        elif r.y0 >= core.y1:                   # linje nedenunder
            out.y1 = min(out.y1, r.y0 - gap)
    # Boksen må aldrig krympe ind i selve koden
    out.x0 = min(out.x0, core.x0); out.x1 = max(out.x1, core.x1)
    out.y0 = min(out.y0, core.y0); out.y1 = max(out.y1, core.y1)
    return out

# ── EAK-tilstand ───────────────────────────────────────────────────────────

def apply_eak_redacts(page, redact_color, page_idx, log=print, inspect=False,
                      settings=EAK_SETTINGS) -> int:
    """Dækker 'EAK' + de efterfølgende cifre med én samlet boks.

    Den finder ordet 'EAK', går derefter mod højre i samme linje og samler ord
    op, indtil der er fundet `digits` cifre. Alle rektangler slås sammen til én
    boks, så både teksten 'EAK' og selve koden forsvinder.
    """
    words = page.get_text("words")
    anchor = re.compile(settings.get("anchor", r"EAK"), re.IGNORECASE)
    max_digits = settings.get("max_digits", 10)
    pad = settings.get("pad", (2, 1, 2, 1))
    max_gap = settings.get("max_gap", 20.0)
    max_width = settings.get("max_width", 120.0)
    connector = re.compile(settings.get("connector", r"^(kode|nr\.?|:|-)$"), re.IGNORECASE)

    if inspect:
        log(f"\n--- INSPECT MODE (EAK): Rå ord på side {page_idx + 1} ---")
        for w in words:
            if w[4].strip():
                log(f"POS: {tuple(round(c,1) for c in w[:4])} | TEXT: '{w[4].strip()}'")
        log("-----------------------------------------------\n")

    n = 0
    consumed: set[int] = set()

    for i, w in enumerate(words):
        if i in consumed:
            continue
        txt = w[4].strip()
        if not anchor.search(txt):
            continue

        rect = fitz.Rect(w[:4])
        found = _count_digits(txt)          # dækker 'EAK170405' i ét ord
        j, used_connector = i, False
        match_idx = {i}

        while found < max_digits:
            nxt = _index_to_the_right(words, j, max_gap)
            if nxt is None:
                break
            otxt = words[nxt][4].strip()

            if not EAK_CODE_WORD.match(otxt):
                # Ét bindeord ('kode', 'nr.', ':') må stå mellem EAK og koden.
                # Alt andet - fx '1,00stk' eller '395,00kg' - stopper boksen.
                if used_connector or not connector.match(otxt):
                    break
                used_connector = True

            cand = rect | fitz.Rect(words[nxt][:4])
            if cand.width > max_width:      # hård bremse mod nabokolonnen
                if inspect: log(f"  ⊘ stoppet ved '{otxt}' (max_width {max_width})")
                break

            rect = cand
            consumed.add(nxt)
            match_idx.add(nxt)
            found += _count_digits(otxt)
            j = nxt

        r = (rect + (-pad[0], -pad[1], pad[2], pad[3])) & page.rect
        if settings.get("clip_to_neighbours", True):
            r = _clip_away_from_neighbours(r, rect, words, match_idx,
                                           settings.get("neighbour_gap", 1.0))
        if r.get_area() > 0:
            if inspect:
                log(f"  ▪ EAK '{txt}' -> {found} cifre | boks x={r.x0:.1f}-{r.x1:.1f} y={r.y0:.1f}")
            page.add_redact_annot(r, fill=redact_color)
            n += 1

    if n: log(f"  ✓ Side {page_idx + 1} | 'EAK' | {n} boks(er) fundet")
    return n

# ── Standard-tilstand ──────────────────────────────────────────────────────

def apply_text_redacts(page, col, redact_color, page_idx, log=print, inspect=False,
                       moms_x=None) -> int:
    raw = col["redact_text"]
    texts = [raw] if isinstance(raw, str) else list(raw)
    pad = col.get("text_pad", (2, 2, 2, 2))
    use_regex = col.get("use_regex", False)
    n = 0

    if inspect:
        log(f"\n--- INSPECT MODE: Rå data på side {page_idx + 1} ---")
        blocks = page.get_text("blocks")
        for b in blocks:
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
                if xr and not (xr[0] <= w[0] <= xr[1]):
                    if inspect: log(f"  ⊘ x={w[0]:.1f} udenfor Moms-kolonnen {xr[0]:.1f}-{xr[1]:.1f}")
                    continue
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

# ── Fælles behandling af ét dokument ───────────────────────────────────────

def process_doc(doc, mode: str, log=print, inspect: bool = False) -> None:
    """Tilføjer redaktioner på hele dokumentet ud fra den valgte tilstand."""
    if mode == "eak":
        for p_idx in range(len(doc)):
            apply_eak_redacts(doc[p_idx], COLOR_EAK, p_idx, log=log, inspect=inspect)
    else:
        moms_x = find_moms_column_x(doc)
        if inspect:
            log(f"Moms-kolonne fundet: {moms_x}" if moms_x else "ADVARSEL: Moms-kolonne IKKE fundet")
        for col in COLUMNS_TO_REDACT:
            for p_idx in resolve_target_pages(col, len(doc)):
                apply_text_redacts(doc[p_idx], col, COLOR_STANDARD, p_idx, log=log,
                                   inspect=inspect, moms_x=moms_x)
    for page in doc:
        page.apply_redactions()

# ── GUI Applikation ────────────────────────────────────────────────────────

def run_gui():
    root = tk.Tk()
    root.title("RedactionTool")
    root.geometry("1000x950")

    folder_var = tk.StringVar()
    inspect_var = tk.BooleanVar(value=False)
    mode_var = tk.StringVar(value="standard")   # "standard" eller "eak"
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
        if not path: return messagebox.showwarning("Mangler mappe", "Vælg en mappe først.")
        pdfs = [p for p in Path(path).glob("*.pdf") if DEFAULT_OUTPUT_SUBDIR not in str(p)]
        if not pdfs: return messagebox.showwarning("Fejl", "Ingen PDF-filer fundet.")

        if inspect_var.get():
            log_box.configure(state="normal")
            log_box.delete("1.0", "end")
            log_box.configure(state="disabled")

        chosen = random.choice(pdfs)
        log(f"\n--- Preview ({mode_var.get()}): {chosen.name} ---")
        if state["doc"]: state["doc"].close()

        try:
            doc = fitz.open(chosen)
            process_doc(doc, mode_var.get(), log=log, inspect=inspect_var.get())
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
        log(f"\n🚀 Behandler {len(pdfs)} filer i tilstand '{mode_var.get()}'...")
        out_dir = Path(path) / DEFAULT_OUTPUT_SUBDIR
        out_dir.mkdir(exist_ok=True)

        for pdf in pdfs:
            try:
                doc = fitz.open(pdf)
                process_doc(doc, mode_var.get(), log=lambda x: None, inspect=False)
                doc.save(out_dir / pdf.name, garbage=4, deflate=True)
                doc.close()
                log(f"  ✓ {pdf.name}")
            except Exception as e:
                log(f"  ✗ FEJL i {pdf.name}: {e}")
        log("\n✅ Færdig!")

    # --- UI Layout ---
    main = ttk.Frame(root, padding=10)
    main.pack(fill="both", expand=True)

    top = ttk.Frame(main)
    top.pack(fill="x", pady=5)
    ttk.Label(top, text="Mappe:").pack(side="left")
    ttk.Entry(top, textvariable=folder_var).pack(side="left", fill="x", expand=True, padx=5)
    ttk.Button(top, text="Gennemse...", command=lambda: folder_var.set(filedialog.askdirectory())).pack(side="left")

    mode_f = ttk.LabelFrame(main, text="Fakturatype", padding=5)
    mode_f.pack(fill="x", pady=5)
    ttk.Radiobutton(mode_f, text="Standard (Moms % + totaler, sort boks)",
                    variable=mode_var, value="standard").pack(side="left", padx=5)
    ttk.Radiobutton(mode_f, text="EAK (EAK + kode, hvid boks)",
                    variable=mode_var, value="eak").pack(side="left", padx=15)

    btns = ttk.Frame(main)
    btns.pack(fill="x", pady=5)
    ttk.Button(btns, text="Kør Batch", command=run_batch).pack(side="left", padx=5)
    ttk.Button(btns, text="Vis tilfældig (Preview)", command=start_preview).pack(side="left", padx=5)
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