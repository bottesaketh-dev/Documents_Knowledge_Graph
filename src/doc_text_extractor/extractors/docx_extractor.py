"""Word DOCX / DOCM / DOTX (and altChunk parts inside them): numbering, tables,
headers/footers, footnotes, comments, tracked changes and images.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Optional


from ..core.models import Options, Page, Result
from ..core.ocr import is_page_scale, ocr_bytes
from ..core.text_utils import decode_bytes, safe_name
from .html_extractor import html_to_text
from .mht_extractor import mht_to_text
from .rtf_extractor import rtf_to_text_bytes




_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
}




W = "{%s}" % _NS["w"]




A = "{%s}" % _NS["a"]




R = "{%s}" % _NS["r"]




V = "{%s}" % _NS["v"]




MC = "{%s}" % _NS["mc"]




def _rels(z: zipfile.ZipFile, part: str) -> dict:
    d, base = {}, part.rsplit("/", 1)[0]
    rp = f"{base}/_rels/{part.rsplit('/', 1)[1]}.rels"
    if rp not in z.namelist():
        return d
    for rel in ET.fromstring(z.read(rp)):
        tgt = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            continue
        full = tgt.lstrip("/") if tgt.startswith("/") else os.path.normpath(f"{base}/{tgt}").replace("\\", "/")
        d[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1], full)
    return d




def _roman(n: int) -> str:
    out = ""
    for v, r in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"),
                 (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out += r; n -= v
    return out




def _letters(n: int) -> str:
    # Word repeats the letter after z: a..z, aa..zz, aaa..
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1) if n > 0 else ""




class _Numbering:
    """Word's automatic list numbering ("1.", "(a)", "Section 2.3"), which is not stored as text in document.xml."""


    def __init__(self, z: zipfile.ZipFile, names: set):
        self.abstract: dict[str, dict[int, tuple]] = {}
        self.nums: dict[str, tuple[str, dict]] = {}
        self.styles: dict[str, tuple] = {}
        self.counters: dict[str, dict[int, int]] = {}
        if "word/numbering.xml" in names:
            root = ET.fromstring(z.read("word/numbering.xml"))
            for an in root.findall(W + "abstractNum"):
                lv = {}
                for l in an.findall(W + "lvl"):
                    g = lambda t: (l.find(W + t).get(W + "val") if l.find(W + t) is not None else None)
                    lv[int(l.get(W + "ilvl", "0"))] = (int(g("start") or 1), g("numFmt") or "decimal", g("lvlText") or "",
                                                       l.find(W + "isLgl") is not None)
                self.abstract[an.get(W + "abstractNumId")] = lv
            for n in root.findall(W + "num"):
                a = n.find(W + "abstractNumId")
                overrides = {}
                for o in n.findall(W + "lvlOverride"):
                    so = o.find(W + "startOverride")
                    if so is not None:
                        overrides[int(o.get(W + "ilvl", "0"))] = int(so.get(W + "val", "1"))
                self.nums[n.get(W + "numId")] = (a.get(W + "val") if a is not None else None, overrides)
        if "word/styles.xml" in names and self.nums:
            for st in ET.fromstring(z.read("word/styles.xml")).findall(W + "style"):
                np_ = st.find(f"{W}pPr/{W}numPr")
                based = st.find(W + "basedOn")
                num = ilvl = None
                if np_ is not None:
                    n_el, l_el = np_.find(W + "numId"), np_.find(W + "ilvl")
                    num = n_el.get(W + "val") if n_el is not None else None
                    ilvl = int(l_el.get(W + "val")) if l_el is not None else None
                self.styles[st.get(W + "styleId")] = (num, ilvl, based.get(W + "val") if based is not None else None)


    def _style_num(self, style: Optional[str]):
        seen = set()
        while style and style not in seen:
            seen.add(style)
            num, ilvl, based = self.styles.get(style, (None, None, None))
            if num is not None:
                return num, ilvl or 0
            style = based
        return None, 0


    @staticmethod
    def _fmt(n: int, fmt: str) -> str:
        if fmt in ("lowerLetter",):
            return _letters(n)
        if fmt == "upperLetter":
            return _letters(n).upper()
        if fmt == "lowerRoman":
            return _roman(n)
        if fmt == "upperRoman":
            return _roman(n).upper()
        if fmt == "decimalZero":
            return f"{n:02d}"
        if fmt == "none":
            return ""
        return str(n)


    def label(self, p) -> str:
        if not self.nums:
            return ""
        ppr = p.find(W + "pPr")
        num, ilvl = None, None
        if ppr is not None:
            np_ = ppr.find(W + "numPr")
            if np_ is not None:
                n_el, l_el = np_.find(W + "numId"), np_.find(W + "ilvl")
                num = n_el.get(W + "val") if n_el is not None else None
                ilvl = int(l_el.get(W + "val")) if l_el is not None else None
            if num is None or ilvl is None:
                ps = ppr.find(W + "pStyle")
                snum, silvl = self._style_num(ps.get(W + "val") if ps is not None else None)
                num = num if num is not None else snum
                ilvl = ilvl if ilvl is not None else silvl
        if not num or num == "0" or num not in self.nums:
            return ""
        abs_id, overrides = self.nums[num]
        levels = self.abstract.get(abs_id, {})
        if ilvl not in levels:
            return ""
        start, fmt, text, legal = levels[ilvl]
        c = self.counters.setdefault(num, {})
        c[ilvl] = c[ilvl] + 1 if ilvl in c else overrides.get(ilvl, start)
        for deeper in [k for k in c if k > ilvl]:
            del c[deeper]
        if fmt == "bullet":
            return "\u2022"
        def sub(m):
            k = int(m.group(1)) - 1
            lv = levels.get(k, (1, "decimal", "", False))
            val = c.get(k, overrides.get(k, lv[0]))
            return self._fmt(val, "decimal" if legal and k != ilvl else lv[1])
        return re.sub(r"%(\d)", sub, text).strip()




