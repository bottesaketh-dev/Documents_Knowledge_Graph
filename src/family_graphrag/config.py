"""Configuration and environment settings loader for Family GraphRAG."""

from __future__ import annotations

import os
import re
from pathlib import Path


def _load_env_file():
    for p in (
        Path("config/settings.env"),
        Path(".env"),
        Path(__file__).resolve().parent.parent.parent / "config" / "settings.env",
    ):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("\"'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            break


_load_env_file()

# LLM Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL",
    "https://api.groq.com/openai/v1" if GROQ_API_KEY else "http://localhost:11434/v1",
)
LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "openai/gpt-oss-120b" if GROQ_API_KEY else "qwen3.5:4b",
)
LLM_API_KEY = os.getenv("LLM_API_KEY") or GROQ_API_KEY or "ollama"
ALLOW_REMOTE_LLM = (
    os.getenv("ALLOW_REMOTE_LLM", "1" if GROQ_API_KEY else "0") == "1"
)
REDACT_IDS = os.getenv("REDACT_IDS", "auto")  # auto (= on for remote LLMs) | 1 | 0
LLM_WORKERS = int(os.getenv("LLM_WORKERS", "4" if GROQ_API_KEY else "1"))
ALLOW_PARALLEL_LOCAL_LLM = os.getenv("ALLOW_PARALLEL_LOCAL_LLM", "0") == "1"

# Embeddings Configuration
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
_BGE_EN = re.search(r"bge-(small|base|large)-en", EMBED_MODEL)
EMBED_QUERY_PREFIX = os.getenv(
    "EMBED_QUERY_PREFIX",
    "Represent this sentence for searching relevant passages: " if _BGE_EN else "",
)

# Neo4j & Storage Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "change-me")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", None)
WORK_DIR = Path(os.getenv("GRAPHRAG_WORK_DIR", "./graphrag_work"))
FAMILY_FILE = os.getenv("FAMILY_FILE", "family.json")
TEXT_EXTS = {
    e.strip().lower()
    for e in os.getenv("TEXT_EXTS", ".txt,.md").split(",")
    if e.strip()
}


def set_work_dir(path: Path | str):
    """Dynamically set the working directory for ChromaDB and state cache."""
    global WORK_DIR
    WORK_DIR = Path(path)


def set_neo4j_uri(uri: str):
    """Dynamically set the Neo4j Bolt URI."""
    global NEO4J_URI
    NEO4J_URI = uri


def set_neo4j_database(db: str | None):
    """Dynamically set the Neo4j target database name."""
    global NEO4J_DATABASE
    NEO4J_DATABASE = db

# Parsing & Prompt Hints
DATE_HINT = os.getenv(
    "DATE_HINT",
    "Numeric dates in these documents are usually day-first (DD/MM/YYYY), as in India.",
)
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "INR")

# Chunking & Card extraction sizes
CHUNK_CHARS = 2000  # personal documents are short and dense; smaller chunks retrieve better
CHUNK_OVERLAP = 300
CARD_HEAD = 6000  # the card sees the start of the document ...
CARD_TAIL = 1500  # ... and its end (signatures, validity, totals often sit there)

# Graph expansion hub cap
HUB_CAP = int(os.getenv("GRAPHRAG_HUB_CAP", "150"))

