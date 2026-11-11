"""Family GraphRAG: Offline knowledge graph and vector RAG for personal archives."""

from __future__ import annotations

from src.family_graphrag.cli import main
from src.family_graphrag.indexer import build, process_doc, save_card
from src.family_graphrag.models import Chunk, Doc, Segment
from src.family_graphrag.search import ask, estimate, expiring, profile
from src.family_graphrag.storage import chroma_collection, embed, neo4j_driver

__all__ = [
    "main",
    "build",
    "ask",
    "expiring",
    "profile",
    "estimate",
    "Doc",
    "Segment",
    "Chunk",
    "process_doc",
    "save_card",
    "chroma_collection",
    "embed",
    "neo4j_driver",
]
