"""Word 97-2003 .doc: antiword, the built-in Word 97 reader, LibreOffice, and OCR of
embedded scans.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import io
import re
import struct
from pathlib import Path


from .. import ocr_config
from ..core.external_tools import _run, convert_with_soffice
from ..core.models import EncryptedError, Options, Page, Result
from ..core.ocr import is_page_scale, ocr_bytes
from ..core.text_utils import _PLACEHOLDER_RE, _has_meaningful_text
from .docx_extractor import extract_docx




def _antiword(path: Path) -> str:
    t = ocr_config.get_tools()
    if not t.antiword:
        return ""
    cmd = [str(t.antiword), "-w", "0"]
    utf8 = bool(t.antiword_home and (t.antiword_home / "UTF-8.txt").exists())
    if utf8:
        cmd += ["-m", "UTF-8.txt"]
    try:
        r = _run(cmd + [str(path)], timeout=120, env=t.antiword_env())
    except Exception:
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout.decode("utf-8" if utf8 else "cp1252", "replace")




def _word97_text(path: Path) -> str:
    """Pure-Python reader for Word 97-2003 text (the piece table). Works where antiword refuses short files."""
    import olefile
    with olefile.OleFileIO(str(path)) as ole:
        wd = ole.openstream("WordDocument").read()
        flags = struct.unpack_from("<H", wd, 0x0A)[0]
        if flags & 0x0100:
            raise EncryptedError("Word document is password protected")
        table = "1Table" if flags & 0x0200 else "0Table"
        if not ole.exists(table):
            return ""
        tbl = ole.openstream(table).read()
    pos = 0x20
    csw = struct.unpack_from("<H", wd, pos)[0]; pos += 2 + csw * 2
    cslw = struct.unpack_from("<H", wd, pos)[0]; pos += 2 + cslw * 4
    pos += 2                                                         # cbRgFcLcb
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, pos + 33 * 8)
    clx = tbl[fc_clx:fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 0x01:                           # skip Prc entries
        i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
    if i >= len(clx) or clx[i] != 0x02:
        return ""
    lcb = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + lcb]
    n = (lcb - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    parts = []
    for k in range(n):
        fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)[0]
        count = cps[k + 1] - cps[k]
        if fc & 0x40000000:
            start = (fc & 0x3FFFFFFF) // 2
            parts.append(wd[start:start + count].decode("cp1252", "replace"))
        else:
            parts.append(wd[fc:fc + 2 * count].decode("utf-16-le", "replace"))
    text = "".join(parts)
    # field codes: keep the displayed result, drop the instruction
    text = re.sub(r"\x13[^\x13\x14\x15]*\x14", "", text)
    text = re.sub(r"\x13[^\x13\x14\x15]*\x15", "", text)
    text = text.replace("\x15", "")
    table_map = {"\r": "\n", "\x0b": "\n", "\x07": "\t", "\x0c": "\f", "\x1e": "-", "\x1f": "", "\x01": "", "\x08": "", "\x02": "", "\x05": ""}
    return "".join(table_map.get(ch, ch) for ch in text)




def _doc_image_blobs(path: Path) -> list[bytes]:
    """Embedded JPEG/PNG images from the .doc's Data stream, in order, each verified with Pillow."""
    try:
        import olefile
        with olefile.OleFileIO(str(path)) as ole:
            data = b"".join(ole.openstream(n).read() for n in ("Data", "WordDocument") if ole.exists(n))
    except Exception:
        return []
    return _carve_images(data)




def _carve_images(data: bytes) -> list[bytes]:
    from PIL import Image
    found = []                                                       # (offset, bytes)


    def valid(b: bytes) -> bool:
        try:
            with Image.open(io.BytesIO(b)) as im:
                im.load()
            return True
        except Exception:
            return False


    i = 0
    while True:
        s = data.find(b"\xff\xd8\xff", i)
        if s == -1:
            break
        e, ok = s + 3, False
        for _ in range(8):                                           # FFD9 can occur inside the JPEG (EXIF thumbnail)
            e = data.find(b"\xff\xd9", e)
            if e == -1:
                break
            e += 2
            if valid(data[s:e]):
                ok = True
                break
        if ok:
            found.append((s, data[s:e])); i = e
        else:
            i = s + 3
    i = 0
    while True:
        s = data.find(b"\x89PNG\r\n\x1a\n", i)
        if s == -1:
            break
        e = data.find(b"IEND\xaeB`\x82", s)
        if e == -1:
            break
        e += 8
        if valid(data[s:e]):
            found.append((s, data[s:e]))
        i = e
    return [b for _, b in sorted(found)]




def extract_doc(path: Path, workdir: Path, opts: Options) -> Result:
    res = Result(detected="doc")
    try:
        raw = _antiword(path)
        method = "antiword"
        if not _has_meaningful_text(raw, opts.min_chars):
            raw = _word97_text(path)
            method = "word97_piece_table"
    except EncryptedError:
        res.status = "encrypted"; res.flags.append("password_required")
        return res
    except Exception as e:
        raw, method = "", "none"
        res.flags.append(f"doc_text_reader_failed:{type(e).__name__}")
    if not _has_meaningful_text(raw, opts.min_chars) and ocr_config.get_tools().soffice:
        conv = convert_with_soffice(path, "docx", workdir, opts)
        if conv:
            sub = extract_docx(conv, workdir, opts)
            sub.detected = "doc"
            sub.flags.append("converted_with_libreoffice")
            for p in sub.pages:
                p.method = "libreoffice+" + p.method
            return sub
    # scanned pages inside the .doc: OCR page-sized images, splice in order when the counts line up (your approach)
    blobs = [b for b in _doc_image_blobs(path) if is_page_scale(b, opts.scan_image_min_dim)]
    ocr_texts = []
    for b in blobs:
        try:
            t, conf = ocr_bytes(b, opts)
            ocr_texts.append(t)
            if conf is not None and conf < 60:
                res.flags.append("low_ocr_confidence_embedded_image")
        except Exception as e:
            res.flags.append(f"embedded_image_ocr_failed:{type(e).__name__}")
    if ocr_texts:
        res.flags.append("embedded_scan_ocr")
    pic_segments = _PLACEHOLDER_RE.split(raw)
    if ocr_texts and len(pic_segments) - 1 == len(ocr_texts):
        merged = pic_segments[0]
        for i, seg in enumerate(pic_segments[1:]):
            merged += "\f" + ocr_texts[i] + "\f" + seg
        raw = merged
    else:
        raw = _PLACEHOLDER_RE.sub("", raw)
        if ocr_texts:
            raw = raw + "\f" + "\f".join(ocr_texts)
    chunks = [c for c in raw.split("\f") if c.strip()]
    for i, c in enumerate(chunks, 1):
        res.pages.append(Page(i, c, method + ("+ocr" if ocr_texts else "")))
    if len(chunks) > 1:
        res.flags.append("page_numbers_approximate")
    return res