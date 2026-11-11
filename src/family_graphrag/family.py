"""Family roster management, name normalization, alias mapping, and kinship resolution."""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.family_graphrag.schema import RELATION_TYPES, SEED_FAMILY, SEED_FAMILY_RELS

FAMILY: list[dict] = []  # [{"key", "name", "relation_to_me", "aliases"}]
FAMILY_LINKS: list[list[str]] = []
FAMILY_NAMES: dict[str, str] = {}  # key -> canonical display name
ALIASES: dict[str, str] = {}  # normalized alias -> canonical key

_HONORIFICS = (
    r"mrs|mr|ms|miss|dr|shri|sri|smt|kumari|kum|km|master|baby|late|prof|"
    r"sir|madam|mx"
)
_ORG_SUFFIX = (
    r"private limited|pvt\.?\s*ltd|pvt|limited|ltd|inc|llc|llp|lp|corporation|corp|co"
)


def _norm(name: str) -> str:
    """'Smt. Sunita Rao' -> 'sunita rao'; 'LG Electronics India Pvt. Ltd.' -> 'lg electronics india'."""
    n = str(name).lower().strip()
    n = re.sub(r"^the\s+", "", n)
    n = re.sub(rf"^(?:(?:{_HONORIFICS})(?:\.\s*|\s+))+", "", n)
    for _ in range(2):
        n = re.sub(rf"[,\.]?\s+(?:{_ORG_SUFFIX})\.?$", "", n)
    n = re.sub(r"[^\w\s&-]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def normalize_name(name: str) -> str:
    """Entity-resolution key, with family aliases folded onto the canonical member."""
    n = _norm(name)
    return ALIASES.get(n, n)


def display_name(key: str, fallback: str) -> str:
    """Canonical display name for key if in family, else fallback."""
    return FAMILY_NAMES.get(key, fallback)


def load_family(path: str | None):
    """Load family member identities, aliases, and kinship connections from family.json."""
    if not path or not Path(path).exists():
        return
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for m in data.get("members", []) or []:
        if not m.get("name"):
            continue
        key = _norm(m["name"])
        aliases = [a for a in (m.get("aliases") or []) if str(a).strip()]
        ALIASES[key] = key
        for a in aliases:
            ALIASES[_norm(a)] = key
        FAMILY_NAMES[key] = m["name"]
        FAMILY.append(
            {
                "key": key,
                "name": m["name"],
                "aliases": aliases,
                "relation_to_me": (m.get("relation_to_me") or "").strip().lower(),
            }
        )
    FAMILY_LINKS.extend(data.get("relationships", []) or [])


def family_roster() -> str:
    """Generate prompt snippet describing known family members and their aliases."""
    if not FAMILY:
        return ""
    lines = [
        "Family members (when the text refers to one of them, use exactly this name):"
    ]
    for m in FAMILY:
        also = ", ".join(m["aliases"]) or "-"
        lines.append(
            f"- {m['name']} ({m['relation_to_me'] or 'family'}); also written as: {also}"
        )
    return "\n".join(lines) + "\n"


REL_WORDS = {
    "mother": ["mother", "mom", "mum", "mummy", "amma", "maa", "ammi"],
    "father": [
        "father",
        "dad",
        "daddy",
        "papa",
        "nanna",
        "appa",
        "abba",
        "pitaji",
    ],
    "spouse": ["wife", "husband", "spouse"],
    "wife": ["wife", "spouse"],
    "husband": ["husband", "spouse"],
    "son": ["son"],
    "daughter": ["daughter"],
    "child": ["child", "kid", "children", "kids"],
    "brother": ["brother", "bro", "anna", "bhaiya", "thammudu"],
    "sister": ["sister", "sis", "akka", "didi", "chelli"],
    "grandmother": [
        "grandmother",
        "grandma",
        "granny",
        "nani",
        "dadi",
        "ammamma",
        "nanamma",
        "avva",
    ],
    "grandfather": ["grandfather", "grandpa", "nana", "dada", "thatha", "tata"],
    "mother-in-law": ["mother-in-law", "mother in law", "atha", "saasu"],
    "father-in-law": ["father-in-law", "father in law", "mama", "sasur"],
}


def resolve_family(text: str) -> list[str]:
    """Family member keys referred to in a question, by name, alias or kinship word."""
    if not FAMILY:
        return []
    qn = re.sub(r"\s+", " ", re.sub(r"[^\w\s&-]", " ", text.lower())).strip()
    keys = []
    for alias, key in ALIASES.items():
        if alias and re.search(rf"\b{re.escape(alias)}\b", qn):
            keys.append(key)
    for m in FAMILY:
        rel = m["relation_to_me"]
        if not rel or rel == "self":
            continue
        for w in REL_WORDS.get(rel, [rel]):
            hit = re.search(rf"\b{re.escape(w)}\b", qn)
            if hit and not (
                rel in ("mother", "father")
                and "in-law" in qn[hit.end() : hit.end() + 8]
            ):
                keys.append(m["key"])
                break
    if not keys and re.search(r"\b(i|me|my|mine|myself)\b", qn):
        keys += [m["key"] for m in FAMILY if m["relation_to_me"] == "self"][:1]
    return list(dict.fromkeys(keys))


def seed_family(session):
    """Seed family nodes and kinship relationships into Neo4j."""
    if not FAMILY:
        return
    session.run(
        SEED_FAMILY,
        members=[
            {
                "key": m["key"],
                "name": m["name"],
                "rel": m["relation_to_me"],
            }
            for m in FAMILY
        ],
    )
    rels = [
        {"s": normalize_name(r[0]), "type": r[1], "t": normalize_name(r[2])}
        for r in FAMILY_LINKS
        if isinstance(r, list) and len(r) == 3 and r[1] in RELATION_TYPES
    ]
    if rels:
        session.run(SEED_FAMILY_RELS, rels=rels)

