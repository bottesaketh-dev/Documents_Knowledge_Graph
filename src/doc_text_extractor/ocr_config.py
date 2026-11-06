"""Find the local tools the extractor uses: Tesseract, Poppler, antiword and (optionally) LibreOffice.


Nothing here changes HOME or other global settings. Each tool is looked up in this order:
  1. an environment variable (TESSERACT_CMD, POPPLER_PATH, ANTIWORD_DIR, SOFFICE_PATH)
  2. a bundled copy under <project>/src/local_installations/ or <project>/src/
     (the two layouts your earlier code used)
  3. the system PATH
  4. the usual Windows install folders
Call describe() to see what was found; run_extraction.py prints it at start-up.
"""
from __future__ import annotations


import glob
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_PKG_DIR = Path(__file__).resolve().parent
_SEARCH_ROOTS = [p for p in {
    _PKG_DIR.parent / "src" / "local_installations",
    _PKG_DIR.parent / "src",
    _PKG_DIR.parent / "local_installations",
    _PKG_DIR.parents[1] / "src" / "local_installations" if len(_PKG_DIR.parents) > 1 else _PKG_DIR,
    _PKG_DIR.parents[1] / "local_installations" if len(_PKG_DIR.parents) > 1 else _PKG_DIR,
    _PKG_DIR.parent,
    _PKG_DIR.parents[1] if len(_PKG_DIR.parents) > 1 else _PKG_DIR,
} if p.exists()]
_PROGRAM_DIRS = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
_PROGRAM_DIRS = [Path(p) for p in _PROGRAM_DIRS if p]




def _first_existing(candidates) -> Optional[Path]:
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None




def _find_tesseract() -> Optional[Path]:
    env = os.environ.get("TESSERACT_CMD")
    cands = [env] if env else []
    for root in _SEARCH_ROOTS:
        cands.append(root / "Tesseract-OCR" / "tesseract.exe")
    which = shutil.which("tesseract")
    cands.append(which)
    for pd in _PROGRAM_DIRS:
        cands += [pd / "Tesseract-OCR" / "tesseract.exe", pd / "Programs" / "Tesseract-OCR" / "tesseract.exe"]
    return _first_existing(cands)




def _find_poppler_bin() -> Optional[Path]:
    env = os.environ.get("POPPLER_PATH")
    cands = [env] if env else []
    for root in _SEARCH_ROOTS:
        cands += [Path(p) for p in sorted(glob.glob(str(root / "poppler-*" / "Library" / "bin")), reverse=True)]
        cands += [Path(p) for p in sorted(glob.glob(str(root / "poppler-*" / "bin")), reverse=True)]
    which = shutil.which("pdftotext")
    if which:
        cands.append(Path(which).parent)
    for pd in _PROGRAM_DIRS:
        cands += [Path(p) for p in glob.glob(str(pd / "poppler*" / "Library" / "bin"))]
    for c in cands:
        if c and (Path(c) / ("pdftotext.exe" if os.name == "nt" else "pdftotext")).exists():
            return Path(c)
    return None




def _find_antiword() -> tuple[Optional[Path], Optional[Path]]:
    """Return (antiword executable, folder holding its mapping files such as UTF-8.txt)."""
    env = os.environ.get("ANTIWORD_DIR")
    exe = None
    for d in ([Path(env)] if env else []) + [r / "antiword" for r in _SEARCH_ROOTS]:
        for name in ("antiword.exe", "antiword"):
            if (d / name).exists():
                exe = d / name
                break
        if exe:
            break
    if exe is None and shutil.which("antiword"):
        exe = Path(shutil.which("antiword"))
    if exe is None:
        return None, None
    mapping = _first_existing([
        os.environ.get("ANTIWORDHOME"),
        exe.parent if (exe.parent / "UTF-8.txt").exists() else None,
        exe.parent / "Resources" if (exe.parent / "Resources" / "UTF-8.txt").exists() else None,
        "/usr/share/antiword" if Path("/usr/share/antiword/UTF-8.txt").exists() else None,
        Path.home() / ".antiword" if (Path.home() / ".antiword" / "UTF-8.txt").exists() else None,
    ])
    return exe, mapping




