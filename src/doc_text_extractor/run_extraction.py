"""
Runner: extract text from every file under a folder of documents, offline.


    python -m doc_text_extractor.run_extraction "D:\\My Documents" "D:\\doc_text" --workers 6
    python -m doc_text_extractor.run_extraction "D:\\My Documents" "D:\\doc_text" --inventory-only


Writes into the output folder:
    text\\<same folders as the source>\\<file name>.txt     page-marked text; attachments go in <file name>.attachments\\
    corpus.jsonl        one JSON record per document (and per attachment), appended as each finishes
    inventory.csv       one row per document: type, status, method, pages, OCR pages, flags
    state.sqlite        resume and duplicate index (shared read-only with the workers)
    summary.json        counts by type / status / meth`od, OCR pages, time taken
    run_manifest.json   tool versions and options used
    extract.log         warnings and errors


Re-running the same command resumes: files whose size and modified time have not changed are skipped.
Exact duplicates (same SHA-256) are extracted once and the result is reused for every copy, including
attachments inside emails and zip files that duplicate a file already processed. Ordinary files are
processed first and emails/zips last, so those attachments find their originals already done.
"""
from __future__ import annotations


import argparse
import csv
import fnmatch
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, FIRST_COMPLETED, wait
from datetime import datetime, timezone
from pathlib import Path


if __package__ in (None, ""):                                   # allow "python run_extraction.py ..."
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "doc_text_extractor"


from doc_text_extractor import extractor, ocr_config               # noqa: E402


log = logging.getLogger("doc_text_extractor")
IS_WIN = os.name == "nt"
CLOUD_ONLY_ATTRS = 0x00400000 | 0x00040000 | 0x00001000          # RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE
MAX_DEPTH = 3                                                     # attachments inside attachments inside ...




# --------------------------------------------------------------------------- #
# Windows long paths (deep folder trees easily pass 260 characters)
# --------------------------------------------------------------------------- #
def lp(p) -> str:
    """Windows path that is safe past 260 characters. Used for every
    file-system call this module makes; external tools never see it (long paths are copied to a short temp path)."""
    s = str(Path(p).absolute())
    if IS_WIN and not s.startswith("\\\\?\\"):
        return "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s
    return s




# System folders that never hold documents. Overridable with --no-default-excludes.
DEFAULT_EXCLUDES = [
    "$RECYCLE.BIN", "System Volume Information", "__pycache__", ".git",
]
CONTAINER_EXTS = {".msg", ".eml", ".zip"}




def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(lp(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()




# --------------------------------------------------------------------------- #
# Walking the source tree
# --------------------------------------------------------------------------- #
def walk(src: Path, excludes: list[str]):
    """Yield (abs_path, rel_posix, stat, skip_reason)."""
    stack = [src]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(os.scandir(lp(d)), key=lambda e: e.name.lower())
        except OSError as e:
            log.warning("cannot list %s: %s", d, e)
            continue
        names = {e.name.lower() for e in entries}
        for e in entries:
            p = Path(d) / e.name
            rel = p.relative_to(src).as_posix()
            if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(e.name, pat) for pat in excludes):
                continue
            if e.is_dir(follow_symlinks=False):
                low = e.name.lower()
                # "Save as Web Page" support folders (Report_files next to Report.htm): images and xml, no new text
                for suf in ("_files", "-files", ".files", "_archivos", "_fichiers", "-dateien"):
                    if low.endswith(suf) and any(n in names for n in (low[: -len(suf)] + ".htm", low[: -len(suf)] + ".html")):
                        break
                else:
                    stack.append(p)
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError as ex:
                yield p, rel, None, f"stat_failed:{ex}"
                continue
            yield p, rel, st, None




# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
_OPTS: extractor.Options | None = None
_STATE_DB: str | None = None
_STATE_CONN = None




def _known_sha(sha: str):
    """(doc_id, text_path) if this content was already extracted in this or an earlier run."""
    global _STATE_CONN
    if not _STATE_DB:
        return None
    try:
        if _STATE_CONN is None:
            _STATE_CONN = sqlite3.connect(f"file:{_STATE_DB}?mode=ro", uri=True, timeout=30)
        return _STATE_CONN.execute("SELECT doc_id, text_path FROM shas WHERE sha=?", (sha,)).fetchone()
    except sqlite3.Error:
        return None




def _init_worker(opts_dict, log_level, state_db=None):
    global _OPTS, _STATE_DB
    _STATE_DB = state_db
    os.environ["OMP_THREAD_LIMIT"] = "1"           # one Tesseract thread per worker; avoids oversubscription
    if not IS_WIN:
        os.environ["TZ"] = "UTC"                   # extract-msg fails on some local timezone names
        try:
            time.tzset()
        except AttributeError:
            pass
    logging.basicConfig(level=log_level)
    ocr_config.get_tools()
    _OPTS = extractor.Options(**opts_dict)




def _record_base(rel, size, mtime, sha, parent_id=None, attachment_name=None):
    p = Path(rel.split("::")[-1]) if "::" in rel else Path(rel)
    doc_id = sha[:16] if parent_id is None else hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16]
    return {
        "doc_id": doc_id, "sha256": sha, "source_path": rel,
        "folder": rel.split("::")[0].rsplit("/", 1)[0] if "/" in rel.split("::")[0] else "",
        "filename": p.name, "ext": p.suffix.lower(), "size_bytes": size,
        "modified": datetime.fromtimestamp(mtime, timezone.utc).isoformat() if mtime else None,
        "parent_id": parent_id, "attachment_name": attachment_name,
    }




