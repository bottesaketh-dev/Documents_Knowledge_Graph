"""Segment-aware document parsing, medium detection, and chunk generation."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from src.family_graphrag.config import (
    CARD_HEAD,
    CARD_TAIL,
    CHUNK_CHARS,
    CHUNK_OVERLAP,
    TEXT_EXTS,
    WORK_DIR,
)
from src.family_graphrag.family import display_name, normalize_name
from src.family_graphrag.models import Chunk, Doc, Segment

HEADER_RE = re.compile(r"^#\s*([A-Za-z][\w ]{0,30}):\s*(.*)$")
PAGE_RE = re.compile(
    r"^[ \t]*(?:={2,}|-{2,})[ \t]*Page[ \t]+(\d+)\b.*$", re.M | re.I
)
TS_RE = re.compile(r"^[ \t]*\[?((?:\d{1,2}:)?\d{1,2}:\d{2})\]?", re.M)
IMAGE_EXT = re.compile(r"\.(jpe?g|png|heic|heif|webp|gif|bmp|tiff?)$", re.I)
VIDEO_EXT = re.compile(r"\.(mp4|mov|m4v|avi|mkv|3gp|webm|wmv)$", re.I)
EMAIL_KEYS = ("Subject", "From", "To", "Cc", "Date")
ADDR_RE = re.compile(
    r'(?:"([^"]*)"|([^<>";,][^<>";]*?))?\s*<([^<>@\s]+@[^<>\s]+)>'
)


def guess_medium(name: str, headers: dict, body: str) -> str:
    """Guess medium (text, image, video) from headers, filename extension, or text preamble."""
    h = (headers.get("medium") or headers.get("type") or "").lower()
    if h in ("image", "photo", "picture", "jpg", "jpeg", "png", "heic", "webp"):
        return "image"
    if h in ("video", "mp4", "mov", "avi", "mkv", "3gp"):
        return "video"
    if IMAGE_EXT.search(name):
        return "image"
    if VIDEO_EXT.search(name):
        return "video"
    head = body[:300].lower()
    if re.search(r"\b(image|photo|picture|photograph)\s+(description|caption)", head):
        return "image"
    if re.search(r"\bvideo\s+(description|transcript|summary)", head):
        return "video"
    return "text"


def split_segments(body: str, medium: str) -> list[Segment]:
    """Split text into segments by page break markers, video timestamps, or form-feed."""
    if medium == "video":
        marks = list(TS_RE.finditer(body))
        if len(marks) >= 2:
            segs = []
            pre = body[: marks[0].start()].strip()
            if pre:
                segs.append(Segment("start", pre))
            for i, mk in enumerate(marks):
                end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
                txt = body[mk.start() : end].strip()
                if txt:
                    segs.append(Segment(mk.group(1), txt))
            return segs

    marks = list(PAGE_RE.finditer(body))
    if marks:
        segs = []
        pre = body[: marks[0].start()].strip()
        for i, mk in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
            txt = body[mk.end() : end].strip()
            if i == 0 and pre:
                txt = (pre + "\n\n" + txt).strip()
            if txt:
                segs.append(Segment(mk.group(1), txt))
        return segs

    if "\f" in body:
        return [
            Segment(str(i), t.strip())
            for i, t in enumerate(body.split("\f"), 1)
            if t.strip()
        ]
    body = body.strip()
    return [Segment("", body)] if body else []


def parse_file(path: Path, root: Path) -> Doc | None:
    """Parse text file into Doc model with metadata headers and segments."""
    raw = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    content_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    lines = raw.split("\n")
    headers: dict[str, str] = {}
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        m = HEADER_RE.match(lines[i])
        if not m:
            break
        for part in f"{m.group(1)}:{m.group(2)}".split("|"):
            k, _, v = part.partition(":")
            if k.strip():
                headers[k.strip().lower()] = v.strip()
        i += 1
    body = "\n".join(lines[i:])

    status = headers.get("status", "ok").lower()
    if status not in ("ok", "success", "done", ""):
        return None
    rel = path.relative_to(root).as_posix()
    display = headers.get("source") or rel
    inner_name = Path(display).name if headers.get("source") else path.stem
    medium = guess_medium(inner_name, headers, body)
    segments = split_segments(body, medium)
    if not segments:
        return None

    email = {}
    top = "\n".join(segments[0].text.splitlines()[:40])
    if re.search(r"^From:", top, re.M) and re.search(r"^(To|Subject):", top, re.M):
        for key in EMAIL_KEYS:
            mh = re.search(rf"^{key}:\s*(.+)$", top, re.M)
            if mh:
                email[key] = mh.group(1).strip()
    return Doc(rel, display, medium, segments, content_hash, email)


def iter_docs(src: Path, only: str | None = None):
    """Iterate through all eligible document text files in vault root."""
    from src.family_graphrag import config

    root = src.resolve()
    base = (root / only).resolve() if only else root
    work = config.WORK_DIR.resolve()
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXTS:
            continue
        if any(
            part.startswith(".") or part == "_index"
            for part in p.relative_to(root).parts
        ):
            continue
        if work == p or work in p.parents:
            continue
        doc = parse_file(p, root)
        if doc:
            yield doc


def fmt_loc(medium: str, locs: list[str] | str) -> str:
    """Format citation location string (e.g. 'p. 1-3' or 'at 00:01:23')."""
    if isinstance(locs, str):
        locs = locs.split(",")
    locs = [x for x in locs if x]
    if not locs:
        return ""
    if medium == "video":
        return "at " + ", ".join(locs)
    return "p. " + (locs[0] if len(locs) == 1 else f"{locs[0]}-{locs[-1]}")


def chunk_doc(doc: Doc) -> list[Chunk]:
    """Pack paragraphs into ~CHUNK_CHARS chunks with overlap, tracking source segments."""
    units: list[tuple[str, str]] = []
    max_unit = CHUNK_CHARS - CHUNK_OVERLAP - 2  # so overlap tail + unit always fits
    for seg in doc.segments:
        for para in re.split(r"\n\s*\n", seg.text):
            para = para.strip()
            while len(para) > max_unit:
                units.append((seg.label, para[:max_unit]))
                para = para[max_unit - CHUNK_OVERLAP :]
            if para:
                units.append((seg.label, para))

    chunks: list[Chunk] = []
    buf, buf_locs, fresh = "", [], False

    def emit():
        cid = f"{doc.doc_id}-{len(chunks):04d}"
        chunks.append(
            Chunk(
                cid,
                doc.doc_id,
                doc.display_path,
                doc.medium,
                list(dict.fromkeys(buf_locs)),
                buf.strip(),
            )
        )

    for label, para in units:
        if fresh and len(buf) + len(para) + 2 > CHUNK_CHARS:
            emit()
            buf, buf_locs, fresh = buf[-CHUNK_OVERLAP:], buf_locs[-1:], False
        buf += ("\n\n" if buf else "") + para
        buf_locs.append(label)
        fresh = True
    if fresh:
        emit()
    return chunks


def card_text(doc: Doc) -> str:
    """Generate truncated text representation showing the beginning and end of the document."""
    full = "\n\n".join(
        (f"[{fmt_loc(doc.medium, [s.label])}]\n" if s.label else "") + s.text
        for s in doc.segments
    )
    if len(full) <= CARD_HEAD + CARD_TAIL:
        return full
    return full[:CARD_HEAD] + "\n\n[...]\n\n" + full[-CARD_TAIL:]


def email_people(headers: dict) -> list[dict]:
    """'"Rao, Anita" <anita@x.com>; Star Health <care@starhealth.in>' -> people with roles."""
    people, seen = [], set()
    for role in ("From", "To", "Cc"):
        for quoted, bare, addr in ADDR_RE.findall(headers.get(role, "")):
            name = (quoted or bare or "").strip(" ;,")
            m = re.match(r"^([^,;]+)[,;]\s*(.+)$", name)
            if m:
                name = f"{m.group(2).strip()} {m.group(1).strip()}"
            if not name:
                name = addr.split("@")[0]
            key = normalize_name(name)
            if key and (key, role) not in seen:
                seen.add((key, role))
                people.append(
                    {
                        "key": key,
                        "name": display_name(key, name),
                        "email": addr.lower(),
                        "role": role.lower(),
                    }
                )
    return people


def folder_chain(rel_path: str) -> list[str]:
    """Build hierarchy list of parent folder paths for a relative document path."""
    parts = rel_path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]

