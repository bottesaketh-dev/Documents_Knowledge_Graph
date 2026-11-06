"""OCR pipeline (Tesseract): page preparation, deskew, despeckle, orientation and retries.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import io
import re
from typing import Optional


from .. import ocr_config
from .models import Options




def _prepare_for_ocr(img):
    """Grayscale, upright (EXIF), square pixels (fax TIFFs are 204x98 dpi) and at least ~300 dpi."""
    from PIL import ImageOps, Image
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    dpi = img.info.get("dpi") or (0, 0)
    try:
        dx, dy = float(dpi[0] or 0), float(dpi[1] or 0)
    except (TypeError, ValueError):
        dx = dy = 0
    img = img.convert("L")
    w, h = img.size
    if dx and dy and abs(dx - dy) / max(dx, dy) > 0.2:          # anisotropic fax resolution
        if dx > dy:
            h = int(h * dx / dy); dy = dx
        else:
            w = int(w * dy / dx); dx = dy
        img = img.resize((w, h), Image.LANCZOS)
    eff = min(dx, dy) if dx and dy else 0
    if (eff and eff < 200) or (not eff and max(w, h) < 1200):
        factor = min(3.0, 300 / eff) if eff else min(3.0, 1700 / max(w, h))
        if factor > 1.1:
            img = img.resize((int(w * factor), int(h * factor)), Image.LANCZOS)
    return img




def _otsu(a) -> int:
    import numpy as np
    hist = np.bincount(a.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    w0 = np.cumsum(hist); w1 = total - w0
    m = np.cumsum(hist * np.arange(256))
    mu0 = m / np.maximum(w0, 1); mu1 = (m[-1] - m) / np.maximum(w1, 1)
    return int(np.argmax(w0 * w1 * (mu0 - mu1) ** 2))




def _skew_angle(img) -> float:
    """Angle (degrees, PIL rotate convention) that straightens text lines: projection-profile search over +-30 degrees."""
    try:
        import numpy as np
    except ImportError:
        return 0.0
    from PIL import Image
    small = img.copy()
    small.thumbnail((1100, 1100))
    a = np.asarray(small, dtype=np.uint8)
    ink = (a < min(_otsu(a), 200)).astype(np.uint8) * 255
    if ink.mean() < 0.5 or ink.mean() > 128:            # blank page or solid image
        return 0.0
    b = Image.fromarray(ink)


    def score(angle):
        rows = np.asarray(b.rotate(angle, resample=Image.NEAREST), dtype=np.float64).sum(axis=1)
        return float(np.sum(np.diff(rows) ** 2))


    base = score(0.0)
    coarse = {a_: score(a_) for a_ in np.arange(-30, 30.01, 1.0)}
    best = max(coarse, key=coarse.get)
    fine = {a_: score(a_) for a_ in np.arange(best - 1, best + 1.01, 0.1)}
    best = float(max(fine, key=fine.get))
    if abs(best) < 0.5 or abs(best) > 29.5 or fine[max(fine, key=fine.get)] < base * 1.15:
        return 0.0
    return round(best, 1)




def _despeckle(img):
    """Heavy salt-and-pepper noise makes Tesseract slow (tens of seconds a page) and blind; remove it first."""
    try:
        import numpy as np
    except ImportError:
        return img
    from PIL import ImageFilter
    clean = img.filter(ImageFilter.MedianFilter(3))
    changed = float(np.mean(np.abs(np.asarray(img, dtype=np.int16) - np.asarray(clean, dtype=np.int16)) > 96))
    return clean if changed > 0.03 else img                  # clean pages change < 1%, heavy speckle 6% and more




def _deskew(img):
    angle = _skew_angle(img)
    if angle:
        from PIL import Image
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    return img, angle




def _clean_variants(img):
    """Cleaner copies of a page for a second OCR attempt: speckle removed, and uneven/dark paper flattened."""
    from PIL import Image, ImageFilter
    yield "median", img.filter(ImageFilter.MedianFilter(3))
    try:
        import numpy as np
    except ImportError:
        return
    a = np.asarray(img, dtype=np.float32)
    bg = np.asarray(img.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(max(10, img.size[0] // 80))), dtype=np.float32)
    flat = np.clip(a / np.maximum(bg, 1) * 255, 0, 255).astype(np.uint8)
    t = _otsu(flat)
    yield "flatten", Image.fromarray(np.where(flat > t, 255, 0).astype(np.uint8)).filter(ImageFilter.MedianFilter(3))
    if t > 128:                                         # faint marks (show-through from the back, pencil, stains) remain: keep strong ink only
        yield "strong_ink", Image.fromarray(np.where(flat > 128, 255, 0).astype(np.uint8)).filter(ImageFilter.MedianFilter(3))




def _tesseract_data(img, opts: Options, psm: Optional[int] = None) -> tuple[str, Optional[float], int]:
    import pytesseract
    psm = psm or opts.ocr_psm
    d = pytesseract.image_to_data(img, lang=opts.ocr_lang, config=f"--oem 1 --psm {psm}",
                                  output_type=pytesseract.Output.DICT, timeout=opts.ocr_timeout)
    lines, cur, key, confs = [], [], None, []
    _tesseract_data.last_quality = 0.0
    for i, word in enumerate(d["text"]):
        if not word or not word.strip():
            continue
        k = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        if key is not None and k != key:
            lines.append(" ".join(cur)); cur = []
            if k[0] != key[0]:                                     # blank line between blocks only
                lines.append("")
        cur.append(word); key = k
        try:
            c = float(d["conf"][i])
            if c >= 0:
                confs.append(c)
        except (TypeError, ValueError):
            pass
    if cur:
        lines.append(" ".join(cur))
    conf = round(sum(confs) / len(confs), 1) if confs else None
    # quality used to choose between OCR attempts: confident words count, junk words count against
    _tesseract_data.last_quality = sum(1 for c in confs if c >= 80) - 0.5 * sum(1 for c in confs if c < 50)
    return "\n".join(lines), conf, len(confs)




def ocr_image(img, opts: Options) -> tuple[str, Optional[float]]:
    """OCR one PIL image.
    1. square pixels, upscale low-resolution images, remove heavy speckle, straighten skew (up to 30 degrees)
    2. low confidence: ask Tesseract's OSD for the page orientation (sideways / upside-down scans) and retry
    3. still poor: retry on cleaned copies (speckle removed, dark or show-through paper flattened), keep the best"""
    if not ocr_config.get_tools().tesseract:
        raise RuntimeError("Tesseract not found (set TESSERACT_CMD)")
    img = _prepare_for_ocr(img)
    img = _despeckle(img)
    img, _angle = _deskew(img)
    text, conf, nwords = _tesseract_data(img, opts)
    quality = _tesseract_data.last_quality
    if conf is None or conf < 60 or nwords < 5:
        try:
            import pytesseract
            osd = pytesseract.image_to_osd(img, config="--psm 0 -c min_characters_to_try=5", timeout=opts.ocr_timeout)
            m = re.search(r"Rotate:\s+(\d+)", osd)
            rot = int(m.group(1)) if m else 0
            if rot:
                turned, _ = _deskew(img.rotate(-rot, expand=True))
                t2, c2, n2 = _tesseract_data(turned, opts)
                q2 = _tesseract_data.last_quality
                if (c2 or 0) > (conf or 0) and q2 >= quality:
                    img, text, conf, nwords, quality = turned, t2, c2, n2, q2
        except Exception:
            pass                                    # OSD needs enough text; failures are normal
    if conf is None or conf < 70:                              # poor page: try cleaned-up copies, keep the best
        best = (quality, text, conf, nwords)
        for _name, variant in _clean_variants(img):
            t2, c2, n2 = _tesseract_data(variant, opts)
            q = _tesseract_data.last_quality
            if q > best[0] + max(5.0, abs(best[0]) * 0.1):
                best = (q, t2, c2, n2)
            if (c2 or 0) >= 85:
                break
        _, text, conf, nwords = best
    return text, conf




def ocr_bytes(data: bytes, opts: Options) -> tuple[str, Optional[float]]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return ocr_image(im, opts)




def is_page_scale(data: bytes, min_dim: int) -> bool:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return max(im.size) >= min_dim
    except Exception:
        return False                                 # EMF/WMF and unreadable blobs: Tesseract cannot read them anyway
