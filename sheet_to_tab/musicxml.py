"""Read pitches, timing, and tablature metadata from partwise MusicXML."""

from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


MAX_XML_BYTES = 16 * 1024 * 1024
SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


class ScoreError(ValueError):
    """A score could not be read or converted."""


@dataclass(frozen=True)
class Position:
    string: int  # Zero-based from the top of the tab.
    fret: int


@dataclass(frozen=True)
class Note:
    onset: Fraction
    duration: Fraction
    staff: int
    voice: str
    written_pitch: Fraction | None
    transpose: Fraction | None
    position: Position | None
    tuning: tuple[int, ...]
    ties: frozenset[str]
    issue: str = ""


@dataclass(frozen=True)
class Measure:
    label: str
    duration: Fraction
    notes: tuple[Note, ...]


@dataclass(frozen=True)
class Part:
    name: str
    staves: tuple[int, ...]
    tab_staves: frozenset[int]
    measures: tuple[Measure, ...]

    def first_tuning(self, staff: int | None) -> tuple[int, ...]:
        for measure in self.measures:
            for note in measure.notes:
                if note.tuning and (staff is None or note.staff == staff):
                    return note.tuning
        return ()


@dataclass(frozen=True)
class Score:
    title: str
    parts: tuple[Part, ...]


def _xml(data: bytes) -> ET.Element:
    # Standard MusicXML DOCTYPEs are fine. ElementTree does not fetch external DTDs.
    if b"<!ENTITY" in data.replace(b"\x00", b""):
        raise ScoreError("The score uses XML entities that this reader cannot import.")
    root = ET.fromstring(data)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return root


def _read_limited(stream) -> bytes:
    data = stream.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise ScoreError("MusicXML files and their uncompressed scores must be 16 MB or smaller.")
    return data


def read_musicxml(path: Path) -> Score:
    try:
        with path.open("rb") as stream:
            data = _read_limited(stream)
        if path.suffix.lower() == ".mxl":
            with ZipFile(BytesIO(data)) as archive:
                with archive.open("META-INF/container.xml") as stream:
                    container = _xml(_read_limited(stream))
                # The first rootfile is the primary score in a MusicXML container.
                rootfile = container.find("rootfiles/rootfile")
                if rootfile is None or not rootfile.get("full-path"):
                    raise ScoreError("The compressed score has no MusicXML root file.")
                with archive.open(rootfile.attrib["full-path"]) as stream:
                    data = _read_limited(stream)
        return _read_score(_xml(data), path.stem)
    except ScoreError:
        raise
    except (OSError, ET.ParseError, BadZipFile, KeyError, RuntimeError, ValueError, ZeroDivisionError) as error:
        raise ScoreError(f"Could not read {path.name}. {error}") from error


def _pitch(element: ET.Element, prefix: str = "") -> Fraction:
    step = element.findtext(prefix + "step", "").strip()
    octave = int(element.findtext(prefix + "octave", ""))
    if not 0 <= octave <= 9:
        raise ScoreError("The score contains an invalid MusicXML pitch octave.")
    alter = _decimal(element.findtext(prefix + "alter", "0"))
    return Fraction(12 * (octave + 1) + SEMITONES[step]) + alter


def _decimal(value: str) -> Fraction:
    value = value.strip()
    if len(value) > 24 or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", value):
        raise ScoreError("The score contains an invalid MusicXML decimal value.")
    return Fraction(value)


