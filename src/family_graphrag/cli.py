"""Command-line interface and argument parsing for Family GraphRAG."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.family_graphrag.config import (
    FAMILY_FILE,
    LLM_WORKERS,
    set_neo4j_database,
    set_neo4j_uri,
    set_work_dir,
)
from src.family_graphrag.family import load_family
from src.family_graphrag.indexer import build
from src.family_graphrag.schema import DOC_CATEGORIES
from src.family_graphrag.search import ask, estimate, expiring, profile

DOCSTRING = """family_graphrag - Offline Knowledge Graph & Vector RAG over personal document archives.

Subcommands:
  estimate     Size the vault and preview estimated LLM tokens before indexing.
  build        Ingest documents into ChromaDB (vectors) and Neo4j (graph).
  ask          Hybrid retrieval answering questions with citations.
  expiring     List upcoming renewals, validity dates, and deadlines.
  profile      Generate a comprehensive dossier for any family member.
  export-html  Export the Neo4j graph into a standalone interactive HTML visualization.
"""


def main(args: list[str] | None = None):
    """Main CLI entrypoint for family_graphrag."""
    ap = argparse.ArgumentParser(
        description=DOCSTRING,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--family",
        default=FAMILY_FILE,
        help="family roster JSON (default config/family.json)",
    )
    common.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="working directory for ChromaDB and state cache (default: from settings.env)",
    )
    common.add_argument(
        "--neo4j-uri",
        default=None,
        help="Neo4j Bolt URI (default: from settings.env)",
    )
    common.add_argument(
        "--neo4j-database",
        default=None,
        help="Neo4j database name (default: default database)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("estimate", "build"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument(
            "--src", required=True, type=Path, help="vault root folder"
        )
        p.add_argument(
            "--only",
            default=None,
            help="subfolder of the vault to limit this run to",
        )
        p.add_argument(
            "--limit",
            type=int,
            default=None,
            help="only the first N files (pilot)",
        )
        if name == "build":
            p.add_argument(
                "--workers",
                type=int,
                default=LLM_WORKERS,
                help=f"parallel LLM calls (default: {LLM_WORKERS}; sequential avoids GPU VRAM overflow)",
            )
            p.add_argument(
                "--prune",
                action="store_true",
                help="remove files deleted from the vault",
            )

    p = sub.add_parser("ask", parents=[common])
    p.add_argument("question")
    p.add_argument("--k", type=int, default=6)
    p.add_argument("--category", choices=DOC_CATEGORIES, default=None)
    p.add_argument("--show-facts", action="store_true")

    p = sub.add_parser("expiring", parents=[common])
    p.add_argument(
        "--days", type=int, default=90, help="look this many days ahead"
    )
    p.add_argument(
        "--past-days",
        type=int,
        default=0,
        help="also show dates up to N days ago",
    )

    p = sub.add_parser("profile", parents=[common])
    p.add_argument(
        "name", help='a name, alias or kinship word, e.g. "Saketh" or "Dad"'
    )

    p = sub.add_parser("export-html", parents=[common])
    p.add_argument(
        "--output",
        default="knowledge_graph.html",
        help="output HTML file path (default: knowledge_graph.html)",
    )
    p.add_argument(
        "--title",
        default="Knowledge Graph Visualization",
        help="title displayed in the HTML dashboard",
    )

    a = ap.parse_args(args)

    if a.work_dir:
        set_work_dir(a.work_dir)
    if a.neo4j_uri:
        set_neo4j_uri(a.neo4j_uri)
    if a.neo4j_database:
        set_neo4j_database(a.neo4j_database)

    load_family(a.family)
    if a.cmd == "estimate":
        estimate(a.src, a.only, a.limit)
    elif a.cmd == "build":
        build(a.src, a.only, a.limit, a.workers, a.prune)
    elif a.cmd == "ask":
        ask(a.question, k=a.k, category=a.category, show=a.show_facts)
    elif a.cmd == "expiring":
        expiring(a.days, a.past_days)
    elif a.cmd == "export-html":
        from src.export_graph_html import export_graph_to_html
        import src.family_graphrag.config as cfg
        export_graph_to_html(
            neo4j_uri=cfg.NEO4J_URI,
            neo4j_user=cfg.NEO4J_USER,
            neo4j_password=cfg.NEO4J_PASSWORD,
            output_html_path=a.output,
            title=a.title,
        )
    else:
        profile(a.name)


if __name__ == "__main__":
    main()
