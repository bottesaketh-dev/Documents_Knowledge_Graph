"""Damaged Office/ZIP files: repair the archive, then extract what survives.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import re
import struct
import zipfile
from pathlib import Path
from typing import Optional


from ..core.detection import _zip_kind
from ..core.models import Options, Page, Result
from ..core.text_utils import _nonspace, safe_name




def _salvage_zip(path: Path, workdir: Path) -> Optional[Path]:
    """Rebuild a damaged or truncated zip (docx/xlsx/pptx/zip) from its local file headers.
    Members cut off by truncation are kept as far as they decompress."""
    import zlib
    data = path.read_bytes()
    out = workdir / ("repaired_" + safe_name(path.stem)[:40] + (path.suffix if path.suffix else ".zip"))
    seen = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for m in re.finditer(rb"PK\x03\x04", data):
            off = m.start()
            try:
                _v, flag, method, _t, _d, _crc, csize, _usize, nlen, xlen = struct.unpack("<HHHHHIIIHH", data[off + 4:off + 30])
            except struct.error:
                break
            name = data[off + 30:off + 30 + nlen].decode("utf-8" if flag & 0x800 else "cp437", "replace")
            if not name or name in seen or name.endswith("/") or flag & 0x1:
                continue
            start = off + 30 + nlen + xlen
            chunk = data[start:] if (flag & 0x08 or not csize) else data[start:start + csize]
            try:
                if method == 8:
                    content = zlib.decompressobj(-15).decompress(chunk)
                elif method == 0 and not flag & 0x08:
                    content = chunk
                else:
                    continue
            except zlib.error:
                continue
            seen.add(name)
            zo.writestr(name, content)
    return out if seen else None




def _xml_text_fallback(raw: bytes) -> str:
    """Text of a cut-off WordprocessingML/SpreadsheetML part that no XML parser will accept."""
    import html as _html
    paras = []
    for chunk in re.split(rb"</w:p>|</row>|</a:p>", raw):
        bits = re.findall(rb"<(?:w:t|t|a:t)(?:\s[^>]*)?>([^<]*)</(?:w:t|t|a:t)>", chunk)
        if bits:
            paras.append(_html.unescape(b"".join(bits).decode("utf-8", "replace")))
    return "\n".join(paras)




def extract_damaged_zip(path: Path, workdir: Path, opts: Options) -> Result:
    fixed = _salvage_zip(path, workdir)
    if fixed is None:
        return Result(status="error", detected="zip_unreadable", flags=["damaged_file_unrecoverable"])
    kind = _zip_kind(fixed)
    from ..core.dispatcher import _HANDLERS          # imported here: the dispatcher imports this module
    handler = _HANDLERS.get(kind)
    try:
        res = handler(fixed, workdir, opts) if handler else None
    except Exception:
        res = None
    if res is None or not any(_nonspace(p.text) for p in res.pages):
        # the main part itself was cut off: pull whatever text survives out of the XML
        with zipfile.ZipFile(fixed) as z:
            parts = [n for n in z.namelist() if re.match(r"(word/document|xl/sharedStrings|xl/worksheets/sheet\d+|ppt/slides/slide\d+)\.xml$", n)]
            text = "\n".join(_xml_text_fallback(z.read(n)) for n in sorted(parts))
        res = Result(detected=kind, pages=[Page(1, text, "xml_salvage")] if text.strip() else [])
        if not text.strip():
            res.status = "error"
    res.detected = kind if kind != "zip" else res.detected
    if res.status == "ok":
        res.status = "partial"
    res.flags.append("repaired_damaged_file")
    return res
