"""Offline text extraction for every common document file type.


This module is now a thin front door to the package. The code lives in:
    core/        shared pieces: models (Options, Page, Result), text helpers, format detection,
                 OCR, external tools (LibreOffice), and the dispatcher (extract_document)
    extractors/  one module per document type: pdf, docx, doc, xlsx, xls, xlsb, pptx, ppt, rtf,
                 html, mht, odf, xml, text, image, msg, eml, zip, damaged zip


Every name the old single-file extractor.py offered is still importable from here, so existing code
such as run_extraction.py (``extractor.extract_document``, ``extractor.Options`` ...) works unchanged.


Main entry point:
    extract_document(path, workdir, options) -> Result
        Result.pages     list of Page(number, text, method, ocr_conf, label)
        Result.sections  extra text that is not on a page: headers/footers, footnotes, comments
        Result.children  (name, temp_path) for email attachments, zip members and embedded files;
                         the caller extracts each one as its own document
        Result.status    ok | empty | encrypted | unsupported | skipped | error
        Result.flags     anything a reviewer should know (OCR confidence, tracked changes, ...)


Back-compatible wrapper:
    extract_text(path, max_chars=80000) -> str     pages joined with [PAGE BREAK], like the old module
"""
from __future__ import annotations


import logging


from .core.models import (  # noqa: F401
    EXTRACTOR_VERSION, Options, Page, Result, EncryptedError,
)
from .core.text_utils import (  # noqa: F401
    _PLACEHOLDER_RE, _PAGE_BREAK, _nonspace, normalize, _has_meaningful_text, _garbage_ratio, _STOPWORDS,
    _alpha_ratio, _english_score, decode_bytes, safe_name,
)
from .core.detection import (  # noqa: F401
    JUNK_NAMES, SHORTCUTS, MEDIA_EXTS, NON_DOCUMENT_EXTS, UNSUPPORTED_ARCHIVES, TEXT_EXTS, IMAGE_EXTS,
    skip_reason_by_name, detect_format, _ole_kind, _zip_kind,
)
from .core.ocr import (  # noqa: F401
    _prepare_for_ocr, _otsu, _skew_angle, _despeckle, _deskew, _clean_variants, _tesseract_data, ocr_image,
    ocr_bytes, is_page_scale,
)
from .core.external_tools import (  # noqa: F401
    _run, convert_with_soffice,
)
from .extractors._attachment_common import (  # noqa: F401
    _is_small_inline_image,
)
from .extractors._spreadsheet_common import (  # noqa: F401
    _cell, _sheet_page,
)
from .extractors.damaged_zip_extractor import (  # noqa: F401
    _salvage_zip, _xml_text_fallback, extract_damaged_zip,
)
from .extractors.doc_extractor import (  # noqa: F401
    _antiword, _word97_text, _doc_image_blobs, _carve_images, extract_doc,
)
from .extractors.docx_extractor import (  # noqa: F401
    _NS, W, A, R, V, MC, _rels, _roman, _letters, _Numbering, _DocxWalker, extract_docx,
)
from .extractors.eml_extractor import (  # noqa: F401
    extract_eml, _tnef_children,
)
from .extractors.html_extractor import (  # noqa: F401
    html_to_text, extract_html,
)
from .extractors.image_extractor import (  # noqa: F401
    extract_image,
)
from .extractors.mht_extractor import (  # noqa: F401
    mht_to_text, extract_mht,
)
from .extractors.msg_extractor import (  # noqa: F401
    extract_msg_file,
)
from .extractors.odf_extractor import (  # noqa: F401
    extract_odf,
)
from .extractors.pdf_extractor import (  # noqa: F401
    _page_digital_text, _image_coverage, _merge_ocr_and_layer, extract_pdf, _pdf_extras,
    _extract_pdf_poppler,
)
from .extractors.ppt_extractor import (  # noqa: F401
    _ppt_text_records, extract_ppt,
)
from .extractors.pptx_extractor import (  # noqa: F401
    extract_pptx,
)
from .extractors.rtf_extractor import (  # noqa: F401
    rtf_to_text_bytes, _rtf_pictures, extract_rtf,
)
from .extractors.text_extractor import (  # noqa: F401
    extract_plain,
)
from .extractors.xls_extractor import (  # noqa: F401
    extract_xls,
)
from .extractors.xlsb_extractor import (  # noqa: F401
    extract_xlsb,
)
from .extractors.xlsx_extractor import (  # noqa: F401
    extract_xlsx,
)
from .extractors.xml_extractor import (  # noqa: F401
    WML2003, SS2003, extract_xml,
)
from .extractors.zip_extractor import (  # noqa: F401
    extract_zip,
)
from .core.dispatcher import (  # noqa: F401
    _HANDLERS, extract_document, ExtractionError, SUPPORTED_EXTENSIONS, extract_text,
)


logger = logging.getLogger(__name__)