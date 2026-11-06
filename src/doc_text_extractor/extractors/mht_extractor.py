"""MHT / MHTML web archives (and Word/Excel files saved as web pages).


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import email
import email.policy
from pathlib import Path


from ..core.models import Options, Page, Result
from ..core.text_utils import decode_bytes
from .html_extractor import html_to_text




def mht_to_text(raw: bytes) -> str:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/html":
            return html_to_text(part.get_payload(decode=True) or b"")
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            return decode_bytes(part.get_payload(decode=True) or b"")[0]
    return ""




def extract_mht(path: Path, workdir: Path, opts: Options) -> Result:
    return Result(detected="mht", pages=[Page(1, mht_to_text(path.read_bytes()), "mht")])
