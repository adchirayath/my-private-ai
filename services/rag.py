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
from core.db import documents_collection
from services.chunker import split_text
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
) -> int:
    """
    Split text into boundary-aware chunks, generate embeddings,
    and persist into ChromaDB.
    """
    chunks = split_text(text)
    if not chunks:
        raise ValueError("No readable text was extracted from this file.")

    embeddings = await ollama_embed(chunks, prefix="search_document: ")

    ids = [f"{file_id}-{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "workspace": workspace,
            "file_id": file_id,
            "filename": filename,
            "chunk_index": i,
        }
        for i in range(len(chunks))
    ]

    documents_collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    return len(chunks)


# ---------------------------------------------------------
# Hybrid Search (BM25 + ChromaDB + Reciprocal Rank Fusion)
# ---------------------------------------------------------
async def search_documents_hybrid(
    workspace: str,
    question: str,
    top_k: int = RETRIEVAL_TOP_K,
) -> list[dict]:
    """
    Performs hybrid retrieval combining:
    1. Dense semantic vectors from ChromaDB
    2. Sparse BM25 keyword matching (for exact terms, part numbers, names, IDs)
    3. Merged via Reciprocal Rank Fusion (RRF).
    """
    if is_greeting_or_smalltalk(question):
        return []

    total_elements = documents_collection.count()
    if total_elements == 0:
        return []

    safe_top_k = max(1, min(top_k * 3, total_elements, 30))

    # 1. Dense Semantic Query
    query_embeddings = await ollama_embed([question], prefix="search_query: ")
    query_emb = query_embeddings[0] if query_embeddings else None

    dense_candidates = []
    dense_id_to_rank = {}
    dense_id_to_dist = {}

    if query_emb:
        try:
            dense_res = documents_collection.query(
                query_embeddings=[query_emb],
                n_results=safe_top_k,
                where={"workspace": workspace},
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
                dense_candidates.append({
                    "id": doc_id,
                    "text": doc,
                    "filename": meta.get("filename", "unknown"),
                    "file_id": meta.get("file_id", ""),
                    "distance": dist,
                })
        except Exception:
            pass

    # 2. Sparse BM25 Query on Workspace Chunks
    all_ws_data = documents_collection.get(
        where={"workspace": workspace},
        include=["documents", "metadatas"],
    )
    ws_ids = all_ws_data.get("ids", [])
    ws_docs = all_ws_data.get("documents", [])
    ws_metas = all_ws_data.get("metadatas", [])

    bm25_id_to_rank = {}
    chunk_catalog = {}

    for doc_id, doc, meta in zip(ws_ids, ws_docs, ws_metas):
        chunk_catalog[doc_id] = {
            "id": doc_id,
            "text": doc,
            "filename": meta.get("filename", "unknown"),
            "file_id": meta.get("file_id", ""),
            "distance": dense_id_to_dist.get(doc_id, None),
        }

    if ws_docs:
        bm25 = BM25Index()
        bm25.fit(ws_docs)
        scores = bm25.get_scores(question)

        # Pair with ids and sort by score descending
        scored_pairs = [(ws_ids[i], s) for i, s in enumerate(scores) if s > 0.0]
        scored_pairs.sort(key=lambda x: x[1], reverse=True)

        for rank, (doc_id, _) in enumerate(scored_pairs[:safe_top_k], start=1):
            bm25_id_to_rank[doc_id] = rank

    # 3. Reciprocal Rank Fusion (RRF)
    # RRF score = sum(1 / (60 + rank))
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

    # Return top_k merged results
    final_matches = []
    for doc_id, _ in rrf_scores[:top_k]:
        item = chunk_catalog.get(doc_id)
        if item:
            final_matches.append({
                "text": item["text"],
                "filename": item["filename"],
                "file_id": item["file_id"],
                "distance": item["distance"],
            })

    return final_matches