def _staff_number(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 64:
        raise ScoreError("Staff numbers and counts must be between 1 and 64.")
    return number


def _read_score(root: ET.Element, fallback_title: str) -> Score:
    if root.tag != "score-partwise":
        raise ScoreError("Choose a partwise MusicXML score (.musicxml, .xml, or .mxl).")
    names = {
        part.get("id"): part.findtext("part-name", "").strip()
        for part in root.findall("part-list/score-part")
    }
    concert = root.find("defaults/concert-score") is not None
    parts = tuple(
        _read_part(part, names.get(part.get("id")) or f"Part {index}", concert)
        for index, part in enumerate(root.findall("part"), start=1)
    )
    if not any(part.measures for part in parts):
        raise ScoreError("The score contains no measures.")
    title = (
        root.findtext("work/work-title", "").strip()
        or root.findtext("movement-title", "").strip()
        or fallback_title
    )
    return Score(title, parts)


def _tuning(details: ET.Element) -> tuple[int, ...]:
    lines = {
        int(item.attrib["line"]): _pitch(item, "tuning-")
        for item in details.findall("staff-tuning")
    }
    count = int(details.findtext("staff-lines", str(len(lines))))
    if not lines or len(lines) != count or min(lines) != 1 or max(lines) != count:
        return ()
    if any(pitch.denominator != 1 for pitch in lines.values()):
        return ()
    return tuple(int(lines[line]) for line in range(count, 0, -1))


def _position(element: ET.Element) -> Position | None:
    technical = element.find("notations/technical")
    if technical is None:
        return None
    string = technical.findtext("string")
    fret = technical.findtext("fret")
    if string is None or fret is None:
        return None
    string_index = int(string) - 1
    fret_number = int(fret)
    if string_index < 0 or fret_number < 0:
        return None
    return Position(string_index, fret_number)


def _read_part(element: ET.Element, name: str, concert: bool) -> Part:
    measures = []
    staves = {1}
    tab_staves = set()
    divisions = Fraction(1)
    measure_length = Fraction(4)
    transpositions: dict[int, Fraction] = {0: Fraction(0)} if concert else {}
    tunings: dict[int, tuple[int, ...]] = {}
    capos: dict[int, int] = {}

    for index, measure in enumerate(element.findall("measure"), start=1):
        label = measure.get("number") or str(index)
        cursor = extent = Fraction(0)
        previous_onset = None
        notes = []
        for child in measure:
            if child.tag == "attributes":
                declared_divisions = child.findtext("divisions")
                if declared_divisions is not None:
                    divisions = _decimal(declared_divisions)
                if divisions <= 0:
                    raise ScoreError(f"Measure {label} has invalid timing divisions.")
                staves.update(range(1, _staff_number(child.findtext("staves", "1")) + 1))
                for clef in child.findall("clef"):
                    if clef.findtext("sign") == "TAB":
                        tab_staves.add(_staff_number(clef.get("number", "1")))
                time = child.find("time")
                if time is not None and time.find("beats") is not None:
                    # A time signature may contain several beats/beat-type pairs.
                    beats = [sum(map(int, (node.text or "").split("+"))) for node in time.findall("beats")]
                    units = [int(node.text or "0") for node in time.findall("beat-type")]
                    if len(beats) != len(units) or min(beats + units) <= 0:
                        raise ScoreError(f"Measure {label} has an invalid time signature.")
                    measure_length = sum(Fraction(4 * count, unit) for count, unit in zip(beats, units))
                for transpose in child.findall("transpose"):
                    staff = _staff_number(transpose.attrib["number"]) if "number" in transpose.attrib else 0
                    if staff == 0:
                        transpositions.clear()
                    transpositions[staff] = (
                        _decimal(transpose.findtext("chromatic", "0"))
                        + 12 * int(transpose.findtext("octave-change", "0"))
                    )
                for details in child.findall("staff-details"):
                    staff = _staff_number(details.get("number", "1"))
                    previous_capo = capos.get(staff, 0)
                    capo = int(details.findtext("capo", str(previous_capo)))
                    if details.find("staff-tuning") is not None:
                        tunings[staff] = tuple(pitch + capo for pitch in _tuning(details))
                    elif capo != previous_capo:
                        tunings[staff] = tuple(pitch + capo - previous_capo for pitch in tunings.get(staff, ()))
                    capos[staff] = capo
                continue

            if child.tag in {"backup", "forward"}:
                duration = _decimal(child.findtext("duration", "0")) / divisions
                if duration <= 0:
                    raise ScoreError(f"Measure {label} has an invalid {child.tag} duration.")
                cursor += duration if child.tag == "forward" else -duration
                if cursor < 0:
                    raise ScoreError(f"Measure {label} moves its timing before the bar line.")
                extent = max(extent, cursor)
                previous_onset = None
                continue
            if child.tag != "note":
                continue

            staff = _staff_number(child.findtext("staff", "1"))
            staves.add(staff)
            duration = _decimal(child.findtext("duration", "0")) / divisions
            if duration < 0:
                raise ScoreError(f"Measure {label} has a negative note duration.")
            if child.find("chord") is None:
                onset = previous_onset = cursor
                cursor += duration
            else:
                if previous_onset is None:
                    raise ScoreError(f"Measure {label} has a chord without a first note.")
                onset = previous_onset
            extent = max(extent, onset + duration)
            if child.find("rest") is not None or child.find("cue") is not None:
                continue

            pitch = child.find("pitch")
            position = _position(child)
            tuning = tunings.get(staff, ())
            issue = ""
            if child.find("grace") is not None:
                issue = "Grace note"
            elif duration == 0:
                issue = "Note without a duration"
            elif pitch is None and not (position and position.string < len(tuning)):
                issue = "Note without a pitch"
            notes.append(
                Note(
                    onset,
                    duration,
                    staff,
                    child.findtext("voice", "1"),
                    _pitch(pitch) if pitch is not None else None,
                    transpositions.get(staff, transpositions.get(0)),
                    position,
                    tuning,
                    frozenset(tie.get("type", "") for tie in child.findall("tie")),
                    issue,
                )
            )
        measures.append(Measure(label, extent or measure_length, tuple(notes)))

    return Part(name, tuple(sorted(staves)), frozenset(tab_staves), tuple(measures))
