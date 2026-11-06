"""PowerPoint 97-2003 .ppt: text records, with LibreOffice when available.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import struct
from pathlib import Path


from .. import ocr_config
from ..core.external_tools import convert_with_soffice
from ..core.models import EncryptedError, Options, Page, Result
from .pptx_extractor import extract_pptx




def _ppt_text_records(path: Path) -> list[str]:
    """Pure-Python fallback for PowerPoint 97-2003: read TextCharsAtom / TextBytesAtom records."""
    import olefile
    with olefile.OleFileIO(str(path)) as ole:
        if ole.exists("EncryptedSummary"):
            raise EncryptedError("encrypted presentation")
        data = ole.openstream("PowerPoint Document").read()
    texts = []


    def walk(off, end):
        while off + 8 <= end:
            ver_inst, rtype, rlen = struct.unpack_from("<HHI", data, off)
            body = off + 8
            if body + rlen > end:
                return
            if (ver_inst & 0x0F) == 0x0F:
                if rtype not in (0x03F8, 0x0FC9, 0x0FC8):            # skip slide masters, handout and title masters
                    walk(body, body + rlen)
            elif rtype == 0x0FA0:
                texts.append(data[body:body + rlen].decode("utf-16-le", "replace"))
            elif rtype == 0x0FA8:
                texts.append(data[body:body + rlen].decode("cp1252", "replace"))
            off = body + rlen
    walk(0, len(data))
    out = []
    for t in texts:
        t = t.replace("\r", "\n").replace("\x0b", "\n").strip()
        if t in ("*", "") or t.startswith(("Click to edit Master", "Click to edit the outline", "Second level\nThird level")):
            continue
        if t and (not out or out[-1] != t):
            out.append(t)
    return out




def extract_ppt(path: Path, workdir: Path, opts: Options) -> Result:
    if ocr_config.get_tools().soffice:
        conv = convert_with_soffice(path, "pptx", workdir, opts)
        if conv:
            r = extract_pptx(conv, workdir, opts)
            r.detected = "ppt"; r.flags.append("converted_with_libreoffice")
            return r
    res = Result(detected="ppt")
    try:
        texts = _ppt_text_records(path)
    except EncryptedError:
        res.status = "encrypted"; res.flags.append("password_required")
        return res
    res.pages.append(Page(1, "\n".join(texts), "ppt_records", label="All slides"))
    res.flags.append("slide_boundaries_not_available")
    return res





