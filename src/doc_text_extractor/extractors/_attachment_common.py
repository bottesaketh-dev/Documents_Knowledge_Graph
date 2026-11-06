"""Helpers shared by the email extractors.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


from pathlib import Path


from ..core.detection import IMAGE_EXTS
from ..core.models import Options
from ..core.ocr import is_page_scale




def _is_small_inline_image(name: str, data: bytes, opts: Options) -> bool:
    if Path(name).suffix.lower() not in IMAGE_EXTS:
        return False
    return not is_page_scale(data, opts.scan_image_min_dim)          # logos, signature images, icons
