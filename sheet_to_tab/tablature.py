"""Map sounding pitches to string/fret positions and keep chord attacks aligned."""

from dataclasses import dataclass
from fractions import Fraction
from itertools import groupby
import re
from statistics import median_low

from .musicxml import Note, Part, Position, ScoreError, SEMITONES


PRESETS = {
    "6-string guitar": "E4 B3 G3 D3 A2 E2",
    "7-string guitar": "E4 B3 G3 D3 A2 E2 B1",
    "4-string bass": "G2 D2 A1 E1",
    "5-string bass": "G2 D2 A1 E1 B0",
}
PITCH_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


@dataclass(frozen=True)
class TabNote:
    position: Position
    tied: bool


@dataclass(frozen=True)
class Column:
    onset: Fraction
    notes: tuple[TabNote, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class TabMeasure:
    label: str
    duration: Fraction
    columns: tuple[Column, ...]


def pitch_name(pitch: int) -> str:
    octave, step = divmod(pitch, 12)
    return f"{PITCH_NAMES[step]}{octave - 1}"


def parse_tuning(text: str, string_count: int) -> tuple[int, ...]:
    tokens = text.replace(",", " ").split()
    if len(tokens) != string_count:
        raise ScoreError(f"Enter {string_count} open-string pitches from top to bottom, including octaves.")
    tuning = []
    for token in tokens:
        match = re.fullmatch(r"([A-Ga-g])([#b♯♭]?)(-1|[0-9])", token)
        if not match:
            raise ScoreError(f"Invalid tuning note {token!r}. Use a pitch such as E4, F#3 or Bb1.")
        letter, accidental, octave = match.groups()
        alter = {"": 0, "#": 1, "♯": 1, "b": -1, "♭": -1}[accidental]
        pitch = 12 * (int(octave) + 1) + SEMITONES[letter.upper()] + alter
        if not 0 <= pitch <= 127:
            raise ScoreError(f"Tuning note {token!r} falls outside the supported pitch range.")
        tuning.append(pitch)
    return tuple(tuning)


def _sounding_pitch(
    note: Note, tab_staff: bool, fallback_shift: int, octave_shift: int
) -> Fraction:
    position = note.position
    if (tab_staff or note.written_pitch is None) and position and position.string < len(note.tuning):
        pitch = Fraction(note.tuning[position.string] + position.fret)
    else:
        offset = note.transpose if note.transpose is not None else fallback_shift
        pitch = note.written_pitch + offset
    return pitch + 12 * octave_shift


def _choose_positions(
    options: list[tuple[Position, ...]], preferred: list[Position | None], blocked: int, hand: int
) -> tuple[Position | None, ...]:
    # One state per set of occupied strings. Rank by missing notes, changed
    # source positions, then distance from the last fretted hand position.
    states = {blocked: ((0, 0, 0), ())}
    for choices, original in zip(options, preferred):
        next_states = {}
        for used, (score, positions) in states.items():
            for choice in (*choices, None):
                if choice is None:
                    mask = used
                    cost = (score[0] + 1, score[1] + int(original is not None), score[2])
                else:
                    bit = 1 << choice.string
                    if used & bit:
                        continue
                    mask = used | bit
                    fret = choice.fret
                    movement = 0 if fret == 0 else 3 * abs(fret - hand) + fret
                    cost = (score[0], score[1] + int(original is not None and original != choice), score[2] + movement)
                if mask not in next_states or cost < next_states[mask][0]:
                    next_states[mask] = (cost, (*positions, choice))
        states = next_states
    return min(states.values(), key=lambda state: state[0])[1]


def _available_positions(pitch: Fraction, tuning: tuple[int, ...], max_fret: int) -> tuple[Position, ...]:
    if pitch.denominator != 1:
        return ()
    return tuple(
        Position(string, int(pitch) - open_pitch)
        for string, open_pitch in enumerate(tuning)
        if 0 <= pitch - open_pitch <= max_fret
    )


def make_tab(
    part: Part,
    staff: int | None,
    tuning: tuple[int, ...],
    max_fret: int,
    fallback_shift: int = 0,
    octave_shift: int = 0,
) -> tuple[TabMeasure, ...]:
    if staff is None and part.tab_staves and len(part.staves) > 1:
        raise ScoreError("Choose one staff to avoid playing the notation and its tablature together.")
    if staff is not None and staff not in part.staves:
        raise ScoreError("Choose a staff from the selected part.")
    if not 4 <= len(tuning) <= 7 or not 1 <= max_fret <= 36:
        raise ScoreError("Choose 4 to 7 strings and a fret limit between 1 and 36.")

    measures = []
    hand = 1
    elapsed = Fraction(0)
    sustaining: dict[int, Fraction] = {}
    ties: dict[tuple, tuple[Fraction, Position]] = {}
    for measure in part.measures:
        columns = []
        selected = (note for note in measure.notes if staff is None or note.staff == staff)
        ordered = sorted(selected, key=lambda note: note.onset)
        for onset, notes in groupby(ordered, key=lambda note: note.onset):
            now = elapsed + onset
            sustaining = {string: end for string, end in sustaining.items() if end > now}
            continuing = {key: pos for key, (end, pos) in ties.items() if end == now}
            ties = {key: value for key, value in ties.items() if value[0] > now}
            attacks = []
            placed = []
            issues = []
            for note in notes:
                if note.issue:
                    issues.append(note.issue)
                    continue
                pitch = _sounding_pitch(note, note.staff in part.tab_staves, fallback_shift, octave_shift)
                key = (note.staff, note.voice, pitch)
                end = now + note.duration
                position = continuing.pop(key, None) if "stop" in note.ties else None
                if position is None or position.string in sustaining:
                    attacks.append((note, pitch, key, end))
                    continue
                placed.append(TabNote(position, tied=True))
                sustaining[position.string] = end
                if "start" in note.ties:
                    ties[key] = (end, position)

            options = [_available_positions(pitch, tuning, max_fret) for _, pitch, _, _ in attacks]
            preferred = [
                note.position if note.position in choices else None
                for (note, _, _, _), choices in zip(attacks, options)
            ]
            blocked = sum(1 << string for string in sustaining)
            positions = _choose_positions(options, preferred, blocked, hand)
            fretted = []
            for (note, pitch, key, end), position, choices in zip(attacks, positions, options):
                if position is None:
                    if pitch.denominator != 1:
                        issues.append(f"Microtonal pitch {float(pitch):g} (MIDI)")
                    elif choices:
                        issues.append(f"{pitch_name(int(pitch))} needs another available string")
                    else:
                        issues.append(f"{pitch_name(int(pitch))} is outside this tuning and fret range")
                    continue
                placed.append(TabNote(position, tied=False))
                sustaining[position.string] = end
                if "start" in note.ties:
                    ties[key] = (end, position)
                if position.fret:
                    fretted.append(position.fret)
            if fretted:
                hand = median_low(fretted)
            columns.append(Column(onset, tuple(placed), tuple(issues)))
        measures.append(TabMeasure(measure.label, measure.duration, tuple(columns)))
        elapsed += measure.duration
    if not any(measure.columns for measure in measures):
        raise ScoreError("The selected part or staff contains no notes.")
    return tuple(measures)
