"""Text clean-up and quality checks shared by every extractor.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import os
import re
import unicodedata
from typing import Optional




_PLACEHOLDER_RE = re.compile(r"\[pic[^\]]*\]", re.IGNORECASE)




_PAGE_BREAK = "\n\n\n[PAGE BREAK]\n\n\n"




def _nonspace(text: Optional[str]) -> int:
    return len("".join((text or "").split()))




def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", (text or "").replace("\x00", ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()




def _has_meaningful_text(text: Optional[str], min_chars: int = 10) -> bool:
    return _nonspace(_PLACEHOLDER_RE.sub("", text or "")) >= min_chars




def _garbage_ratio(text: str) -> float:
    """Share of characters that signal a broken text layer (missing ToUnicode maps, (cid:NN), private-use glyphs)."""
    s = "".join(text.split())
    if not s:
        return 0.0
    bad = len(re.findall(r"\(cid:\d+\)", text)) * 6
    bad += sum(1 for ch in s if ch == "\ufffd" or 0xE000 <= ord(ch) <= 0xF8FF or (ord(ch) < 32))
    return min(1.0, bad / len(s))




_STOPWORDS = frozenset("the of and to in a or be by for this any shall that with as such is on its at from not are all "
                       "which other an will may have under no if party parties agreement".split())




def _alpha_ratio(text: str) -> float:
    s = "".join(text.split())
    return sum(ch.isalnum() for ch in s) / len(s) if s else 1.0




def _english_score(text: str) -> Optional[float]:
    """Share of words that are common English function words. Real prose scores 0.25-0.45; a text layer
    from a font with a scrambled encoding ("Wkh Djuhhphqw vkdoo...") scores close to 0. None = too few words to judge."""
    words = re.findall(r"[A-Za-z]+", text)
    if len(words) < 40:
        return None
    return sum(w.lower() in _STOPWORDS for w in words) / len(words)




def decode_bytes(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace"), "utf-16"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace"), "cp1252"




def safe_name(name: str, default: str = "attachment") -> str:
    """Make a file name safe for Windows and short enough for deep folders."""
    name = (name or default).replace("\x00", "").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .") or default
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com\d|lpt\d)(\..*)?", name):
        name = "_" + name
    stem, ext = os.path.splitext(name)
    return stem[:80] + ext[:15]