"""OpenDocument text, spreadsheet and presentation (.odt / .ods / .odp).


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


from ..core.models import Options, Page, Result




def extract_odf(path: Path, workdir: Path, opts: Options) -> Result:
    res = Result(detected="odf")
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    paras = []
    for el in root.iter():
        if el.tag.endswith("}p") or el.tag.endswith("}h"):
            t = "".join(el.itertext()).strip()
            if t:
                paras.append(t)
    res.pages.append(Page(1, "\n".join(paras), "odf_xml"))
    return res