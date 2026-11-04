"""Compatibility entry point for the ID-labelled RAG benchmark.

Legacy structural composites are not reported as Ragas or grounded generation scores.
"""
import json
from pathlib import Path
from src.config import config
from src.rag.storage import RAGStore
from src.rag.indexing import build_index
from src.rag.engine import RAGEngine
from src.rag.evaluation import evaluate, generate_cases, markdown_report


class BenchmarkRunner:
    def __init__(self):
        self.store = RAGStore(Path(config.DATA_DIR) / "rag/index.db")
        try:
            self.store.load()
        except KeyError:
            build_index(self.store, config.DATA_DIR, provider_kind="lsa")
        self.engine = RAGEngine(self.store, config.DATA_DIR)

    def run_benchmark(self):
        report = evaluate(self.engine, generate_cases(self.engine.payload))
        output = Path(config.BASE_DIR) / "output"
        output.mkdir(parents=True, exist_ok=True)
        (output / "retrieval_benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "retrieval_benchmark.md").write_text(markdown_report(report), encoding="utf-8")
        return report


if __name__ == "__main__":
    print(json.dumps(BenchmarkRunner().run_benchmark()["summaries"], indent=2))
