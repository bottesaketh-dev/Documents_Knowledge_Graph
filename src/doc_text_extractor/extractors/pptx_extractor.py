"""PowerPoint PPTX / PPTM / PPSX: slide text, notes and OCR of image-only slides.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


from pathlib import Path


from ..core.models import Options, Page, Result
from ..core.ocr import is_page_scale, ocr_bytes




def extract_pptx(path: Path, workdir: Path, opts: Options) -> Result:
    res = Result(detected="pptx")
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(str(path))


    def shape_text(shape, out):
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for s in shape.shapes:
                shape_text(s, out)
            return
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            t = shape.text_frame.text
            if t.strip():
                out.append(t)
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                out.append(" | ".join(c.text for c in row.cells))
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            try:
                blob = shape.image.blob
                if is_page_scale(blob, opts.scan_image_min_dim):
                    t, _ = ocr_bytes(blob, opts)
                    if t.strip():
                        out.append(t); res.flags.append("embedded_scan_ocr")
            except Exception:
                pass


    for i, slide in enumerate(prs.slides, 1):
        out = []
        for shape in slide.shapes:
            shape_text(shape, out)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text
            if notes.strip():
                out.append("[Speaker notes]\n" + notes)
        res.pages.append(Page(i, "\n".join(out), "pptx", label=f"Slide {i}"))
    return res