def _find_soffice() -> Optional[Path]:
    env = os.environ.get("SOFFICE_PATH")
    cands = [env] if env else []
    cands += [shutil.which("soffice"), shutil.which("libreoffice")]
    for pd in _PROGRAM_DIRS:
        cands.append(pd / "LibreOffice" / "program" / "soffice.exe")
    return _first_existing(cands)




@dataclass
class Tools:
    tesseract: Optional[Path] = None
    tessdata: Optional[Path] = None
    poppler_bin: Optional[Path] = None
    antiword: Optional[Path] = None
    antiword_home: Optional[Path] = None
    soffice: Optional[Path] = None
    versions: dict = field(default_factory=dict)


    def poppler(self, name: str) -> Optional[str]:
        if not self.poppler_bin:
            return None
        exe = self.poppler_bin / (name + (".exe" if os.name == "nt" else ""))
        return str(exe) if exe.exists() else None


    def antiword_env(self) -> dict:
        """Environment for the antiword subprocess only (it finds its mapping files through these)."""
        env = dict(os.environ)
        if self.antiword_home:
            env["ANTIWORDHOME"] = str(self.antiword_home)
            env["HOME"] = str(self.antiword_home.parent)
        return env




def _version(cmd) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=20)
        out = (r.stdout or r.stderr).decode("utf-8", "replace").strip().splitlines()
        return out[0] if out else ""
    except Exception:
        return ""




_TOOLS: Optional[Tools] = None




def get_tools() -> Tools:
    """Locate the tools once per process and wire Tesseract into pytesseract."""
    global _TOOLS
    if _TOOLS is not None:
        return _TOOLS
    t = Tools()
    t.tesseract = _find_tesseract()
    if t.tesseract:
        td = t.tesseract.parent / "tessdata"
        if td.exists() and not os.environ.get("TESSDATA_PREFIX"):
            os.environ["TESSDATA_PREFIX"] = str(td)
        t.tessdata = Path(os.environ["TESSDATA_PREFIX"]) if os.environ.get("TESSDATA_PREFIX") else None
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = str(t.tesseract)
        except ImportError:
            pass
    t.poppler_bin = _find_poppler_bin()
    t.antiword, t.antiword_home = _find_antiword()
    t.soffice = _find_soffice()
    _TOOLS = t
    return t




def describe() -> dict:
    t = get_tools()
    info = {
        "tesseract": str(t.tesseract) if t.tesseract else None,
        "tesseract_version": _version([str(t.tesseract), "--version"]) if t.tesseract else None,
        "tessdata": str(t.tessdata) if t.tessdata else None,
        "poppler_bin": str(t.poppler_bin) if t.poppler_bin else None,
        "pdftotext_version": _version([t.poppler("pdftotext"), "-v"]) if t.poppler("pdftotext") else None,
        "antiword": str(t.antiword) if t.antiword else None,
        "antiword_utf8_mapping": bool(t.antiword_home and (t.antiword_home / "UTF-8.txt").exists()),
        "libreoffice": str(t.soffice) if t.soffice else None,
    }
    for mod in ("pymupdf", "pytesseract", "olefile", "extract_msg", "openpyxl", "xlrd", "pyxlsb", "pptx", "bs4", "striprtf", "pillow_heif", "tnefparse"):
        try:
            m = __import__(mod)
            info[f"py:{mod}"] = getattr(m, "__version__", getattr(m, "VersionBind", "installed"))
        except Exception:
            info[f"py:{mod}"] = None
    return info




# Back-compat with the old module: importing it configures pytesseract.
get_tools()
TESSERACT_PATH = _TOOLS.tesseract if _TOOLS else None
POPPLER_PATH = _TOOLS.poppler_bin if _TOOLS else None