"""Vector store (ChromaDB) and Knowledge Graph (Neo4j) database interfaces."""

from __future__ import annotations

from src.family_graphrag.config import (
    EMBED_MODEL,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    WORK_DIR,
)
from src.family_graphrag.schema import ORPHAN_CYPHER, PURGE_CYPHER, SCHEMA_CYPHER

_embedder = None


def embed(texts: list[str]) -> list[list[float]]:
    """Compute normalized dense embeddings using SentenceTransformer."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        try:
            # Prefer local cached weights for seamless offline operation and faster startup
            _embedder = SentenceTransformer(
                EMBED_MODEL, device="cpu", local_files_only=True
            )
        except Exception:
            _embedder = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _embedder.encode(texts, normalize_embeddings=True, batch_size=32).tolist()


def chroma_collection():
    """Get or create the ChromaDB vector collection for family chunks."""
    import chromadb
    from src.family_graphrag import config

    client = chromadb.PersistentClient(path=str(config.WORK_DIR / "chroma"))
    return client.get_or_create_collection(
        "family_chunks", metadata={"hnsw:space": "cosine"}
    )


def neo4j_driver():
    """Create a Neo4j driver with silenced GQL status warnings."""
    from neo4j import GraphDatabase
    from src.family_graphrag import config

    driver = GraphDatabase.driver(
        config.NEO4J_URI,
        auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
        notifications_min_severity="OFF",
    )
    if config.NEO4J_DATABASE:
        orig_session = driver.session

        def session(**kwargs):
            if "database" not in kwargs and config.NEO4J_DATABASE:
                kwargs["database"] = config.NEO4J_DATABASE
            return orig_session(**kwargs)

        driver.session = session
    return driver


def init_schema(driver):
    """Ensure all required uniqueness constraints and indexes exist in Neo4j."""
    with driver.session() as s:
        for q in SCHEMA_CYPHER:
            s.run(q)


def purge_doc(doc_id: str, col, driver):
    """Remove a document's chunks, vectors and extracted edges (before re-processing or on delete)."""
    col.delete(where={"doc_id": doc_id})
    with driver.session() as s:
        for q in PURGE_CYPHER:
            s.run(q, doc_id=doc_id, prefix=f"{doc_id}-")


def clean_orphans(driver):
    """Clean up orphan entity nodes not tied to any family or document."""
    with driver.session() as s:
        s.run(ORPHAN_CYPHER)

