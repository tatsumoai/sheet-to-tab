"""Windows-friendly desktop entry point. Run with python -m sheet_to_tab."""

import logging
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .import_score import (
    SCAN_EXTENSIONS, SOURCE_INSTRUMENTS, ImportedScore, find_audiveris, import_score,
)
from .musicxml import Part, ScoreError
from .tablature import PRESETS, make_tab, parse_tuning, pitch_name
from .tab_view import ACCENT, INK, MUTED, PAPER, WARNING, draw_tab


BG = "#edf3f0"
SIZES = {"Medium": 23, "Large": 29, "Extra large": 35}
MEASURES_PER_PAGE = 2
EXAMPLE = Path(__file__).with_name("example.musicxml")


class SheetToTab:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Sheet to Tab")
        root.geometry("1040x835")
        root.minsize(780, 675)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.source = tk.StringVar()
        self.selection = tk.StringVar()
        self.source_shift = tk.StringVar(value="0")
        self.target = tk.StringVar(value=next(iter(PRESETS)))
        self.tuning = tk.StringVar(value=PRESETS[self.target.get()])
        self.max_fret = tk.StringVar(value="24")
        self.octaves = tk.StringVar(value="0")
        self.fret_size = tk.StringVar(value="Large")
        self.status = tk.StringVar(value="Choose the original instrument to open your score.")
        self.file_summary = tk.StringVar(value="MusicXML, PDF, PNG, JPEG, and TIFF")
        self.tab_title = tk.StringVar(value="Tablature")
        self.page_text = tk.StringVar(value="—")
        self.imported: ImportedScore | None = None
        self.parts: list[tuple[str, Part, int | None]] = []
        self.audiveris = find_audiveris()
        self.cancel = Event()
        self.pending = False
        self.closing = False
        self.results: Queue[ImportedScore | Exception] = Queue()
        self.tab = ()
        self.tab_tuning = ()
        self.issues = []
        self.page = 0

        self.build()
        self.source.trace_add("write", self.sync_controls)
        self.selection.trace_add("write", self.settings_changed)
        for setting in (self.tuning, self.source_shift, self.max_fret, self.octaves):
            setting.trace_add("write", self.settings_changed)
        self.sync_controls()
        self.source_field.focus_set()

    def build(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TButton", font=("Segoe UI", 10), padding=(11, 5),
                        background="#f1f6f3", bordercolor="#aebfb8")
        for control in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(control, padding=(4, 3), fieldbackground=PAPER, bordercolor="#aebfb8")
            style.map(control, fieldbackground=[("disabled", "#edf2ef"), ("readonly", PAPER)])
        style.configure("Primary.TButton", background=ACCENT, foreground=PAPER,
                        bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
        style.map("Primary.TButton", background=[("disabled", "#d8e4df"), ("active", "#174f40")],
                  foreground=[("disabled", "#5f706b"), ("!disabled", PAPER)])
        style.configure("Card.TFrame", background=PAPER)
        style.configure("Card.TLabel", background=PAPER, foreground=INK, font=("Segoe UI", 10))
        style.configure("Heading.Card.TLabel", font=("Segoe UI", 12, "bold"), foreground=INK)
        style.configure("Hint.Card.TLabel", foreground=MUTED, font=("Segoe UI", 9))

        body = tk.Frame(self.root, bg=BG, padx=20, pady=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        header = tk.Frame(body, bg=BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        tk.Label(header, text="Sheet to Tab", font=("Segoe UI", 26, "bold"), bg=BG, fg=INK).pack(anchor="w")
        tk.Label(header, text="Turn sheet music into readable guitar and bass tablature.",
                 font=("Segoe UI", 10), bg=BG, fg=MUTED).pack(anchor="w")
        self.demo = ttk.Button(header, text="Try example", command=self.load_example)
        self.demo.place(relx=1, y=7, anchor="ne")

        source_card = ttk.Frame(body, style="Card.TFrame", padding=(15, 10))
        source_card.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        source_card.columnconfigure(0, weight=3)
        source_card.columnconfigure(2, weight=2)
        self.label(source_card, "1   Open sheet music", 0, 0, heading=True, span=3)
        self.label(source_card, "Original instrument (required)", 1, 0)
        self.label(source_card, "Score part or staff", 1, 2)
        self.label(source_card, "Source shift (semitones)", 1, 3)
        self.source_field = ttk.Combobox(source_card, textvariable=self.source, values=SOURCE_INSTRUMENTS)
        self.source_field.grid(row=2, column=0, sticky="ew", padx=(0, 8))
        self.source_field.bind("<Return>", lambda _event: self.choose_file())
        self.open_button = ttk.Button(source_card, text="Choose file…", command=self.choose_file)
        self.open_button.grid(row=2, column=1, sticky="w", padx=(0, 18))
        self.part_field = ttk.Combobox(source_card, textvariable=self.selection, state="disabled", width=24)
        self.part_field.grid(row=2, column=2, sticky="ew", padx=(0, 12))
        ttk.Spinbox(source_card, textvariable=self.source_shift, from_=-24, to=24, width=8).grid(
            row=2, column=3, sticky="w"
        )
        self.label(source_card, self.file_summary, 3, 0, hint=True, span=2, wrap=415)
        self.label(source_card, "Source shift applies if the score omits transposition.", 3, 2,
                   hint=True, span=2, wrap=300)

        target_card = ttk.Frame(body, style="Card.TFrame", padding=(15, 10))
        target_card.grid(row=2, column=0, sticky="ew", pady=(0, 9))
        target_card.columnconfigure(1, weight=1)
        self.label(target_card, "2   Set up tablature", 0, 0, heading=True, span=4)
        for col, label in enumerate(("Play on", "Open strings · top to bottom", "Last fret", "Octave shift")):
            self.label(target_card, label, 1, col)
        preset = ttk.Combobox(target_card, textvariable=self.target, values=tuple(PRESETS), state="readonly", width=17)
        preset.grid(row=2, column=0, sticky="ew", padx=(0, 12))
        preset.bind("<<ComboboxSelected>>", self.change_preset)
        tuning_field = ttk.Frame(target_card, style="Card.TFrame")
        tuning_field.grid(row=2, column=1, sticky="ew", padx=(0, 12))
        ttk.Entry(tuning_field, textvariable=self.tuning, width=20).pack(side="left", fill="x", expand=True)
        self.score_tuning_button = ttk.Button(tuning_field, text="Use score tuning", command=self.use_score_tuning)
        self.score_tuning_button.pack(side="left", padx=(6, 0))
        ttk.Spinbox(target_card, textvariable=self.max_fret, from_=1, to=36, width=6).grid(
            row=2, column=2, sticky="w", padx=(0, 12)
        )
        ttk.Spinbox(target_card, textvariable=self.octaves, from_=-2, to=2, width=6).grid(row=2, column=3, sticky="w")
        self.generate_button = ttk.Button(target_card, text="Generate tab", command=self.generate, style="Primary.TButton")
        self.generate_button.grid(row=2, column=4, padx=(12, 0), sticky="e")
        self.label(target_card, "Enter each pitch with its octave, for example E4 or Bb1.",
                   3, 1, hint=True, span=4)

        reader = ttk.Frame(body, style="Card.TFrame", padding=(15, 10))
        reader.grid(row=3, column=0, sticky="nsew")
        reader.columnconfigure(0, weight=1)
        reader.rowconfigure(2, weight=1)
        self.label(reader, self.tab_title, 0, 0, heading=True, wrap=420)
        nav = ttk.Frame(reader, style="Card.TFrame")
        nav.grid(row=0, column=1, sticky="e")
        self.prev = ttk.Button(nav, text="‹", width=3, command=lambda: self.move_page(-1))
        self.prev.pack(side="left")
        ttk.Label(nav, textvariable=self.page_text, style="Card.TLabel", padding=(9, 0)).pack(side="left")
        self.next = ttk.Button(nav, text="›", width=3, command=lambda: self.move_page(1))
        self.next.pack(side="left")
        ttk.Label(nav, text="Fret size", style="Card.TLabel", padding=(13, 0, 5, 0)).pack(side="left")
        size = ttk.Combobox(nav, textvariable=self.fret_size, values=tuple(SIZES), state="readonly", width=9)
        size.pack(side="left")
        size.bind("<<ComboboxSelected>>", lambda _event: self.redraw())

        self.label(reader, "0 = open string   ·   ( ) = let the note ring   ·   ! = review", 1, 0, hint=True, span=2)
        self.canvas = tk.Canvas(reader, background=PAPER, highlightthickness=0, width=1, height=1)
        self.canvas.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(5, 0))
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        horizontal = ttk.Scrollbar(reader, orient="horizontal", command=self.canvas.xview)
        vertical = ttk.Scrollbar(reader, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        horizontal.grid(row=3, column=0, columnspan=2, sticky="ew")
        vertical.grid(row=2, column=2, sticky="ns", pady=(5, 0))
        self.review = ttk.Button(reader, text="Review notes", command=self.review_issues)
        self.review.grid(row=4, column=0, sticky="w", pady=(7, 0))
        self.review.grid_remove()

        self.status_label = tk.Label(body, textvariable=self.status, fg=MUTED, bg=BG,
                                     font=("Segoe UI", 10), justify="left", anchor="w", wraplength=720)
        self.status_label.grid(row=4, column=0, sticky="ew", pady=(8, 0))

    @staticmethod
    def label(parent, value, row, column, *, heading=False, hint=False, span=1, wrap=0):
        style = "Heading.Card.TLabel" if heading else "Hint.Card.TLabel" if hint else "Card.TLabel"
        text = {"textvariable": value} if isinstance(value, tk.StringVar) else {"text": value}
        ttk.Label(parent, **text, style=style, wraplength=wrap, justify="left").grid(
            row=row, column=column, columnspan=span, sticky="w", pady=(1, 6 if heading else 2)
        )

    def show_status(self, message, *, error=False):
        self.status.set(message)
        self.status_label.configure(fg=WARNING if error else MUTED)

    def current_part(self):
        index = self.part_field.current()
        return self.parts[index][1:] if 0 <= index < len(self.parts) else (None, None)

    def sync_controls(self, *_args):
        part, staff = self.current_part()
        self.source_field.configure(state="disabled" if self.pending else "normal")
        self.open_button.configure(state="normal" if self.source.get().strip() and not self.pending else "disabled")
        self.demo.configure(state="disabled" if self.pending else "normal")
        self.generate_button.configure(state="normal" if part is not None and not self.pending else "disabled")
        has_tuning = part is not None and len(part.first_tuning(staff)) in {4, 5, 6, 7}
        self.score_tuning_button.configure(state="normal" if has_tuning and not self.pending else "disabled")
        self.prev.configure(state="normal" if self.page else "disabled")
        self.next.configure(state="normal" if (self.page + 1) * MEASURES_PER_PAGE < len(self.tab) else "disabled")

    def choose_file(self):
        source = self.source.get().strip()
        if not source or self.pending:
            return
        filename = filedialog.askopenfilename(
            parent=self.root,
            title=f"Open sheet music for {source}",
            filetypes=[
                ("Sheet music", "*.musicxml *.xml *.mxl *.pdf *.png *.jpg *.jpeg *.tif *.tiff"),
                ("MusicXML", "*.musicxml *.xml *.mxl"),
                ("PDF and images", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff"),
            ],
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() in SCAN_EXTENSIONS and self.audiveris is None:
            if not messagebox.askyesno(
                "PDF and image import",
                "This file needs Audiveris for music recognition. Locate an installed Audiveris executable?",
                parent=self.root,
            ):
                self.show_status("Install Audiveris to open scans, or choose a MusicXML score.")
                return
            engine = filedialog.askopenfilename(
                parent=self.root, title="Locate Audiveris", filetypes=[("Audiveris executable", "*.exe")]
            )
            if not engine:
                return
            self.audiveris = Path(engine)
        self.start_import(path, source)

    def load_example(self):
        self.start_import(EXAMPLE, "Piano", example=True)

    def start_import(self, path, source, *, example=False):
        if self.pending:
            return
        self.pending = True
        self.cancel.clear()
        self.sync_controls()
        reading_scan = path.suffix.lower() in SCAN_EXTENSIONS
        self.show_status("Recognizing the printed music…" if reading_scan else "Reading the score…")
        audiveris = self.audiveris

        def read():
            try:
                result = import_score(path, source, audiveris, self.cancel)
            except (ScoreError, OSError) as error:
                result = error
            except Exception:
                logging.exception("Score import failed")
                result = ScoreError("This score could not be imported. Try exporting it again as MusicXML.")
            self.results.put(result)

        Thread(target=read, daemon=True).start()
        self.root.after(100, self.finish_import, example)

    def finish_import(self, example):
        try:
            result = self.results.get_nowait()
        except Empty:
            self.root.after(100, self.finish_import, example)
            return
        self.pending = False
        if self.closing:
            self.root.destroy()
            return
        if isinstance(result, Exception):
            self.show_status(str(result), error=True)
            self.sync_controls()
            return

        self.imported = result
        self.parts = []
        for index, part in enumerate(result.score.parts, start=1):
            name = f"{index}. {part.name}"
            if len(part.staves) == 1:
                self.parts.append((name, part, part.staves[0]))
                continue
            if not part.tab_staves:
                self.parts.append((name + " · all staves", part, None))
            for staff in part.staves:
                kind = f"staff {staff} (TAB)" if staff in part.tab_staves else f"staff {staff}"
                self.parts.append((f"{name} · {kind}", part, staff))
        self.part_field.configure(values=[label for label, _, _ in self.parts], state="readonly")
        self.selection.set("")
        if len(self.parts) == 1:
            self.part_field.current(0)
        self.source_shift.set(str(result.fallback_shift))
        self.source.set("")
        self.file_summary.set(f"{result.filename}  ·  Original instrument {result.source_instrument}")
        if result.scanned:
            self.show_status("Scan ready. Compare the recognized notes with the original score.")
        elif len(self.parts) == 1:
            self.show_status("Score ready. Check the target tuning, then generate tab.")
        else:
            self.show_status("Score ready. Select a part or staff to convert.")
        self.sync_controls()
        (self.generate_button if len(self.parts) == 1 else self.part_field).focus_set()
        if example:
            self.target.set(next(iter(PRESETS)))
            self.change_preset()
            self.max_fret.set("24")
            self.octaves.set("0")
            self.generate()

    def change_preset(self, _event=None):
        self.tuning.set(PRESETS[self.target.get()])

    def use_score_tuning(self):
        part, staff = self.current_part()
        if part is None:
            return
        tuning = part.first_tuning(staff)
        for preset, notes in PRESETS.items():
            if len(tuning) == len(notes.split()):
                self.target.set(preset)
                self.tuning.set(" ".join(map(pitch_name, tuning)))
                return

    def settings_changed(self, *_args):
        if self.tab:
            self.tab = ()
            self.page = 0
            self.tab_title.set("Tablature")
            self.review.grid_remove()
            self.show_status("Settings changed. Generate tab to see the result.")
            self.redraw()
        self.sync_controls()

    def generate(self):
        part, staff = self.current_part()
        if part is None or self.pending:
            return
        try:
            string_count = len(PRESETS[self.target.get()].split())
            tuning = parse_tuning(self.tuning.get(), string_count)
            max_fret = int(self.max_fret.get())
            fallback = int(self.source_shift.get())
            octaves = int(self.octaves.get())
            if not -24 <= fallback <= 24 or not -2 <= octaves <= 2:
                raise ScoreError("Use a source shift from −24 to 24 and an octave shift from −2 to 2.")
            self.tab = make_tab(part, staff, tuning, max_fret, fallback, octaves)
        except (ScoreError, ValueError) as error:
            self.show_status(
                str(error) if isinstance(error, ScoreError) else "Enter whole numbers for fret and pitch shift settings.",
                error=True,
            )
            return
        self.tab_tuning = tuning
        self.page = 0
        self.tab_title.set(self.imported.score.title)
        self.issues = [
            f"Measure {measure.label} · quarter-note offset {column.onset} · {issue}"
            for measure in self.tab for column in measure.columns for issue in column.issues
        ]
        if self.issues:
            self.review.configure(text=f"Review notes ({len(self.issues)})")
            self.review.grid()
        else:
            self.review.grid_remove()
        detail = " Compare recognized notes with the original." if self.imported.scanned else ""
        self.show_status(f"{len(self.tab)} measures  ·  {self.target.get()}  ·  {part.name}.{detail}")
        self.move_page(0)

    def move_page(self, delta):
        self.page += delta
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.redraw()
        self.sync_controls()

    def redraw(self):
        first = self.page * MEASURES_PER_PAGE
        measures = self.tab[first:first + MEASURES_PER_PAGE]
        page_label = "—"
        if measures:
            page_label = str(first + 1)
            if len(measures) > 1:
                page_label += f"–{first + len(measures)}"
            page_label += f" of {len(self.tab)}"
        self.page_text.set(page_label)
        draw_tab(self.canvas, measures, self.tab_tuning, SIZES[self.fret_size.get()])

    def review_issues(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Notes to review")
        dialog.transient(self.root)
        text = ScrolledText(dialog, width=70, height=13, wrap="word", padx=12, pady=8)
        text.pack(fill="both", expand=True)
        text.insert("1.0", "\n".join(self.issues))
        text.configure(state="disabled")
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=8)

    def close(self):
        if self.pending:
            self.closing = True
            self.cancel.set()
            self.show_status("Closing the score reader…")
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    SheetToTab(root)
    root.mainloop()


if __name__ == "__main__":
    main()
