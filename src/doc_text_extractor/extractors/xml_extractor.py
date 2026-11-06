"""XML files, including Word 2003 and Excel 2003 XML documents.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import xml.etree.ElementTree as ET
from pathlib import Path


from ..core.models import Options, Page, Result
from ..core.text_utils import decode_bytes




WML2003 = "{http://schemas.microsoft.com/office/word/2003/wordml}"




SS2003 = "{urn:schemas-microsoft-com:office:spreadsheet}"




def extract_xml(path: Path, workdir: Path, opts: Options) -> Result:
    raw = path.read_bytes()
    detected = "xml"
    try:
        root = ET.fromstring(raw)
        if root.tag == WML2003 + "wordDocument":       # Word 2003 XML: body text only, not the style/font tables
            detected, method = "xml_office", "word2003_xml"
            body = root.find(WML2003 + "body")
            paras = []
            for p in (body if body is not None else root).iter(WML2003 + "p"):
                paras.append("".join((t.text or "") if t.tag == WML2003 + "t" else "\t" if t.tag == WML2003 + "tab" else "\n"
                                     for t in p.iter() if t.tag in (WML2003 + "t", WML2003 + "tab", WML2003 + "br")))
            text = "\n".join(paras)
        elif root.tag == SS2003 + "Workbook":           # Excel 2003 XML
            detected, method = "xml_office", "excel2003_xml"
            rows = []
            for r in root.iter(SS2003 + "Row"):
                rows.append("\t".join("".join(d.itertext()).strip() for d in r.iter(SS2003 + "Data")))
            text = "\n".join(rows)
        else:
            text = "\n".join(t.strip() for t in root.itertext() if t.strip())
            method = "xml_text"
    except ET.ParseError:
        text, method = decode_bytes(raw)[0], "xml_raw"
    return Result(detected=detected, pages=[Page(1, text, method)])
