"""Excel 97-2003 .xls (xlrd).


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


from pathlib import Path


from ..core.models import Options, Result
from ._spreadsheet_common import _cell, _sheet_page




def extract_xls(path: Path, workdir: Path, opts: Options) -> Result:
    res = Result(detected="xls")
    import xlrd
    try:
        wb = xlrd.open_workbook(str(path), on_demand=True)
    except xlrd.biffh.XLRDError as e:
        if "encrypt" in str(e).lower():
            res.status = "encrypted"; res.flags.append("password_required")
            return res
        raise
    for i, name in enumerate(wb.sheet_names(), 1):
        ws = wb.sheet_by_name(name)


        def rows():
            for r in range(ws.nrows):
                out = []
                for c in ws.row(r):
                    if c.ctype == xlrd.XL_CELL_DATE:
                        try:
                            out.append(_cell(xlrd.xldate.xldate_as_datetime(c.value, wb.datemode)))
                            continue
                        except Exception:
                            pass
                    out.append(_cell(c.value) if c.ctype not in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) else "")
                yield out
        res.pages.append(_sheet_page(i, name, rows(), res, opts, getattr(ws, "visibility", 0) != 0))
        wb.unload_sheet(name)
    return res
