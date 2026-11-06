"""Routes each file to the extractor for its detected format, plus the back-compatible
extract_text() API.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import logging
import tempfile
from pathlib import Path
from typing import Optional


from .. import ocr_config
from .detection import detect_format
from .external_tools import convert_with_soffice
from .models import EncryptedError, Options, Result
from .text_utils import _PAGE_BREAK
from ..extractors.damaged_zip_extractor import extract_damaged_zip
from ..extractors.doc_extractor import extract_doc
from ..extractors.docx_extractor import extract_docx
from ..extractors.eml_extractor import extract_eml
from ..extractors.html_extractor import extract_html
from ..extractors.image_extractor import extract_image
from ..extractors.mht_extractor import extract_mht
from ..extractors.msg_extractor import extract_msg_file
from ..extractors.odf_extractor import extract_odf
from ..extractors.pdf_extractor import extract_pdf
from ..extractors.ppt_extractor import extract_ppt
from ..extractors.pptx_extractor import extract_pptx
from ..extractors.rtf_extractor import extract_rtf
from ..extractors.text_extractor import extract_plain
from ..extractors.xls_extractor import extract_xls
from ..extractors.xlsb_extractor import extract_xlsb
from ..extractors.xlsx_extractor import extract_xlsx
from ..extractors.xml_extractor import extract_xml
from ..extractors.zip_extractor import extract_zip


logger = logging.getLogger(__name__)




_HANDLERS = {
    "pdf": extract_pdf, "docx": extract_docx, "doc": extract_doc, "xlsx": extract_xlsx, "xls": extract_xls,
    "xlsb": extract_xlsb, "pptx": extract_pptx, "ppt": extract_ppt, "rtf": extract_rtf, "html": extract_html,
    "mht": extract_mht, "odf": extract_odf, "xml": extract_xml, "xml_office": extract_xml, "text": extract_plain,
    "image": extract_image, "msg": extract_msg_file, "eml": extract_eml, "zip": extract_zip,
    "zip_unreadable": extract_damaged_zip,
}




def extract_document(path: Path, workdir: Optional[Path] = None, opts: Optional[Options] = None) -> Result:
    """Extract one file. `workdir` receives temporary files and attachments; the caller cleans it up."""
    path = Path(path)
    opts = opts or Options()
    own_tmp = workdir is None
    workdir = Path(tempfile.mkdtemp(prefix="lte_")) if own_tmp else Path(workdir)
    kind = detect_format(path)
    res: Result
    try:
        if kind.startswith("skip:"):
            res = Result(status="skipped", detected=kind, flags=[kind.split(":", 1)[1]])
        elif kind == "empty_file":
            res = Result(status="empty", detected=kind, flags=["zero_byte_file"])
        elif kind == "office_encrypted":
            res = Result(status="encrypted", detected=kind, flags=["password_required"])
        elif kind == "office_other":
            conv = convert_with_soffice(path, "pdf", workdir, opts) if ocr_config.get_tools().soffice else None
            if conv:
                res = extract_pdf(conv, workdir, opts)
                res.detected = "office_other"; res.flags.append("converted_with_libreoffice")
            else:
                res = Result(status="unsupported", detected=kind, flags=["needs_libreoffice"])
        elif kind in _HANDLERS:
            res = _HANDLERS[kind](path, workdir, opts)
        else:
            res = Result(status="unsupported", detected=kind, flags=[f"unsupported:{kind}"])
    except EncryptedError:
        res = Result(status="encrypted", detected=kind, flags=["password_required"])
    except Exception as e:
        logger.debug("extraction failed for %s", path, exc_info=True)
        res = Result(status="error", detected=kind, flags=[f"{type(e).__name__}: {str(e)[:200]}"])
    ext = path.suffix.lower().lstrip(".")
    family = {"docm": "docx", "dotx": "docx", "dotm": "docx", "dot": "doc", "xlsm": "xlsx", "xltx": "xlsx",
              "pptm": "pptx", "ppsx": "pptx", "pps": "ppt", "htm": "html", "mhtml": "mht", "tiff": "image", "tif": "image",
              "jpg": "image", "jpeg": "image", "png": "image", "bmp": "image", "gif": "image", "csv": "text", "txt": "text",
              "log": "text", "md": "text", "tsv": "text", "json": "text", "ics": "text", "vcf": "text", "ini": "text",
              "jfif": "image", "webp": "image", "heic": "image", "heif": "image",
              "odt": "odf", "ods": "odf", "odp": "odf", "eml": "eml", "msg": "msg"}.get(ext, ext)
    known_family = ext in {"pdf", "doc", "dot", "docx", "docm", "dotx", "dotm", "xls", "xlsx", "xlsm", "xlsb", "ppt", "pptx",
                           "pps", "pptm", "ppsx", "rtf", "htm", "html", "mht", "mhtml", "msg", "eml", "zip", "txt", "csv",
                           "xml", "tif", "tiff", "jpg", "jpeg", "png", "gif", "bmp", "odt", "ods", "odp"}
    if known_family and res.detected and not res.detected.startswith(("skip", "empty", "error", "office_encrypted")) and family != res.detected \
            and not (family == "xml" and res.detected == "xml_office"):
        res.flags.append(f"extension_mismatch:.{ext}_is_{res.detected}")
    return res.finish()




class ExtractionError(RuntimeError):
    pass




SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".docm", ".dotx", ".dotm", ".doc", ".dot", ".rtf", ".txt", ".log", ".md", ".csv", ".xml", ".json",
    ".htm", ".html", ".mht", ".mhtml", ".xlsx", ".xlsm", ".xls", ".xlsb", ".pptx", ".ppt", ".msg", ".eml", ".zip",
    ".odt", ".ods", ".odp", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic",
}




def extract_text(path: Path, max_chars: int = 80000) -> str:
    """Text of one file (attachments not included), pages joined with [PAGE BREAK]."""
    with tempfile.TemporaryDirectory(prefix="lte_") as tmp:
        res = extract_document(Path(path), Path(tmp))
    if res.status == "error":
        raise ExtractionError(f"Failed to extract text from {Path(path).name}: {'; '.join(res.flags)}")
    if res.status in ("unsupported", "skipped"):
        raise ExtractionError(f"Unsupported file: {Path(path).name} ({res.detected})")
    text = _PAGE_BREAK.join(p.text for p in res.pages if p.text.strip())
    if res.sections:
        text += "".join(f"\n\n[{s['name']}]\n{s['text']}" for s in res.sections)
    text = text.strip()
    return text[:max_chars] if max_chars and len(text) > max_chars else text