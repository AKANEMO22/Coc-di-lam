"""Hybrid retrieval over uploaded documents, with metadata and source provenance."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from rank_bm25 import BM25Plus

from .contracts import RetrievalCollection, RetrievalQuery, RetrievedChunk
from .documents import DocumentStore
from .ontology import tokens


def _values(value: Any) -> list[Any]:
    return value if isinstance(value, (list, tuple, set)) else [value]


def _matches(metadata: dict, filters: dict) -> bool:
    for key, expected in filters.items():
        if key not in metadata:
            return False
        actual_values = _values(metadata[key])
        expected_values = _values(expected)
        if not any(str(a).casefold() == str(e).casefold() for a in actual_values for e in expected_values):
            return False
    return True


class DocumentRetriever:
    def __init__(self, store: DocumentStore):
        self.store = store

    def search(self, query: RetrievalQuery, kb_version: str, base_client) -> list[RetrievedChunk]:
        # The existing adapter owns the structured question/rubric/role semantics.
        collection = str(getattr(query.collection, "value", query.collection))
        base_query = replace(query, filters={k: v for k, v in query.filters.items() if k != "knowledge_base_id"}, top_k=min(100, max(query.top_k * 3, 20)))
        base_version = self.store.version_base(query.knowledge_base_id, kb_version)
        if base_version != base_client.version:
            raise ValueError("Requested seed index version is not loaded")
        base_chunks = base_client._search_sync(base_query)
        if collection != RetrievalCollection.COMPANY_KNOWLEDGE.value or query.top_k <= 0:
            return base_chunks[:query.top_k]

        base_engine = base_client.engine
        candidates = self.store.chunks_for_version(query.knowledge_base_id, kb_version)
        candidates = [row for row in candidates if _matches(row["metadata"], query.filters)]
        if not candidates:
            return base_chunks[:query.top_k]

        query_terms = tokens(query.text or "")
        corpus = [tokens(row["content"]) for row in candidates]
        bm25_scores = BM25Plus(corpus).get_scores(query_terms) if query_terms and corpus else np.zeros(len(candidates))
        query_term_set = set(query_terms)
        bm25_order = [int(index) for index in np.argsort(-bm25_scores, kind="stable")
                      if query_term_set.intersection(corpus[index])]
        dense_order: list[int] = []
        if query.text.strip():
            with base_engine.lock:
                q = base_engine.provider.encode([query.text], query=True)[0]
            compatible = []
            for index, row in enumerate(candidates):
                vector = row["vector"]
                if vector and len(vector) == q.size * 4:
                    score = float(np.dot(q, np.frombuffer(vector, dtype=np.float32)))
                    if score > 1e-6:
                        compatible.append((index, score))
            dense_order = [index for index, _ in sorted(compatible, key=lambda item: (-item[1], candidates[item[0]]["id"]))]

        ranks: dict[str, float] = {}
        docs_by_id: dict[str, dict] = {}
        for rank, chunk in enumerate(base_chunks, 1):
            docs_by_id[chunk.chunk_id] = {"kind": "base", "chunk": chunk}
            ranks[chunk.chunk_id] = ranks.get(chunk.chunk_id, 0.0) + 1.0 / (60 + rank)
        for order in (bm25_order, dense_order):
            for rank, index in enumerate(order, 1):
                row = candidates[int(index)]
                chunk_id = row["id"]
                docs_by_id[chunk_id] = {"kind": "document", "row": row}
                ranks[chunk_id] = ranks.get(chunk_id, 0.0) + 1.0 / (60 + rank)
        ordered = sorted(ranks, key=lambda chunk_id: (-ranks[chunk_id], chunk_id))[:query.top_k]
        result: list[RetrievedChunk] = []
        for chunk_id in ordered:
            item = docs_by_id[chunk_id]
            if item["kind"] == "base":
                chunk = item["chunk"]
                metadata = {**chunk.metadata, "knowledge_base_id": query.knowledge_base_id, "index_version": kb_version}
                result.append(RetrievedChunk(chunk.chunk_id, chunk.collection, chunk.content, ranks[chunk_id], metadata, chunk.source))
                continue
            row = item["row"]
            metadata = {
                **row["metadata"],
                "title": row["title"],
                "knowledge_base_id": query.knowledge_base_id,
                "index_version": kb_version,
            }
            result.append(RetrievedChunk(
                chunk_id=chunk_id,
                collection=RetrievalCollection.COMPANY_KNOWLEDGE.value,
                content=row["content"],
                score=ranks[chunk_id],
                metadata=metadata,
                source=f"kb://{query.knowledge_base_id}/documents/{row['document_id']}?revision={row['revision']}#page={row['page'] or 1}",
            ))
        return result

