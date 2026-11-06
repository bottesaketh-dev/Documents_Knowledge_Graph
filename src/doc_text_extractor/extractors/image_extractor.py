"""Images (TIFF, JPEG, PNG, BMP, GIF, WebP, HEIC): OCR of every frame.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


from pathlib import Path


from ..core.models import Options, Page, Result
from ..core.ocr import ocr_image




def extract_image(path: Path, workdir: Path, opts: Options) -> Result:
    res = Result(detected="image")
    from PIL import Image, ImageSequence
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    with Image.open(path) as im:
        frames = getattr(im, "n_frames", 1)
        for i, frame in enumerate(ImageSequence.Iterator(im), 1):
            if opts.max_ocr_pages is not None and i > opts.max_ocr_pages:
                res.flags.append("ocr_page_limit_reached")
                break
            f = frame.copy()
            f.info["dpi"] = im.info.get("dpi", f.info.get("dpi"))
            if max(f.size) < 150:
                res.flags.append(f"image_too_small_p{i}")
                continue
            t, conf = ocr_image(f, opts)
            res.pages.append(Page(i, t, "ocr", conf, label=f"Frame {i}" if frames > 1 else None))
            if conf is not None and conf < 60:
                res.flags.append(f"low_ocr_confidence_p{i}")
    return res