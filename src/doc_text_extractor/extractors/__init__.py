"""One module per document type. Each exposes an extract_<type>(path, workdir, opts) -> Result function,
registered by format name in core/dispatcher.py."""