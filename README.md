# Sheet to Tab

Sheet to Tab is a local desktop prototype that turns a selected part of a score into large, readable guitar or bass tablature.

## Run on Windows

Run this command from the project folder.

```powershell
py -m sheet_to_tab
```

Use Python 3.10 or newer with Tkinter. **Try example** opens a four-measure original score and generates standard guitar tab. MusicXML imports work with the Python standard library.

1. Specify the **original instrument**. Choose a suggestion or type the name. Each new file requires this choice.
2. Choose a file and select the **score part or staff** when the score has multiple options.
3. Choose what you will **play on**. The app includes 6- and 7-string guitar and 4- and 5-string bass presets. Edit the open pitches from the top line to the bottom line, using pitches like `E4 B3 G3 D3 A2 E2`. **Use score tuning** applies an imported tuning when present.
4. Generate tab. Each line represents a string. A fret of **0** means open, parentheses indicate a tie continuation, and **!** marks a group with notes to review.

## Input formats

- **MusicXML** works with partwise `.musicxml`, `.xml`, and compressed `.mxl` scores. When an encoded string and fret can produce the intended pitch on the selected tuning, the converter prefers that placement. On a TAB staff with complete tuning, the source string and fret determine the sounding pitch.
- **PDF and images** require [Audiveris](https://github.com/Audiveris/audiveris/releases). Choose its Windows console installer. The app looks in the default installation directory and on `PATH`. You can also browse to `Audiveris.exe` when opening a scan or set `AUDIVERIS_PATH` before launching the app. Use clear printed standard notation in PDF, PNG, JPEG, or TIFF. Recognition runs on the computer. Review the results against the score, or correct the recognition in Audiveris and import its MusicXML export. [Audiveris recognizes standard notation and skips detected TAB staves.](https://audiveris.github.io/audiveris/_pages/guides/specific/tablature/)

The source shift is used when a score omits transposition information. MusicXML defaults to zero in that case. For scans, common transposing instruments get an editable starting value. For example, written B-flat clarinet parts default to −2 semitones. **Octave shift** moves the selected part by the chosen number of octaves. Notes beyond the target's range or available strings are flagged at the affected measure.

## Conversion scope

The viewer follows score order and keeps attack times, chord alignment, and ties. Placement favors matching source positions and frets near the previous hand position. Each sounding note needs a free string. The app presents suggested positions and marks conflicts in dense or sustained passages. The simplified display omits detailed rhythm symbols, expressive techniques, and expanded repeats.

## Code and checks

- `sheet_to_tab/musicxml.py` reads notes, timing, score parts, transposition and tab data.
- `sheet_to_tab/tablature.py` assigns pitches to the selected strings and frets.
- `sheet_to_tab/import_score.py` requires the source instrument and routes files to MusicXML or Audiveris.
- `sheet_to_tab/__main__.py` and `sheet_to_tab/tab_view.py` implement the desktop workflow and reader.

```powershell
py -m unittest discover -s tests -v
```
