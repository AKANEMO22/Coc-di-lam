"""Neural embeddings and an explicitly labelled offline LSA baseline."""

import hashlib
import os
from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path

import numpy as np


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def signature(self) -> str: ...

    @abstractmethod
    def encode(self, texts: list[str], *, query: bool = False) -> np.ndarray: ...


class NeuralEmbedding(EmbeddingProvider):
    def __init__(
        self, model: str = "Qwen/Qwen3-Embedding-0.6B", revision: str | None = None
    ):
        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(int(os.getenv("RAG_TORCH_THREADS", "4")))
        self.name = model
        self.query_cache = OrderedDict()
        self.revision = revision
        self.model = SentenceTransformer(
            model,
            revision=revision,
            device=os.getenv("RAG_DEVICE", "cpu"),
            trust_remote_code=False,
        )
        self.model.max_seq_length = 512
        if Path(model).is_dir():
            hasher = hashlib.sha256()
            for path in sorted(Path(model).rglob("*")):
                if path.is_file() and path.suffix in {".json", ".safetensors", ".bin"}:
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            hasher.update(block)
            self.resolved_revision = None
            self.local_hash = hasher.hexdigest()[:24]
        else:
            from huggingface_hub import hf_hub_download

            config_path = Path(hf_hub_download(model, "config.json", revision=revision))
            self.resolved_revision = config_path.parent.name
            self.local_hash = None

    @property
    def signature(self):
        protocol = ":qwen-query-v1" if "qwen3-embedding" in self.name.casefold() else ""
        return f"neural:{self.name}@{self.resolved_revision or self.local_hash}:512{protocol}"

    def encode(self, texts, *, query=False):
        if not texts:
            return np.empty(
                (0, self.model.get_sentence_embedding_dimension()), dtype=np.float32
            )
        if query and len(texts) == 1:
            key = texts[0]
            if key not in self.query_cache:
                self.query_cache[key] = self._encode([key], query=True)[0]
                if len(self.query_cache) > 128:
                    self.query_cache.popitem(last=False)
            self.query_cache.move_to_end(key)
            return self.query_cache[key].copy()[None, :]
        return self._encode(texts, query=query)

    def _encode(self, texts, *, query=False):
        if "qwen3-embedding" in self.name.casefold():
            # Official Qwen protocol: instruction on queries, no prompt on documents.
            return np.asarray(
                self.model.encode(
                    texts,
                    **({"prompt_name": "query"} if query else {"prompt": ""}),
                    batch_size=4,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                ),
                dtype=np.float32,
            )
        prefix = "query: " if query else "passage: "
        # E5 requires asymmetric prefixes. Other models use their own model prompts.
        if "e5" in self.name.casefold():
            texts = [prefix + t for t in texts]
        method = (
            self.model.encode
            if "e5" in self.name.casefold()
            else self.model.encode_query
            if query
            else self.model.encode_document
        )
        return np.asarray(
            method(
                texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False
            ),
            dtype=np.float32,
        )


class LSAEmbedding(EmbeddingProvider):
    def __init__(self, state: dict | None = None):
        from src.persistence.vector_store import DenseVectorIndex

        self.index = DenseVectorIndex(dim=64)
        if state:
            self.index.vocab = state["vocab"]
            self.index.idf = state["idf"]
            self.index.components = (
                np.asarray(state["components"])
                if state["components"] is not None
                else None
            )

    @property
    def signature(self):
        return "lsa:tfidf-svd-64-v1"

    def fit(self, texts):
        # Fix the seed for scipy svds used by the legacy baseline.
        np.random.seed(42)
        self.index.build_index(
            [{"chunk_id": str(i), "searchable_text": t} for i, t in enumerate(texts)]
        )
        return np.asarray(self.index.doc_vectors, dtype=np.float32)

    def encode(self, texts, *, query=False):
        result = []
        for text in texts:
            counts = {}
            for token in self.index._tokenize(text):
                counts[token] = counts.get(token, 0) + 1
            vector = np.zeros(len(self.index.vocab))
            for token, count in counts.items():
                if token in self.index.vocab:
                    vector[self.index.vocab[token]] = count * self.index.idf[token]
            if self.index.components is not None:
                vector = vector @ self.index.components.T
            norm = np.linalg.norm(vector)
            result.append(vector / norm if norm else vector)
        return np.asarray(result, dtype=np.float32)

    def export(self):
        return {
            "vocab": self.index.vocab,
            "idf": self.index.idf,
            "components": self.index.components.tolist()
            if self.index.components is not None
            else None,
        }
