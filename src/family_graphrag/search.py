"""Query, retrieval, and dossier search interfaces for Family GraphRAG."""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path

from src.family_graphrag.config import (
    CARD_HEAD,
    CARD_TAIL,
    EMBED_QUERY_PREFIX,
    HUB_CAP,
    TEXT_EXTS,
)
from src.family_graphrag.family import (
    FAMILY_NAMES,
    family_roster,
    normalize_name,
    resolve_family,
)
from src.family_graphrag.llm import chat
from src.family_graphrag.parser import chunk_doc, fmt_loc, iter_docs
from src.family_graphrag.prompts import ANSWER_PROMPT
from src.family_graphrag.schema import (
    DOC_INFO_CYPHER,
    EXTRA_CHUNKS_CYPHER,
    FACTS_CYPHER,
    PERSON_DOCS_CYPHER,
    PROFILE_RELS,
)
from src.family_graphrag.storage import chroma_collection, embed, neo4j_driver

NOT_DEADLINE = re.compile(
    r"\b(birth|born|issued?|purchased?|bill|invoice|admi(ssion|tted)|"
    r"discharged?|marriage|dated)\b",
    re.I,
)
IS_DEADLINE = re.compile(
    r"due|end|expir|renew|valid|matur|until|last|follow", re.I
)


def estimate(src: Path, only: str | None = None, limit: int | None = None):
    """Estimate chunk counts, LLM calls, and input/output tokens before spending time."""
    n_docs = n_chunks = n_chars = 0
    by_medium: dict[str, int] = {}
    by_ext: dict[str, int] = {}
    for doc in iter_docs(src, only):
        chunks = chunk_doc(doc)
        n_docs += 1
        n_chunks += len(chunks)
        n_chars += sum(len(c.text) for c in chunks)
        by_medium[doc.medium] = by_medium.get(doc.medium, 0) + 1
        dp = Path(doc.display_path)
        ext = (
            (Path(dp.stem).suffix or dp.suffix).lower()
            if dp.suffix.lower() in TEXT_EXTS
            else dp.suffix.lower()
        )
        ext = ext or "(none)"
        by_ext[ext] = by_ext.get(ext, 0) + 1
        if limit and n_docs >= limit:
            break
    calls = n_docs + n_chunks  # 1 card per doc + 1 per chunk
    card_chars = min(n_chars, n_docs * (CARD_HEAD + CARD_TAIL))
    tokens_in = (n_chars + card_chars) / 4 + calls * 900  # text + prompt overhead
    tokens_out = n_docs * 300 + n_chunks * 400
    print(f"documents: {n_docs}   by medium: {by_medium}")
    print(
        f"original file types: {dict(sorted(by_ext.items(), key=lambda x: -x[1]))}"
    )
    print(
        f"chunks: {n_chunks}   LLM calls: {calls} ({n_docs} cards + {n_chunks} extractions)"
    )
    print(f"approx input tokens:  {tokens_in/1e6:.2f}M")
    print(f"approx output tokens: {tokens_out/1e6:.2f}M")
    print(
        "A 14B model on a decent GPU manages very roughly 5-15 s per call; time a --limit 20 "
        "pilot to get your own rate."
    )


def ask(
    question: str,
    k: int = 6,
    extra: int = 6,
    max_facts: int = 40,
    category: str | None = None,
    show: bool = False,
):
    """Answer family questions by retrieving seed vector chunks, expanding graph facts, and generating cited answers."""
    keys = resolve_family(question)
    names = [FAMILY_NAMES[x] for x in keys if x in FAMILY_NAMES]
    query = question + (" (" + ", ".join(names) + ")" if names else "")

    col = chroma_collection()
    if col.count() == 0:
        sys.exit("The index is empty - run `build` first.")
    hits = col.query(
        query_embeddings=embed([EMBED_QUERY_PREFIX + query]),
        n_results=k,
        where={"category": category} if category else None,
    )
    seeds = [
        {
            "id": i,
            "text": t,
            "path": m["path"],
            "locs": m.get("locs", ""),
            "medium": m.get("medium", "text"),
        }
        for i, t, m in zip(
            hits["ids"][0], hits["documents"][0], hits["metadatas"][0]
        )
    ]
    seed_ids = [c["id"] for c in seeds]

    driver = neo4j_driver()
    with driver.session() as s:
        facts = [
            r.data()
            for r in s.run(FACTS_CYPHER, seed_ids=seed_ids, max_facts=max_facts)
        ]
        extra_chunks = [
            {**r.data(), "locs": ",".join(r["locs"] or [])}
            for r in s.run(
                EXTRA_CHUNKS_CYPHER,
                seed_ids=seed_ids,
                hub_cap=HUB_CAP,
                max_extra=extra,
            )
        ]
        sources = seeds + extra_chunks
        info = {
            r["id"]: r.data()
            for r in s.run(DOC_INFO_CYPHER, ids=[c["id"] for c in sources])
        }
        person_docs = (
            [r.data() for r in s.run(PERSON_DOCS_CYPHER, keys=keys)]
            if keys
            else []
        )
    driver.close()

    def head(i, c):
        d = info.get(c["id"], {})
        loc = fmt_loc(c["medium"], c["locs"])
        meta = f" - {d['title']} [{d['category']}]" if d.get("title") else ""
        exp = f" (expires {d['expires']})" if d.get("expires") else ""
        return f"[{i}] {c['path']}{', ' + loc if loc else ''}{meta}{exp}"

    src_txt = "\n\n".join(
        f"{head(i, c)}\n{c['text']}" for i, c in enumerate(sources, 1)
    )

    def fact_line(f):
        extras = [
            x
            for x in (
                f.get("dt") and f"on {f['dt']}",
                f.get("until") and f"until {f['until']}",
                f.get("amount") is not None and f"amount {f['amount']:g}",
            )
            if x
        ]
        return f"- {f['s']} --{f['rt']}--> {f['o']}{' (' + ', '.join(extras) + ')' if extras else ''}  (\"{f['ev']}\")"

    fact_txt = "\n".join(fact_line(f) for f in facts) or "(none)"
    pdoc_txt = (
        "\n".join(
            f"- {d['person']} ({d['role']}): {d['title']} [{d['category']}]"
            f"{' expires ' + d['expires'] if d.get('expires') else ''}"
            for d in person_docs
        )
        or "(none)"
    )
    prompt = ANSWER_PROMPT.format(
        today=date.today().isoformat(),
        family=family_roster(),
        person_docs=pdoc_txt,
        facts=fact_txt,
        sources=src_txt,
        question=question,
    )
    ans = chat(prompt)
    if ans.strip():
        print(ans)
    else:
        print(
            "[No response generated by the local LLM. Check Ollama logs or try with fewer chunks / a different model.]"
        )
    print("\nSources:")
    for i, c in enumerate(sources, 1):
        print("  " + head(i, c))
    if show:
        print("\nGraph facts used:\n" + fact_txt)
        print("\nPeople's documents used:\n" + pdoc_txt)


