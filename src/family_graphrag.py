"""
family_graphrag.py - GraphRAG over a family's personal document archive, fully local.


Re-targeted from legal_graphrag.py at a folder of text files holding a family's
personal records: birth/marriage certificates, government IDs, medical records and
prescriptions, educational certificates, invoices, receipts, warranty cards, product
manuals and service guidelines, health/life/vehicle insurance, legal papers, bonds and
stamp papers, property and bank papers - plus written descriptions of photos and videos.


What it builds
    Vectors (Chroma): every chunk, tagged with category / medium, for semantic search.
    Graph (Neo4j):
      (:Folder)-[:CONTAINS]->(:Document)-[:HAS_CHUNK]->(:Chunk)
      (:Document)-[:IN_CATEGORY]->(:Category)            one "card" per document
      (:Entity {type:'Person'})-[:SUBJECT_OF {role}]->(:Document)
      (:Document)-[:ISSUED_BY]->(:Entity {type:'Organization'})
      (:Entity)-[:MENTIONED_IN]->(:Chunk)
      (:Entity)-[:REL {type, evidence, chunk_id, date, amount, ...}]->(:Entity)
      Family links from family.json become REL edges with chunk_id 'family.json'.
      Emailed documents (From/To/Subject at the top) get EMAIL edges with no LLM call.


Two LLM passes
    1. Document card - 1 call per document: category, title, people and their roles,
       issuer, document number, issue/expiry dates, amount and other deadlines.
       Powers the `expiring` and `profile` commands.
    2. Chunk extraction - 1 call per chunk: entities + relations from a fixed schema.


Privacy
    These files hold ID numbers (Aadhaar, PAN, passport), medical history and finances.
    By default the script refuses any LLM endpoint that is not on this machine or your
    private network. Set ALLOW_REMOTE_LLM=1 to override; ID-like numbers and email
    addresses are then masked before sending (best effort - names, addresses and
    diagnoses are still sent). Keep Neo4j bound to localhost and change its password.


Input format (flexible)
    Any .txt / .md file (TEXT_EXTS to change). Optional header lines at the very top:
        # Source: Medical/Dad/2024-03 discharge summary.pdf
        # Medium: image          (text | image | video; also guessed from the file name)
        # Status: ok             (files with any other status are skipped)
    Pages: lines like "=== Page 3 ===" or "--- Page 3 ---", or form-feed characters.
    Video descriptions: lines starting with a timestamp such as "[00:01:23]" become
    segments, so answers can cite the moment in the video.
    Photo/video descriptions are recognised by name (IMG_0042.jpg.txt, bday.mp4.txt),
    by a "# Medium:" header, or by an opening line like "Image description:".


family.json (optional, strongly recommended - this is how "Mom" and "S. Rao" resolve)
    {"members": [
        {"name": "Anita Rao",  "relation_to_me": "self",   "aliases": ["Anita", "A. Rao"]},
        {"name": "Suresh Rao", "relation_to_me": "father", "aliases": ["S. Rao", "Suresh"]}],
     "relationships": [["Suresh Rao", "PARENT_OF", "Anita Rao"]]}


Usage
    pip install chromadb sentence-transformers neo4j openai tqdm
    docker run -d -p 127.0.0.1:7474:7474 -p 127.0.0.1:7687:7687 \\
        -e NEO4J_AUTH=neo4j/<a-strong-password> neo4j:5
    ollama pull qwen2.5:14b


    set NEO4J_PASSWORD=<a-strong-password>               (export on mac/linux)
    python family_graphrag.py estimate --src "D:/FamilyVault"
    python family_graphrag.py build    --src "D:/FamilyVault" --limit 30        (pilot)
    python family_graphrag.py build    --src "D:/FamilyVault" --prune
    python family_graphrag.py ask "What does Dad's health insurance cover for cataract surgery?"
    python family_graphrag.py ask "When does the fridge warranty end?" --category warranty
    python family_graphrag.py expiring --days 90
    python family_graphrag.py profile "Mom"


    Always point --src at the vault root (use --only for a subfolder) so ids stay stable.
    Re-running build processes only new or changed files; --prune drops deleted ones.
    Documents in Hindi, Telugu or other languages: set EMBED_MODEL=BAAI/bge-m3 before
    the first build (changing it later means deleting graphrag_work/ and rebuilding).
"""


from __future__ import annotations


import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Configuration (environment variables override defaults)
# ---------------------------------------------------------------------------
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:14b")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
ALLOW_REMOTE_LLM = os.getenv("ALLOW_REMOTE_LLM", "0") == "1"
REDACT_IDS = os.getenv("REDACT_IDS", "auto")          # auto (= on for remote LLMs) | 1 | 0


EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
_BGE_EN = re.search(r"bge-(small|base|large)-en", EMBED_MODEL)
EMBED_QUERY_PREFIX = os.getenv(
    "EMBED_QUERY_PREFIX",
    "Represent this sentence for searching relevant passages: " if _BGE_EN else "")


NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "change-me")
WORK_DIR = Path(os.getenv("GRAPHRAG_WORK_DIR", "./graphrag_work"))
FAMILY_FILE = os.getenv("FAMILY_FILE", "family.json")
TEXT_EXTS = {e.strip().lower() for e in os.getenv("TEXT_EXTS", ".txt,.md").split(",") if e.strip()}


DATE_HINT = os.getenv(
    "DATE_HINT", "Numeric dates in these documents are usually day-first (DD/MM/YYYY), as in India.")
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "INR")


CHUNK_CHARS = 2000      # personal documents are short and dense; smaller chunks retrieve better
CHUNK_OVERLAP = 300
CARD_HEAD = 6000        # the card sees the start of the document ...
CARD_TAIL = 1500        # ... and its end (signatures, validity, totals often sit there)


# Entities mentioned in more than HUB_CAP chunks (you, your spouse, your city) connect
# everything to everything; skip them for the multi-hop jump.
HUB_CAP = int(os.getenv("GRAPHRAG_HUB_CAP", "150"))


