"""Core data structures and models for documents, segments, and chunks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class Segment:
    """A single page, video timestamp segment, or whole document segment."""
    label: str  # page number, video timestamp, or "" for unpaginated text
    text: str


@dataclass
class Doc:
    """Parsed document representation from a vault text file."""
    rel_path: str  # path of the .txt file relative to the vault root (stable id)
    display_path: str  # original file it describes ("# Source:" header), else rel_path
    medium: str  # text | image | video
    segments: list[Segment]
    content_hash: str
    email: dict = field(default_factory=dict)

    @property
    def doc_id(self) -> str:
        return hashlib.sha1(self.rel_path.encode("utf-8")).hexdigest()[:16]


@dataclass
class Chunk:
    """A segment-aware text chunk with citation anchor metadata."""
    chunk_id: str
    doc_id: str
    display_path: str
    medium: str
    locs: list[str]
    text: str

