"""Draw readable, two-measure pages of tablature on a Tk canvas."""

import tkinter as tk

from .tablature import TabMeasure, pitch_name


PAPER = "#ffffff"
INK = "#172a2a"
MUTED = "#4d625e"
RULE = "#b1bfbb"
ACCENT = "#1b624f"
WARNING = "#9a4423"


def draw_tab(
    canvas: tk.Canvas, measures: tuple[TabMeasure, ...], tuning: tuple[int, ...], size: int
) -> None:
    canvas.delete("all")
    viewport = canvas.winfo_width()
    if not measures:
        canvas.configure(scrollregion=(0, 0, viewport, canvas.winfo_height()))
        canvas.create_text(
            max(24, viewport / 2), 65, text="Your tablature will appear here.",
            fill=MUTED, font=("Segoe UI", 12), width=max(1, viewport - 32),
        )
        return

    spacing = size + 11
    top = 48
    bottom = top + (len(tuning) - 1) * spacing
    left = 77
    bars = []
    for measure in measures:
        offsets = []
        gap = size * 2 + 6
        previous_x = 26 - gap
        for column in measure.columns:
            # Keep rhythm-based spacing with a minimum distance for legible double-digit frets.
            x = max(previous_x + gap, 30 + float(column.onset) * 41)
            offsets.append(x)
            previous_x = x
        width = max(185, previous_x + 36, float(measure.duration) * 41 + 30)
        bars.append((measure, offsets, width))

    natural_width = sum(width for _, _, width in bars)
    scale = max(1.0, (viewport - left - 20) / natural_width)
    right = left + natural_width * scale
    canvas.configure(scrollregion=(0, 0, right + 20, bottom + 40))
    for string, pitch in enumerate(tuning):
        y = top + string * spacing
        canvas.create_line(left, y, right, y, fill=RULE)
        canvas.create_text(left - 15, y, text=pitch_name(pitch), anchor="e",
                           font=("Segoe UI", 11, "bold"), fill=MUTED)

    bar_x = left
    for measure, offsets, width in bars:
        canvas.create_line(bar_x, top, bar_x, bottom, fill=MUTED, width=2)
        canvas.create_text(bar_x + 12, 18, text=f"MEASURE {measure.label}", anchor="w",
                           font=("Segoe UI", 10, "bold"), fill=ACCENT)
        if not measure.columns:
            canvas.create_text(bar_x + width * scale / 2, bottom + 23, text="Rest", fill=MUTED,
                               font=("Segoe UI", 10))
        for offset, column in zip(offsets, measure.columns):
            x = bar_x + offset * scale
            for note in column.notes:
                position = note.position
                y = top + position.string * spacing
                text = f"({position.fret})" if note.tied else str(position.fret)
                item = canvas.create_text(x, y, text=text, font=("Segoe UI", -size, "bold"),
                                          fill=MUTED if note.tied else INK)
                x1, y1, x2, y2 = canvas.bbox(item)
                background = canvas.create_rectangle(x1 - 2, y1, x2 + 2, y2, fill=PAPER, outline="")
                canvas.tag_lower(background, item)
            if column.issues:
                canvas.create_text(x, bottom + 24, text="!", fill=WARNING, font=("Segoe UI", 13, "bold"))
        bar_x += width * scale
    canvas.create_line(right, top, right, bottom, fill=MUTED, width=2)
