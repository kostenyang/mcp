#!/usr/bin/env python3
"""
VMware docs RAG — offline ingestion script.

Builds the vector index that the `search_vmware_docs` MCP tool queries.
This runs OFFLINE (manually or via cron). The raw documents never enter
Claude's context — only the top-k chunks the tool returns at query time.

Layout it expects:

    docs-src/
      official/   ← VCF 9.1 / vSphere / vSAN doc PDFs, release notes
      kb/         ← Broadcom KB articles (saved as .pdf / .html / .txt)
    <vcf9.1-lab repo>/**/*.md   ← this lab's own troubleshooting notes

Each file's `source` tag comes from where it was found:
  docs-src/official/* → "official"
  docs-src/kb/*       → "kb"
  --lab-notes repo    → "lab-notes"

Supported file types: .pdf .md .txt .html .htm

Usage:
    python3 ingest_docs.py \
        --docs-src   ./docs-src \
        --lab-notes  /path/to/vcf9.1-lab \
        --index-dir  /opt/vcf-mcp/docs-index

Re-run whenever the docs change — it rebuilds the collection from scratch.

Dependencies (beyond the server's):
    pip install chromadb sentence-transformers pypdf
"""

import argparse
import datetime
import hashlib
import html
import os
import pathlib
import re
import sys

# ── Defaults (override via CLI / env) ─────────────────────────────────────────
DEFAULT_INDEX_DIR  = os.getenv("VMWARE_DOCS_INDEX_DIR", "/opt/vcf-mcp/docs-index")
DEFAULT_EMBED_MODEL = os.getenv("VMWARE_DOCS_EMBED_MODEL", "BAAI/bge-m3")
COLLECTION_NAME    = "vmware_docs"

# Chunk sizing in characters. ~3500 chars ≈ ~900 tokens for English;
# overlap keeps a sentence that straddles a boundary retrievable from both sides.
CHUNK_CHARS   = 3500
OVERLAP_CHARS = 500

TEXT_SUFFIXES = {".md", ".txt"}
HTML_SUFFIXES = {".html", ".htm"}
ALL_SUFFIXES  = TEXT_SUFFIXES | HTML_SUFFIXES | {".pdf"}

ADD_BATCH = 256  # chunks per collection.add() call — bounds embedding memory


# ── File loaders ──────────────────────────────────────────────────────────────

def load_pdf(path: pathlib.Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def load_html(path: pathlib.Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", raw,
                 flags=re.S | re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return re.sub(r"[ \t]*\n[ \t]*", "\n", raw)


def load_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_file(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return load_pdf(path)
    if suffix in HTML_SUFFIXES:
        return load_html(path)
    return load_text(path)


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list[str]:
    """Paragraph-aware char chunking with overlap. Never splits mid-paragraph
    unless a single paragraph is itself larger than CHUNK_CHARS."""
    text = text.strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > CHUNK_CHARS:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(para), CHUNK_CHARS - OVERLAP_CHARS):
                chunks.append(para[i:i + CHUNK_CHARS])
            continue
        if buf and len(buf) + len(para) + 2 > CHUNK_CHARS:
            chunks.append(buf)
            buf = buf[-OVERLAP_CHARS:] + "\n\n" + para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


# ── Source discovery ──────────────────────────────────────────────────────────

def iter_docs_src(docs_src: pathlib.Path):
    """Yield (path, source_tag) for files under docs-src/official and docs-src/kb."""
    for tag in ("official", "kb"):
        sub = docs_src / tag
        if not sub.is_dir():
            continue
        for path in sorted(sub.rglob("*")):
            if path.is_file() and path.suffix.lower() in ALL_SUFFIXES:
                yield path, tag


def iter_lab_notes(repo: pathlib.Path):
    """Yield (path, 'lab-notes') for every .md file in the lab repo."""
    for path in sorted(repo.rglob("*.md")):
        if path.is_file():
            yield path, "lab-notes"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Build the VMware docs RAG index.")
    ap.add_argument("--docs-src", default="./docs-src",
                    help="dir with official/ and kb/ subfolders")
    ap.add_argument("--lab-notes", default=None,
                    help="path to the vcf9.1-lab repo (its *.md get indexed)")
    ap.add_argument("--index-dir", default=DEFAULT_INDEX_DIR,
                    help="Chroma persistent dir to write")
    ap.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL,
                    help="sentence-transformers model name")
    args = ap.parse_args()

    import chromadb
    from chromadb.utils import embedding_functions

    sources: list[tuple[pathlib.Path, str]] = []
    docs_src = pathlib.Path(args.docs_src)
    if docs_src.is_dir():
        sources.extend(iter_docs_src(docs_src))
    else:
        print(f"warning: --docs-src {docs_src} not found, skipping official/kb",
              file=sys.stderr)
    if args.lab_notes:
        repo = pathlib.Path(args.lab_notes)
        if repo.is_dir():
            sources.extend(iter_lab_notes(repo))
        else:
            print(f"warning: --lab-notes {repo} not found, skipping",
                  file=sys.stderr)

    if not sources:
        print("error: no source files found — nothing to ingest", file=sys.stderr)
        return 1

    # Build chunks with metadata.
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    per_source: dict[str, int] = {}
    for path, tag in sources:
        try:
            text = load_file(path)
        except Exception as exc:
            print(f"warning: failed to read {path}: {exc}", file=sys.stderr)
            continue
        chunks = chunk_text(text)
        if not chunks:
            print(f"warning: no text extracted from {path}", file=sys.stderr)
            continue
        for idx, chunk in enumerate(chunks):
            cid = hashlib.sha1(f"{path}:{idx}".encode()).hexdigest()
            ids.append(cid)
            documents.append(chunk)
            metadatas.append({
                "source": tag,
                "title": path.stem,
                "path": str(path),
                "chunk": idx,
            })
        per_source[tag] = per_source.get(tag, 0) + len(chunks)
        print(f"  {tag:10s} {path.name}  ({len(chunks)} chunks)")

    print(f"\nTotal: {len(documents)} chunks from {len(sources)} files")

    # Rebuild the collection from scratch — deterministic, no stale chunks.
    client = chromadb.PersistentClient(path=args.index_dir)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=args.embed_model)
    collection = client.create_collection(
        COLLECTION_NAME,
        embedding_function=ef,
        metadata={
            "hnsw:space": "cosine",
            "embed_model": args.embed_model,
            "ingested_at": datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds"),
        },
    )

    print(f"Embedding with {args.embed_model} (first run downloads the model)…")
    for start in range(0, len(documents), ADD_BATCH):
        end = start + ADD_BATCH
        collection.add(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )
        print(f"  embedded {min(end, len(documents))}/{len(documents)}")

    print(f"\nDone. Index at {args.index_dir}")
    for tag, n in sorted(per_source.items()):
        print(f"  {tag}: {n} chunks")
    print("Restart the MCP server so search_vmware_docs picks up the new index.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
