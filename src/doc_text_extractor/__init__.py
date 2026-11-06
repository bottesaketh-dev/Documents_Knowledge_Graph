"""Offline text extraction for a folder of documents.


    from doc_text_extractor import extract_document, Options
    result = extract_document(path, workdir, Options(ocr_lang="eng"))


See README.md for the folder layout and how to add a new document type.
"""
from .core.dispatcher import extract_document, extract_text, ExtractionError, SUPPORTED_EXTENSIONS  # noqa: F401
from .core.models import EXTRACTOR_VERSION, Options, Page, Result, EncryptedError  # noqa: F401