def _extract_tree(path: Path, rel: str, size, mtime, sha, workdir: Path, parent_id=None, attachment_name=None, depth=0):
    t0 = time.time()
    rec = _record_base(rel, size, mtime, sha, parent_id, attachment_name)
    sub = workdir / f"d{depth}_{rec['doc_id']}"
    sub.mkdir(parents=True, exist_ok=True)
    res = extractor.extract_document(path, sub, _OPTS)
    rec.update({
        "detected_format": res.detected, "status": res.status, "flags": res.flags,
        "pages": [p.as_dict() for p in res.pages], "sections": res.sections, "meta": res.meta,
        "page_count": len(res.pages),
        "ocr_pages": sum(1 for p in res.pages if "ocr" in p.method and "skipped" not in p.method and "failed" not in p.method),
        "char_count": sum(len(p.text) for p in res.pages) + sum(len(s["text"]) for s in res.sections),
        "methods": sorted({p.method for p in res.pages}),
        "attachments": [],
        "extractor_version": extractor.EXTRACTOR_VERSION,
    })
    out = [rec]
    used_names = set()
    seen_here = {}                                               # sha -> rel of the first copy inside this document
    for name, cpath in res.children:
        if depth + 1 > MAX_DEPTH:
            rec["flags"].append("nested_attachments_not_extracted:max_depth")
            break
        try:
            csha = sha256_file(cpath)
            cst = Path(cpath).stat()
        except OSError:
            continue
        crel = f"{rel}::{extractor.safe_name(Path(name).name)}"
        if crel in used_names:                                   # two attachments with the same name
            stem, ext = os.path.splitext(crel)
            crel = f"{stem} ({len(used_names) + 1}){ext}"
        used_names.add(crel)
        known = _known_sha(csha)
        if known or csha in seen_here:                          # attachment already extracted elsewhere: reuse it
            dup = _record_base(crel, cst.st_size, None, csha, rec["doc_id"], name)
            dup.update(_dup_fields(known[0] if known else hashlib.sha256(seen_here[csha].encode()).hexdigest()[:16]))
            if known:
                dup["copy_text_from"] = known[1]
            else:
                dup["copy_text_from_rel"] = seen_here[csha]
            rec["attachments"].append({"doc_id": dup["doc_id"], "name": name})
            out.append(dup)
            continue
        seen_here[csha] = crel
        kids = _extract_tree(Path(cpath), crel, cst.st_size, None, csha, workdir, rec["doc_id"], name, depth + 1)
        rec["attachments"].append({"doc_id": kids[0]["doc_id"], "name": name})
        out.extend(kids)
    rec["seconds"] = round(time.time() - t0, 2)
    rec["extracted_at"] = datetime.now(timezone.utc).isoformat()
    shutil.rmtree(sub, ignore_errors=True)
    return out




