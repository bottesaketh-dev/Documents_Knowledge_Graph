"""PDF: text layer per page, OCR for scanned pages, comments, form fields and
embedded attachments (PyMuPDF, with a Poppler fallback).


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import logging
import re
from pathlib import Path


from .. import ocr_config
from ..core.external_tools import _run
from ..core.models import Options, Page, Result
from ..core.ocr import ocr_image
from ..core.text_utils import _alpha_ratio, _english_score, _garbage_ratio, _nonspace, safe_name


logger = logging.getLogger(__name__)




def _page_digital_text(page, pymupdf) -> tuple[str, float]:
    """Text layer in reading order + share of the page covered by text ."""
    blocks = page.get_text("blocks")
    blocks.sort(key=lambda b: (round(b[1], 1), b[0]))
    parts, area = [], 0.0
    for b in blocks:
        if len(b) >= 7 and b[6] != 0:
            continue
        content = (b[4] or "").strip()
        if content:
            parts.append(content)
            area += abs(pymupdf.Rect(b[:4]))
    return "\n\n".join(parts), area / (abs(page.rect) or 1.0)




def _image_coverage(page) -> float:
    try:
        infos = page.get_image_info()
    except Exception:
        return 0.0
    pr = page.rect
    total = 0.0
    for info in infos:
        try:
            import pymupdf
            r = pymupdf.Rect(info["bbox"]) & pr
            total += abs(r)
        except Exception:
            continue
    return min(1.0, total / (abs(pr) or 1.0))




def _merge_ocr_and_layer(ocr: str, layer: str) -> str:
    """Scanned body from OCR + any typed overlay (e-signature ID, page stamp, watermark text) not already in the OCR text."""
    if not layer.strip():
        return ocr
    ocr_flat = " ".join(ocr.lower().split())
    extra = [ln for ln in layer.splitlines() if ln.strip() and " ".join(ln.lower().split()) not in ocr_flat]
    return (ocr.rstrip() + ("\n\n" + "\n".join(extra) if extra else "")).strip()




def extract_pdf(path: Path, workdir: Path, opts: Options) -> Result:
    try:
        import pymupdf
    except ImportError:
        return _extract_pdf_poppler(path, workdir, opts)
    res = Result(detected="pdf")
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        logger.debug("PyMuPDF could not open %s (%s); trying Poppler", path, e)
        r = _extract_pdf_poppler(path, workdir, opts)
        r.flags.append("pymupdf_open_failed")
        return r
    with doc:
        if doc.needs_pass and not doc.authenticate(""):
            res.status = "encrypted"; res.flags.append("password_required")
            return res
        if doc.is_encrypted or doc.metadata.get("encryption"):
            res.flags.append("owner_restricted_pdf")
        meta = {k: v for k, v in (doc.metadata or {}).items() if v and k in ("title", "author", "creator", "producer", "creationDate", "modDate")}
        res.meta.update(meta)
        ocr_done = 0
        for page in doc:
            n = page.number + 1
            try:
                layer, ratio = _page_digital_text(page, pymupdf)
            except Exception:
                layer, ratio = "", 0.0
                res.flags.append(f"text_layer_error_p{n}")
            garbage = _garbage_ratio(layer) > 0.3 or (_nonspace(layer) >= 50 and _alpha_ratio(layer) < 0.4)
            if garbage:
                res.flags.append(f"garbled_text_layer_p{n}")
            layer_score = _english_score(layer) if opts.ocr_lang.startswith("eng") else None
            suspect = (not garbage) and layer_score is not None and layer_score < 0.04
            cover = _image_coverage(page)
            # a page with almost no text (a cover sheet, a lone e-signature ID) is still digital when it has no image
            has_layer = (ratio > 0.01 or cover < 0.05) and _nonspace(layer) >= opts.min_chars and not garbage
            # Your rule, plus: a page that is mostly an image with only a thin typed overlay is a scan,
            # and a layer that does not read as English (scrambled font encoding) is checked against OCR.
            needs_ocr = (not has_layer) or (cover >= 0.5 and _nonspace(layer) < 200) or suspect
            if not needs_ocr:
                res.pages.append(Page(n, layer, "text_layer"))
                continue
            if opts.max_ocr_pages is not None and ocr_done >= opts.max_ocr_pages:
                res.pages.append(Page(n, "" if garbage else layer, "ocr_skipped_limit"))
                res.flags.append("ocr_page_limit_reached")
                continue
            try:
                dpi = opts.dpi
                w_in, h_in = page.rect.width / 72, page.rect.height / 72
                if w_in * h_in * dpi * dpi > opts.max_render_pixels:
                    dpi = max(100, int((opts.max_render_pixels / (w_in * h_in)) ** 0.5))
                pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, annots=True)
                from PIL import Image
                img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
                img.info["dpi"] = (dpi, dpi)
                ocr, conf = ocr_image(img, opts)
                ocr_done += 1
            except Exception as e:
                res.pages.append(Page(n, "" if garbage else layer, "ocr_failed"))
                res.flags.append(f"ocr_failed_p{n}:{type(e).__name__}")
                continue
            if conf is not None and conf < 60:
                res.flags.append(f"low_ocr_confidence_p{n}")
            if suspect:
                ocr_score = _english_score(ocr) or 0.0
                if ocr_score > layer_score + 0.08:
                    res.flags.append(f"garbled_text_layer_p{n}")
                    res.pages.append(Page(n, ocr, "ocr", conf))
                else:
                    res.pages.append(Page(n, layer, "text_layer"))
                continue
            if has_layer:                                      # image-heavy page with a real but thin layer
                res.pages.append(Page(n, _merge_ocr_and_layer(ocr, layer), "ocr+text_layer", conf))
            elif _nonspace(ocr) >= _nonspace(layer) or garbage:
                res.pages.append(Page(n, ocr, "ocr", conf))
            else:
                res.pages.append(Page(n, layer, "text_layer", conf))
        _pdf_extras(doc, workdir, res)
    return res




def _pdf_extras(doc, workdir: Path, res: Result) -> None:
    """Reviewer comments / sticky notes, filled form fields and embedded file attachments."""
    notes, fields = [], []
    try:
        for page in doc:
            for a in page.annots() or []:
                info = a.info or {}
                content = (info.get("content") or "").strip()
                if content:
                    who = (info.get("title") or "").strip()
                    notes.append(f"[p{page.number + 1}] " + (f"{who}: " if who else "") + content)
            for w in page.widgets() or []:
                v = w.field_value
                if v in (None, "", "Off", False) or w.field_type_string in ("Signature",):
                    continue
                label = (w.field_label or w.field_name or "").strip()
                fields.append(f"{label}: {v}" if label else str(v))
    except Exception as e:
        res.flags.append(f"pdf_annotations_unreadable:{type(e).__name__}")
    if notes:
        res.sections.append({"name": "pdf_comments", "text": "\n".join(notes)})
        res.flags.append("has_comments")
    if fields:
        res.sections.append({"name": "pdf_form_fields", "text": "\n".join(dict.fromkeys(fields))})
    try:
        names = doc.embfile_names()
    except Exception:
        names = []
    if names:
        att = workdir / "pdf_attachments"
        att.mkdir(parents=True, exist_ok=True)
        for i, nm in enumerate(names):
            try:
                info = doc.embfile_info(nm)
                fname = safe_name(info.get("filename") or info.get("ufilename") or nm)
                target = att / f"{i:03d}_{fname}"
                target.write_bytes(doc.embfile_get(nm))
                res.children.append((fname, target))
            except Exception as e:
                res.flags.append(f"pdf_attachment_unreadable:{type(e).__name__}")
        res.flags.append("pdf_has_attachments")




def _extract_pdf_poppler(path: Path, workdir: Path, opts: Options) -> Result:
    """Fallback when PyMuPDF is missing or cannot open the file: Poppler text per page, Tesseract where empty."""
    t = ocr_config.get_tools()
    res = Result(detected="pdf")
    info, totext, toppm = t.poppler("pdfinfo"), t.poppler("pdftotext"), t.poppler("pdftoppm")
    if not (info and totext and toppm):
        res.status = "error"; res.flags.append("no_pymupdf_and_no_poppler")
        return res
    r = _run([info, str(path)])
    err = r.stderr.decode("utf-8", "replace")
    if "Incorrect password" in err:
        res.status = "encrypted"; res.flags.append("password_required")
        return res
    m = re.search(r"^Pages:\s+(\d+)", r.stdout.decode("utf-8", "replace"), re.M)
    n = int(m.group(1)) if m else 0
    if not n:
        res.status = "error"; res.flags.append("pdf_unreadable:" + err[-150:])
        return res
    texts = _run([totext, "-layout", "-enc", "UTF-8", str(path), "-"]).stdout.decode("utf-8", "replace").split("\f")
    ocr_done = 0
    for i in range(n):
        layer = texts[i] if i < len(texts) else ""
        if _nonspace(layer) >= opts.min_chars and _garbage_ratio(layer) <= 0.3:
            res.pages.append(Page(i + 1, layer, "text_layer:poppler")); continue
        if opts.max_ocr_pages is not None and ocr_done >= opts.max_ocr_pages:
            res.pages.append(Page(i + 1, layer, "ocr_skipped_limit")); res.flags.append("ocr_page_limit_reached"); continue
        stem = workdir / f"pp_{i + 1}"
        _run([toppm, "-r", str(opts.dpi), "-gray", "-png", "-f", str(i + 1), "-l", str(i + 1), "-singlefile", str(path), str(stem)])
        png = stem.with_suffix(".png")
        if not png.exists():
            res.pages.append(Page(i + 1, layer, "ocr_failed")); res.flags.append(f"render_failed_p{i + 1}"); continue
        from PIL import Image
        with Image.open(png) as im:
            im.load(); im.info["dpi"] = (opts.dpi, opts.dpi)
            text, conf = ocr_image(im, opts)
        png.unlink(missing_ok=True); ocr_done += 1
        res.pages.append(Page(i + 1, text if _nonspace(text) >= _nonspace(layer) else layer, "ocr:poppler", conf))
        if conf is not None and conf < 60:
            res.flags.append(f"low_ocr_confidence_p{i + 1}")
    return res