# ---------------------------------------------------------------------------
# Schema. A fixed schema is the single biggest quality lever.
# ---------------------------------------------------------------------------
DOC_CATEGORIES = [
    "birth_certificate", "death_certificate", "marriage_certificate",
    "identity_document", "address_proof",
    "education_certificate", "education_record",
    "medical_record", "prescription", "lab_report", "vaccination_record",
    "health_insurance", "life_insurance", "vehicle_insurance", "other_insurance", "insurance_claim",
    "invoice", "receipt", "warranty", "product_manual", "service_guideline",
    "legal_agreement", "property_document", "bond_or_stamp_paper", "will_or_succession",
    "court_or_police", "bank_statement", "investment", "loan", "tax_document",
    "employment", "vehicle_document", "utility_bill", "correspondence",
    "photo", "video", "other",
]


PERSON_ROLES = [
    "holder", "subject", "patient", "insured", "policyholder", "nominee", "beneficiary",
    "buyer", "owner", "student", "applicant", "employee", "signatory", "witness",
    "parent", "spouse", "child", "depicted", "sender", "recipient", "other",
]


ENTITY_TYPES = [
    "Person", "Organization", "Record", "InsurancePolicy", "Product", "Service", "Asset",
    "MedicalCondition", "Medication", "Procedure", "Qualification", "Event", "Location",
]


FAMILY_RELS = ["PARENT_OF", "SPOUSE_OF", "SIBLING_OF", "GUARDIAN_OF", "RELATIVE_OF"]
RELATION_TYPES = FAMILY_RELS + [
    # documents and identity
    "HOLDS", "ISSUED_TO", "ISSUED_BY", "SIGNED", "WITNESSED", "PARTY_TO",
    "REFERENCES", "AMENDS", "SUPERSEDES", "RENEWS",
    # insurance
    "POLICYHOLDER_OF", "INSURED_UNDER", "NOMINEE_OF", "BENEFICIARY_OF",
    "COVERS", "EXCLUDES", "CLAIMED_UNDER",
    # purchases, warranties, services
    "PURCHASED", "SOLD_BY", "MANUFACTURED_BY", "WARRANTY_FOR", "SERVICED_BY", "PAID_TO",
    # health
    "TREATED_AT", "TREATED_BY", "DIAGNOSED_WITH", "PRESCRIBED", "UNDERWENT", "ALLERGIC_TO",
    # education and work
    "STUDIED_AT", "AWARDED", "AWARDED_BY", "EMPLOYED_BY",
    # general
    "OWNS", "LIVES_AT", "BORN_IN", "LOCATED_IN", "PARTICIPATED_IN", "RELATED_TO",
]
PROFILE_RELS = FAMILY_RELS + [
    "HOLDS", "POLICYHOLDER_OF", "INSURED_UNDER", "NOMINEE_OF", "BENEFICIARY_OF", "OWNS",
    "PURCHASED", "DIAGNOSED_WITH", "ALLERGIC_TO", "PRESCRIBED", "UNDERWENT",
    "STUDIED_AT", "AWARDED", "EMPLOYED_BY", "LIVES_AT", "BORN_IN",
]


CARD_PROMPT = """You are cataloguing one file from a family's personal document archive.
{family}
File: {path}
Medium: {medium}  (text = a scanned or typed document; image / video = a written description of a photo or video)


Return JSON only, with these keys:
{{"category": one of [{categories}],
  "title": "short human title, e.g. 'Passport - Anita Rao' or 'LG washing machine invoice'",
  "summary": "one or two sentences: what it is and why it matters",
  "people": [{{"name": "...", "role": one of [{roles}]}}],
  "issuer": "organization or office that issued, sold, wrote or insured it, or null",
  "document_number": "policy / certificate / invoice / registration number as printed, or null",
  "issue_date": "YYYY-MM-DD or null",
  "expiry_date": "YYYY-MM-DD or null (end of validity, cover, warranty or licence)",
  "amount": main money amount as a number (premium, invoice total, sum insured) or null,
  "currency": "ISO code or null",
  "key_dates": [{{"label": "a deadline or validity end, e.g. 'premium due', 'warranty ends', 'policy renewal', 'maturity', 'follow-up visit', 'licence expires'", "date": "YYYY-MM-DD"}}]}}


Rules:
- Only what the text states. Leave a field null rather than guess.
- {date_hint} Always output dates as YYYY-MM-DD.
- If amounts show "Rs" or no currency at all, the currency is {currency}.
- If a warranty or cover period is stated as a duration from a stated date, you may compute
  the end date; label it e.g. "warranty ends (computed)".
- key_dates holds only deadlines and end dates, not birth, issue or purchase dates.
- For photos and videos list only people the description names, with role "depicted".


Text:
\"\"\"
{text}
\"\"\"
"""


EXTRACTION_PROMPT = """You extract a knowledge graph from part of a family's personal document archive.
{family}
Entity types (use exactly these): {etypes}
  Person = individual (family member, doctor, agent, seller, witness, teacher);
  Organization = government office, hospital, clinic, school, university, exam board, insurer,
    bank, employer, shop, manufacturer, service centre, law firm, court, registrar;
  Record = a named document: certificate, ID card, agreement, deed, bond, stamp paper, invoice,
    receipt, warranty card, policy schedule, prescription, report, licence;
  InsurancePolicy = health, life, vehicle, home or travel insurance policy or plan;
  Product = purchased goods: appliance, gadget, furniture, jewellery item;
  Service = subscription, maintenance contract (AMC), utility or telecom connection, membership;
  Asset = house, flat, land, vehicle, bank account, fixed deposit, investment, bond;
  MedicalCondition; Medication; Procedure = surgery, test, scan, vaccination, therapy;
  Qualification = degree, diploma, exam, course, grade, rank;
  Event = birth, marriage, death, admission, discharge, accident, claim, travel, ceremony;
  Location = address, city, state, country, venue.


Relation types (use exactly these): {rtypes}


Rules:
- Only extract what the text states. No guessing. For photo/video descriptions, only people
  the description names.
- Use full canonical names; for family members use the roster names above.
- Name Records and InsurancePolicies with their number when shown,
  e.g. "Star Health policy P/161113/01/2024/012345".
- "evidence" is a short phrase (max 20 words) copied from the text that supports the relation.
- {date_hint} Output dates as YYYY-MM-DD: "date" for when it happened or was issued,
  "effective_date" / "expiration_date" for validity.
- Put a stated money value in "amount" (a number) on PURCHASED, PAID_TO, COVERS (sum insured),
  CLAIMED_UNDER and similar relations.


Return JSON only:
{{"entities": [{{"name": "...", "type": "...", "description": "one line"}}],
  "relations": [{{"source": "...", "target": "...", "type": "...", "evidence": "...",
                  "date": null, "effective_date": null, "expiration_date": null, "amount": null}}]}}


File: {path}
Medium: {medium}
Location in file: {locs}


Text:
\"\"\"
{text}
\"\"\"
"""


