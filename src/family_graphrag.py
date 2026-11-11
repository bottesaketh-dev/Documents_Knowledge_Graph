"""family_graphrag.py - Backward-compatible proxy to the modular family_graphrag package.

All implementation modules reside cleanly under `src.family_graphrag`:
  - `config`: Environment settings, model names, defaults
  - `models`: Doc, Segment, Chunk data containers
  - `schema`: Categories, roles, entity/relation types, Cypher queries
  - `prompts`: Extraction and QA prompt templates
  - `family`: Roster loading, alias resolution, kinship mapping
  - `llm`: Client lifecycle, chat completions, PII redaction
  - `parser`: Medium detection, document reading, chunking
  - `storage`: Vector store (ChromaDB) and graph store (Neo4j)
  - `indexer`: Two-pass extraction, state caching, build pipeline
  - `search`: Hybrid vector/graph QA, expiring deadlines, personal profile, estimator
  - `cli`: Command line argument parser and entrypoint
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ and project root are on sys.path when executed directly as a script
_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
_PROJECT_ROOT = _SRC_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Re-export key functions and objects from the flat family_graphrag package
from src.family_graphrag import (
    Chunk,
    Doc,
    Segment,
    ask,
    build,
    chroma_collection,
    embed,
    estimate,
    expiring,
    main,
    neo4j_driver,
    process_doc,
    profile,
    save_card,
)

__all__ = [
    "main",
    "build",
    "ask",
    "expiring",
    "profile",
    "estimate",
    "embed",
    "chroma_collection",
    "neo4j_driver",
    "Doc",
    "Segment",
    "Chunk",
    "process_doc",
    "save_card",
]

if __name__ == "__main__":
    main()
