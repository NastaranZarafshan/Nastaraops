"""Safe, idempotent import of reviewer-1 spreadsheet verdicts.

Never use the AI suggestion columns as human votes. Import is deliberately
non-blind: a spreadsheet may have exposed suggestions before upload.
"""
from __future__ import annotations

import csv
import hashlib
import io
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any

from app.human_review import LABELS

IMPORT_VERSION = '8.2.0'
MAX_UPLOAD_BYTES = 3 * 1024 * 1024
MAX_UNCOMPRESSED = 20 * 1024 * 1024
MAX_ROWS = 125
REQ = ('case_id', 'reviewer_1_label', 'reviewer_1_reason', 'reviewer_1_confidence_1to5')
XMLNS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
OFFNS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKGNS = 'http://schemas.openxmlformats.org/package/2006/relationships'


class ImportFormatError(ValueError):
    pass


def _text(v: Any) -> str:
    return str(v or '').strip()


def _column_letters_to_index(ref: str) -> int:
    m = re.match(r'^([A-Z]{1,3})', ref)
    if not m:
        raise ImportFormatError('Invalid spreadsheet cell reference')
    index = 0
    for c in m.group(1):
        index = index * 26 + ord(c) - 64
    return index - 1


def _xlsx_records(data: bytes) -> list[dict]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ImportFormatError('Invalid XLSX file') from exc
    with z:
        infos = z.infolist()
        if len(infos) > 500 or sum(x.file_size for x in infos) > MAX_UNCOMPRESSED:
            raise ImportFormatError('Spreadsheet too large or overly complex')
        for info in infos:
            path = info.filename
            if path.startswith('/') or '..' in path.split('/') or '\\' in path:
                raise ImportFormatError('Unsafe path inside XLSX')
        def read(path: str) -> bytes:
            if path not in z.namelist():
                raise ImportFormatError('Spreadsheet missing ' + path)
            return z.read(path)
        try:
            workbook = ET.fromstring(read('xl/workbook.xml'))
            rels = ET.fromstring(read('xl/_rels/workbook.xml.rels'))
        except ET.ParseError as exc:
            raise ImportFormatError('Invalid Excel metadata') from exc
        relations = {r.get('Id'): r.get('Target') for r in rels.findall(f'{{{PKGNS}}}Relationship')}
        sheets = workbook.findall(f'.//{{{XMLNS}}}sheet')
        selected = next((s for s in sheets if s.get('name') == 'Review 100'), None)
        if selected is None:
            raise ImportFormatError('Excel must contain a sheet named Review 100')
        target = relations.get(selected.get(f'{{{OFFNS}}}id'))
        if not target:
            raise ImportFormatError('Missing Review 100 worksheet relationship')
        sheet_path = (target.lstrip('/') if target.startswith('/')
                      else posixpath.normpath(posixpath.join('xl', target)))
        if not sheet_path.startswith('xl/worksheets/'):
            raise ImportFormatError('Unexpected worksheet path')
        try:
            root = ET.fromstring(read(sheet_path))
        except ET.ParseError as exc:
            raise ImportFormatError('Invalid Review 100 XML') from exc
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            sx = ET.fromstring(read('xl/sharedStrings.xml'))
            for si in sx.findall(f'{{{XMLNS}}}si'):
                shared.append(''.join(t.text or '' for t in si.iter(f'{{{XMLNS}}}t')))
            if len(shared) > 20_000:
                raise ImportFormatError('Too many Excel shared strings')
        grid = []
        for row in root.findall(f'.//{{{XMLNS}}}sheetData/{{{XMLNS}}}row'):
            cells = {}
            for cell in row.findall(f'{{{XMLNS}}}c'):
                index = _column_letters_to_index(cell.get('r') or '')
                if index > 100:
                    continue
                formula = cell.find(f'{{{XMLNS}}}f')
                if formula is not None and index <= 14:
                    # Never trust formula display caches as a human-entered review.
                    cells[index] = {'__formula__': True}
                    continue
                t = cell.get('t')
                if t == 'inlineStr':
                    inline = cell.find(f'{{{XMLNS}}}is')
                    value = ''.join(x.text or '' for x in inline.iter(f'{{{XMLNS}}}t')) if inline is not None else ''
                else:
                    val = cell.find(f'{{{XMLNS}}}v')
                    value = val.text if val is not None else ''
                    if t == 's' and value:
                        try:
                            value = shared[int(value)]
                        except (IndexError, ValueError) as exc:
                            raise ImportFormatError('Invalid Excel shared string reference') from exc
                cells[index] = value
            grid.append(cells)
            if len(grid) > MAX_ROWS + 1:
                raise ImportFormatError('Maximum 125 case rows per import')
        if not grid:
            raise ImportFormatError('Review 100 sheet is empty')
        header = {i: _text(value) for i, value in grid[0].items()}
        if len(set(header.values())) != len(header):
            raise ImportFormatError('Duplicate Excel column headers')
        records = []
        for row in grid[1:]:
            row_dict = {name: row.get(col, '') for col, name in header.items() if name}
            if not any(_text(v) for v in row_dict.values()):
                continue
            records.append(row_dict)
        return records


