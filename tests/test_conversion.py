import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from zipfile import ZipFile

from sheet_to_tab.import_score import import_score
from sheet_to_tab.musicxml import Position, ScoreError
from sheet_to_tab.tablature import make_tab, parse_tuning


GUITAR = parse_tuning("E4 B3 G3 D3 A2 E2", 6)
EXAMPLE = Path(__file__).resolve().parents[1] / "sheet_to_tab" / "example.musicxml"


def score_xml(measures):
    return f"""<score-partwise version="4.0">
        <part-list><score-part id="P1"><part-name>Lead</part-name></score-part></part-list>
        <part id="P1">{measures}</part>
    </score-partwise>"""


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def read_part(self, measures):
        path = self.folder / "score.musicxml"
        path.write_text(score_xml(measures), encoding="utf-8")
        return import_score(path, "Piano").score.parts[0]

    def test_source_instrument_required_before_read(self):
        with self.assertRaisesRegex(ScoreError, "Specify the instrument"):
            import_score(self.folder / "missing.musicxml", "  ")

    def test_example_chord_open_string_and_tie(self):
        source = import_score(EXAMPLE, "  Piano  ")
        self.assertEqual(source.source_instrument, "Piano")
        part = source.score.parts[0]
        result = make_tab(part, 1, GUITAR, 24)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0].columns[0].notes[0].position, Position(5, 0))
        self.assertEqual(result[0].columns[2].onset, Fraction(3, 2))
        chord = result[1].columns[0].notes
        self.assertEqual(len({note.position.string for note in chord}), 4)
        self.assertEqual(result[1].columns[1].onset, 2)  # Rest advanced the cursor.
        tie_start = result[2].columns[-1].notes[0]
        tie_end = result[3].columns[0].notes[0]
        self.assertTrue(tie_end.tied)
        self.assertEqual(tie_start.position, tie_end.position)

    def test_compressed_musicxml_uses_the_container_root(self):
        archive = self.folder / "score.mxl"
        with ZipFile(archive, "w") as zip_file:
            zip_file.writestr("decoy.xml", "<wrong/>")
            zip_file.writestr(
                "META-INF/container.xml",
                '<container xmlns="urn:example"><rootfiles>'
                '<rootfile full-path="music/main.musicxml"/></rootfiles></container>',
            )
            zip_file.write(EXAMPLE, "music/main.musicxml")
        self.assertEqual(import_score(archive, "Piano").score, import_score(EXAMPLE, "Piano").score)

    def test_explicit_transposition_overrides_source_fallback(self):
        part = self.read_part("""
            <measure number="1">
                <attributes><transpose><chromatic>-2</chromatic></transpose></attributes>
                <note><pitch><step>F</step><alter>1</alter><octave>2</octave></pitch><duration>1</duration></note>
            </measure>""")
        tab = make_tab(part, 1, GUITAR, 24, fallback_shift=-12)
        self.assertEqual(tab[0].columns[0].notes[0].position, Position(5, 0))

    def test_octave_shift_is_required_to_reach_low_notes(self):
        part = self.read_part("""
            <measure number="1">
                <note><pitch><step>E</step><octave>1</octave></pitch><duration>1</duration></note>
            </measure>""")
        self.assertIn("outside", make_tab(part, 1, GUITAR, 24)[0].columns[0].issues[0])
        tab = make_tab(part, 1, GUITAR, 24, octave_shift=1)
        self.assertEqual(tab[0].columns[0].notes[0].position, Position(5, 0))

    def test_voice_overlap_keeps_the_sustained_string_reserved(self):
        part = self.read_part("""
            <measure number="1">
                <note><pitch><step>A</step><octave>2</octave></pitch><duration>4</duration><voice>1</voice></note>
                <backup><duration>4</duration></backup>
                <forward><duration>1</duration></forward>
                <note><pitch><step>B</step><octave>2</octave></pitch><duration>1</duration><voice>2</voice></note>
            </measure>""")
        columns = make_tab(part, 1, GUITAR, 24)[0].columns
        self.assertEqual(columns[0].notes[0].position, Position(4, 0))
        self.assertEqual(columns[1].onset, 1)
        self.assertEqual(columns[1].notes[0].position, Position(5, 7))

    def test_chord_solver_finds_a_distinct_string_for_each_pitch(self):
        part = self.read_part("""
            <measure number="1">
                <note><pitch><step>A</step><octave>2</octave></pitch><duration>1</duration></note>
                <note><chord/><pitch><step>E</step><octave>2</octave></pitch><duration>1</duration></note>
            </measure>""")
        column = make_tab(part, 1, GUITAR, 24)[0].columns[0]
        self.assertEqual({note.position for note in column.notes}, {Position(4, 0), Position(5, 0)})
        self.assertFalse(column.issues)

    def test_lowered_seven_string_tab_preserves_the_encoded_placement(self):
        tuning = parse_tuning("D4 A3 F3 C3 G2 D2 A1", 7)
        line_data = "".join(
            f'<staff-tuning line="{line}"><tuning-step>{name[0]}</tuning-step>'
            f'<tuning-octave>{name[1]}</tuning-octave></staff-tuning>'
            for line, name in enumerate(("A1", "D2", "G2", "C3", "F3", "A3", "D4"), 1)
        )
        part = self.read_part(f"""
            <measure number="1">
                <attributes>
                    <staves>2</staves><clef number="2"><sign>TAB</sign></clef>
                    <staff-details number="2"><staff-lines>7</staff-lines>{line_data}</staff-details>
                </attributes>
                <note><pitch><step>G</step><octave>3</octave></pitch><duration>1</duration><staff>1</staff></note>
                <backup><duration>1</duration></backup>
                <note><pitch><step>G</step><octave>3</octave></pitch><duration>1</duration><staff>2</staff>
                    <notations><technical><string>4</string><fret>7</fret></technical></notations>
                </note>
            </measure>""")
        self.assertEqual(part.first_tuning(2), tuning)
        with self.assertRaisesRegex(ScoreError, "Choose one staff"):
            make_tab(part, None, tuning, 24)
        column = make_tab(part, 2, tuning, 24)[0].columns[0]
        self.assertEqual([note.position for note in column.notes], [Position(3, 7)])

    def test_bass_preset_and_custom_flat_tuning(self):
        tuning = parse_tuning("Gb2, Db2, Ab1, Eb1, Bb0", 5)
        part = self.read_part("""
            <measure number="1">
                <note><pitch><step>B</step><alter>-1</alter><octave>0</octave></pitch><duration>1</duration></note>
            </measure>""")
        column = make_tab(part, 1, tuning, 22)[0].columns[0]
        self.assertEqual(column.notes[0].position, Position(4, 0))

    def test_xml_entities_are_rejected(self):
        path = self.folder / "unsafe.xml"
        path.write_text('<!DOCTYPE score-partwise [<!ENTITY x "y">]><score-partwise/>', encoding="utf-16")
        with self.assertRaisesRegex(ScoreError, "XML entities"):
            import_score(path, "Piano")


if __name__ == "__main__":
    unittest.main()
