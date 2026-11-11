"""Ontology schema definitions, category taxonomies, and Cypher statements."""

from __future__ import annotations

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
    "photo", "video", "application_form", "aadhaar_card", "pan_card", "passport", "other",
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

# ---------------------------------------------------------------------------
# Neo4j Cypher Statements
# ---------------------------------------------------------------------------

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

