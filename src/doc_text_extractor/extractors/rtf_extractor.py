"""Rich Text Format (.rtf), including OCR of embedded pictures.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import hashlib
import re
from pathlib import Path


from ..core.models import Options, Page, Result
from ..core.ocr import is_page_scale, ocr_bytes




def rtf_to_text_bytes(raw: bytes) -> str:
    from striprtf.striprtf import rtf_to_text
    m = re.search(rb"\\ansicpg(\d+)", raw[:4000])
    enc = f"cp{m.group(1).decode()}" if m else "cp1252"
    try:
        "".encode(enc)
    except LookupError:
        enc = "cp1252"
    # drop embedded picture hex (very slow to strip and carries no text)
    raw = re.sub(rb"\{\\\*?\\(?:pict|shppict|nonshppict|objdata|blipuid)(?:[^{}]|\{[^{}]*\})*\}", b"", raw)
    return rtf_to_text(raw.decode("latin-1"), encoding=enc, errors="replace")




def _rtf_pictures(raw: bytes) -> list[bytes]:
    """PNG/JPEG pictures stored as hex inside an RTF file (scans pasted into Word and saved as RTF)."""
    out, seen = [], set()
    for m in re.finditer(rb"\\(pngblip|jpegblip)", raw):
        i, n = m.end(), len(raw)
        while i < n:                                   # skip the remaining picture properties
            c = raw[i:i + 1]
            if c in b" \r\n\t":
                i += 1
            elif c == b"\\":
                j = i + 1
                while j < n and raw[j:j + 1].isalpha():
                    j += 1
                if j < n and raw[j:j + 1] == b"-":
                    j += 1
                while j < n and raw[j:j + 1].isdigit():
                    j += 1
                i = j
            elif c == b"{":
                depth = 0
                while i < n:
                    if raw[i:i + 1] == b"{":
                        depth += 1
                    elif raw[i:i + 1] == b"}":
                        depth -= 1
                        if depth == 0:
                            i += 1
                            break
                    i += 1
            else:
                break
        end = raw.find(b"}", i)
        if end < 0:
            continue
        hexdata = re.sub(rb"\s+", b"", raw[i:end])
        if len(hexdata) < 200 or not re.fullmatch(rb"[0-9a-fA-F]+", hexdata):
            continue
        try:
            data = bytes.fromhex(hexdata.decode("ascii"))
        except ValueError:
            continue
        key = hashlib.sha1(data).digest()
        if key not in seen:
            seen.add(key)
            out.append(data)
    return out




def extract_rtf(path: Path, workdir: Path, opts: Options) -> Result:
    res = Result(detected="rtf")
    raw = path.read_bytes()
    text = rtf_to_text_bytes(raw)
    res.pages.append(Page(1, text, "striprtf"))
    if b"\\pict" in raw:
        n = 1
        for data in _rtf_pictures(raw):
            if not is_page_scale(data, opts.scan_image_min_dim):
                continue
            try:
                t, conf = ocr_bytes(data, opts)
            except Exception as e:
                res.flags.append(f"embedded_image_ocr_failed:{type(e).__name__}")
                continue
            if conf is not None and conf < 60:
                res.flags.append("low_ocr_confidence_embedded_image")
            if t.strip():
                n += 1
                res.pages.append(Page(n, t, "rtf_picture_ocr", conf))
                res.flags.append("embedded_scan_ocr")
    return res
