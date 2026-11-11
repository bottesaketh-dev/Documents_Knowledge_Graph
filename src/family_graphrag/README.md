# family_graphrag

Offline, privacy-first Knowledge Graph and Vector RAG system tailored for personal and family archives.

Decomposed from the original monolithic script into a clean, flat, modular Python package.

---

## Package Layout

```
src/family_graphrag/
├── __init__.py          # Package front door: build, ask, expiring, profile, estimate
├── __main__.py          # Entrypoint for `python -m src.family_graphrag`
├── cli.py               # Argument parser and command routing
├── config.py            # Settings loader (config/settings.env) and global constants
├── models.py            # Dataclasses: Doc, Segment, Chunk
├── schema.py            # Categories, roles, entity types, Cypher queries
├── prompts.py           # Card prompt, extraction prompt, and answer prompt
├── family.py            # family.json roster loading, alias & kinship resolution
├── llm.py               # OpenAI/Ollama client, reasoning model support, PII redaction
├── parser.py            # Medium detection, document reading, chunking, loc formatting
├── storage.py           # ChromaDB vector store & Neo4j driver with silenced warnings
├── indexer.py           # Two-pass extraction, state tracking (state.jsonl), build pipeline
├── search.py            # ask() hybrid QA, expiring() deadlines, profile(), estimate()
└── README.md            # This documentation
```

---

## Architecture Overview

```
               [ Extracted Document Archive ]
                              │
                    ┌─────────▼─────────┐
                    │ parser.py         │ (split segments, detect medium)
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │ indexer.py        │
                    ├───────────────────┤
                    │ Pass 1: Card Extr │ ───► llm.py ──► <work-dir>/cards/<path>.json
                    │ Pass 2: Chunk KGs │ ───► llm.py
                    └─────┬───────┬─────┘
                          │       │
          dense vectors   │       │ structured entities & relations
                          │       │
        ┌─────────────────▼┐     ┌▼──────────────────┐
        │ storage.py       │     │ storage.py        │
        │ ChromaDB         │     │ Neo4j             │
        │ (Dense HNSW)     │     │ (Graph Nodes/Rel) │
        └────────┬─────────┘     └────────┬──────────┘
                 │                        │
                 └───────────┬────────────┘
                             │
                    ┌────────▼──────────┐
                    │ search.py         │
                    │ ask / profile /   │
                    │ expiring          │
                    └────────┬──────────┘
                             │
                    [ User Question / CLI ]
```

---

## Usage

All commands support both direct script and module execution:

```powershell
# 1. Preview vault token and chunk estimates
python -m src.family_graphrag estimate --src "data/extracted/text"

# 2. Build the knowledge graph (Neo4j + ChromaDB) - sequential (workers=1) by default to prevent VRAM overflow
python -m src.family_graphrag build --src "data/extracted/text"

# 3. Ask questions (hybrid retrieval with citations)
python -m src.family_graphrag ask "What are my marks in intermediate?"

# 4. Check upcoming deadlines and expirations
python -m src.family_graphrag expiring --days 90

# 5. Compile dossier for a family member
python -m src.family_graphrag profile "Saketh"
```