ANSWER_PROMPT = """You are a careful assistant helping a family find information in their own personal documents.
Today's date: {today}
{family}
Answer ONLY from the material below. Cite every factual statement as [n] using the source numbers.
Give figures, dates, numbers and names exactly as written. When a date matters (expiry, warranty,
renewal, premium due), say whether it has passed or how far away it is, relative to today.
If the material does not answer the question, say what is missing and which kind of document
would contain it. Report what the documents say; do not give medical, legal or financial advice.


Documents on file for the people in the question:
{person_docs}


Graph facts (relationships extracted from the documents):
{facts}


Sources:
{sources}


Question: {question}
"""




# ---------------------------------------------------------------------------
# 0. Family roster and name resolution
# ---------------------------------------------------------------------------
FAMILY: list[dict] = []            # [{"key", "name", "relation_to_me", "aliases"}]
FAMILY_LINKS: list[list[str]] = []
FAMILY_NAMES: dict[str, str] = {}  # key -> canonical display name
ALIASES: dict[str, str] = {}       # normalized alias -> canonical key


_HONORIFICS = (r"mrs|mr|ms|miss|dr|shri|sri|smt|kumari|kum|km|master|baby|late|prof|"
               r"sir|madam|mx")
_ORG_SUFFIX = (r"private limited|pvt\.?\s*ltd|pvt|limited|ltd|inc|llc|llp|lp|corporation|corp|co")




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




def load_family(path: str | None):
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
        FAMILY.append({"key": key, "name": m["name"], "aliases": aliases,
                       "relation_to_me": (m.get("relation_to_me") or "").strip().lower()})
    FAMILY_LINKS.extend(data.get("relationships", []) or [])




def family_roster() -> str:
    if not FAMILY:
        return ""
    lines = ["Family members (when the text refers to one of them, use exactly this name):"]
    for m in FAMILY:
        also = ", ".join(m["aliases"]) or "-"
        lines.append(f"- {m['name']} ({m['relation_to_me'] or 'family'}); also written as: {also}")
    return "\n".join(lines) + "\n"




REL_WORDS = {
    "mother": ["mother", "mom", "mum", "mummy", "amma", "maa", "ammi"],
    "father": ["father", "dad", "daddy", "papa", "nanna", "appa", "abba", "pitaji"],
    "spouse": ["wife", "husband", "spouse"],
    "wife": ["wife", "spouse"],
    "husband": ["husband", "spouse"],
    "son": ["son"], "daughter": ["daughter"],
    "child": ["child", "kid", "children", "kids"],
    "brother": ["brother", "bro", "anna", "bhaiya", "thammudu"],
    "sister": ["sister", "sis", "akka", "didi", "chelli"],
    "grandmother": ["grandmother", "grandma", "granny", "nani", "dadi", "ammamma", "nanamma", "avva"],
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
            if hit and not (rel in ("mother", "father") and "in-law" in qn[hit.end():hit.end() + 8]):
                keys.append(m["key"])
                break
    if not keys and re.search(r"\b(i|me|my|mine|myself)\b", qn):
        keys += [m["key"] for m in FAMILY if m["relation_to_me"] == "self"][:1]
    return list(dict.fromkeys(keys))




# ---------------------------------------------------------------------------
# 1. LLM access, with a local-only guard and ID masking for remote endpoints
# ---------------------------------------------------------------------------
def _is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "host.docker.internal") or host.endswith((".localhost", ".local")):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False




LLM_IS_LOCAL = _is_local(LLM_BASE_URL)
REDACT = REDACT_IDS == "1" or (REDACT_IDS == "auto" and not LLM_IS_LOCAL)




def _keep4(m: re.Match) -> str:
    digits = re.sub(r"\W", "", m.group(0))
    return f"[REDACTED-{digits[-4:]}]"




_REDACTIONS = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), lambda m: "[REDACTED-EMAIL]"),
    (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), _keep4),               # card / long account numbers
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"), _keep4),          # Aadhaar
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), _keep4),                 # PAN
    (re.compile(r"\b[A-Z]\d{7}\b"), _keep4),                         # passport
    (re.compile(r"\b\d{9,11}\b"), _keep4),                           # bank account / phone
]




def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text




_client = None




def llm_client():
    global _client
    if not LLM_IS_LOCAL and not ALLOW_REMOTE_LLM:
        sys.exit(f"LLM_BASE_URL {LLM_BASE_URL} is not on this machine or your private network.\n"
                 "Personal documents would leave your control. Use a local model (Ollama), or set "
                 "ALLOW_REMOTE_LLM=1 if you accept that (ID numbers will be masked).")
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    return _client




def chat(prompt: str, json_mode: bool = False) -> str:
    if REDACT:
        prompt = redact(prompt)
    client = llm_client()
    kwargs = dict(model=LLM_MODEL, temperature=0, messages=[{"role": "user", "content": prompt}])
    if json_mode:
        try:
            resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except Exception:      # some endpoints lack JSON mode; the prompt still asks for JSON
            resp = client.chat.completions.create(**kwargs)
    else:
        resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""




