"""LLM client, chat execution, JSON extraction, and PII guard."""

from __future__ import annotations

import ipaddress
import json
import re
import sys
from datetime import date
from urllib.parse import urlparse

from src.family_graphrag.config import (
    ALLOW_REMOTE_LLM,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    REDACT_IDS,
)


def _is_local(url: str) -> bool:
    """Check if an endpoint resolves to localhost, private IP or local network."""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "host.docker.internal") or host.endswith(
        (".localhost", ".local")
    ):
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
    (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), _keep4),  # card / long account numbers
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"), _keep4),  # Aadhaar
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), _keep4),  # PAN
    (re.compile(r"\b[A-Z]\d{7}\b"), _keep4),  # passport
    (re.compile(r"\b\d{9,11}\b"), _keep4),  # bank account / phone
]


def redact(text: str) -> str:
    """Mask common sensitive identifiers before transmitting to remote LLM."""
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


_client = None


def llm_client():
    """Initializes and guards the OpenAI-compatible LLM client (Ollama/vLLM/etc)."""
    global _client
    if not LLM_IS_LOCAL and not ALLOW_REMOTE_LLM:
        sys.exit(
            f"LLM_BASE_URL {LLM_BASE_URL} is not on this machine or your private network.\n"
            "Personal documents would leave your control. Use a local model (Ollama), or set "
            "ALLOW_REMOTE_LLM=1 if you accept that (ID numbers will be masked)."
        )
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    return _client


def chat(prompt: str, json_mode: bool = False) -> str:
    """Send prompt to LLM and return response text, supporting reasoning/thinking models."""
    if REDACT:
        prompt = redact(prompt)
    client = llm_client()
    kwargs = dict(
        model=LLM_MODEL,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    if json_mode:
        try:
            resp = client.chat.completions.create(
                response_format={"type": "json_object"}, **kwargs
            )
        except Exception:  # some endpoints lack JSON mode; prompt still instructs JSON
            resp = client.chat.completions.create(**kwargs)
    else:
        resp = client.chat.completions.create(**kwargs)

    msg = resp.choices[0].message
    content = msg.content or ""
    if not content.strip():
        # Support reasoning/thinking models (e.g., qwen3.5, deepseek-r1 in Ollama)
        reasoning = getattr(msg, "reasoning_content", None) or getattr(
            msg, "thinking", None
        )
        if reasoning:
            content = reasoning
    return content


def parse_json_loose(text: str) -> dict:
    """LLMs sometimes wrap JSON in prose or ``` fences; recover the dictionary object."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                out = json.loads(text[start : end + 1])
                return out if isinstance(out, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


def iso_date(v) -> str | None:
    """Ensure value conforms to ISO YYYY-MM-DD date."""
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
    """Extract numeric value from string or number."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
        return float(m.group(0)) if m else None
    return None

