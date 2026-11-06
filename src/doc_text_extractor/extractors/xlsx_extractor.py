"""Excel XLSX / XLSM / XLTX (openpyxl).


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


from ..core.models import Options, Page, Result
from ._spreadsheet_common import _cell, _sheet_page




def extract_xlsx(path: Path, workdir: Path, opts: Options) -> Result:
    res = Result(detected="xlsx")
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        for i, ws in enumerate(wb.worksheets, 1):
            rows = ([_cell(v) for v in r] for r in ws.iter_rows(values_only=True))
            res.pages.append(_sheet_page(i, ws.title, rows, res, opts, getattr(ws, "sheet_state", "visible") != "visible"))
        wb.close()
    except Exception as e:
        res.flags.append(f"openpyxl_failed:{type(e).__name__}")
        res.pages = []
        with zipfile.ZipFile(path) as z:                             # raw fallback: every string in the workbook
            for i, n in enumerate(sorted(x for x in z.namelist() if x.startswith("xl/") and x.endswith(".xml") and
                                         ("sharedStrings" in x or "worksheets/sheet" in x)), 1):
                txt = " ".join(t for t in ET.fromstring(z.read(n)).itertext() if t.strip())
                res.pages.append(Page(i, txt, "xlsx_raw_xml", label=n))
    return res
