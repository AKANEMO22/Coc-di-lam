"""Retrieval backend selection from application settings.

Nối cấu hình ứng dụng với retrieval port khi runtime được khởi tạo.
Mặc định là idq: core không tự chạy RAG, knowledge pack lấy từ dịch vụ ID_Q
(RAG_CocDiLam) theo id_q. build_retrieval_client chọn adapter idq, rag hoặc mock; build_knowledge_service
bọc adapter với timeout và số kết quả theo settings. Client có thể được inject
để tái sử dụng; backend không được nhận diện gây lỗi rõ ràng thay vì đổi sang mock.
"""

from typing import List, Optional

from config.settings import Settings
from core.retrieval.knowledge import InterviewKnowledgeService
from core.retrieval.mock import MockRetrievalClient
from core.retrieval.port import RetrievalClient, RetrievalError, RetrievalQuery, RetrievedChunk


class IDQPackOnlyClient:
    """Placeholder adapter for `RETRIEVAL_BACKEND=idq`: Core never searches a knowledge index itself.

    Sessions get their rubric, question bank and company context from the prepared ID_Q pack
    pinned in the workspace (`inputs/idq_bundle.json`); any direct search is a wiring error.
    """

    backend_name = "idq"

    async def search(self, query: RetrievalQuery) -> List[RetrievedChunk]:
        raise RetrievalError("Core does not run retrieval; knowledge comes from prepared ID_Q packs")


def uses_idq_packs(settings: Settings) -> bool:
    """True when knowledge comes only from ID_Q packs (no in-process retrieval)."""
    return settings.RETRIEVAL_BACKEND.strip().lower() == "idq"


def build_retrieval_client(settings: Settings) -> RetrievalClient:
    """Instantiate the retrieval adapter named by `RETRIEVAL_BACKEND`.

    `idq` (default) performs no retrieval: knowledge comes from ID_Q packs. `rag` is the legacy
    in-process framework-backed retriever; `mock` remains available for
    isolated tests and explicitly selected development fixtures.

    Raises:
        ValueError: If the configured backend is unknown.
    """
    backend = settings.RETRIEVAL_BACKEND.strip().lower()
    if backend == "idq":
        return IDQPackOnlyClient()
    if backend == "rag":
        # Import nhánh RAG khi được chọn để mock không phải khởi tạo phụ thuộc index/embedding.
        from rag.adapter import RAGRetrievalClient

        return RAGRetrievalClient(
            data_dir=settings.RAG_DATA_DIR,
            index_path=settings.RAG_INDEX_PATH,
            embedding_provider=settings.RAG_EMBEDDING_PROVIDER,
        )
    if backend == "mock":
        return MockRetrievalClient()
    raise ValueError(
        f"Unsupported RETRIEVAL_BACKEND '{backend}'. Choose 'idq', 'rag' or 'mock'."
    )


def build_knowledge_service(
    settings: Settings,
    client: Optional[RetrievalClient] = None,
) -> InterviewKnowledgeService:
    """Build the interview knowledge service around the configured (or injected) adapter."""
    return InterviewKnowledgeService(
        client=client or build_retrieval_client(settings),
        timeout_seconds=settings.RETRIEVAL_TIMEOUT_SECONDS,
        questions_per_competency=settings.RETRIEVAL_QUESTIONS_PER_COMPETENCY,
        company_top_k=settings.RETRIEVAL_COMPANY_TOP_K,
    )
