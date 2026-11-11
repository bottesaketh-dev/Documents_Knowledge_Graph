"""Prompts for document card extraction, chunk entity/relation extraction, and QA synthesis."""

from __future__ import annotations

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

