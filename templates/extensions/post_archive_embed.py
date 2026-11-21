#!/usr/bin/env python3
"""Decoupled post-archival EmbeddingGemma 2 indexing sink for Facet.

When ``enable_post_archive_embed: true`` is configured in ``.pipeline_config.json``
(or when invoked directly from the command line), this helper splits completed
``specs/<slug>/`` artifacts into overlapping text windows, embeds them via
Ollama ``POST /api/embed`` using ``embeddinggemma-2:740m-mxfp8``, normalizes each
vector to unit length, and stores them in ``$COPILOT_HOME/knowledge.db`` for
semantic recall on future runs.

If Ollama or ``embeddinggemma-2`` is offline, archival still completes without error.

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Default local Ollama embedding model and the archived files indexed per run.
EMBED_MODEL: str = "embeddinggemma-2:740m-mxfp8"
ARCHIVED_ARTIFACTS: Tuple[str, ...] = (
    "SOURCE_OCR.md",
    "RESEARCH.md",
    "manifest.json",
    "mapping.json",
    "VERIFICATION.md",
    "PROVENANCE.json",
)
# Window size and overlap in characters so equations spanning chunk borders stay searchable.
DEFAULT_CHUNK_CHARS: int = 2000
DEFAULT_OVERLAP_CHARS: int = 200


def get_default_db_path() -> Path:
    """Resolve the default SQLite knowledge-store path.

    Returns:
        Path to ``knowledge.db`` inside ``$COPILOT_HOME`` or ``~/.copilot``.
    """
    env_home = os.environ.get("COPILOT_HOME", "").strip()
    base = Path(env_home).expanduser() if env_home else (Path.home() / ".copilot")
    return base / "knowledge.db"


def get_ollama_base_url() -> str:
    """Resolve the configured Ollama HTTP endpoint base URL.

    Returns:
        Normalized ``http://`` or ``https://`` base URL without a trailing slash.
    """
    # Check standard Ollama host environment variables before falling back to localhost.
    raw = (os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434").strip()
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw.rstrip("/")


def chunk_artifact_text(
    text: str,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> List[str]:
    r"""Split artifact text into overlapping character windows.

    Prefers splitting on newline boundaries inside the second half of a window
    so paragraphs and JSON objects stay intact when possible.

    Args:
        text: Raw document text to segment.
        max_chars: Maximum character length per chunk.
        overlap: Character overlap retained between adjacent chunks.

    Returns:
        Non-empty list of trimmed text chunks.
    """
    cleaned = text.strip()
    if not cleaned:
        return []
    # Short files fit inside a single chunk with no splitting needed.
    if len(cleaned) <= max_chars:
        return [cleaned]
    # Advance by at least 256 characters per window so progress is strictly forward.
    step = max(max_chars - max(overlap, 0), 256)
    chunks: List[str] = []
    start = 0
    total = len(cleaned)
    # Repeat step-by-step while `start < total`.
    while start < total:
        end = min(start + max_chars, total)
        # Snap the cut point backward to the nearest newline in the second half of the
        # window so lines and JSON fields are not cut mid-token.
        if end < total:
            nl = cleaned.rfind("\n", start + step // 2, end)
            if nl > start:
                end = nl
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        # Stop scanning further once `end >= total`.
        if end >= total:
            break
        # Step back by `overlap` characters so context carries across adjacent chunks.
        start = max(end - overlap, start + 1)
    return chunks


def format_document_prompt(
    concept_slug: str,
    artifact_name: str,
    chunk_index: int,
    chunk_text: str,
) -> str:
    """Format a corpus chunk using EmbeddingGemma 2's document prompt.

    Args:
        concept_slug: Archived specification directory name.
        artifact_name: Source filename (for example ``manifest.json``).
        chunk_index: Zero-based chunk index within ``artifact_name``.
        chunk_text: Text slice to embed.

    Returns:
        Prefixed string ``"title: <slug>/<artifact> (chunk i) | text: ..."``.
    """
    # EmbeddingGemma 2 expects `"title: ... | text: ..."` when indexing documents
    # and `"task: ... | query: ..."` when embedding user search queries.
    return f"title: {concept_slug}/{artifact_name} (chunk {chunk_index}) | text: {chunk_text}"


def format_query_prompt(query_text: str, code_retrieval: bool = False) -> str:
    """Format a search query using EmbeddingGemma 2's asymmetric query prompt.

    Args:
        query_text: Natural-language or technical search query.
        code_retrieval: When ``True``, emit the ``code retrieval`` task prefix
            instead of ``search result``.

    Returns:
        Prefixed query prompt string ready for embedding.
    """
    task_name = "code retrieval" if code_retrieval else "search result"
    return f"task: {task_name} | query: {query_text.strip()}"


def l2_normalize(vec: Sequence[float]) -> List[float]:
    """Return the unit-length L2-normalized copy of ``vec``.

    Args:
        vec: Input numeric embedding vector.

    Returns:
        Float list normalized to unit Euclidean norm (unchanged if near-zero).
    """
    # Normalizing every vector to length 1 lets cosine similarity reduce to a simple dot product.
    norm = math.sqrt(sum(float(v) * float(v) for v in vec))
    if norm <= 1e-15:
        return [float(v) for v in vec]
    return [float(v) / norm for v in vec]


def init_db(conn: sqlite3.Connection) -> None:
    """Create the ``specification_chunks`` table if it does not yet exist.

    Args:
        conn: Open SQLite database connection.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS specification_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            concept_slug TEXT NOT NULL,
            artifact_name TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL DEFAULT 0,
            embedding_blob BLOB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(concept_slug, artifact_name, chunk_index)
        );
        """
    )
    conn.commit()


def fetch_embeddings_batch(
    inputs: Sequence[str],
    endpoint_base: Optional[str] = None,
    model: str = EMBED_MODEL,
) -> List[List[float]]:
    r"""Query Ollama ``/api/embed`` and L2-normalize the returned vectors.

    Args:
        inputs: Sequence of pre-formatted prompt strings to embed.
        endpoint_base: Optional Ollama base URL override.
        model: Ollama embedding model identifier.

    Returns:
        List of L2-normalized embedding vectors matching ``inputs``, or ``[]``
        if Ollama is offline or returns an incomplete batch.
    """
    if not inputs:
        return []
    base = (endpoint_base or get_ollama_base_url()).rstrip("/")
    embed_url = f"{base}/api/embed"
    payload = json.dumps({"model": model, "input": list(inputs), "truncate": True}).encode("utf-8")
    # Send bounded loopback HTTP request and fail gracefully if the service is offline.
    try:
        req = urllib.request.Request(embed_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        # Open HTTP response stream and close the socket automatically when done.
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_embs = data.get("embeddings")
            # Process list elements once we confirm `isinstance(raw_embs, list) and len(raw_embs) == len(inputs)`.
            if isinstance(raw_embs, list) and len(raw_embs) == len(inputs):
                return [l2_normalize(vec) for vec in raw_embs if isinstance(vec, list) and vec]
    except Exception as exc:
        sys.stderr.write(f"Warning: Failed to query {model} at {embed_url}: {exc}\n")
    return []


def index_archived_directory(
    target_dir: Path,
    db_path: Optional[Path] = None,
    endpoint_base: Optional[str] = None,
) -> int:
    """Chunk, embed, and idempotently upsert archived artifacts into SQLite.

    Args:
        target_dir: Archived ``specs/<concept_slug>/`` directory to index.
        db_path: Optional override path for the SQLite vector store.
        endpoint_base: Optional Ollama base URL override.

    Returns:
        Total number of chunks indexed across all found artifacts.
    """
    if not target_dir.is_dir():
        return 0
    resolved_db = db_path if db_path is not None else get_default_db_path()
    resolved_db.parent.mkdir(parents=True, exist_ok=True)
    concept_slug = target_dir.name

    conn = sqlite3.connect(str(resolved_db))
    indexed_chunks = 0
    # Guard optional step so unexpected runtime errors degrade safely instead of crashing.
    try:
        init_db(conn)
        # Step through `ARCHIVED_ARTIFACTS` (`artifact`) to update or verify each item.
        for artifact in ARCHIVED_ARTIFACTS:
            fpath = target_dir / artifact
            # Skip this iteration when `not fpath.is_file()`.
            if not fpath.is_file():
                continue
            raw_text = fpath.read_text(encoding="utf-8", errors="replace")
            chunks = chunk_artifact_text(raw_text)
            # Skip this iteration when `not chunks`.
            if not chunks:
                continue
            prompts = [format_document_prompt(concept_slug, artifact, idx, ch) for idx, ch in enumerate(chunks)]
            vectors = fetch_embeddings_batch(prompts, endpoint_base=endpoint_base)
            # Skip this iteration when `len(vectors) != len(chunks)`.
            if len(vectors) != len(chunks):
                continue
            # Prune stale trailing chunks if an updated artifact shrunk on re-index.
            conn.execute(
                "DELETE FROM specification_chunks WHERE concept_slug = ? AND artifact_name = ? AND chunk_index >= ?",
                (concept_slug, artifact, len(chunks)),
            )
            # Step through `enumerate(zip(chunks, vectors))` (`(idx, (chunk_str, vec))`) to update or verify each item.
            for idx, (chunk_str, vec) in enumerate(zip(chunks, vectors)):
                conn.execute(
                    """
                    INSERT INTO specification_chunks
                        (concept_slug, artifact_name, chunk_index, content, embedding_dim, embedding_blob)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(concept_slug, artifact_name, chunk_index) DO UPDATE SET
                        content = excluded.content,
                        embedding_dim = excluded.embedding_dim,
                        embedding_blob = excluded.embedding_blob,
                        created_at = CURRENT_TIMESTAMP
                    """,
                    (concept_slug, artifact, idx, chunk_str, len(vec), json.dumps(vec).encode("utf-8")),
                )
                indexed_chunks += 1
        conn.commit()
    finally:
        conn.close()
    return indexed_chunks


def query_knowledge_store(
    query: str,
    top_k: int = 5,
    db_path: Optional[Path] = None,
    endpoint_base: Optional[str] = None,
) -> List[Dict[str, object]]:
    """Compute cosine similarity against L2-normalized specification chunks.

    Args:
        query: Search query string.
        top_k: Maximum number of ranked matches to return.
        db_path: Optional override path for the SQLite vector store.
        endpoint_base: Optional Ollama base URL override.

    Returns:
        Top-``k`` match dictionaries sorted by descending cosine similarity.
    """
    resolved_db = db_path if db_path is not None else get_default_db_path()
    if not resolved_db.is_file():
        return []
    # Embed the user query once, then compute exact dot-product cosine similarity
    # against every stored unit-length specification chunk in SQLite.
    q_vecs = fetch_embeddings_batch([format_query_prompt(query)], endpoint_base=endpoint_base)
    if not q_vecs:
        return []
    q_vec = q_vecs[0]
    conn = sqlite3.connect(str(resolved_db))
    scored: List[Dict[str, object]] = []
    # Guard optional step so unexpected runtime errors degrade safely instead of crashing.
    try:
        rows = conn.execute(
            "SELECT concept_slug, artifact_name, chunk_index, content, embedding_blob FROM specification_chunks"
        ).fetchall()
        # Step through `rows` (`(slug, art, cidx...`) to update or verify each item.
        for slug, art, cidx, content, blob in rows:
            doc_vec = json.loads(blob.decode("utf-8") if isinstance(blob, bytes) else str(blob))
            if len(doc_vec) == len(q_vec):
                # Because both vectors are L2-normalized, `sum(a * b)` equals cosine similarity.
                sim = sum(float(a) * float(b) for a, b in zip(q_vec, doc_vec))
                scored.append({
                    "score": round(sim, 6),
                    "concept_slug": slug,
                    "artifact_name": art,
                    "chunk_index": int(cidx),
                    "snippet": str(content)[:240],
                })
    finally:
        conn.close()
    # Rank matches from highest similarity to lowest and return the top `k`.
    scored.sort(key=lambda r: float(r["score"]), reverse=True)  # type: ignore[arg-type]
    return scored[: max(top_k, 1)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the CLI indexer or semantic query entrypoint.

    Args:
        argv: Optional argument sequence overriding ``sys.argv[1:]``.

    Returns:
        Zero exit status on completion.
    """
    parser = argparse.ArgumentParser(description="Facet Post-Archival EmbeddingGemma 2 SQLite Indexer")
    parser.add_argument("target_dir", nargs="?", default=None, help="Path to specs/<concept_slug> directory to index")
    parser.add_argument("--db-path", type=str, default=None, help="Override SQLite knowledge.db path")
    parser.add_argument("--query", type=str, default=None, help="Run semantic search query against knowledge.db")
    parser.add_argument("--top-k", type=int, default=5, help="Number of top semantic matches to return")
    args = parser.parse_args(argv)

    db_override = Path(args.db_path).expanduser().resolve() if args.db_path else None
    # Update local state when `args.query`.
    if args.query:
        hits = query_knowledge_store(args.query, top_k=args.top_k, db_path=db_override)
        print(json.dumps({"query": args.query, "results": hits}, indent=2))
        return 0

    if not args.target_dir:
        print("Usage: post_archive_embed.py <specs_concept_directory> [--db-path PATH] | --query TEXT")
        return 0

    target = Path(args.target_dir)
    # Return `0` early if the target path is absent on disk (`not target.exists()`).
    if not target.exists():
        return 0

    count = index_archived_directory(target, db_path=db_override)
    resolved_db = db_override if db_override is not None else get_default_db_path()
    print(f"[OK] Indexed {count} chunk(s) for '{target.name}' into local knowledge store ({resolved_db}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
