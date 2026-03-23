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
REDACT_COLOR = (0, 0, 0) # Sort boks

COLUMNS_TO_REDACT = [
    {"name": "Boks venstre for 25", "redact_text": r"^25$", "use_regex": True, "text_pad": (100, 2, -50, 2)},
    {"name": "Boks højre for 25", "redact_text": r"^25$", "use_regex": True, "text_pad": (-48, 2, 80, 2)},
    {"name": "Faktura Totaler", "redact_text": ["Subtotal", "beløb", "Momsbeløb", "inkl."], "use_regex": False, "text_pad": (-90, 2, 130, 2)},
]

# ── Logik ──────────────────────────────────────────────────────────────────

def resolve_target_pages(col: dict, total_pages: int) -> list[int]:
    raw = col.get("pages")
    if raw is None: return list(range(total_pages))
    if raw == "last": return [total_pages - 1]
    if raw == "first": return [0]
    return [i for i in raw if isinstance(i, int) and 0 <= i < total_pages]

def apply_text_redacts(page, col, redact_color, page_idx, log=print) -> int:
    raw = col["redact_text"]
    texts = [raw] if isinstance(raw, str) else list(raw)
    pad = col.get("text_pad", (2, 2, 2, 2))
    use_regex = col.get("use_regex", False)
    n = 0
    
    if use_regex:
        words = page.get_text("words")
        for s in texts:
            pattern = re.compile(s)
            for w in words:
                if pattern.search(w[4]):
                    r = (fitz.Rect(w[:4]) + (-pad[0], -pad[1], pad[2], pad[3])) & page.rect
                    if r.get_area() > 0:
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
    root.geometry("1000x900")

    folder_var = tk.StringVar()
    # preview_state gemmes nu i en ordbog, som vi tilgår via funktioner
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
            
            # Zoom ind for bedre læsbarhed (1.5x)
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
        if not pdfs: return messagebox.showwarning("Fejl", "Ingen PDF-filer fundet i mappen.")
        
        chosen = random.choice(pdfs)
        log(f"\n--- Visning af tilfældig fil: {chosen.name} ---")
        
        if state["doc"]: state["doc"].close()
        
        try:
            doc = fitz.open(chosen)
            for col in COLUMNS_TO_REDACT:
                for p_idx in resolve_target_pages(col, len(doc)):
                    apply_text_redacts(doc[p_idx], col, REDACT_COLOR, p_idx, log=log)
            
            for page in doc: page.apply_redactions()
            
            state["doc"] = doc
            state["total_pages"] = len(doc)
            state["page_idx"] = 0
            render_page()
        except Exception as e:
            log(f"Kunne ikke åbne preview: {e}")

    def run_batch():
        path = folder_var.get().strip()
        if not path: return messagebox.showwarning("Mangler mappe", "Vælg venligst en mappe først.")
        
        pdfs = [p for p in Path(path).glob("*.pdf") if DEFAULT_OUTPUT_SUBDIR not in str(p)]
        log(f"\n🚀 Starter batch-behandling af {len(pdfs)} filer...")
        
        out_dir = Path(path) / DEFAULT_OUTPUT_SUBDIR
        out_dir.mkdir(exist_ok=True)
        
        for pdf in pdfs:
            log(f"Behandler: {pdf.name}")
            try:
                doc = fitz.open(pdf)
                for col in COLUMNS_TO_REDACT:
                    for p_idx in resolve_target_pages(col, len(doc)):
                        apply_text_redacts(doc[p_idx], col, REDACT_COLOR, p_idx, log=lambda x: None) # Stille log under batch
                
                for page in doc: page.apply_redactions()
                doc.save(out_dir / pdf.name, garbage=4, deflate=True)
                doc.close()
                log(f"  ✓ Gemt i mappen '{DEFAULT_OUTPUT_SUBDIR}'")
            except Exception as e:
                log(f"  ✗ FEJL i {pdf.name}: {e}")
        
        log("\n✅ Batch-behandling er færdig!")

    # --- Layout (Dansk) ---
    main = ttk.Frame(root, padding=10)
    main.pack(fill="both", expand=True)

    # Top: Mappevælger
    top = ttk.Frame(main)
    top.pack(fill="x", pady=5)
    ttk.Label(top, text="PDF Mappe:").pack(side="left")
    ttk.Entry(top, textvariable=folder_var).pack(side="left", fill="x", expand=True, padx=5)
    ttk.Button(top, text="Vælg mappe...", command=lambda: folder_var.set(filedialog.askdirectory())).pack(side="left")

    # Knapper
    btns = ttk.Frame(main)
    btns.pack(fill="x", pady=5)
    ttk.Button(btns, text="Start Batch-behandling", command=run_batch).pack(side="left", padx=5)
    ttk.Button(btns, text="Vis tilfældig PDF (Preview)", command=start_preview).pack(side="left", padx=5)

    # Preview Sektion
    prev_f = ttk.LabelFrame(main, text="Forhåndsvisning", padding=5)
    prev_f.pack(fill="both", expand=True, pady=5)
    
    nav = ttk.Frame(prev_f)
    nav.pack(fill="x", pady=2)
    ttk.Button(nav, text="<- Forrige side", command=lambda: skift_side(-1)).pack(side="left")
    page_label = ttk.Label(nav, text="Side 0 ud af 0")
    page_label.pack(side="left", padx=20)
    ttk.Button(nav, text="Næste side ->", command=lambda: skift_side(1)).pack(side="right")

    preview_canvas = tk.Canvas(prev_f, bg="gray90", height=400)
    vsb = ttk.Scrollbar(prev_f, orient="vertical", command=preview_canvas.yview)
    hsb = ttk.Scrollbar(prev_f, orient="horizontal", command=preview_canvas.xview)
    preview_canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    
    vsb.pack(side="right", fill="y")
    hsb.pack(side="bottom", fill="x")
    preview_canvas.pack(fill="both", expand=True)

    # Log
    ttk.Label(main, text="Statuslog:").pack(anchor="w")
    log_box = scrolledtext.ScrolledText(main, height=10, state="disabled", font=("Consolas", 9))
    log_box.pack(fill="x", side="bottom", pady=5)

    root.mainloop()

if __name__ == "__main__":
    run_gui()