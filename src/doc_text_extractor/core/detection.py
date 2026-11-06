"""Format detection by content (extensions in a 20-year-old share are not reliable),
and the files skipped on their name alone.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import re
import zipfile
from pathlib import Path
from typing import Optional




JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "themedata.thmx", "colorschememapping.xml", "filelist.xml"}




SHORTCUTS = {".lnk", ".url", ".website"}




# Not worth reading as text:
MEDIA_EXTS = {".mp4", ".wmv", ".mov", ".avi", ".mpg", ".mpeg", ".m4v", ".flv", ".mp3", ".wav", ".m4a", ".wma", ".aac"}




NON_DOCUMENT_EXTS = {".thmx", ".otf", ".ttf", ".woff", ".woff2", ".js", ".css", ".eps", ".lex", ".stop", ".dat", ".x1s",
                     ".exe", ".dll", ".msi", ".db", ".ini", ".psd", ".ai", ".indd"}




UNSUPPORTED_ARCHIVES = {".sitx", ".sit", ".7z", ".rar", ".gz", ".tar", ".tgz"}




TEXT_EXTS = {".txt", ".log", ".md", ".csv", ".tsv", ".json", ".ics", ".vcf", ".ini", ".cfg", ".sql"}




IMAGE_EXTS = {".tif", ".tiff", ".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".gif", ".webp", ".heic", ".heif"}




def skip_reason_by_name(path: Path) -> Optional[str]:
    """Files skipped on their name alone (checked before reading or de-duplicating them)."""
    name = path.name.lower()
    ext = path.suffix.lower()
    if name in JUNK_NAMES or name.startswith("~$") or ext in {".tmp"}:
        return "skip:temp_or_system_file"         # ~$ Office lock files, ~WRL*.tmp Word backups
    if ext in SHORTCUTS:
        return "skip:shortcut"
    if ext in MEDIA_EXTS:
        return "skip:audio_video_not_transcribed"  # transcribe separately (e.g. Whisper) if needed
    if ext in NON_DOCUMENT_EXTS:
        return "skip:not_a_document"              # fonts, themes, scripts, search-index files
    if ext in UNSUPPORTED_ARCHIVES:
        return "skip:archive_type_not_supported"
    return None




def detect_format(path: Path) -> str:
    name = path.name.lower()
    ext = path.suffix.lower()
    skip = skip_reason_by_name(path)
    if skip:
        return skip
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError as e:
        return f"error:{e.__class__.__name__}"
    if not head:
        return "empty_file"
    # container signatures first: a zip whose first member is a PDF also has "%PDF" in its first kilobyte
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _ole_kind(path)
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return _zip_kind(path)
    if b"%PDF" in head[:1024]:
        return "pdf"
    stripped = head.lstrip(b"\xef\xbb\xbf \r\n\t")
    if stripped.startswith(b"{\\rtf"):
        return "rtf"
    low = head[:4096].lower()
    if b"mime-version:" in low and (b"multipart/related" in low or b"text/html" in low) and ext in {".mht", ".mhtml", ".doc", ".xls", ".htm", ".html"}:
        return "mht"
    if ext == ".eml" or (re.search(rb"(?im)^(received|message-id|from):", head[:2048]) and re.search(rb"(?im)^subject:", head[:4096]) and ext in {".eml", ".txt", ".msg", ""}):
        return "eml"
    if b"<html" in low or b"<!doctype html" in low:
        return "html"
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
        if b"w:worddocument" in low or b"progid=\"word.document\"" in low:
            return "xml_office"
        if b"progid=\"excel.sheet\"" in low or b"urn:schemas-microsoft-com:office:spreadsheet" in low:
            return "xml_office"
        if ext in {".htm", ".html"}:
            return "html"
        return "xml"
    if head.startswith((b"II*\x00", b"MM\x00*", b"\xff\xd8\xff", b"\x89PNG", b"BM", b"GIF87a", b"GIF89a")) or \
            (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "image"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in TEXT_EXTS or (b"\x00" not in head[:4096] and ext not in {".exe", ".dll", ".bin", ".dat", ".db"}):
        return "text"
    if ext in {".wpd", ".wps", ".pages", ".numbers", ".key", ".odt", ".ods", ".odp"}:
        return "office_other"
    return "unsupported"




def _ole_kind(path: Path) -> str:
    try:
        import olefile
        with olefile.OleFileIO(str(path)) as ole:
            names = {"/".join(e) for e in ole.listdir(streams=True, storages=True)}
    except Exception:
        return "ole_unreadable"
    top = {n.split("/")[0] for n in names}
    if "EncryptedPackage" in top or "EncryptionInfo" in top:
        return "office_encrypted"
    if "WordDocument" in top:
        return "doc"
    if "Workbook" in top or "Book" in top:
        return "xls"
    if "PowerPoint Document" in top:
        return "ppt"
    if any(n.startswith("__substg1.0_") or n.startswith("__properties_version1.0") for n in top):
        return "msg"
    if "Contents" in top or "CONTENTS" in top or "\x01Ole10Native" in top:
        return "ole_package"
    return "ole_unknown"




def _zip_kind(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            mimetype = z.read("mimetype").decode("ascii", "ignore") if "mimetype" in names else ""
    except Exception:
        return "zip_unreadable"
    if "word/document.xml" in names or any(n.startswith("word/document") for n in names):
        return "docx"
    if "xl/workbook.bin" in names:
        return "xlsb"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if mimetype.startswith("application/vnd.oasis.opendocument"):
        return "odf"
    return "zip"