class _DocxWalker:
    """Walks a WordprocessingML part in document order and produces pages of text."""


    def __init__(self, z: zipfile.ZipFile, part: str, opts: Options, res: Result, workdir: Path):
        self.z, self.part, self.opts, self.res, self.workdir = z, part, opts, res, workdir
        self.rels = _rels(z, part)
        self.pages: list[list[str]] = [[]]
        self.ocr_cache: dict[str, str] = {}
        self.tracked_ins = self.tracked_del = 0
        self.saw_rendered_breaks = False
        self.ocr_pages: set[int] = set()
        self.numbering: Optional[_Numbering] = None


    def new_page(self):
        if self.pages[-1]:
            self.pages.append([])


    def emit(self, s: str):
        if s and s.strip():
            self.pages[-1].append(s.rstrip())


    # --- block level -------------------------------------------------------
    def walk_blocks(self, parent):
        for el in parent:
            tag = el.tag
            if tag == W + "p":
                self.emit(self.paragraph(el))
            elif tag == W + "tbl":
                self.table(el)
            elif tag in (W + "sdt", W + "customXml", W + "smartTag"):
                content = el.find(W + "sdtContent")
                self.walk_blocks(content if content is not None else el)
            elif tag == W + "altChunk":
                self.alt_chunk(el)
            elif tag in (W + "ins", W + "moveTo"):
                self.walk_blocks(el)
            elif tag == MC + "AlternateContent":
                choice = el.find(MC + "Choice")
                self.walk_blocks(choice if choice is not None else el)


    def table(self, tbl):
        for tr in tbl.iter(W + "tr"):
            cells = []
            for tc in tr.findall(W + "tc") + [c for s in tr.findall(W + "sdt") for c in s.iter(W + "tc")]:
                parts = []
                for child in tc:
                    if child.tag == W + "p":
                        parts.append(self.paragraph(child))
                    elif child.tag == W + "tbl":
                        sub = _DocxWalker(self.z, self.part, self.opts, self.res, self.workdir)
                        sub.ocr_cache = self.ocr_cache
                        sub.numbering = self.numbering
                        sub.table(child)
                        parts.append(" / ".join(" ".join(p) for p in sub.pages if p))
                    elif child.tag == W + "sdt":
                        c = child.find(W + "sdtContent")
                        for p in (c if c is not None else child).iter(W + "p"):
                            parts.append(self.paragraph(p))
                cells.append(" ".join(x.strip() for x in parts if x and x.strip()))
            if any(cells):
                self.emit(" | ".join(cells))


    # --- inline level ------------------------------------------------------
    def paragraph(self, p) -> str:
        out: list[str] = []
        if self.numbering is not None:
            lab = self.numbering.label(p)
            if lab:
                out.append(lab + " ")
        self.inline(p, out)
        return "".join(out)


    def inline(self, node, out):
        for el in node:
            tag = el.tag
            if tag == W + "r":
                self.run(el, out)
            elif tag in (W + "hyperlink", W + "smartTag", W + "customXml", W + "fldSimple", W + "ins", W + "moveTo", W + "bdo", W + "dir"):
                if tag == W + "ins":
                    self.tracked_ins += 1
                self.inline(el, out)
            elif tag == W + "sdt":
                c = el.find(W + "sdtContent")
                self.inline(c if c is not None else el, out)
            elif tag in (W + "del", W + "moveFrom"):
                self.tracked_del += 1                                 # deleted text: final view drops it
            elif tag == MC + "AlternateContent":
                choice = el.find(MC + "Choice")
                self.inline(choice if choice is not None else el, out)


    def run(self, r, out):
        for el in r:
            tag = el.tag
            if tag == W + "t":
                out.append(el.text or "")
            elif tag == W + "tab" or tag == W + "ptab":
                out.append("\t")
            elif tag == W + "br":
                if el.get(W + "type") == "page":
                    self.emit("".join(out)); out.clear(); self.new_page()
                else:
                    out.append("\n")
            elif tag == W + "cr":
                out.append("\n")
            elif tag == W + "lastRenderedPageBreak":
                self.saw_rendered_breaks = True
                self.emit("".join(out)); out.clear(); self.new_page()
            elif tag == W + "noBreakHyphen":
                out.append("-")
            elif tag == W + "sym":
                try:
                    code = int(el.get(W + "char"), 16)
                    out.append(chr(code - 0xF000) if code >= 0xF000 else chr(code))
                except (TypeError, ValueError):
                    pass
            elif tag in (W + "drawing", W + "pict", W + "object") or tag == MC + "AlternateContent":
                self.drawing(el, out)
            elif tag == W + "delText":
                self.tracked_del += 1


    def drawing(self, el, out):
        if el.tag == MC + "AlternateContent":
            choice = el.find(MC + "Choice")
            el = choice if choice is not None else el
        # text boxes
        for tx in el.iter(W + "txbxContent"):
            texts = [self.paragraph(p) for p in tx.iter(W + "p")]
            t = "\n".join(x for x in texts if x.strip())
            if t:
                out.append("\n" + t + "\n")
        # pictures (DrawingML and legacy VML); OCR only page-sized ones
        rids = [b.get(R + "embed") for b in el.iter(A + "blip")] + [i.get(R + "id") for i in el.iter(V + "imagedata")]
        for rid in dict.fromkeys(x for x in rids if x):
            if rid not in self.rels:
                continue
            target = self.rels[rid][1]
            try:
                data = self.z.read(target)
            except KeyError:
                continue
            if not is_page_scale(data, self.opts.scan_image_min_dim):
                continue
            if rid not in self.ocr_cache:
                try:
                    t, conf = ocr_bytes(data, self.opts)
                    self.ocr_cache[rid] = t
                    self.res.flags.append("embedded_scan_ocr")
                    if conf is not None and conf < 60:
                        self.res.flags.append("low_ocr_confidence_embedded_image")
                except Exception as e:
                    self.ocr_cache[rid] = ""
                    self.res.flags.append(f"embedded_image_ocr_failed:{type(e).__name__}")
            if self.ocr_cache[rid]:
                out.append("\n" + self.ocr_cache[rid] + "\n")
                self.ocr_pages.add(len(self.pages) - 1)


    def alt_chunk(self, el):
        rid = el.get(R + "id")
        if rid not in self.rels:
            return
        target = self.rels[rid][1]
        try:
            data = self.z.read(target)
        except KeyError:
            return
        self.res.flags.append("altchunk_content")
        kind = target.rsplit(".", 1)[-1].lower()
        if data.lstrip()[:5] == b"{\\rtf":
            self.emit(rtf_to_text_bytes(data))
        elif kind in ("htm", "html", "xhtml") or b"<html" in data[:2000].lower():
            self.emit(html_to_text(data))
        elif kind in ("mht", "mhtml") or b"mime-version" in data[:2000].lower():
            self.emit(mht_to_text(data))
        elif data[:2] == b"PK":
            p = self.workdir / f"altchunk_{rid}.docx"
            p.write_bytes(data)
            sub = extract_docx(p, self.workdir, self.opts)
            for pg in sub.pages:
                self.emit(pg.text)
        else:
            self.emit(decode_bytes(data)[0])


    def page_texts(self) -> list[str]:
        return ["\n".join(b) for b in self.pages if any(x.strip() for x in b)]


    def page_methods(self, base: str) -> list[str]:
        return [base + ("+ocr" if i in self.ocr_pages else "") for i, b in enumerate(self.pages) if any(x.strip() for x in b)]




