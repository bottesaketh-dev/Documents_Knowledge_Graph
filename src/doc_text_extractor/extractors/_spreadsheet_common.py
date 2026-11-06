"""Cell formatting and sheet-to-page helpers shared by the spreadsheet extractors.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations




from ..core.models import Page




def _cell(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        s = v.isoformat()
        return s[:10] if s.endswith("T00:00:00") else s
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).replace("\t", " ").replace("\n", " ")




def _sheet_page(n, name, rows, res, opts, hidden=False):
    lines, truncated = [], False
    for k, row in enumerate(rows):
        if k >= opts.max_sheet_rows:
            truncated = True
            break
        line = "\t".join(row).rstrip("\t")
        if line.strip():
            lines.append(line)
    if truncated:
        res.flags.append(f"sheet_truncated:{name}")
    if hidden:
        res.flags.append(f"hidden_sheet:{name}")
    return Page(n, "\n".join(lines), "spreadsheet", label=name)





