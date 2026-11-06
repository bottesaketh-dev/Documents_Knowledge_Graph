"""HTML / HTM pages.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


from pathlib import Path


from ..core.models import Options, Page, Result




def html_to_text(raw: bytes) -> str:
    from bs4 import BeautifulSoup, UnicodeDammit
    markup = UnicodeDammit(raw, ["utf-8", "windows-1252"]).unicode_markup or raw.decode("utf-8", "replace")
    soup = BeautifulSoup(markup, "html.parser")
    for s in soup(["script", "style", "head", "noscript"]):
        s.decompose()
    return soup.get_text("\n")




def extract_html(path: Path, workdir: Path, opts: Options) -> Result:
    return Result(detected="html", pages=[Page(1, html_to_text(path.read_bytes()), "html")])