def parse_json_loose(text: str) -> dict:
    """LLMs sometimes wrap JSON in prose or ``` fences; recover the object."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                out = json.loads(text[start:end + 1])
                return out if isinstance(out, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}




def iso_date(v) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        return None




def to_number(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
        return float(m.group(0)) if m else None
    return None




# ---------------------------------------------------------------------------
# 2. Load the vault
# ---------------------------------------------------------------------------
@dataclass
class Segment:
    label: str      # page number, video timestamp, or "" for unpaginated text
    text: str




@dataclass
class Doc:
    rel_path: str           # path of the .txt file relative to the vault root (stable id)
    display_path: str       # original file it describes ("# Source:" header), else rel_path
    medium: str             # text | image | video
    segments: list[Segment]
    content_hash: str
    email: dict = field(default_factory=dict)


    @property
    def doc_id(self) -> str:
        return hashlib.sha1(self.rel_path.encode("utf-8")).hexdigest()[:16]




HEADER_RE = re.compile(r"^#\s*([A-Za-z][\w ]{0,30}):\s*(.*)$")
PAGE_RE = re.compile(r"^[ \t]*(?:={2,}|-{2,})[ \t]*Page[ \t]+(\d+)\b.*$", re.M | re.I)
TS_RE = re.compile(r"^[ \t]*\[?((?:\d{1,2}:)?\d{1,2}:\d{2})\]?", re.M)
IMAGE_EXT = re.compile(r"\.(jpe?g|png|heic|heif|webp|gif|bmp|tiff?)$", re.I)
VIDEO_EXT = re.compile(r"\.(mp4|mov|m4v|avi|mkv|3gp|webm|wmv)$", re.I)
EMAIL_KEYS = ("Subject", "From", "To", "Cc", "Date")




def guess_medium(name: str, headers: dict, body: str) -> str:
    h = (headers.get("medium") or headers.get("type") or "").lower()
    if h in ("image", "photo", "picture", "jpg", "jpeg", "png", "heic", "webp"):
        return "image"
    if h in ("video", "mp4", "mov", "avi", "mkv", "3gp"):
        return "video"
    if IMAGE_EXT.search(name):
        return "image"
    if VIDEO_EXT.search(name):
        return "video"
    head = body[:300].lower()
    if re.search(r"\b(image|photo|picture|photograph)\s+(description|caption)", head):
        return "image"
    if re.search(r"\bvideo\s+(description|transcript|summary)", head):
        return "video"
    return "text"




def split_segments(body: str, medium: str) -> list[Segment]:
    if medium == "video":
        marks = list(TS_RE.finditer(body))
        if len(marks) >= 2:
            segs = []
            pre = body[:marks[0].start()].strip()
            if pre:
                segs.append(Segment("start", pre))
            for i, mk in enumerate(marks):
                end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
                txt = body[mk.start():end].strip()
                if txt:
                    segs.append(Segment(mk.group(1), txt))
            return segs
    marks = list(PAGE_RE.finditer(body))
    if marks:
        segs = []
        pre = body[:marks[0].start()].strip()
        for i, mk in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
            txt = body[mk.end():end].strip()
            if i == 0 and pre:
                txt = (pre + "\n\n" + txt).strip()
            if txt:
                segs.append(Segment(mk.group(1), txt))
        return segs
    if "\f" in body:
        return [Segment(str(i), t.strip()) for i, t in enumerate(body.split("\f"), 1) if t.strip()]
    body = body.strip()
    return [Segment("", body)] if body else []




def parse_file(path: Path, root: Path) -> Doc | None:
    raw = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    content_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    lines = raw.split("\n")
    headers: dict[str, str] = {}
    i = 0
    while i < len(lines):                        # optional "# Key: value" header block
        if not lines[i].strip():
            i += 1
            continue
        m = HEADER_RE.match(lines[i])
        if not m:
            break
        for part in f"{m.group(1)}:{m.group(2)}".split("|"):   # "# Status: ok | Type: pdf"
            k, _, v = part.partition(":")
            if k.strip():
                headers[k.strip().lower()] = v.strip()
        i += 1
    body = "\n".join(lines[i:])


    status = headers.get("status", "ok").lower()
    if status not in ("ok", "success", "done", ""):
        return None
    rel = path.relative_to(root).as_posix()
    display = headers.get("source") or rel
    inner_name = Path(display).name if headers.get("source") else path.stem   # x.jpg.txt -> x.jpg
    medium = guess_medium(inner_name, headers, body)
    segments = split_segments(body, medium)
    if not segments:
        return None


    email = {}
    top = "\n".join(segments[0].text.splitlines()[:40])
    if re.search(r"^From:", top, re.M) and re.search(r"^(To|Subject):", top, re.M):
        for key in EMAIL_KEYS:
            mh = re.search(rf"^{key}:\s*(.+)$", top, re.M)
            if mh:
                email[key] = mh.group(1).strip()
    return Doc(rel, display, medium, segments, content_hash, email)




def iter_docs(src: Path, only: str | None = None):
    root = src.resolve()
    base = (root / only).resolve() if only else root
    work = WORK_DIR.resolve()
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXTS:
            continue
        if any(part.startswith(".") or part == "_index" for part in p.relative_to(root).parts):
            continue
        if work == p or work in p.parents:
            continue
        doc = parse_file(p, root)
        if doc:
            yield doc




# ---------------------------------------------------------------------------
# 3. Segment-aware chunking (keeps page numbers / video timestamps for citations)
# ---------------------------------------------------------------------------
@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    display_path: str
    medium: str
    locs: list[str]
    text: str




def fmt_loc(medium: str, locs: list[str] | str) -> str:
    if isinstance(locs, str):
        locs = locs.split(",")
    locs = [x for x in locs if x]
    if not locs:
        return ""
    if medium == "video":
        return "at " + ", ".join(locs)
    return "p. " + (locs[0] if len(locs) == 1 else f"{locs[0]}-{locs[-1]}")




def chunk_doc(doc: Doc) -> list[Chunk]:
    """Pack paragraphs into ~CHUNK_CHARS chunks with overlap, tracking source segments."""
    units: list[tuple[str, str]] = []
    max_unit = CHUNK_CHARS - CHUNK_OVERLAP - 2      # so overlap tail + unit always fits
    for seg in doc.segments:
        for para in re.split(r"\n\s*\n", seg.text):
            para = para.strip()
            while len(para) > max_unit:
                units.append((seg.label, para[:max_unit]))
                para = para[max_unit - CHUNK_OVERLAP:]
            if para:
                units.append((seg.label, para))


    chunks: list[Chunk] = []
    buf, buf_locs, fresh = "", [], False             # fresh = buf holds text not yet emitted


    def emit():
        cid = f"{doc.doc_id}-{len(chunks):04d}"
        chunks.append(Chunk(cid, doc.doc_id, doc.display_path, doc.medium,
                            list(dict.fromkeys(buf_locs)), buf.strip()))


    for label, para in units:
        if fresh and len(buf) + len(para) + 2 > CHUNK_CHARS:
            emit()
            buf, buf_locs, fresh = buf[-CHUNK_OVERLAP:], buf_locs[-1:], False
        buf += ("\n\n" if buf else "") + para
        buf_locs.append(label)
        fresh = True
    if fresh:
        emit()
    return chunks




# ---------------------------------------------------------------------------
# 4. LLM passes: document card + chunk extraction
# ---------------------------------------------------------------------------
def card_text(doc: Doc) -> str:
    full = "\n\n".join((f"[{fmt_loc(doc.medium, [s.label])}]\n" if s.label else "") + s.text
                       for s in doc.segments)
    if len(full) <= CARD_HEAD + CARD_TAIL:
        return full
    return full[:CARD_HEAD] + "\n\n[...]\n\n" + full[-CARD_TAIL:]




def display_name(key: str, fallback: str) -> str:
    return FAMILY_NAMES.get(key, fallback)




def clean_card(d: dict, doc: Doc) -> dict:
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
    issuer = [{"key": normalize_name(issuer_name), "name": issuer_name}] if _norm(issuer_name) else []


    key_dates = []
    for kd in d.get("key_dates") or []:
        if isinstance(kd, dict) and iso_date(kd.get("date")):
            key_dates.append((str(kd.get("label") or "date").strip()[:60], iso_date(kd["date"])))
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
    prompt = CARD_PROMPT.format(
        family=family_roster(), path=doc.display_path, medium=doc.medium,
        categories=", ".join(DOC_CATEGORIES), roles=", ".join(PERSON_ROLES),
        date_hint=DATE_HINT, currency=DEFAULT_CURRENCY, text=card_text(doc))
    return clean_card(parse_json_loose(chat(prompt, json_mode=True)), doc)




def clean_extraction(data: dict) -> dict:
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
            ents.append({"key": key, "name": display_name(key, name), "type": etype,
                         "description": str(e.get("description", ""))[:300]})
    rels = []
    for r in data.get("relations", []) or []:
        if not isinstance(r, dict):
            continue
        s, t = normalize_name(str(r.get("source", ""))), normalize_name(str(r.get("target", "")))
        for k in (s, t):          # family members may appear only inside a relation
            if k in FAMILY_NAMES and k not in keep:
                keep[k] = "Person"
                ents.append({"key": k, "name": FAMILY_NAMES[k], "type": "Person", "description": ""})
        rtype = r.get("type") if r.get("type") in RELATION_TYPES else "RELATED_TO"
        if s in keep and t in keep and s != t:
            rels.append({"source": s, "target": t, "type": rtype,
                         "evidence": str(r.get("evidence", ""))[:300],
                         "date": iso_date(r.get("date")),
                         "effective_date": iso_date(r.get("effective_date")),
                         "expiration_date": iso_date(r.get("expiration_date")),
                         "amount": to_number(r.get("amount"))})
    return {"entities": ents, "relations": rels}




def extract(chunk: Chunk) -> dict:
    prompt = EXTRACTION_PROMPT.format(
        family=family_roster(), etypes=", ".join(ENTITY_TYPES), rtypes=", ".join(RELATION_TYPES),
        date_hint=DATE_HINT, path=chunk.display_path, medium=chunk.medium,
        locs=fmt_loc(chunk.medium, chunk.locs) or "whole file", text=chunk.text)
    return clean_extraction(parse_json_loose(chat(prompt, json_mode=True)))




# ---------------------------------------------------------------------------
# 5. Storage: Chroma (vectors) + Neo4j (graph)
# ---------------------------------------------------------------------------
_embedder = None




def embed(texts: list[str]) -> list[list[float]]:
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _embedder.encode(texts, normalize_embeddings=True, batch_size=32).tolist()




def chroma_collection():
    import chromadb
    client = chromadb.PersistentClient(path=str(WORK_DIR / "chroma"))
    return client.get_or_create_collection("family_chunks", metadata={"hnsw:space": "cosine"})




def neo4j_driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))




SCHEMA_CYPHER = [
    "CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT ent_key IF NOT EXISTS FOR (e:Entity) REQUIRE e.key IS UNIQUE",
    "CREATE CONSTRAINT folder_path IF NOT EXISTS FOR (f:Folder) REQUIRE f.path IS UNIQUE",
    "CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE",
    "CREATE INDEX rel_chunk IF NOT EXISTS FOR ()-[r:REL]-() ON (r.chunk_id)",
]


SEED_FAMILY = """
UNWIND $members AS m
MERGE (n:Entity {key: m.key})
SET n.name = m.name, n.type = 'Person', n.family = true, n.relation_to_me = m.rel
"""


SEED_FAMILY_RELS = """
UNWIND $rels AS r
MATCH (s:Entity {key: r.s}), (t:Entity {key: r.t})
MERGE (s)-[e:REL {type: r.type, chunk_id: 'family.json'}]->(t)
SET e.evidence = 'family.json'
"""


# FOREACH (not UNWIND) so an empty list never skips the rest of the statement.
WRITE_DOC = """
MERGE (d:Document {id: $doc_id})
  SET d.path = $path, d.display = $display, d.name = $name, d.medium = $medium
