"""Load digital scores or run the optional local Audiveris recognizer."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic

from .musicxml import Score, ScoreError, read_musicxml


XML_EXTENSIONS = {".xml", ".musicxml", ".mxl"}
SCAN_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
# These shifts apply to typical written parts when a scan has no transpose tag.
WRITTEN_OFFSETS = {
    "guitar": -12,
    "bass guitar": -12,
    "piccolo": 12,
    "b-flat clarinet": -2,
    "b-flat trumpet": -2,
    "alto saxophone": -9,
    "tenor saxophone": -14,
    "horn in f": -7,
}
SOURCE_INSTRUMENTS = (
    "Guitar", "Bass guitar", "Piano", "Violin", "Viola", "Cello", "Flute",
    "Piccolo", "B-flat clarinet", "B-flat trumpet", "Alto saxophone", "Tenor saxophone", "Horn in F",
)


@dataclass(frozen=True)
class ImportedScore:
    source_instrument: str
    filename: str
    score: Score
    scanned: bool
    fallback_shift: int


def find_audiveris() -> Path | None:
    configured = os.environ.get("AUDIVERIS_PATH")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return path.resolve()
    for directory in ("ProgramFiles", "ProgramW6432"):
        if directory in os.environ:
            path = Path(os.environ[directory]) / "Audiveris" / "Audiveris.exe"
            if path.is_file():
                return path
    command = shutil.which("Audiveris.exe" if os.name == "nt" else "audiveris")
    if command:
        return Path(command).resolve()
    return None


def import_score(
    path: Path, source_instrument: str, audiveris: Path | None = None, cancel: Event | None = None
) -> ImportedScore:
    instrument = source_instrument.strip()
    if not instrument:
        raise ScoreError("Specify the instrument the score was written for before opening a file.")
    extension = path.suffix.lower()
    if extension in XML_EXTENSIONS:
        return ImportedScore(instrument, path.name, read_musicxml(path), scanned=False, fallback_shift=0)
    if extension not in SCAN_EXTENSIONS:
        raise ScoreError("Choose MusicXML, PDF, PNG, JPEG, or TIFF sheet music.")
    if audiveris is None:
        raise ScoreError("PDF and image import requires the Audiveris executable.")
    score = _recognize(path, audiveris, cancel or Event())
    fallback = WRITTEN_OFFSETS.get(instrument.casefold(), 0)
    return ImportedScore(instrument, path.name, score, scanned=True, fallback_shift=fallback)


def _recognize(path: Path, audiveris: Path, cancel: Event) -> Score:
    with TemporaryDirectory(prefix="sheet-to-tab-") as directory:
        output = Path(directory)
        command = [
            str(audiveris.resolve()), "-batch", "-transcribe", "-export",
            "-output", str(output), "--", str(path.resolve()),
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            with subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags
            ) as process:
                deadline = monotonic() + 180
                while process.poll() is None:
                    if cancel.wait(0.2):
                        process.kill()
                        raise ScoreError("Recognition canceled.")
                    if monotonic() > deadline:
                        process.kill()
                        raise ScoreError("Recognition timed out. Try importing fewer pages at once.")
                if process.returncode:
                    raise ScoreError(
                        "Audiveris could not read this score. Try a clear scan or open it directly in Audiveris."
                    )
        except OSError as error:
            raise ScoreError(f"Could not run Audiveris. {error}") from error
        scores = [file for file in output.rglob("*") if file.suffix.lower() in XML_EXTENSIONS and file.is_file()]
        if not scores:
            raise ScoreError("Audiveris found no score. Use a clear image of standard notation.")
        if len(scores) > 1:
            raise ScoreError("Audiveris found separate scores. Export and import the desired score as MusicXML.")
        score = read_musicxml(scores[0])
        if not any(measure.notes for part in score.parts for measure in part.measures):
            raise ScoreError("Audiveris found no notes. Use a clear image of standard notation.")
        return score