def expiring(days: int = 90, past_days: int = 30):
    """Find and display upcoming renewal dates, deadlines, and warranties in Neo4j."""
    today = date.today()
    lo, hi = today - timedelta(days=past_days), today + timedelta(days=days)
    driver = neo4j_driver()
    with driver.session() as s:
        rows = [
            r.data()
            for r in s.run("""
            MATCH (d:Document) WHERE size(coalesce(d.date_values, [])) > 0
            OPTIONAL MATCH (p:Entity)-[:SUBJECT_OF]->(d)
            RETURN d.title AS title, d.category AS category, d.display AS path,
                   d.date_labels AS labels, d.date_values AS vals,
                   collect(DISTINCT p.name) AS people""")
        ]
    driver.close()
    items = []
    for r in rows:
        for label, val in zip(r["labels"] or [], r["vals"] or []):
            if NOT_DEADLINE.search(label) and not IS_DEADLINE.search(label):
                continue
            try:
                when = date.fromisoformat(val)
            except (TypeError, ValueError):
                continue
            if lo <= when <= hi:
                items.append((when, label, r))
    if not items:
        print(f"Nothing due between {lo} and {hi}.")
        return
    for when, label, r in sorted(items, key=lambda x: x[0]):
        delta = (when - today).days
        rel = (
            "today"
            if delta == 0
            else (f"in {delta} days" if delta > 0 else f"{-delta} days ago")
        )
        who = ", ".join(x for x in r["people"] if x)
        print(
            f"{when}  ({rel:>12})  {label}: {r['title']} [{r['category']}]"
            f"{'  - ' + who if who else ''}\n{'':28}{r['path']}"
        )


def profile(name: str):
    """Compile and display a complete dossier for a family member from Neo4j."""
    keys = resolve_family(name) or [normalize_name(name)]
    driver = neo4j_driver()
    with driver.session() as s:
        people = [
            r.data()
            for r in s.run(
                "MATCH (p:Entity) WHERE p.key IN $keys RETURN p.key AS key, p.name AS name",
                keys=keys,
            )
        ]
        if not people:
            print(f"No one called '{name}' in the graph.")
            driver.close()
            return
        for p in people:
            print(f"\n=== {p['name']} ===")
            docs = [
                r.data()
                for r in s.run(
                    """
                MATCH (:Entity {key: $key})-[s:SUBJECT_OF]->(d:Document)
                RETURN d.category AS category, d.title AS title, s.role AS role,
                       d.doc_number AS number, d.expiry_date AS expires, d.display AS path
                ORDER BY category, title""",
                    key=p["key"],
                )
            ]
            current = None
            for d in docs:
                if d["category"] != current:
                    current = d["category"]
                    print(f"\n  {current}")
                bits = [
                    d["role"],
                    d["number"] and f"no. {d['number']}",
                    d["expires"] and f"expires {d['expires']}",
                ]
                print(
                    f"    - {d['title']} ({', '.join(b for b in bits if b)})\n      {d['path']}"
                )
            facts = [
                r.data()
                for r in s.run(
                    """
                MATCH (p:Entity {key: $key})-[r:REL]-(q:Entity) WHERE r.type IN $types
                RETURN DISTINCT r.type AS rt, startNode(r) = p AS outgoing, q.name AS other
                ORDER BY rt, other LIMIT 80""",
                    key=p["key"],
                    types=PROFILE_RELS,
                )
            ]
            if facts:
                print("\n  key facts")
                for f in facts:
                    arrow = (
                        f"--{f['rt']}-->"
                        if f["outgoing"]
                        else f"<--{f['rt']}--"
                    )
                    print(f"    {p['name']} {arrow} {f['other']}")
    driver.close()