def _dup_fields(orig_id):
    return {"duplicate_of": orig_id, "status": "duplicate", "detected_format": "", "flags": ["exact_duplicate"],
            "pages": [], "sections": [], "meta": {}, "page_count": 0, "ocr_pages": 0, "char_count": 0, "methods": [],
            "attachments": [], "seconds": 0, "extracted_at": datetime.now(timezone.utc).isoformat()}




def work(abs_path: str, rel: str, size: int, mtime: float, sha: str):
    """Runs in a worker process. Long Windows paths are copied to a short temp path first."""
    with tempfile.TemporaryDirectory(prefix="lte_") as tmp:
        tmp = Path(tmp)
        src = Path(abs_path)
        if IS_WIN and len(str(src)) >= 240:                     # external tools cannot open long paths
            short = tmp / ("src" + src.suffix)
            shutil.copyfile(lp(src), short)
            src = short
        try:
            return _extract_tree(src, rel, size, mtime, sha, tmp)
        except Exception as e:                                   # never lose the record
            rec = _record_base(rel, size, mtime, sha)
            rec.update({"status": "error", "flags": [f"{type(e).__name__}: {str(e)[:300]}"], "pages": [], "sections": [],
                        "page_count": 0, "ocr_pages": 0, "char_count": 0, "methods": [], "attachments": []})
            return [rec]




# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
class Writer:
    INV_COLS = ["source_path", "ext", "detected_format", "status", "parent_id", "doc_id", "duplicate_of", "page_count",
                "ocr_pages", "char_count", "methods", "seconds", "size_bytes", "flags"]


    def __init__(self, out: Path):
        self.out = out
        (out / "text").mkdir(parents=True, exist_ok=True)
        self.corpus = open(out / "corpus.jsonl", "a", encoding="utf-8")
        new_inv = not (out / "inventory.csv").exists()
        self.inv_f = open(out / "inventory.csv", "a", newline="", encoding="utf-8-sig")
        self.inv = csv.writer(self.inv_f)
        if new_inv:
            self.inv.writerow(self.INV_COLS)
        self.db_path = out / "state.sqlite"
        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS files (rel TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, doc_id TEXT, sha TEXT, text_path TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS shas (sha TEXT PRIMARY KEY, doc_id TEXT, text_path TEXT)")
        self._import_old_state()
        self.db.commit()
        self.stats = Counter()
        self.by_ext = defaultdict(Counter)


    def _import_old_state(self):
        old = self.out / "state.tsv"                              # from the first version of this tool
        if not old.exists() or self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]:
            return
        with open(old, encoding="utf-8") as f:
            next(f, None)
            for line in f:
                c = line.rstrip("\n").split("\t")
                if len(c) == 6:
                    self.db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)", (c[0], int(c[1]), int(c[2]), c[3], c[4], c[5]))
                    if c[5]:
                        self.db.execute("INSERT OR IGNORE INTO shas VALUES (?,?,?)", (c[4], c[3], c[5]))


    def done_files(self) -> dict:
        return {r[0]: (r[1], r[2]) for r in self.db.execute("SELECT rel, size, mtime_ns FROM files")}


    def known_sha(self, sha):
        return self.db.execute("SELECT doc_id, text_path FROM shas WHERE sha=?", (sha,)).fetchone()


    def text_path(self, rel: str) -> Path:
        parts = rel.split("::")
        p = Path(parts[0] + ".txt") if len(parts) == 1 else Path(parts[0] + ".attachments")
        for i, child in enumerate(parts[1:], 1):
            p = p / (child + (".txt" if i == len(parts) - 1 else ".attachments"))
        return self.out / "text" / p


    def write_text(self, rec) -> Path:
        tp = self.text_path(rec["source_path"])
        os.makedirs(lp(tp.parent), exist_ok=True)
        src = rec.pop("copy_text_from", None)
        src_rel = rec.pop("copy_text_from_rel", None)
        if src_rel:
            src = self.text_path(src_rel).relative_to(self.out).as_posix()
        if rec.get("status") == "duplicate" and src:
            try:
                shutil.copyfile(lp(self.out / src), lp(tp))
                return tp
            except OSError:
                pass
        head = [f"# Source: {rec['source_path']}", f"# Status: {rec['status']}  |  Type: {rec.get('detected_format', '')}"
                f"  |  Pages: {rec['page_count']}  |  OCR pages: {rec['ocr_pages']}"]
        if rec.get("duplicate_of"):
            head.append(f"# Exact duplicate of doc {rec['duplicate_of']}")
        if rec["flags"]:
            head.append("# Flags: " + "; ".join(rec["flags"])[:2000])
        body = []
        for pg in rec.get("pages", []):
            label = f" ({pg['label']})" if pg.get("label") else ""
            conf = f", OCR confidence {pg['ocr_conf']}" if pg.get("ocr_conf") is not None else ""
            body.append(f"=== Page {pg['page']}{label} [{pg['method']}{conf}] ===\n{pg['text']}")
        for sec in rec.get("sections", []):
            body.append(f"=== {sec['name']} ===\n{sec['text']}")
        if rec.get("attachments"):
            body.append("=== Attachments ===\n" + "\n".join(a["name"] for a in rec["attachments"]))
        with open(lp(tp), "w", encoding="utf-8") as f:
            f.write("\n".join(head) + "\n\n" + "\n\n".join(body) + "\n")
        return tp


    def write(self, rec, key=None):
        tp = self.write_text(rec)
        rec["text_path"] = tp.relative_to(self.out).as_posix()
        self.corpus.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.inv.writerow([rec.get(c) if c not in ("methods", "flags") else ";".join(rec.get(c) or []) for c in self.INV_COLS])
        if key:
            self.db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                            (key[0], key[1], key[2], rec["doc_id"], rec["sha256"], rec["text_path"]))
        if rec.get("sha256") and rec["status"] not in ("duplicate", "error", "skipped"):
            self.db.execute("INSERT OR IGNORE INTO shas VALUES (?,?,?)", (rec["sha256"], rec["doc_id"], rec["text_path"]))
        self.stats[rec["status"]] += 1
        self.stats["ocr_pages"] += rec.get("ocr_pages", 0)
        self.stats["records"] += 1
        self.by_ext[rec["ext"] or "(none)"][rec["status"]] += 1
        for m in rec.get("methods", []):
            self.stats["method:" + m] += 1


    def flush(self):
        self.corpus.flush(); self.inv_f.flush(); self.db.commit()


    def close(self):
        self.flush(); self.corpus.close(); self.inv_f.close(); self.db.close()




