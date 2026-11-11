"""Document indexing pipeline: card extraction, chunk entity/relation extraction, and dual ingestion."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.family_graphrag.config import (
    ALLOW_PARALLEL_LOCAL_LLM,
    DATE_HINT,
    DEFAULT_CURRENCY,
    LLM_BASE_URL,
    LLM_WORKERS,
    WORK_DIR,
)
from src.family_graphrag.family import (
    FAMILY_NAMES,
    _norm,
    display_name,
    family_roster,
    normalize_name,
    seed_family,
)
from src.family_graphrag.llm import (
    LLM_IS_LOCAL,
    chat,
    iso_date,
    llm_client,
    parse_json_loose,
    to_number,
)
from src.family_graphrag.models import Chunk, Doc
from src.family_graphrag.parser import (
    card_text,
    chunk_doc,
    email_people,
    fmt_loc,
    folder_chain,
    iter_docs,
)
from src.family_graphrag.prompts import CARD_PROMPT, EXTRACTION_PROMPT
from src.family_graphrag.schema import (
    DOC_CATEGORIES,
    ENTITY_TYPES,
    ORPHAN_CYPHER,
    PERSON_ROLES,
    RELATION_TYPES,
    SCHEMA_CYPHER,
    WRITE_CARD,
    WRITE_CHUNK,
    WRITE_DOC,
    WRITE_EMAIL,
)
from src.family_graphrag.storage import (
    chroma_collection,
    embed,
    neo4j_driver,
    purge_doc,
)


def clean_card(d: dict, doc: Doc) -> dict:
    """Validate and clean the LLM-extracted document card JSON."""
    cat = str(d.get("category") or "other").strip().lower().replace(" ", "_")
    if cat not in DOC_CATEGORIES:
        cat = "other"
    people, seen = [], set()
    for p in d.get("people") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        role = str(p.get("role") or "other").strip().lower()
        role = role if role in PERSON_ROLES else "other"
        key = normalize_name(name)
        if key and (key, role) not in seen:
            seen.add((key, role))
            people.append({"key": key, "name": display_name(key, name), "role": role})
    issuer_name = str(d.get("issuer") or "").strip()
    issuer = (
        [{"key": normalize_name(issuer_name), "name": issuer_name}]
        if _norm(issuer_name)
        else []
    )

    key_dates = []
    for kd in d.get("key_dates") or []:
        if isinstance(kd, dict) and iso_date(kd.get("date")):
            key_dates.append(
                (str(kd.get("label") or "date").strip()[:60], iso_date(kd["date"]))
            )
    expiry = iso_date(d.get("expiry_date"))
    if expiry and expiry not in [v for _, v in key_dates]:
        key_dates.append(("expires", expiry))

    props = {
        "category": cat,
        "title": str(d.get("title") or Path(doc.display_path).name).strip()[:150],
        "summary": str(d.get("summary") or "").strip()[:500] or None,
        "issuer": issuer_name or None,
        "doc_number": str(d.get("document_number") or "").strip()[:80] or None,
        "issue_date": iso_date(d.get("issue_date")),
        "expiry_date": expiry,
        "amount": to_number(d.get("amount")),
        "currency": str(d.get("currency") or "").strip().upper()[:8] or None,
        "date_labels": [lab for lab, _ in key_dates],
        "date_values": [val for _, val in key_dates],
    }
    return {"props": props, "people": people, "issuer": issuer}


def extract_card(doc: Doc) -> dict:
    """Run Pass 1 LLM extraction to generate a document card."""
    prompt = CARD_PROMPT.format(
        family=family_roster(),
        path=doc.display_path,
        medium=doc.medium,
        categories=", ".join(DOC_CATEGORIES),
        roles=", ".join(PERSON_ROLES),
        date_hint=DATE_HINT,
        currency=DEFAULT_CURRENCY,
        text=card_text(doc),
    )
    return clean_card(parse_json_loose(chat(prompt, json_mode=True)), doc)


def clean_extraction(data: dict) -> dict:
    """Validate, normalize names, and constrain entities/relations to schema."""
    ents, keep = [], {}
    for e in data.get("entities", []) or []:
        if not isinstance(e, dict):
            continue
        name, etype = str(e.get("name", "")).strip(), e.get("type")
        if not name or etype not in ENTITY_TYPES:
            continue
        key = normalize_name(name)
        if key and key not in keep:
            keep[key] = etype
            ents.append(
                {
                    "key": key,
                    "name": display_name(key, name),
                    "type": etype,
                    "description": str(e.get("description", ""))[:300],
                }
            )

    rels = []
    for r in data.get("relations", []) or []:
        if not isinstance(r, dict):
            continue
        s, t = normalize_name(str(r.get("source", ""))), normalize_name(
            str(r.get("target", ""))
        )
        for k in (s, t):  # family members may appear only inside a relation
            if k in FAMILY_NAMES and k not in keep:
                keep[k] = "Person"
                ents.append(
                    {
                        "key": k,
                        "name": FAMILY_NAMES[k],
                        "type": "Person",
                        "description": "",
                    }
                )
        rtype = (
            r.get("type")
            if r.get("type") in RELATION_TYPES
            else "RELATED_TO"
        )
        if s in keep and t in keep and s != t:
            rels.append(
                {
                    "source": s,
                    "target": t,
                    "type": rtype,
                    "evidence": str(r.get("evidence", ""))[:300],
                    "date": iso_date(r.get("date")),
                    "effective_date": iso_date(r.get("effective_date")),
                    "expiration_date": iso_date(r.get("expiration_date")),
                    "amount": to_number(r.get("amount")),
                }
            )
    return {"entities": ents, "relations": rels}


def extract(chunk: Chunk) -> dict:
    """Run Pass 2 LLM extraction over a chunk to discover entities and relations."""
    prompt = EXTRACTION_PROMPT.format(
        family=family_roster(),
        etypes=", ".join(ENTITY_TYPES),
        rtypes=", ".join(RELATION_TYPES),
        date_hint=DATE_HINT,
        path=chunk.display_path,
        medium=chunk.medium,
        locs=fmt_loc(chunk.medium, chunk.locs) or "whole file",
        text=chunk.text,
    )
    return clean_extraction(parse_json_loose(chat(prompt, json_mode=True)))


def state_path() -> Path:
    """Return path to state.jsonl file."""
    from src.family_graphrag import config

    return config.WORK_DIR / "state.jsonl"


def load_state() -> dict[str, dict]:
    """Load previously indexed file state indexed by doc_id."""
    st: dict[str, dict] = {}
    p = state_path()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("deleted"):
                st.pop(rec.get("id"), None)
            elif rec.get("id"):
                st[rec["id"]] = rec
    return st


def record_state(rec: dict):
    """Append indexed or deleted document state record to state.jsonl."""
    with state_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def save_card(doc: Doc, card: dict) -> Path:
    """Save extracted card JSON to <WORK_DIR>/cards/<rel_path>.json."""
    from src.family_graphrag import config

    out_file = (
        config.WORK_DIR / "cards" / f"{Path(doc.rel_path).with_suffix('.json')}"
    )
    out_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "doc_id": doc.doc_id,
        "rel_path": doc.rel_path,
        "display_path": doc.display_path,
        "medium": doc.medium,
        **card,
    }
    out_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out_file


def process_doc(doc: Doc, col, driver, workers: int) -> bool:
    """Returns True when every LLM call succeeded (only then is the file marked done)."""
    chunks = chunk_doc(doc)
    purge_doc(doc.doc_id, col, driver)
    if not chunks:
        return True
    try:
        card = extract_card(doc)
    except Exception as exc:
        print(f"  card failed {doc.rel_path}: {exc}", file=sys.stderr)
        return False

    save_card(doc, card)

    folders = folder_chain(doc.rel_path)
    with driver.session() as s:
        s.run(
            WRITE_DOC,
            doc_id=doc.doc_id,
            path=doc.rel_path,
            display=doc.display_path,
            name=Path(doc.display_path).name,
            medium=doc.medium,
            folder_pairs=[list(p) for p in zip(folders, folders[1:])],
            leaf=folders[-1:],
        )
        s.run(WRITE_CARD, doc_id=doc.doc_id, **card)

    col.upsert(
        ids=[c.chunk_id for c in chunks],
        embeddings=embed([c.text for c in chunks]),
        documents=[c.text for c in chunks],
        metadatas=[
            {
                "doc_id": c.doc_id,
                "path": c.display_path,
                "medium": c.medium,
                "locs": ",".join(c.locs),
                "category": card["props"]["category"],
                "title": card["props"]["title"],
            }
            for c in chunks
        ],
    )

    ok = True
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(extract, c): c for c in chunks}
            for fut in as_completed(futures):
                c = futures[fut]
                try:
                    kg = fut.result()
                except Exception as exc:  # keep going; file is retried on next build
                    print(f"  extraction failed {c.chunk_id}: {exc}", file=sys.stderr)
                    ok, kg = False, {"entities": [], "relations": []}
                with driver.session() as s:
                    s.run(
                        WRITE_CHUNK,
                        doc_id=doc.doc_id,
                        chunk_id=c.chunk_id,
                        text=c.text,
                        locs=c.locs,
                        display=c.display_path,
                        medium=c.medium,
                        entities=kg["entities"],
                        relations=kg["relations"],
                    )
    else:
        for c in chunks:
            try:
                kg = extract(c)
            except Exception as exc:  # keep going; file is retried on next build
                print(f"  extraction failed {c.chunk_id}: {exc}", file=sys.stderr)
                ok, kg = False, {"entities": [], "relations": []}
            with driver.session() as s:
                s.run(
                    WRITE_CHUNK,
                    doc_id=doc.doc_id,
                    chunk_id=c.chunk_id,
                    text=c.text,
                    locs=c.locs,
                    display=c.display_path,
                    medium=c.medium,
                    entities=kg["entities"],
                    relations=kg["relations"],
                )

    if doc.email:
        with driver.session() as s:
            s.run(
                WRITE_EMAIL,
                doc_id=doc.doc_id,
                subject=doc.email.get("Subject"),
                date=doc.email.get("Date"),
                people=email_people(doc.email),
            )
    return ok


def build(
    src: Path,
    only: str | None = None,
    limit: int | None = None,
    workers: int = LLM_WORKERS,
    prune: bool = False,
):
    """Build or update the dual ChromaDB and Neo4j knowledge graph incrementally."""
    from tqdm import tqdm

    if LLM_IS_LOCAL and workers > 1 and not ALLOW_PARALLEL_LOCAL_LLM:
        print(
            f"Notice: Local LLM detected at {LLM_BASE_URL}. Enforcing sequential execution (workers=1) "
            "to prevent GPU VRAM from spilling into system RAM and slowing down inference."
        )
        workers = 1

    from src.family_graphrag import config

    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    llm_client()  # fails fast on a non-local endpoint
    state = load_state()
    col, driver = chroma_collection(), neo4j_driver()
    with driver.session() as s:
        for q in SCHEMA_CYPHER:
            s.run(q)
        seed_family(s)

    pending, seen = [], set()
    for doc in iter_docs(src, only):
        seen.add(doc.doc_id)
        prev = state.get(doc.doc_id)
        if not prev or prev.get("hash") != doc.content_hash:
            pending.append(doc)
    if limit:
        pending = pending[:limit]
    print(
        f"{len(pending)} new or changed files to process ({len(state)} already indexed)."
    )

    failed = 0
    for doc in tqdm(pending, desc="documents"):
        if process_doc(doc, col, driver, workers):
            record_state(
                {
                    "id": doc.doc_id,
                    "path": doc.rel_path,
                    "hash": doc.content_hash,
                }
            )
        else:
            failed += 1

    if prune:
        if only:
            print("--prune skipped: it needs a full scan (run without --only).")
        else:
            gone = [i for i in state if i not in seen]
            for doc_id in gone:
                purge_doc(doc_id, col, driver)
                rec = state.get(doc_id, {})
                if rec.get("path"):
                    card_p = (
                        config.WORK_DIR
                        / "cards"
                        / f"{Path(rec['path']).with_suffix('.json')}"
                    )
                    if card_p.exists():
                        try:
                            card_p.unlink()
                        except OSError:
                            pass
                record_state({"id": doc_id, "deleted": True})
            print(f"pruned {len(gone)} files that no longer exist.")

    with driver.session() as s:
        s.run(ORPHAN_CYPHER)
    driver.close()
    if failed:
        print(
            f"{failed} files had LLM errors and will be retried on the next build."
        )

