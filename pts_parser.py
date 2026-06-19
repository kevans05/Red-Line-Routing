"""Parser for Protection Test Sheet (.docx / .doc) files.

Reads the Word XML table structure to extract engineering standard IDs
(pattern: ES NN-XNNNN+, e.g. "ES 62-K0100") from the TESTS REQUIRED column,
along with section and system context for display.

Typical PTS table structure
---------------------------
  Header row : SYSTEM | NEW/DEC/MOD | TESTS REQUIRED | TESTED BY | DATE | COMMENTS/DEF
  Section row: "A) Typical Project"  (spans all columns)
  Subsection : "Panel F3"  (spans several columns, or 6 cols with few filled)
  Data rows (2-row pairs per item):
    Row 1 (vMerge restart): system_name | NEW | "Tested as per CN ..." | _ | _ | _
    Row 2 (vMerge cont.)  : (merged)    |     | "Tested as per ES 62-K0100" | _ | _ | _
  OR a single combined row:
    CN & AC Cables | NEW | Tested as per ES 62-K0100 ES 62-K0101 | _ | _ | _

Return value from parse_pts()
------------------------------
{
    'entries': [
        {
            'section':      str,    # e.g. "A) Typical Project"
            'subsection':   str,    # e.g. "Panel F3"
            'system':       str,    # e.g. "CN & AC Cables"
            'new_dec_mod':  str,    # "NEW" / "DEC" / "MOD" / ""
            'tests':        str,    # full TESTS REQUIRED cell text
            'standard_ids': list,   # ["ES 62-K0100", "ES 62-K0101", ...]
        },
        ...
    ],
    'all_standard_ids': list,   # unique IDs, ordered by first appearance
    'error':            str|None,
}
"""
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET

# Word 2007+ XML namespace
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# Engineering standard ID:  ES <series>-<section-letter><number>
# e.g. "ES 62-K0100", "ES 00-A0001"
_ES_ID = re.compile(r'\bES\s+\d+[A-Z]*-[A-Z]\d{4,}\b')

# Section-header rows start with a capital letter and closing paren: "A) ..."
_SECTION_RE = re.compile(r'^[A-Z]\)\s', re.IGNORECASE)

# The column-label header row always has "SYSTEM" as the first cell.
# A broader word-list check causes false positives when data rows contain
# text like "Tested as per CN & SCADA System Tests".
_HEADER_FIRST_CELL = 'system'


# ── XML helpers ───────────────────────────────────────────────────────


def _cell_text(tc_elem):
    """All text in a w:tc element, whitespace-normalised to a single line."""
    parts = []
    for t in tc_elem.iter(f'{{{_W}}}t'):
        if t.text:
            parts.append(t.text)
    return ' '.join(' '.join(parts).split())


def _is_vmerge_cont(tc_elem):
    """True if this cell is a vertical-merge continuation (not the restart row)."""
    vm = tc_elem.find(f'.//{{{_W}}}vMerge')
    if vm is None:
        return False
    # Restart rows carry val="restart"; continuation rows omit val or set it to ""
    return vm.get(f'{{{_W}}}val', '') != 'restart'


def _grid_span(tc_elem):
    """Number of grid columns this cell occupies (default 1)."""
    gs = tc_elem.find(f'.//{{{_W}}}gridSpan')
    try:
        return int(gs.get(f'{{{_W}}}val', 1)) if gs is not None else 1
    except (ValueError, TypeError):
        return 1


def _row_info(tr_elem):
    """Return (texts, spans, vmerge_conts) lists for every cell in a row."""
    texts, spans, conts = [], [], []
    for tc in tr_elem.findall(f'{{{_W}}}tc'):
        texts.append(_cell_text(tc))
        spans.append(_grid_span(tc))
        conts.append(_is_vmerge_cont(tc))
    return texts, spans, conts


def _is_header_row(texts):
    """Return True if this row is the column-name header.

    Checks only the first cell to avoid false positives from data rows that
    contain common words (e.g. "SCADA System Tests" would trigger a word-list
    match on "system").
    """
    return bool(texts) and texts[0].strip().lower() == _HEADER_FIRST_CELL


# ── .doc conversion ──────────────────────────────────────────────────


