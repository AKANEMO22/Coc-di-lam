"""Document extraction, chunking, embedding, and restartable ingestion worker."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
from pathlib import Path

import numpy as np

from .documents import DocumentStore
from .engine import RAGEngine

logger = logging.getLogger(__name__)
ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


def extract_pages(path: str | Path) -> list[str]:
    path = Path(path)
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        from pypdf import PdfReader

        pages = [page.extract_text() or "" for page in PdfReader(path).pages]
        if not any(text.strip() for text in pages):
            raise ValueError("PDF has no text layer; scanned PDF requires OCR")
        return pages
    if suffix == ".docx":
        from rag.idq_extraction import _extract_pages

        return _extract_pages(path.name, path.read_bytes())
    if suffix in {".txt", ".md"}:
        return [path.read_text(encoding="utf-8-sig")]
    raise ValueError("Supported documents are PDF, DOCX, TXT and Markdown")


def split_pages(pages: list[str], *, max_chars: int = 1400, overlap: int = 180) -> list[dict]:
    chunks: list[dict] = []
    for page_number, page in enumerate(pages, 1):
        text = "\n".join(line.strip() for line in page.splitlines() if line.strip())
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            if end < len(text):
                boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end), text.rfind(" ", start, end))
                if boundary > start + max_chars // 2:
                    end = boundary + (1 if text[boundary:boundary + 1] == "\n" else 0)
            part = text[start:end].strip()
            if part:
                chunks.append({"page": page_number, "content": part})
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks


def _embed_chunks(chunks: list[dict], engine: RAGEngine) -> None:
    texts = [chunk["content"] for chunk in chunks]
    with engine.lock:
        for start in range(0, len(texts), 64):
            vectors = engine.provider.encode(texts[start:start + 64])
            if not np.isfinite(vectors).all():
                raise ValueError("Embedding provider returned a non-finite vector")
            for offset, vector in enumerate(vectors):
                chunks[start + offset]["vector"] = np.asarray(vector, dtype=np.float32).tobytes()


def process_job(store: DocumentStore, job: dict, upload_dir: Path, engine: RAGEngine) -> str:
    source = store.job_input(job["document_id"], job["revision"])
    path = Path(source["storage_path"])
    if not path.is_file() or upload_dir.resolve() not in path.resolve().parents:
        raise ValueError("Stored document is unavailable")
    pages = extract_pages(path)
    chunks = split_pages(pages)
    if not chunks:
        raise ValueError("Document contains no extractable text")
    _embed_chunks(chunks, engine)
    document_metadata = source["metadata"]
    kb_id = source["knowledge_base_id"]
    for index, chunk in enumerate(chunks):
        chunk["id"] = f"{source['id']}:{job['revision']}:{index}"
        chunk["chunk_index"] = index
        chunk["metadata"] = {
            **document_metadata,
            "knowledge_base_id": kb_id,
            "document_id": source["id"],
            "file_name": source["original_name"],
            "page": chunk["page"],
            "topic": document_metadata.get("topic", "general"),
            "company": document_metadata.get("company", "generic"),
            "confidence": float(document_metadata.get("confidence", 0.7)),
            "provenance": "user_uploaded",
            "review_status": document_metadata.get("review_status", "unreviewed"),
            "content_hash": source["content_hash"],
        }
    return store.complete_job(job, pages, chunks, engine.version)


async def run_worker(store: DocumentStore, upload_dir: Path, get_engine, stop: asyncio.Event) -> None:
    """Poll durable jobs; any processing job is requeued on process startup."""
    while not stop.is_set():
        job = await asyncio.to_thread(store.claim_job)
        if not job:
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.75)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            engine = get_engine()
            await asyncio.to_thread(process_job, store, job, upload_dir, engine)
        except Exception as exc:
            logger.exception("Document ingestion failed for job %s", job["id"])
            await asyncio.to_thread(store.fail_job, job, str(exc))


def validate_upload_name(filename: str | None) -> tuple[str, str]:
    name = Path(filename or "document").name
    suffix = Path(name).suffix.casefold()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Supported file types: PDF, DOCX, TXT and Markdown")
    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return name[:255], suffix


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

