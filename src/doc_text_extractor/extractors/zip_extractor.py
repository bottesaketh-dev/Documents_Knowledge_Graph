"""ZIP archives: every member becomes a child document.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import shutil
import zipfile
from pathlib import Path


from ..core.models import Options, Page, Result
from ..core.text_utils import safe_name




def extract_zip(path: Path, workdir: Path, opts: Options, max_total=4_000_000_000, max_members=5000) -> Result:
    res = Result(detected="zip")
    names = []
    with zipfile.ZipFile(path) as z:
        total = 0
        for k, info in enumerate(z.infolist()):
            if info.is_dir():
                continue
            names.append(info.filename)
            if k >= max_members:
                res.flags.append("zip_member_limit_reached"); break
            if info.flag_bits & 0x1:
                res.flags.append(f"encrypted_member:{info.filename}"); continue
            total += info.file_size
            if total > max_total:
                res.flags.append("zip_size_limit_reached"); break
            target = workdir / "zip" / f"{k}_{safe_name(Path(info.filename).name)}"
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            res.children.append((info.filename, target))
    res.pages.append(Page(1, "Archive contents:\n" + "\n".join(names), "zip_listing"))
    return res