def write_duplicate(writer, rel, size, mtime_s, mtime_ns, sha, orig_id, orig_text_path):
    """A byte-identical copy of a file already extracted: the record points at the original, the .txt is copied."""
    rec = _record_base(rel, size, mtime_s, sha)
    rec["doc_id"] = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16]
    rec.update(_dup_fields(orig_id))
    rec["copy_text_from"] = orig_text_path
    writer.write(rec, (rel, size, mtime_ns))




# --------------------------------------------------------------------------- #
# Inventory-only mode: what types are there, how big, what the content really is
# --------------------------------------------------------------------------- #
def inventory_only(src: Path, out: Path, excludes):
    out.mkdir(parents=True, exist_ok=True)
    by_ext, by_kind, rows = Counter(), Counter(), []
    size_by_ext = Counter()
    cloud = 0
    for p, rel, st, skip in walk(src, excludes):
        ext = p.suffix.lower() or "(none)"
        kind = extractor.detect_format(Path(lp(p))) if st else "error"
        if st is not None and IS_WIN and getattr(st, "st_file_attributes", 0) & CLOUD_ONLY_ATTRS:
            cloud += 1
        by_ext[ext] += 1; by_kind[kind] += 1; size_by_ext[ext] += st.st_size if st else 0
        rows.append([rel, ext, kind, st.st_size if st else "", skip or ""])
    with open(out / "inventory_formats.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["source_path", "ext", "detected_format", "size_bytes", "note"]); w.writerows(rows)
    print(f"\n{sum(by_ext.values()):,} files, {sum(size_by_ext.values())/1e9:.2f} GB")
    print(f"{'extension':<12}{'files':>9}{'GB':>9}")
    for ext, n in by_ext.most_common():
        print(f"{ext:<12}{n:>9,}{size_by_ext[ext]/1e9:>9.2f}")
    print("\nDetected by content:")
    for k, n in by_kind.most_common():
        print(f"  {k:<28}{n:>9,}")
    if cloud:
        print(f"\nWARNING: {cloud:,} files are cloud-only OneDrive placeholders; reading them downloads them.")
    print(f"\nWrote {out / 'inventory_formats.csv'}")




# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Offline text extraction for a folder of documents.")
    ap.add_argument("src", help="folder of documents, e.g. D:\\My Documents")
    ap.add_argument("out", help="output folder")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--lang", default="eng", help="Tesseract languages, e.g. eng or eng+spa")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--psm", type=int, default=4, help="Tesseract layout mode: 4 = line by line (default), 3 = detect columns")
    ap.add_argument("--max-ocr-pages", type=int, default=None, help="OCR at most N pages per document (default: all)")
    ap.add_argument("--exclude", action="append", default=[], help="glob on relative path or name; repeatable")
    ap.add_argument("--no-default-excludes", action="store_true", help=f"also process {DEFAULT_EXCLUDES[0]!r} and similar")
    ap.add_argument("--limit", type=int, default=None, help="stop after N new files (for a pilot)")
    ap.add_argument("--skip-cloud-only", action="store_true", help="skip OneDrive files that are not downloaded")
    ap.add_argument("--inventory-only", action="store_true", help="list file types without extracting")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)


    src, out = Path(a.src).resolve(), Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(out / "extract.log"), level=logging.DEBUG if a.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    excludes = a.exclude + ([] if a.no_default_excludes else DEFAULT_EXCLUDES)
    if a.inventory_only:
        return inventory_only(src, out, excludes)


    tools = ocr_config.describe()
    print("Tools:", json.dumps({k: v for k, v in tools.items() if not k.startswith("py:")}, indent=1))
    missing = [k for k in ("tesseract", "poppler_bin") if not tools.get(k)]
    if missing:
        print(f"WARNING: not found: {', '.join(missing)}. Scanned pages will not be OCR'd.")
    if not tools.get("antiword"):
        print("NOTE: antiword not found; .doc files use the built-in Word 97 reader (and LibreOffice if installed).")
    opts = dict(ocr_lang=a.lang, dpi=a.dpi, max_ocr_pages=a.max_ocr_pages, ocr_psm=a.psm)
    (out / "run_manifest.json").write_text(json.dumps({
        "started": datetime.now(timezone.utc).isoformat(), "source": str(src), "options": opts, "workers": a.workers,
        "extractor_version": extractor.EXTRACTOR_VERSION, "tools": tools, "python": sys.version}, indent=1), encoding="utf-8")


    writer = Writer(out)
    done = writer.done_files()
    t0 = time.time()
    submitted = new = skipped_done = dups = cloud = 0
    inflight = {}                  # future -> (key, rel, sha)
    pending_sha = {}               # sha -> future of the first copy being extracted
    waiting_dups = defaultdict(list)
    max_inflight = a.workers * 3


    print("Listing files ...", flush=True)
    entries = list(walk(src, excludes))
    # ordinary documents first, containers (emails, zips) last: their attachments then find their originals done
    entries.sort(key=lambda e: e[0].suffix.lower() in CONTAINER_EXTS)
    total = len(entries)
    print(f"{total:,} files to consider ({sum(1 for e in entries if e[0].suffix.lower() in CONTAINER_EXTS):,} emails/zips last)", flush=True)


    def drain(block: bool):
        nonlocal dups
        if not inflight:
            return
        finished, _ = wait(list(inflight), timeout=None if block else 0, return_when=FIRST_COMPLETED)
        for fut in finished:
            key, rel, sha = inflight.pop(fut)
            try:
                recs = fut.result()
            except Exception as e:                      # worker crashed (e.g. out of memory)
                rec = _record_base(rel, key[1], key[2] / 1e9, sha)
                rec.update({"status": "error", "flags": [f"worker_crashed:{type(e).__name__}"], "pages": [], "sections": [],
                            "page_count": 0, "ocr_pages": 0, "char_count": 0, "methods": [], "attachments": []})
                recs = [rec]
            for i, rec in enumerate(recs):
                writer.write(rec, key if i == 0 else None)
            top = recs[0]
            if pending_sha.get(sha) is fut:                   # junk files share no duplicates
                pending_sha.pop(sha)
                for dkey, drel, dsize, dmtime in waiting_dups.pop(sha, []):
                    write_duplicate(writer, drel, dsize, dmtime, dkey[2], sha, top["doc_id"], top["text_path"]); dups += 1
            writer.flush()
            n = writer.stats["records"]
            if n % 50 == 0:
                el = time.time() - t0
                print(f"  {n:,} records | {new + skipped_done:,}/{total:,} files | OCR pages {writer.stats['ocr_pages']:,} | "
                      f"{el/60:.1f} min", flush=True)


    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init_worker,
                             initargs=(opts, logging.DEBUG if a.verbose else logging.WARNING, str(writer.db_path))) as pool:
        containers_started = False
        for p, rel, st, skip in entries:
            if not containers_started and p.suffix.lower() in CONTAINER_EXTS:
                containers_started = True                              # finish every ordinary file first, so email and
                while inflight:                                        # zip attachments can reuse their results
                    drain(block=True)
            if st is None:
                rec = _record_base(rel, 0, None, "")
                rec.update({"status": "error", "flags": [skip], "pages": [], "sections": [], "page_count": 0,
                            "ocr_pages": 0, "char_count": 0, "methods": [], "attachments": []})
                writer.write(rec); continue
            key = (rel, st.st_size, st.st_mtime_ns)
            if done.get(rel) == (st.st_size, st.st_mtime_ns):
                skipped_done += 1; continue
            if IS_WIN and getattr(st, "st_file_attributes", 0) & CLOUD_ONLY_ATTRS:
                cloud += 1
                if a.skip_cloud_only:
                    continue
            if a.limit is not None and new >= a.limit:
                break
            try:
                sha = sha256_file(p)
            except OSError as e:
                rec = _record_base(rel, st.st_size, st.st_mtime, "")
                rec.update({"status": "error", "flags": [f"read_failed:{e}"], "pages": [], "sections": [], "page_count": 0,
                            "ocr_pages": 0, "char_count": 0, "methods": [], "attachments": []})
                writer.write(rec, None); continue
            new += 1
            if extractor.skip_reason_by_name(p):                       # junk is recorded as skipped, never as a duplicate
                fut = pool.submit(work, str(p), rel, st.st_size, st.st_mtime, sha)
                inflight[fut] = (key, rel, sha); submitted += 1
                continue
            if sha in pending_sha:                                     # duplicate of a file still being extracted
                waiting_dups[sha].append((key, rel, st.st_size, st.st_mtime)); continue
            known = writer.known_sha(sha)
            if known:                                                  # duplicate of a file finished earlier
                write_duplicate(writer, rel, st.st_size, st.st_mtime, st.st_mtime_ns, sha, known[0], known[1])
                dups += 1
                continue
            fut = pool.submit(work, str(p), rel, st.st_size, st.st_mtime, sha)
            inflight[fut] = (key, rel, sha)
            pending_sha[sha] = fut
            submitted += 1
            while len(inflight) >= max_inflight:
                drain(block=True)
        while inflight:
            drain(block=True)
    writer.close()


    elapsed = time.time() - t0
    summary = {"finished": datetime.now(timezone.utc).isoformat(), "minutes": round(elapsed / 60, 1),
               "files_new": new, "files_already_done": skipped_done, "exact_duplicates": dups,
               "cloud_only_files": cloud, "records_written": writer.stats["records"],
               "by_status": {k: v for k, v in writer.stats.items() if not k.startswith("method:") and k not in ("records", "ocr_pages")},
               "ocr_pages": writer.stats["ocr_pages"],
               "by_method": {k[7:]: v for k, v in writer.stats.items() if k.startswith("method:")},
               "by_extension": {k: dict(v) for k, v in sorted(writer.by_ext.items())}}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("minutes", "files_new", "files_already_done", "exact_duplicates",
                                              "records_written", "by_status", "ocr_pages")}, indent=1))
    print(f"Output: {out}")




if __name__ == "__main__":
    main()