FOREACH (pair IN $folder_pairs |
  MERGE (a:Folder {path: pair[0]}) SET a.name = last(split(pair[0], '/'))
  MERGE (b:Folder {path: pair[1]}) SET b.name = last(split(pair[1], '/'))
  MERGE (a)-[:CONTAINS]->(b))
FOREACH (leaf IN $leaf |
  MERGE (f:Folder {path: leaf}) SET f.name = last(split(leaf, '/'))
  MERGE (f)-[:CONTAINS]->(d))
"""


WRITE_CARD = """
MATCH (d:Document {id: $doc_id})
SET d += $props
MERGE (cat:Category {name: $props.category})
MERGE (d)-[:IN_CATEGORY]->(cat)
FOREACH (p IN $people |
  MERGE (n:Entity {key: p.key})
    ON CREATE SET n.name = p.name, n.type = 'Person'
  MERGE (n)-[:SUBJECT_OF {role: p.role}]->(d))
FOREACH (o IN $issuer |
  MERGE (n:Entity {key: o.key})
    ON CREATE SET n.name = o.name, n.type = 'Organization'
  MERGE (d)-[:ISSUED_BY]->(n))
"""


WRITE_CHUNK = """
MATCH (d:Document {id: $doc_id})
MERGE (c:Chunk {id: $chunk_id})
  SET c.text = $text, c.locs = $locs, c.display = $display, c.medium = $medium