def _doc_to_docx(path):
    """Convert a legacy .doc to .docx using LibreOffice headless.

    Returns (docx_path, tmpdir) on success.  Both are None if no converter
    is available.  Caller must delete tmpdir with shutil.rmtree().
    """
    abs_path = os.path.abspath(path)
    for cmd in ('libreoffice', 'soffice'):
        tmpdir = tempfile.mkdtemp()
        try:
            r = subprocess.run(
                [cmd, '--headless', '--convert-to', 'docx',
                 '--outdir', tmpdir, abs_path],
                timeout=60, capture_output=True)
            if r.returncode == 0:
                stem = os.path.splitext(os.path.basename(abs_path))[0]
                out = os.path.join(tmpdir, stem + '.docx')
                if os.path.isfile(out):
                    return out, tmpdir
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)
    return None, None


# ── Public API ────────────────────────────────────────────────────────


def parse_pts(path):
    """Parse a Protection Test Sheet (.docx or .doc).

    Returns a dict; see module docstring for the full schema.
    'error' is None on success, or a human-readable string on failure.
    """
    tmpdir = None
    try:
        ext = os.path.splitext(path)[1].lower()
        docx_path = path

        if ext == '.doc':
            docx_path, tmpdir = _doc_to_docx(path)
            if docx_path is None:
                return {
                    'entries': [], 'all_standard_ids': [],
                    'error': (
                        'Could not convert .doc — LibreOffice is not installed or '
                        'not found on PATH.  Install LibreOffice and try again.'
                    ),
                }

        try:
            with zipfile.ZipFile(docx_path) as z:
                xml_bytes = z.read('word/document.xml')
        except (KeyError, zipfile.BadZipFile, OSError) as exc:
            return {'entries': [], 'all_standard_ids': [],
                    'error': f'Cannot read Word document: {exc}'}

        root = ET.fromstring(xml_bytes)
        body = root.find(f'{{{_W}}}body')
        if body is None:
            return {'entries': [], 'all_standard_ids': [], 'error': None}

        entries = []
        seen_ids: dict = {}        # id → insertion-order index (preserves appearance order)
        current_section    = ''
        current_subsection = ''

        for tbl in body.iter(f'{{{_W}}}tbl'):
            prev_system = ''
            prev_ndm    = ''

            for tr in tbl.findall(f'{{{_W}}}tr'):
                texts, spans, conts = _row_info(tr)
                if not texts:
                    continue

                # Skip the table's column-name header row
                if _is_header_row(texts):
                    continue

                non_empty   = [t for t in texts if t.strip()]
                total_span  = sum(spans)
                first       = texts[0].strip() if texts else ''

                # Wide merged row (few non-empty cells) whose first cell is NOT a
                # vMerge continuation → section or subsection label.
                # The `not conts[0]` guard excludes vMerge continuation rows that
                # happen to have only the TESTS column filled.
                if total_span >= 4 and len(non_empty) <= 2 and not conts[0]:
                    if first:
                        if _SECTION_RE.match(first):
                            current_section    = first
                            current_subsection = ''
                        else:
                            current_subsection = first
                    continue

                # Need at least 3 columns (SYSTEM, NDM, TESTS REQUIRED)
                if len(texts) < 3:
                    continue

                # Resolve vMerge: continuation rows inherit the previous row's values
                if conts[0]:
                    system = prev_system
                    ndm    = prev_ndm
                else:
                    system      = first
                    ndm         = texts[1].strip() if len(texts) > 1 else ''
                    prev_system = system
                    prev_ndm    = ndm

                tests = texts[2].strip() if len(texts) > 2 else ''
                if not tests:
                    continue

                ids = list(dict.fromkeys(_ES_ID.findall(tests)))
                if not ids:
                    continue

                for sid in ids:
                    if sid not in seen_ids:
                        seen_ids[sid] = len(seen_ids)

                entries.append({
                    'section':      current_section,
                    'subsection':   current_subsection,
                    'system':       system,
                    'new_dec_mod':  ndm,
                    'tests':        tests,
                    'standard_ids': ids,
                })

        all_ids = sorted(seen_ids, key=lambda x: seen_ids[x])
        return {'entries': entries, 'all_standard_ids': all_ids, 'error': None}

    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
