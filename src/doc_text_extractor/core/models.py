"""Options, results and errors shared by every extractor.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


from dataclasses import dataclass, field
from typing import Optional


from .text_utils import _nonspace, normalize




EXTRACTOR_VERSION = "2026.10.30"




@dataclass
class Options:
    ocr_lang: str = "eng"
    dpi: int = 300
    max_ocr_pages: Optional[int] = None      # per document; None = OCR every page that needs it
    ocr_timeout: int = 180                    # seconds per page
    ocr_psm: int = 4                          # Tesseract layout mode. 4 reads each line across the full width, which keeps
                                              # "Effective Date:  March 30, 2026" together on forms and letters. Use 3 for
                                              # genuinely multi-column documents (newsletters, brochures).
    min_chars: int = 10                       # non-space chars for a page to count as having a text layer
    scan_image_min_dim: int = 700             # embedded image's longer edge (px) to count as a scanned page
    max_sheet_rows: int = 20000
    soffice_timeout: int = 300
    max_render_pixels: int = 45_000_000       # caps memory when rendering very large PDF pages




@dataclass
class Page:
    number: int
    text: str
    method: str
    ocr_conf: Optional[float] = None
    label: Optional[str] = None               # sheet or slide name


    def as_dict(self):
        d = {"page": self.number, "text": self.text, "method": self.method}
        if self.ocr_conf is not None:
            d["ocr_conf"] = self.ocr_conf
        if self.label:
            d["label"] = self.label
        return d




@dataclass
class Result:
    pages: list = field(default_factory=list)
    sections: list = field(default_factory=list)      # [{"name": ..., "text": ...}]
    children: list = field(default_factory=list)      # [(name, Path)]
    status: str = "ok"
    flags: list = field(default_factory=list)
    detected: str = ""
    meta: dict = field(default_factory=dict)


    def finish(self):
        for p in self.pages:
            p.text = normalize(p.text)
        for s in self.sections:
            s["text"] = normalize(s["text"])
        if self.status == "ok" and not any(_nonspace(p.text) for p in self.pages) and not self.children:
            self.status = "empty"
        self.flags = list(dict.fromkeys(self.flags))
        return self




class EncryptedError(Exception):
    pass