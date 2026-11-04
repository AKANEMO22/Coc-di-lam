"""Keep legacy ContextAssembler callers compatible with the new engine."""

from src.models.context import RetrievedContext
from src.models.framework import InterviewFramework, TechnicalQuestion
from src.retrieval.base import BaseRetriever

from .schemas import RetrievalRequest


class HybridGraphRAGRetriever(BaseRetriever):
    def __init__(self, engine, mode="hybrid"):
        self.engine, self.mode = engine, mode

    def retrieve(self, candidate, job, top_k_questions=3):
        result = self.engine.retrieve(
            RetrievalRequest(
                candidate=candidate,
                job=job,
                mode=self.mode,
                top_k=top_k_questions,
                token_budget=24000,
            ),
            persist=False,
        )
        if result.status.startswith("unsupported"):
            raise ValueError(result.status)
        fw = InterviewFramework.model_validate(
            self.engine.payload["frameworks"][result.role_id]
        )
        return RetrievedContext(
            position_id=result.role_id,
            role_title=fw.role_title,
            target_level=result.target_level,
            interview_stages=fw.interview_stages,
            evaluation_matrix=fw.evaluation_matrix,
            scoring_anchors=fw.scoring_anchors,
            passing_threshold=fw.passing_thresholds.get(result.target_level, ""),
            selected_technical_questions=[
                TechnicalQuestion(
                    id=q.original_id,
                    question=q.question,
                    level=q.level,
                    expected_answer=q.expected_answer,
                    scoring_rubric=q.rubric,
                    key_concepts=[
                        self.engine.payload["concepts"][c] for c in q.concepts
                    ],
                )
                for q in result.selected_questions
                if q.question_type == "technical"
            ],
            selected_behavioral_questions=fw.behavioral_questions[:1],
            market_context=fw.vn_recruitment_process,
            retrieval_strategy_used="hybrid_graph_rag_v1",
            evidence_paths=[p.model_dump() for p in result.graph_paths],
            source_refs=[
                s.model_dump() for q in result.selected_questions for s in q.sources
            ],
            uncovered_requirements=result.uncovered_requirements,
            index_version=result.index_version,
        )
