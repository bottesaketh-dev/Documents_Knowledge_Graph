# doc_text_extractor


Offline text extraction for a folder of documents: digital and scanned PDFs, Word, Excel, PowerPoint,
RTF, HTML/MHT, OpenDocument, XML, plain text, images (photos of certificates, receipts, ID cards),
Outlook and .eml emails, and zip archives. Scanned pages and images are OCR'd with Tesseract.
Nothing leaves your computer.


## Folder layout


```
doc_text_extractor/
├── __init__.py              package front door: extract_document, extract_text, Options, Result
├── extractor.py             one place that re-exports every function (handy for older code)
├── run_extraction.py        runner: walks a folder, resumes, de-duplicates, writes the outputs
├── ocr_config.py            finds Tesseract, Poppler, antiword, LibreOffice (incl. local_installations)
├── README.md
│
├── core/                    shared building blocks
│   ├── models.py            EXTRACTOR_VERSION, Options, Page, Result, EncryptedError
│   ├── text_utils.py        normalize, decode_bytes, safe_name, text-quality checks
│   ├── detection.py         detect_format (by content), skip_reason_by_name, extension lists
│   ├── ocr.py               ocr_image / ocr_bytes: deskew, despeckle, orientation, retries
│   ├── external_tools.py    subprocess wrapper, LibreOffice conversion
│   └── dispatcher.py        format -> extractor table, extract_document, extract_text
│
└── extractors/              one module per document type
    ├── pdf_extractor.py            .pdf (PyMuPDF, Poppler fallback, OCR of scanned pages)
    ├── docx_extractor.py           .docx .docm .dotx .dotm
    ├── doc_extractor.py            .doc .dot (Word 97-2003)
    ├── xlsx_extractor.py           .xlsx .xlsm .xltx
    ├── xls_extractor.py            .xls (Excel 97-2003)
    ├── xlsb_extractor.py           .xlsb
    ├── pptx_extractor.py           .pptx .pptm .ppsx
    ├── ppt_extractor.py            .ppt .pps (PowerPoint 97-2003)
    ├── rtf_extractor.py            .rtf
    ├── html_extractor.py           .html .htm
    ├── mht_extractor.py            .mht .mhtml
    ├── odf_extractor.py            .odt .ods .odp
    ├── xml_extractor.py            .xml (incl. Word/Excel 2003 XML)
    ├── text_extractor.py           .txt .csv .log .md .json ...
    ├── image_extractor.py          .tif .jpg .png .bmp .gif .webp .heic
    ├── msg_extractor.py            Outlook .msg
    ├── eml_extractor.py            .eml (and winmail.dat / TNEF attachments)
    ├── zip_extractor.py            .zip
    ├── damaged_zip_extractor.py    damaged Office / zip files (repair, then extract what survives)
    ├── _spreadsheet_common.py      shared by the three Excel extractors
    └── _attachment_common.py       shared by the two email extractors
```


Dependencies only point one way: `extractors/*` use `core/*`, and `core/dispatcher.py` uses the
extractors. A few extractors reuse another's helper (Word .doc falls back to the .docx reader, .ppt to
.pptx, emails and .docx use the HTML and RTF converters).


`ocr_config.py` sits at the package root and looks for bundled tools in `<project>/src/local_installations/`,
`<project>/src/` and the other folders listed at its top, then on PATH.


## Setup


```
pip install pymupdf pytesseract pillow numpy olefile extract-msg openpyxl xlrd pyxlsb python-pptx beautifulsoup4 striprtf pillow-heif tnefparse
```


Install Tesseract and Poppler (or keep them in `local_installations`). For documents in Hindi, Telugu or
other languages, install those Tesseract language packs and pass them with `--lang`, e.g. `--lang eng+hin+tel`.


## Running


```
python -m doc_text_extractor.run_extraction "D:\My Documents" "D:\doc_text" --workers 4
python doc_text_extractor\run_extraction.py "D:\My Documents" "D:\doc_text" --inventory-only
```


The output folder gets `text\` (one page-marked .txt per document, same folders as the source),
`corpus.jsonl`, `inventory.csv`, `summary.json`, `run_manifest.json` and `extract.log`. Running the same
command again only processes new or changed files, and exact duplicates are extracted once.


Audio and video files are skipped; they need a transcription tool (e.g. Whisper) instead.


In your own code:


```python
from doc_text_extractor import extract_document, Options
res = extract_document("D:/My Documents/Insurance/policy.pdf", opts=Options(ocr_lang="eng"))
print(res.status, [p.text for p in res.pages])
```


## Adding or changing a document type


1. Create `extractors/<type>_extractor.py` with a function `extract_<type>(path, workdir, opts) -> Result`.
   Build pages with `Page(number, text, method)`. Put attachments or embedded files in `res.children` as
   `(name, path)`; the runner extracts each child as its own document.
2. Register it in `_HANDLERS` in `core/dispatcher.py` under the format name.
3. If the format can't be told apart by content yet, teach `core/detection.py` to return that name.
4. Raise `EXTRACTOR_VERSION` in `core/models.py` when a change alters extracted text.


To change how one type is read, edit only its module. For example, PDF rules live in
`extractors/pdf_extractor.py` and OCR tuning lives in `core/ocr.py`.