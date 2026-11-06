"""Excel binary workbook .xlsb (pyxlsb).


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


from pathlib import Path


from ..core.models import Options, Result
from ._spreadsheet_common import _cell, _sheet_page




def extract_xlsb(path: Path, workdir: Path, opts: Options) -> Result:
    res = Result(detected="xlsb")
    from pyxlsb import open_workbook
    with open_workbook(str(path)) as wb:
        for i, name in enumerate(wb.sheets, 1):
            with wb.get_sheet(name) as ws:
                rows = ([_cell(c.v) for c in row] for row in ws.rows())
                res.pages.append(_sheet_page(i, name, rows, res, opts))
    res.flags.append("xlsb_dates_shown_as_serial_numbers")
    return res