MERGE (d)-[:HAS_CHUNK]->(c)
FOREACH (e IN $entities |
  MERGE (n:Entity {key: e.key})
    ON CREATE SET n.name = e.name, n.type = e.type, n.description = e.description
  MERGE (n)-[:MENTIONED_IN]->(c))
FOREACH (r IN $relations |
  MERGE (s:Entity {key: r.source})
  MERGE (t:Entity {key: r.target})
  MERGE (s)-[rel:REL {type: r.type, chunk_id: $chunk_id}]->(t)
    SET rel.evidence = r.evidence, rel.date = r.date, rel.amount = r.amount,
        rel.effective_date = r.effective_date, rel.expiration_date = r.expiration_date)
"""


WRITE_EMAIL = """
MATCH (d:Document {id: $doc_id})
SET d.subject = $subject, d.sent = $date
FOREACH (p IN $people |
  MERGE (n:Entity {key: p.key})
    ON CREATE SET n.name = p.name, n.type = 'Person'
  SET n.email = p.email
  MERGE (n)-[:EMAIL {role: p.role}]->(d))
"""


PURGE_CYPHER = [
    "MATCH ()-[r:REL]->() WHERE r.chunk_id STARTS WITH $prefix DELETE r",
    "MATCH (:Document {id: $doc_id})-[:HAS_CHUNK]->(c:Chunk) DETACH DELETE c",
    "MATCH (d:Document {id: $doc_id}) DETACH DELETE d",
]
ORPHAN_CYPHER = "MATCH (e:Entity) WHERE NOT (e)--() AND e.family IS NULL DELETE e"


ADDR_RE = re.compile(r'(?:"([^"]*)"|([^<>";,][^<>";]*?))?\s*<([^<>@\s]+@[^<>\s]+)>')




def email_people(headers: dict) -> list[dict]:
    """'"Rao, Anita" <anita@x.com>; Star Health <care@starhealth.in>' -> people with roles."""
    people, seen = [], set()
    for role in ("From", "To", "Cc"):
        for quoted, bare, addr in ADDR_RE.findall(headers.get(role, "")):
            name = (quoted or bare or "").strip(" ;,")
            m = re.match(r"^([^,;]+)[,;]\s*(.+)$", name)          # "Rao, Anita"
            if m:
                name = f"{m.group(2).strip()} {m.group(1).strip()}"
            if not name:
                name = addr.split("@")[0]
            key = normalize_name(name)
            if key and (key, role) not in seen:
                seen.add((key, role))
                people.append({"key": key, "name": display_name(key, name),
                               "email": addr.lower(), "role": role.lower()})
    return people




def folder_chain(rel_path: str) -> list[str]:
    parts = rel_path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]




def seed_family(session):
    if not FAMILY:
        return
    session.run(SEED_FAMILY, members=[{"key": m["key"], "name": m["name"], "rel": m["relation_to_me"]}
                                      for m in FAMILY])
    rels = [{"s": normalize_name(r[0]), "type": r[1], "t": normalize_name(r[2])}
            for r in FAMILY_LINKS
            if isinstance(r, list) and len(r) == 3 and r[1] in RELATION_TYPES]
    if rels:
        session.run(SEED_FAMILY_RELS, rels=rels)




def purge_doc(doc_id: str, col, driver):
    """Remove a document's chunks, vectors and extracted edges (before re-processing or on delete)."""
    col.delete(where={"doc_id": doc_id})
    with driver.session() as s:
        for q in PURGE_CYPHER:
            s.run(q, doc_id=doc_id, prefix=f"{doc_id}-")




# ---------------------------------------------------------------------------
# 6. Build (incremental: new and changed files only)
# ---------------------------------------------------------------------------
def state_path() -> Path:
    return WORK_DIR / "state.jsonl"




def load_state() -> dict[str, dict]:
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
    with state_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")




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


    folders = folder_chain(doc.rel_path)
    with driver.session() as s:
        s.run(WRITE_DOC, doc_id=doc.doc_id, path=doc.rel_path, display=doc.display_path,
              name=Path(doc.display_path).name, medium=doc.medium,
              folder_pairs=[list(p) for p in zip(folders, folders[1:])], leaf=folders[-1:])
        s.run(WRITE_CARD, doc_id=doc.doc_id, **card)


    col.upsert(ids=[c.chunk_id for c in chunks],
               embeddings=embed([c.text for c in chunks]),
               documents=[c.text for c in chunks],
               metadatas=[{"doc_id": c.doc_id, "path": c.display_path, "medium": c.medium,
                           "locs": ",".join(c.locs), "category": card["props"]["category"],
                           "title": card["props"]["title"]} for c in chunks])


    ok = True
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(extract, c): c for c in chunks}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                kg = fut.result()
            except Exception as exc:          # keep going; the file is retried on the next build
                print(f"  extraction failed {c.chunk_id}: {exc}", file=sys.stderr)
                ok, kg = False, {"entities": [], "relations": []}
            with driver.session() as s:
                s.run(WRITE_CHUNK, doc_id=doc.doc_id, chunk_id=c.chunk_id, text=c.text,
                      locs=c.locs, display=c.display_path, medium=c.medium,
                      entities=kg["entities"], relations=kg["relations"])


    if doc.email:
        with driver.session() as s:
            s.run(WRITE_EMAIL, doc_id=doc.doc_id, subject=doc.email.get("Subject"),
                  date=doc.email.get("Date"), people=email_people(doc.email))
    return ok




def build(src: Path, only: str | None, limit: int | None, workers: int, prune: bool):
    from tqdm import tqdm
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    llm_client()                                  # fails fast on a non-local endpoint
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
    print(f"{len(pending)} new or changed files to process ({len(state)} already indexed).")


    failed = 0
    for doc in tqdm(pending, desc="documents"):
        if process_doc(doc, col, driver, workers):
            record_state({"id": doc.doc_id, "path": doc.rel_path, "hash": doc.content_hash})
        else:
            failed += 1


    if prune:
        if only:
            print("--prune skipped: it needs a full scan (run without --only).")
        else:
            gone = [i for i in state if i not in seen]
            for doc_id in gone:
                purge_doc(doc_id, col, driver)
                record_state({"id": doc_id, "deleted": True})
            print(f"pruned {len(gone)} files that no longer exist.")


    with driver.session() as s:
        s.run(ORPHAN_CYPHER)
    driver.close()
    if failed:
        print(f"{failed} files had LLM errors and will be retried on the next build.")




