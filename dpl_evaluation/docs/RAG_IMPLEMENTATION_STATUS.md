# Báo cáo triển khai P0–P5

Ngày cập nhật migration: 2026-10-11. Baseline E5 kiểm chứng ngày 2026-10-10. Chi tiết vận hành: [RAG_MODULE.md](RAG_MODULE.md).

## Migration Gemini Embedding API

Provider mặc định trong code/CLI: `gemini-embedding-2`, vector 768 chiều L2-normalize, REST qua httpx. Query/document format theo Google, một request cho mỗi văn bản; persistent hash/vector document cache, RAM-only query cache, bounded timeout/retry, error redaction và email/phone masking. Runtime load provider/dimensions từ snapshot, không trộn embeddings. Docker mặc định không tải Torch/model local. Model alias được Google quản lý, không phải immutable weights revision.

Kiểm chứng migration: **46 tests passed (51.52s)**; Ruff, compileall, git diff whitespace check và Compose config validation passed. Các test Gemini dùng HTTP mock, kiểm tra query/document format, vector normalization/shape/finiteness, query/document cache, missing key, retry 429, error redaction, snapshot reload, fallback retrieval và active snapshot preservation khi build lỗi. Chưa chạy Docker container thực tế do Engine chưa khả dụng.

Máy kiểm chứng chưa cấu hình `GEMINI_API_KEY`/`GOOGLE_API_KEY`. Chưa gọi API thật, chưa publish Gemini snapshot, chưa calibrate và chưa có benchmark Gemini. Snapshot đang active vẫn là E5. Kết quả snapshot, HTTP smoke và benchmark bên dưới là **baseline E5 trước migration**, không phải kết quả Gemini. Không dùng ngưỡng E5 0.82 cho Gemini; score floor 0.0 chỉ là provisional config, cần calibrate dev trước production.

Qwen3 0.6B vẫn là tùy chọn local trong code; việc tải weights đã dừng theo hướng chuyển API. Không có benchmark Qwen được nghiệm thu.

## Phạm vi đã triển khai

| Giai đoạn | Kết quả trong repository |
| --- | --- |
| P0 | Schema hợp đồng, kiểm toán dữ liệu, provenance, phân biệt skill mention và proficiency, bỏ điểm faithfulness giả |
| P1 | BM25, neural multilingual E5, text fusion, LSA baseline rõ tên |
| P2 | Knowledge graph theo miền, alias/concept linking, truy vấn multi-hop có giới hạn, evidence paths |
| P3 | Hybrid fusion, coverage/diversity planner, CV/JD context riêng tư, session/follow-up, adapter legacy |
| P4 | Benchmark theo question ID, group split, ablation, bootstrap, review gate, evaluator grounding riêng |
| P5 | API có xác thực, CLI, lưu trữ snapshot/session, rollback, Docker packaging và hướng dẫn tích hợp |

GraphRAG ở đây là graph theo miền nghiệp vụ phục vụ truy xuất bằng chứng; không phải Microsoft GraphRAG community/global search. Pipeline chạy exact vector search và adjacency phù hợp với corpus hiện tại.

## Kiểm chứng baseline E5 trước migration

- `pytest -q`: **37 passed**.
- Ruff kiểm tra package mới, smoke script và tests: **passed**.
- `compileall`, `git diff --check`, `pip check`: **passed**.
- HTTP smoke bằng tiến trình Uvicorn thật: **passed**; kiểm tra xác thực, context, rubric/source, trace, follow-up không lặp và xóa session.
- Neural smoke trả `IF_AGENTIC_AI_AG_Q09`, `IF_AGENTIC_AI_AG_Q11` và **4 graph paths**.
- Compose validation: **passed**. Chưa build/run container: Docker Desktop Linux Engine không khả dụng trên máy kiểm chứng.

Snapshot: `cee17dd16fbfb135b497ebd0`.

Embedding provider: `neural:intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3:512`.

Corpus: 25 frameworks, 350 technical questions và 75 behavioral questions; tổng 425 atomic bundles, 124 concepts, 1.227 nodes, 3.235 edges. Không có question-level external citation cho 425 bundles; internal source locators/hashes vẫn được giữ. Quan hệ ontology chưa có chuyên gia phê duyệt.

## Benchmark kỹ thuật baseline E5 trước migration

150 synthetic cases được chia theo nhóm, test split 97 cases (77 answerable, 20 OOD). Nhãn **synthetic_unreviewed**, không phải human gold. Kết quả dùng kiểm tra pipeline, không phải tuyên bố chất lượng production.

| Mode | Recall@5 | nDCG@5 | Warm P95 (ms) |
| --- | ---: | ---: | ---: |
| BM25 | 0.8961 | 0.8379 | 18.66 |
| Dense | 0.6753 | 0.6753 | 78.55 |
| Text | 0.8961 | 0.8405 | 18.81 |
| Graph | 0.7532 | 0.5773 | 20.25 |
| Hybrid | 0.9740 | 0.9191 | 27.47 |
| Hybrid, no expansion | 0.9091 | 0.8729 | 27.24 |
| Hybrid, no planner | 0.9610 | 0.9126 | 27.75 |

Các mode có 0 guard violations và rubric integrity 1.0 trên bộ kiểm thử này. Hybrid so với Text: paired group-bootstrap delta nDCG 0.09065, CI95 [0.03556, 0.14996], 32 nhóm. Latency đo local sau warm-up/có query cache, không bao gồm sinh câu trả lời LLM và không phải load-test. Precision với nhãn chưa đầy đủ không phản ánh chính xác mọi câu liên quan.

Báo cáo chi tiết được tạo tại `output/rag_benchmark.md` và `.json` (ignored generated artifacts). Faithfulness chưa đo, trả `null`; `acceptance_ready=false` đúng chủ đích.

## Điều kiện trước production

1. Chuyên gia duyệt câu hỏi/rubric, nguồn, ontology và gold labels; bổ sung query thật, CV/JD ẩn danh và hard negatives.
2. Đánh giá output Interviewer/Grader thật với evidence và annotations độc lập; không tự gán human sign-off.
3. Kiểm chứng Docker trên Docker Engine hoạt động, rồi load-test và hiệu chỉnh dense threshold bằng development labels được review.
4. Triển khai auth/tenant provisioning theo hạ tầng đích nếu cần multi-tenant; API hiện dùng một principal/tenant cố định mỗi instance. Payload có answer/rubric dành cho backend, không chuyển nguyên payload cho ứng viên.

Không tự push Git, publish cloud hoặc mở firewall trong lần triển khai này.