def extract_docx(path: Path, workdir: Path, opts: Options) -> Result:
    res = Result(detected="docx")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        main = "word/document.xml"
        if main not in names:
            ct = z.read("[Content_Types].xml").decode("utf-8", "ignore") if "[Content_Types].xml" in names else ""
            m = re.search(r'PartName="/([^"]+)"[^>]*ContentType="[^"]*document\.main\+xml', ct)
            main = m.group(1) if m else main
        walker = _DocxWalker(z, main, opts, res, workdir)
        try:
            walker.numbering = _Numbering(z, names)
        except Exception as e:                                       # numbering is a nicety; never fail on it
            res.flags.append(f"list_numbering_unreadable:{type(e).__name__}")
        body = ET.fromstring(z.read(main)).find(W + "body")
        if body is not None:
            walker.walk_blocks(body)
        texts = walker.page_texts()
        for i, (t, m) in enumerate(zip(texts, walker.page_methods("docx_xml")), 1):
            res.pages.append(Page(i, t, m))
        if walker.saw_rendered_breaks or len(texts) > 1:
            res.flags.append("page_numbers_approximate")
        if walker.tracked_ins or walker.tracked_del:
            res.flags.append("tracked_changes")
            res.meta["tracked_insertions"] = walker.tracked_ins
            res.meta["tracked_deletions"] = walker.tracked_del
        # headers / footers / footnotes / endnotes / comments
        sections = {"Headers and footers": [], "Footnotes": [], "Endnotes": [], "Comments": []}
        for rid, (rtype, target) in walker.rels.items():
            key = {"header": "Headers and footers", "footer": "Headers and footers", "footnotes": "Footnotes",
                   "endnotes": "Endnotes", "comments": "Comments"}.get(rtype)
            if not key or target not in names:
                continue
            sub = _DocxWalker(z, target, opts, res, workdir)
            root = ET.fromstring(z.read(target))
            if rtype in ("footnotes", "endnotes", "comments"):
                for item in root:
                    if item.get(W + "type") in ("separator", "continuationSeparator"):
                        continue
                    auth = item.get(W + "author")
                    t = "\n".join(sub.paragraph(p) for p in item.iter(W + "p")).strip()
                    if t:
                        sections[key].append(f"{auth}: {t}" if auth else t)
            else:
                sub.walk_blocks(root)
                t = "\n".join(sub.page_texts()).strip()
                if t:
                    sections[key].append(t)
        for name, items in sections.items():
            uniq = list(dict.fromkeys(items))
            if uniq:
                res.sections.append({"name": name, "text": "\n".join(uniq)})
        if sections["Comments"]:
            res.flags.append("has_comments")
        # embedded files (exhibits pasted as objects)
        for n in sorted(names):
            if n.startswith("word/embeddings/"):
                ext = Path(n).suffix.lower()
                if ext in (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pdf", ".doc", ".xls", ".ppt"):
                    p = workdir / f"embedded_{safe_name(Path(n).name)}"
                    p.write_bytes(z.read(n)); res.children.append((Path(n).name, p))
                elif ext == ".bin":
                    res.flags.append("embedded_ole_object_not_extracted")
        # core properties
        if "docProps/core.xml" in names:
            try:
                core = ET.fromstring(z.read("docProps/core.xml"))
                for tag, key in (("dc:title", "title"), ("dc:creator", "author"), ("cp:lastModifiedBy", "last_modified_by"),
                                 ("dcterms:created", "created"), ("dcterms:modified", "modified"), ("cp:revision", "revision")):
                    el = core.find(tag, _NS)
                    if el is not None and el.text:
                        res.meta[key] = el.text
            except ET.ParseError:
                pass
    return res