# ---------------------------------------------------------------------------
# 7. Ask: vector search -> graph expansion -> cited answer
# ---------------------------------------------------------------------------
FACTS_CYPHER = """
MATCH (e:Entity)-[:MENTIONED_IN]->(c:Chunk) WHERE c.id IN $seed_ids
WITH DISTINCT e
MATCH (e)-[r:REL]-(:Entity)
WITH DISTINCT r
RETURN startNode(r).name AS s, r.type AS rt, endNode(r).name AS o, r.evidence AS ev,
       coalesce(r.date, r.effective_date) AS dt, r.expiration_date AS until, r.amount AS amount
ORDER BY CASE WHEN r.chunk_id IN $seed_ids THEN 0 ELSE 1 END
LIMIT $max_facts
"""


# The multi-hop jump: chunks that mention the seed entities or their graph neighbours,
# ranked by how many of those entities they share. This is what lets
# "Dad's cataract surgery" (discharge summary) meet "sum insured" (policy schedule).
EXTRA_CHUNKS_CYPHER = """
MATCH (e:Entity)-[:MENTIONED_IN]->(c:Chunk) WHERE c.id IN $seed_ids
WITH collect(DISTINCT e) AS seeds
UNWIND seeds AS e
OPTIONAL MATCH (e)-[:REL]-(n:Entity)
WITH seeds, collect(DISTINCT n) AS hop1
UNWIND seeds + hop1 AS x
WITH DISTINCT x
WHERE COUNT { (x)-[:MENTIONED_IN]->(:Chunk) } <= $hub_cap
MATCH (x)-[:MENTIONED_IN]->(c2:Chunk) WHERE NOT c2.id IN $seed_ids
WITH c2, count(DISTINCT x) AS shared
ORDER BY shared DESC LIMIT $max_extra
RETURN c2.id AS id, c2.text AS text, c2.display AS path, c2.locs AS locs, c2.medium AS medium
"""


DOC_INFO_CYPHER = """
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.id IN $ids
RETURN c.id AS id, d.title AS title, d.category AS category, d.expiry_date AS expires
"""


PERSON_DOCS_CYPHER = """
MATCH (p:Entity)-[s:SUBJECT_OF]->(d:Document) WHERE p.key IN $keys
RETURN p.name AS person, s.role AS role, d.title AS title, d.category AS category,
       d.expiry_date AS expires, d.display AS path
ORDER BY person, category, title LIMIT 60
"""




def ask(question: str, k: int = 6, extra: int = 6, max_facts: int = 40,
        category: str | None = None, show: bool = False):
    keys = resolve_family(question)
    names = [FAMILY_NAMES[x] for x in keys if x in FAMILY_NAMES]
    query = question + (" (" + ", ".join(names) + ")" if names else "")


    col = chroma_collection()
    if col.count() == 0:
        sys.exit("The index is empty - run `build` first.")
    hits = col.query(query_embeddings=embed([EMBED_QUERY_PREFIX + query]), n_results=k,
                     where={"category": category} if category else None)
    seeds = [{"id": i, "text": t, "path": m["path"], "locs": m.get("locs", ""),
              "medium": m.get("medium", "text")}
             for i, t, m in zip(hits["ids"][0], hits["documents"][0], hits["metadatas"][0])]
    seed_ids = [c["id"] for c in seeds]


    driver = neo4j_driver()
    with driver.session() as s:
        facts = [r.data() for r in s.run(FACTS_CYPHER, seed_ids=seed_ids, max_facts=max_facts)]
        extra_chunks = [{**r.data(), "locs": ",".join(r["locs"] or [])}
                        for r in s.run(EXTRA_CHUNKS_CYPHER, seed_ids=seed_ids,
                                       hub_cap=HUB_CAP, max_extra=extra)]
        sources = seeds + extra_chunks
        info = {r["id"]: r.data() for r in s.run(DOC_INFO_CYPHER, ids=[c["id"] for c in sources])}
        person_docs = [r.data() for r in s.run(PERSON_DOCS_CYPHER, keys=keys)] if keys else []
    driver.close()


    def head(i, c):
        d = info.get(c["id"], {})
        loc = fmt_loc(c["medium"], c["locs"])
        meta = f" - {d['title']} [{d['category']}]" if d.get("title") else ""
        exp = f" (expires {d['expires']})" if d.get("expires") else ""
        return f"[{i}] {c['path']}{', ' + loc if loc else ''}{meta}{exp}"


    src_txt = "\n\n".join(f"{head(i, c)}\n{c['text']}" for i, c in enumerate(sources, 1))


    def fact_line(f):
        extras = [x for x in (f.get("dt") and f"on {f['dt']}", f.get("until") and f"until {f['until']}",
                              f.get("amount") is not None and f"amount {f['amount']:g}") if x]
        return f"- {f['s']} --{f['rt']}--> {f['o']}{' (' + ', '.join(extras) + ')' if extras else ''}  (\"{f['ev']}\")"


    fact_txt = "\n".join(fact_line(f) for f in facts) or "(none)"
    pdoc_txt = "\n".join(f"- {d['person']} ({d['role']}): {d['title']} [{d['category']}]"
                         f"{' expires ' + d['expires'] if d.get('expires') else ''}"
                         for d in person_docs) or "(none)"
    prompt = ANSWER_PROMPT.format(today=date.today().isoformat(), family=family_roster(),
                                  person_docs=pdoc_txt, facts=fact_txt, sources=src_txt,
                                  question=question)
    print(chat(prompt))
    print("\nSources:")
    for i, c in enumerate(sources, 1):
        print("  " + head(i, c))
    if show:
        print("\nGraph facts used:\n" + fact_txt)
        print("\nPeople's documents used:\n" + pdoc_txt)




