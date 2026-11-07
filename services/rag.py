"""
services/rag.py - Hybrid Retrieval-Augmented Generation Engine
--------------------------------------------------------------
Why Hybrid Search?
- Vector Search (ChromaDB): Excellent at matching concepts and synonyms
  (e.g., searching "automobile" will match "car").
- BM25 Keyword Search: Excellent at matching specific names, IDs, numbers,
  and codes (e.g., "Error 404", "Project-X-99", "INV-2026-001").

By fusing both using Reciprocal Rank Fusion (RRF), we get the best of both worlds!
"""

import math
import re
from collections import Counter
from typing import Optional

from core.config import RETRIEVAL_TOP_K, MAX_RETRIEVAL_DISTANCE
from core.db import (
    documents_collection,
    index_fts_chunks,
    search_fts_chunks,
)
from services.chunker import split_text, split_text_with_metadata
from services.ollama import ollama_embed


# ---------------------------------------------------------
# Conversational / Greeting Intent Detection
# ---------------------------------------------------------
COMMON_GREETINGS = {
    "hi", "hello", "hey", "hey there", "hi there", "hello there",
    "good morning", "good afternoon", "good evening", "good day",
    "howdy", "hola", "yo", "sup", "greetings",
    "how are you", "how are you doing", "how are you today",
    "how is it going", "hows it going",
    "who are you", "what are you", "what can you do", "help",
    "thanks", "thank you", "thank you so much", "thanks a lot",
    "bye", "goodbye", "see you", "see ya", "have a good day",
    "yes", "yeah", "yep", "no", "nope", "ok", "okay", "sure", "alright",
    "cool", "got it", "i see", "never mind", "nevermind",
}


def is_greeting_or_smalltalk(text: str) -> bool:
    """
    Check if user input is a casual greeting, farewell, or pleasantry
    that should not trigger heavy document vector retrieval.
    """
    if not text:
        return False
    cleaned = re.sub(r"[^\w\s]", "", text.lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned in COMMON_GREETINGS


# ---------------------------------------------------------
# BM25 Pure-Python Sparse Search Engine
# ---------------------------------------------------------
class BM25Index:
    """
    Lightweight, vectorized BM25Okapi implementation for exact keyword
    and token-level matching across document chunks.
    """
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = 0
        self.avgdl = 0.0
        self.doc_lengths = []
        self.doc_freqs = []
        self.idf = {}

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    def fit(self, documents: list[str]) -> None:
        self.corpus_size = len(documents)
        if self.corpus_size == 0:
            return

        self.doc_lengths = []
        self.doc_freqs = []
        df = Counter()

        for doc in documents:
            tokens = self._tokenize(doc)
            self.doc_lengths.append(len(tokens))
            term_frequencies = Counter(tokens)
            self.doc_freqs.append(term_frequencies)
            for term in term_frequencies:
                df[term] += 1

        self.avgdl = sum(self.doc_lengths) / self.corpus_size if self.corpus_size > 0 else 0.0

        # Calculate BM25 IDF
        self.idf = {}
        for term, freq in df.items():
            # Standard Lucene/BM25 IDF formula
            self.idf[term] = math.log(1.0 + (self.corpus_size - freq + 0.5) / (freq + 0.5))

    def get_scores(self, query: str) -> list[float]:
        if self.corpus_size == 0:
            return []

        query_tokens = self._tokenize(query)
        scores = [0.0] * self.corpus_size

        for term in query_tokens:
            if term not in self.idf:
                continue
            idf_val = self.idf[term]
            for idx, doc_freq in enumerate(self.doc_freqs):
                freq = doc_freq.get(term, 0)
                if freq == 0:
                    continue
                doc_len = self.doc_lengths[idx]
                numerator = freq * (self.k1 + 1.0)
                denominator = freq + self.k1 * (1.0 - self.b + self.b * (doc_len / self.avgdl))
                scores[idx] += idf_val * (numerator / denominator)

        return scores


# ---------------------------------------------------------
# Document Indexing
# ---------------------------------------------------------
async def index_document(
    workspace: str,
    file_id: str,
    filename: str,
    text: str,
    user_id: Optional[int] = None,
) -> int:
    """
    Split text into boundary-aware chunks with page tracking,
    generate embeddings, and persist into ChromaDB and SQLite FTS5 (scoped to user).
    """
    chunks_meta = split_text_with_metadata(text)
    if not chunks_meta:
        raise ValueError("No readable text was extracted from this file.")

    chunks = [c["content"] for c in chunks_meta]
    embeddings = await ollama_embed(chunks, prefix="search_document: ")

    uid_val = int(user_id) if user_id is not None else 0
    ids = [f"{file_id}-{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "workspace": workspace,
            "file_id": file_id,
            "filename": filename,
            "chunk_index": i,
            "page_number": chunks_meta[i].get("page_number") or "",
            "user_id": uid_val,
        }
        for i in range(len(chunks))
    ]

    documents_collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    # Fast persistent SQLite FTS5 index for BM25 ranking (scoped to user)
    fts_chunks = [
        {
            "id": ids[i],
            "content": chunks[i],
            "page_number": chunks_meta[i].get("page_number") or "",
        }
        for i in range(len(chunks))
    ]
    try:
        index_fts_chunks(workspace, file_id, filename, fts_chunks, user_id=user_id)
    except Exception:
        pass

    return len(chunks)


