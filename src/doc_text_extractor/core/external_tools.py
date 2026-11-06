"""Running external programs: the subprocess wrapper and LibreOffice conversion.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import logging
import os
import subprocess
from pathlib import Path
from typing import Optional


from .. import ocr_config
from .models import Options


logger = logging.getLogger(__name__)




def _run(cmd, timeout=600, env=None) -> subprocess.CompletedProcess:
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000                     # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, **kw)




def convert_with_soffice(path: Path, target: str, workdir: Path, opts: Options) -> Optional[Path]:
    exe = ocr_config.get_tools().soffice
    if not exe:
        return None
    outdir = workdir / f"lo_{target}"
    outdir.mkdir(exist_ok=True)
    profile = (workdir / "lo_profile").resolve().as_uri()
    try:
        _run([str(exe), f"-env:UserInstallation={profile}", "--headless", "--norestore",
              "--convert-to", target, "--outdir", str(outdir), str(path)], timeout=opts.soffice_timeout)
    except Exception as e:
        logger.debug("LibreOffice failed on %s: %s", path, e)
        return None
    out = outdir / (path.stem + "." + target.split(":")[0])
    return out if out.exists() else None