def _csv_records(data: bytes) -> list[dict]:
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ImportFormatError('CSV must be UTF-8 encoded') from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ImportFormatError('CSV headers missing or duplicated')
    records = []
    for record in reader:
        records.append(record)
        if len(records) > MAX_ROWS:
            raise ImportFormatError('Maximum 125 case rows per import')
    return records


def parse_upload(data: bytes, filename: str) -> tuple[list[dict], str]:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ImportFormatError('Only non-empty files up to 3 MiB are allowed')
    name = filename.lower().strip()
    if name.endswith('.xlsx'):
        rows = _xlsx_records(data)
    elif name.endswith('.csv'):
        rows = _csv_records(data)
    else:
        raise ImportFormatError('Only .xlsx or UTF-8 .csv files are supported')
    if not rows:
        raise ImportFormatError('No review rows were found')
    for field in REQ:
        if field not in rows[0]:
            raise ImportFormatError('Missing required column: ' + field)
    return rows, hashlib.sha256(data).hexdigest()


def _canonical_matches(record: dict, case: dict) -> bool:
    mapping = {'question': 'question', 'candidate_answer': 'candidate'}
    for excel_field, case_field in mapping.items():
        val = record.get(excel_field)
        if val is not None and _text(val) and _text(val) != _text(case[case_field]):
            return False
    evidence = record.get('retrieved_source_text')
    if evidence is not None and _text(evidence):
        canonical_evidence = '\n'.join(s['text'] for s in case['sources'])
        if _text(evidence) != canonical_evidence:
            return False
    val = record.get('retrieved_source_id')
    if val is not None and _text(val) and _text(val) != '; '.join(case['retrieved_source_ids']) and _text(val) != ', '.join(case['retrieved_source_ids']):
        return False
    return True


def prepare_import(records: list[dict], cases: list[dict], existing: dict[str, dict],
                   reviewer_id: str) -> dict:
    """Classify every source row; never read AI columns, gold or predicted labels.

    existing: latest reviewer1 slots, usually from current_decisions().
    """
    valid = {c['case_id']: c for c in cases}
    duplicate = set()
    counts = Counter()
    rows = []
    inserts = []
    for position, record in enumerate(records, start=2):
        case_id = _text(record.get('case_id'))
        if not case_id:
            if any(_text(record.get(k)) for k in REQ[1:]):
                status, message = 'error', 'Missing case_id'
            else:
                counts['empty'] += 1
                continue
        elif case_id in duplicate:
            status, message = 'error', 'Duplicate case_id inside workbook'
        elif case_id not in valid:
            status, message = 'error', 'Unknown case_id'
        elif not _canonical_matches(record, valid[case_id]):
            status, message = 'error', 'Question, candidate or source IDs differ from the frozen dataset'
        elif case_id in existing:
            slot = existing[case_id]
            status = 'existing' if slot['reviewer_id'].casefold() == reviewer_id.casefold() else 'locked'
            message = ('Already recorded; will NOT overwrite' if status == 'existing'
                       else 'Reviewer 1 is assigned to a different ID')
        else:
            label = record.get('reviewer_1_label')
            reason = record.get('reviewer_1_reason')
            confidence = record.get('reviewer_1_confidence_1to5')
            if any(isinstance(v, dict) for v in (label,reason,confidence)):
                status, message = 'error', 'Formula cells are not allowed in reviewer columns'
            elif not _text(label) and not _text(reason) and not _text(confidence):
                status, message = 'empty', 'Human reviewer columns are blank (AI suggestion ignored)'
            elif _text(label).lower() not in LABELS:
                status, message = 'error', 'Invalid reviewer_1_label'
            elif len(_text(reason)) < 12 or len(_text(reason)) > 2000:
                status, message = 'error', 'Human reason must be 12–2000 characters'
            elif _text(confidence) not in {'1','2','3','4','5'}:
                # XLSX may use integers; string conversion handles numeric 1..5.
                status, message = 'error', 'Confidence must be an integer from 1 to 5'
            else:
                status, message = 'ready', 'New human-entered vote; spreadsheet import is NOT blind'
                inserts.append({'case_id':case_id,'label':_text(label).lower(),
                                'reason':_text(reason),'confidence':int(_text(confidence))})
        if case_id:
            duplicate.add(case_id)
        counts[status] += 1
        rows.append({'row':position,'case_id':case_id,'status':status,'message':message})
    return {
        'rows':rows, 'ready':inserts, 'counts':{key:counts.get(key,0) for key in
                ('ready','existing','locked','empty','error')},
        'warning':'Only reviewer_1_* columns are read. AI suggestions (columns P–U) are NEVER imported as human votes. Spreadsheet-sourced votes are NOT independently blinded. Existing votes are never overwritten.',
    }