# ---------------------------------------------------------
# Hybrid Search (BM25 via SQLite FTS5 + ChromaDB + Reciprocal Rank Fusion)
# ---------------------------------------------------------
async def search_documents_hybrid(
    workspace: str,
    question: str,
    top_k: int = RETRIEVAL_TOP_K,
    user_id: Optional[int] = None,
) -> list[dict]:
    """
    Performs hybrid retrieval combining:
    1. Dense semantic vectors from ChromaDB (scoped to user)
    2. Sub-millisecond sparse BM25 keyword matching via SQLite FTS5 (scoped to user)
    3. Merged via Reciprocal Rank Fusion (RRF).
    """
    if is_greeting_or_smalltalk(question):
        return []

    total_elements = documents_collection.count()
    if total_elements == 0:
        return []

    safe_top_k = max(1, min(top_k * 3, total_elements, 30))

    where_filter = (
        {"$and": [{"workspace": workspace}, {"user_id": int(user_id)}]}
        if user_id is not None
        else {"workspace": workspace}
    )

    # 1. Dense Semantic Query
    query_embeddings = await ollama_embed([question], prefix="search_query: ")
    query_emb = query_embeddings[0] if query_embeddings else None

    dense_candidates = []
    dense_id_to_rank = {}
    dense_id_to_dist = {}
    chunk_catalog = {}

    if query_emb:
        try:
            dense_res = documents_collection.query(
                query_embeddings=[query_emb],
                n_results=safe_top_k,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
            dense_ids = dense_res.get("ids", [[]])[0]
            dense_docs = dense_res.get("documents", [[]])[0]
            dense_metas = dense_res.get("metadatas", [[]])[0]
            dense_dists = dense_res.get("distances", [[]])[0]

            for rank, (doc_id, doc, meta, dist) in enumerate(
                zip(dense_ids, dense_docs, dense_metas, dense_dists), start=1
            ):
                dense_id_to_rank[doc_id] = rank
                dense_id_to_dist[doc_id] = dist
                chunk_catalog[doc_id] = {
                    "id": doc_id,
                    "text": doc,
                    "filename": meta.get("filename", "unknown"),
                    "file_id": meta.get("file_id", ""),
                    "page_number": meta.get("page_number", ""),
                    "distance": dist,
                }
                dense_candidates.append(chunk_catalog[doc_id])
        except Exception:
            pass

    # 2. Sparse BM25 Query: Try fast SQLite FTS5 index first (scoped to user)
    bm25_id_to_rank = {}
    try:
        fts_matches = search_fts_chunks(workspace, question, limit=safe_top_k, user_id=user_id)
        for rank, match in enumerate(fts_matches, start=1):
            doc_id = match["id"]
            bm25_id_to_rank[doc_id] = rank
            if doc_id not in chunk_catalog:
                chunk_catalog[doc_id] = {
                    "id": doc_id,
                    "text": match["text"],
                    "filename": match["filename"],
                    "file_id": match["file_id"],
                    "page_number": match.get("page_number", ""),
                    "distance": dense_id_to_dist.get(doc_id, None),
                }
    except Exception:
        pass

    # Fallback to in-memory BM25 if FTS5 had no matches but chunks exist
    if not bm25_id_to_rank:
        try:
            all_ws_data = documents_collection.get(
                where=where_filter,
                include=["documents", "metadatas"],
            )
            ws_ids = all_ws_data.get("ids", [])
            ws_docs = all_ws_data.get("documents", [])
            ws_metas = all_ws_data.get("metadatas", [])

            for doc_id, doc, meta in zip(ws_ids, ws_docs, ws_metas):
                if doc_id not in chunk_catalog:
                    chunk_catalog[doc_id] = {
                        "id": doc_id,
                        "text": doc,
                        "filename": meta.get("filename", "unknown"),
                        "file_id": meta.get("file_id", ""),
                        "page_number": meta.get("page_number", ""),
                        "distance": dense_id_to_dist.get(doc_id, None),
                    }

            if ws_docs:
                bm25 = BM25Index()
                bm25.fit(ws_docs)
                scores = bm25.get_scores(question)
                scored_pairs = [(ws_ids[i], s) for i, s in enumerate(scores) if s > 0.0]
                scored_pairs.sort(key=lambda x: x[1], reverse=True)
                for rank, (doc_id, _) in enumerate(scored_pairs[:safe_top_k], start=1):
                    bm25_id_to_rank[doc_id] = rank
        except Exception:
            pass

    # 3. Reciprocal Rank Fusion (RRF)
    all_candidate_ids = set(dense_id_to_rank.keys()) | set(bm25_id_to_rank.keys())
    rrf_scores = []

    for doc_id in all_candidate_ids:
        dense_rank = dense_id_to_rank.get(doc_id, None)
        bm25_rank = bm25_id_to_rank.get(doc_id, None)

        score = 0.0
        if dense_rank is not None:
            score += 1.0 / (60.0 + dense_rank)
        if bm25_rank is not None:
            score += 1.0 / (60.0 + bm25_rank)

        # Distance guardrail: if dense search returned a distance and it's too far, discard
        dist = dense_id_to_dist.get(doc_id, None)
        if dist is not None and dist > MAX_RETRIEVAL_DISTANCE and bm25_rank is None:
            continue

        rrf_scores.append((doc_id, score))

    rrf_scores.sort(key=lambda x: x[1], reverse=True)

    # Return top_k merged results with rich snippet & page citation metadata
    final_matches = []
    for doc_id, score_val in rrf_scores[:top_k]:
        item = chunk_catalog.get(doc_id)
        if item:
            snippet = item["text"][:280] + ("…" if len(item["text"]) > 280 else "")
            final_matches.append({
                "text": item["text"],
                "filename": item["filename"],
                "file_id": item["file_id"],
                "page_number": item.get("page_number") or None,
                "distance": item.get("distance"),
                "snippet": snippet,
                "score": round(score_val, 4),
            })

    return final_matches