# ---------------------------------------------------------------------------
# 8. Expiring: renewals, warranties, premiums and validity dates coming up
# ---------------------------------------------------------------------------
NOT_DEADLINE = re.compile(r"\b(birth|born|issued?|purchased?|bill|invoice|admi(ssion|tted)|"
                          r"discharged?|marriage|dated)\b", re.I)
IS_DEADLINE = re.compile(r"due|end|expir|renew|valid|matur|until|last|follow", re.I)




def expiring(days: int, past_days: int):
    today = date.today()
    lo, hi = today - timedelta(days=past_days), today + timedelta(days=days)
    driver = neo4j_driver()
    with driver.session() as s:
        rows = [r.data() for r in s.run("""
            MATCH (d:Document) WHERE size(coalesce(d.date_values, [])) > 0
            OPTIONAL MATCH (p:Entity)-[:SUBJECT_OF]->(d)
            RETURN d.title AS title, d.category AS category, d.display AS path,
                   d.date_labels AS labels, d.date_values AS vals,
                   collect(DISTINCT p.name) AS people""")]
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
        rel = "today" if delta == 0 else (f"in {delta} days" if delta > 0 else f"{-delta} days ago")
        who = ", ".join(x for x in r["people"] if x)
        print(f"{when}  ({rel:>12})  {label}: {r['title']} [{r['category']}]"
              f"{'  - ' + who if who else ''}\n{'':28}{r['path']}")




# ---------------------------------------------------------------------------
# 9. Profile: everything on file for one person
# ---------------------------------------------------------------------------
def profile(name: str):
    keys = resolve_family(name) or [normalize_name(name)]
    driver = neo4j_driver()
    with driver.session() as s:
        people = [r.data() for r in s.run(
            "MATCH (p:Entity) WHERE p.key IN $keys RETURN p.key AS key, p.name AS name", keys=keys)]
        if not people:
            print(f"No one called '{name}' in the graph.")
            driver.close()
            return
        for p in people:
            print(f"\n=== {p['name']} ===")
            docs = [r.data() for r in s.run("""
                MATCH (:Entity {key: $key})-[s:SUBJECT_OF]->(d:Document)
                RETURN d.category AS category, d.title AS title, s.role AS role,
                       d.doc_number AS number, d.expiry_date AS expires, d.display AS path
                ORDER BY category, title""", key=p["key"])]
            current = None
            for d in docs:
                if d["category"] != current:
                    current = d["category"]
                    print(f"\n  {current}")
                bits = [d["role"], d["number"] and f"no. {d['number']}",
                        d["expires"] and f"expires {d['expires']}"]
                print(f"    - {d['title']} ({', '.join(b for b in bits if b)})\n      {d['path']}")
            facts = [r.data() for r in s.run("""
                MATCH (p:Entity {key: $key})-[r:REL]-(q:Entity) WHERE r.type IN $types
                RETURN DISTINCT r.type AS rt, startNode(r) = p AS outgoing, q.name AS other
                ORDER BY rt, other LIMIT 80""", key=p["key"], types=PROFILE_RELS)]
            if facts:
                print("\n  key facts")
                for f in facts:
                    arrow = f"--{f['rt']}-->" if f["outgoing"] else f"<--{f['rt']}--"
                    print(f"    {p['name']} {arrow} {f['other']}")
    driver.close()




# ---------------------------------------------------------------------------
# 10. Estimate: size the job before spending LLM time
# ---------------------------------------------------------------------------
def estimate(src: Path, only: str | None, limit: int | None):
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
        ext = (Path(dp.stem).suffix or dp.suffix).lower() if dp.suffix.lower() in TEXT_EXTS else dp.suffix.lower()
        ext = ext or "(none)"
        by_ext[ext] = by_ext.get(ext, 0) + 1
        if limit and n_docs >= limit:
            break
    calls = n_docs + n_chunks                                  # 1 card per doc + 1 per chunk
    card_chars = min(n_chars, n_docs * (CARD_HEAD + CARD_TAIL))
    tokens_in = (n_chars + card_chars) / 4 + calls * 900       # text + prompt overhead
    tokens_out = n_docs * 300 + n_chunks * 400
    print(f"documents: {n_docs}   by medium: {by_medium}")
    print(f"original file types: {dict(sorted(by_ext.items(), key=lambda x: -x[1]))}")
    print(f"chunks: {n_chunks}   LLM calls: {calls} ({n_docs} cards + {n_chunks} extractions)")
    print(f"approx input tokens:  {tokens_in/1e6:.2f}M")
    print(f"approx output tokens: {tokens_out/1e6:.2f}M")
    print("A 14B model on a decent GPU manages very roughly 5-15 s per call; time a --limit 20 "
          "pilot to get your own rate.")




def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--family", default=FAMILY_FILE, help="family roster JSON (default family.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("estimate", "build"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--src", required=True, type=Path, help="vault root folder")
        p.add_argument("--only", default=None, help="subfolder of the vault to limit this run to")
        p.add_argument("--limit", type=int, default=None, help="only the first N files (pilot)")
        if name == "build":
            p.add_argument("--workers", type=int, default=2, help="parallel LLM calls")
            p.add_argument("--prune", action="store_true", help="remove files deleted from the vault")
    p = sub.add_parser("ask", parents=[common])
    p.add_argument("question")
    p.add_argument("--k", type=int, default=6)
    p.add_argument("--category", choices=DOC_CATEGORIES, default=None)
    p.add_argument("--show-facts", action="store_true")
    p = sub.add_parser("expiring", parents=[common])
    p.add_argument("--days", type=int, default=90, help="look this many days ahead")
    p.add_argument("--past-days", type=int, default=0, help="also show dates up to N days ago")
    p = sub.add_parser("profile", parents=[common])
    p.add_argument("name", help='a name, alias or kinship word, e.g. "Anita" or "Mom"')
    a = ap.parse_args()


    load_family(a.family)
    if a.cmd == "estimate":
        estimate(a.src, a.only, a.limit)
    elif a.cmd == "build":
        build(a.src, a.only, a.limit, a.workers, a.prune)
    elif a.cmd == "ask":
        ask(a.question, k=a.k, category=a.category, show=a.show_facts)
    elif a.cmd == "expiring":
        expiring(a.days, a.past_days)
    else:
        profile(a.name)




if __name__ == "__main__":
    main()