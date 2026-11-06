"""Plain text files (.txt, .csv, .log, .md, .json, ...).


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


from pathlib import Path


from ..core.models import Options, Page, Result
from ..core.text_utils import decode_bytes




def extract_plain(path: Path, workdir: Path, opts: Options) -> Result:
    text, enc = decode_bytes(path.read_bytes())
    return Result(detected="text", pages=[Page(1, text, f"text:{enc}")])
